from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from chordatlas.analysis.models import AnalysisError
from chordatlas.analysis.store import AnalysisStore
from chordatlas.review.models import ReviewEdit, ReviewError, ReviewHead, ReviewedTimeline
from chordatlas.review.store import ReviewStore


class ReviewService:
    """Trusted Stage 3 coordinator; clients never supply raw timelines or paths."""

    def __init__(
        self,
        project_root: Path,
        *,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ) -> None:
        self.analysis = AnalysisStore.initialize(project_root)
        self.store = ReviewStore.initialize(project_root)
        self._clock = clock

    def create(self, run_id: str, *, idempotency_key: str) -> dict[str, Any]:
        run, base = self._base_for_run(run_id)
        session = self.store.create_session(
            source_id=run.source_id,
            analysis_run_id=run.id,
            base=base,
            idempotency_key=idempotency_key,
            created_at=self._clock(),
        )
        return self.get(session.id)

    def list(
        self,
        *,
        source_id: str | None = None,
        analysis_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_sessions(
            source_id=source_id,
            analysis_run_id=analysis_run_id,
        )

    def get(self, session_id: str) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        base = self._base_for_session(session)
        loaded, head, revision, timeline = self.store.current(session.id, base=base)
        return _public_review(
            loaded,
            head,
            revision.id,
            timeline,
            current_depth=self.store.revision_depth(loaded, revision.id),
        )

    def load_ready_revision(
        self,
        session_id: str,
        revision_id: str,
        *,
        expected_head_token: str | None,
        require_current: bool,
    ):
        """Trusted Stage 4 seam for one exact immutable ready revision."""

        session = self.store.load_session(session_id)
        base = self._base_for_session(session)
        if require_current:
            head = self.store.load_head(session.id)
            if expected_head_token != head.token:
                raise ReviewError(
                    "review_conflict",
                    "The review changed. Refresh the promotion draft and try again.",
                    retryable=True,
                )
            if revision_id != head.revision_id:
                raise ReviewError(
                    "review_conflict",
                    "Approval must reference the current ready review revision.",
                    retryable=True,
                )
        revision, timeline = self.store.materialize_revision(
            session,
            revision_id,
            base=base,
        )
        if timeline.phase != "ready_for_approval":
            raise ReviewError(
                "review_not_ready",
                "Only a ready_for_approval review revision can be promoted.",
            )
        return session, revision, timeline, base

    def claim_for_approval(self, session_id: str):
        """Return the trusted Stage 4 transaction lock for a validated session."""

        session = self.store.load_session(session_id)
        return self.store._claim(self.store.locks / f"{session.id}.lock")

    def apply(
        self,
        session_id: str,
        *,
        expected_token: str,
        idempotency_key: str,
        kind: str,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        base = self._base_for_session(session)
        edit = ReviewEdit.create(kind, parameters)
        head, revision, timeline = self.store.apply(
            session.id,
            base=base,
            expected_token=expected_token,
            idempotency_key=idempotency_key,
            edit=edit,
            created_at=self._clock(),
        )
        return _public_review(
            session,
            head,
            revision.id,
            timeline,
            current_depth=self.store.revision_depth(session, revision.id),
        )

    def undo(
        self,
        session_id: str,
        *,
        expected_token: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        base = self._base_for_session(session)
        head, revision, timeline = self.store.undo(
            session.id,
            base=base,
            expected_token=expected_token,
            idempotency_key=idempotency_key,
            created_at=self._clock(),
        )
        return _public_review(
            session,
            head,
            revision.id,
            timeline,
            current_depth=self.store.revision_depth(session, revision.id),
        )

    def redo(
        self,
        session_id: str,
        *,
        expected_token: str,
        idempotency_key: str,
        requested_revision_id: str,
    ) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        base = self._base_for_session(session)
        head, revision, timeline = self.store.redo(
            session.id,
            base=base,
            expected_token=expected_token,
            idempotency_key=idempotency_key,
            requested_revision_id=requested_revision_id,
            created_at=self._clock(),
        )
        return _public_review(
            session,
            head,
            revision.id,
            timeline,
            current_depth=self.store.revision_depth(session, revision.id),
        )

    def _base_for_run(self, run_id: str):
        try:
            run = self.analysis.load_run(run_id)
            state = self.analysis.load_state(run_id)
            if state.status != "succeeded":
                raise ReviewError(
                    "review_unavailable",
                    "Only a successful analysis run can start or restore review.",
                )
            base = self.analysis.timeline_for_run(run_id)
        except ReviewError:
            raise
        except AnalysisError:
            raise ReviewError(
                "review_unavailable",
                "The selected analysis run is unavailable for review.",
            ) from None
        return run, base

    def _base_for_session(self, session):
        run, base = self._base_for_run(session.analysis_run_id)
        if run.source_id != session.source_id or base.id != session.base_timeline_id:
            raise ReviewError(
                "review_storage_integrity",
                "Private review storage failed an integrity check.",
            )
        return base


def _public_review(
    session,
    head: ReviewHead,
    revision_id: str,
    timeline: ReviewedTimeline,
    *,
    current_depth: int,
) -> dict[str, Any]:
    wall_elapsed_ms = _wall_elapsed_ms(session.created_at, head.updated_at)
    return {
        "session": session.to_public_mapping(),
        "head": {
            "token": head.token,
            "generation": head.generation,
            "revision_id": revision_id,
            "can_undo": revision_id != session.root_revision_id,
            "can_redo": bool(head.redo_revision_ids),
            "redo_revision_id": (
                head.redo_revision_ids[-1] if head.redo_revision_ids else None
            ),
            "updated_at": head.updated_at,
        },
        "timeline": timeline.to_public_mapping(),
        "local_measurements": {
            "accepted_edit_events": head.accepted_edit_events,
            "undo_events": head.undo_events,
            "redo_events": head.redo_events,
            "reset_events": head.reset_events,
            "current_applied_edit_depth": current_depth,
            "session_wall_elapsed_ms": wall_elapsed_ms,
            "wall_time_includes_idle": True,
            "external_telemetry": False,
            "interpretation": (
                "Local event counts and wall time only; not accuracy, effort, "
                "productivity, or time saved."
            ),
        },
    }


def _wall_elapsed_ms(start: str, end: str) -> int:
    try:
        start_value = datetime.fromisoformat(start)
        end_value = datetime.fromisoformat(end)
    except ValueError:
        raise ReviewError(
            "review_storage_integrity",
            "Private review storage failed an integrity check.",
        ) from None
    return max(int((end_value - start_value).total_seconds() * 1_000), 0)
