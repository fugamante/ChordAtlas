from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from chordatlas._fs import (
    ProjectAnchor,
    TargetOccupiedError,
    create_text_exclusive,
    replace_text,
)
from chordatlas.analysis.models import ChordCandidateTimeline
from chordatlas.review.models import (
    MAX_REVISIONS,
    ReviewEdit,
    ReviewError,
    ReviewHead,
    ReviewRevision,
    ReviewSession,
    ReviewedTimeline,
    apply_edit,
    edit_from_mapping,
    head_from_mapping,
    revision_from_mapping,
    root_timeline,
    session_from_mapping,
    timeline_from_mapping,
)

_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_TIMELINE_BYTES = 1024 * 1024
_MAX_SESSIONS = 1_000
_MAX_HEAD_EVENTS = MAX_REVISIONS * 4
_SESSION_RE = "review_"
_REVISION_RE = "rrv_"
_EDIT_RE = "red_"
_TIMELINE_RE = "rtl_"


class ReviewStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        storage_root = self.project_root / ".chordatlas"
        try:
            self.anchor = ProjectAnchor.for_project(self.project_root)
        except OSError:
            raise _integrity_error() from None
        self.root = storage_root / "review"
        self.sessions = self.root / "sessions"
        self.edits = self.root / "edits" / "sha256"
        self.revisions = self.root / "revisions" / "sha256"
        self.timelines = self.root / "timelines" / "sha256"
        self.create_keys = self.root / "create-keys"
        self.locks = self.root / "locks"
        self.tmp = self.root / "tmp"
        self._lock = threading.RLock()

    @classmethod
    def initialize(cls, project_root: Path) -> ReviewStore:
        store = cls(project_root)
        for path in (
            store.root,
            store.sessions,
            store.root / "edits",
            store.edits,
            store.root / "revisions",
            store.revisions,
            store.root / "timelines",
            store.timelines,
            store.create_keys,
            store.locks,
            store.tmp,
        ):
            _ensure_dir(path, store.anchor)
        return store

    def _verify_anchor(self) -> None:
        try:
            self.anchor.verify()
        except OSError:
            raise _integrity_error() from None

    def create_session(
        self,
        *,
        source_id: str,
        analysis_run_id: str,
        base: ChordCandidateTimeline,
        idempotency_key: str,
        created_at: str,
    ) -> ReviewSession:
        self._verify_anchor()
        root = root_timeline(base)
        key_hash = _key_hash(idempotency_key)
        request_fingerprint = hashlib.sha256(
            f"create:{source_id}:{analysis_run_id}:{base.id}".encode()
        ).hexdigest()
        session_id = f"review_{hashlib.sha256(f'{key_hash}:{request_fingerprint}'.encode()).hexdigest()[:32]}"
        with self._claim(self.locks / "project.lock"):
            key_path = self.create_keys / f"{key_hash}.json"
            if _path_exists(key_path, self.anchor):
                key_record = self._read_json(key_path)
                if (
                    set(key_record)
                    != {
                        "review_create_key_schema_version",
                        "key_hash",
                        "request_fingerprint",
                        "session_id",
                        "created_at",
                    }
                    or key_record["review_create_key_schema_version"] != "1.0.0-draft"
                    or key_record["key_hash"] != key_hash
                    or key_record["request_fingerprint"] != request_fingerprint
                    or key_record["session_id"] != session_id
                    or not _is_timestamp(key_record["created_at"])
                ):
                    if key_record.get("request_fingerprint") != request_fingerprint:
                        raise ReviewError(
                            "idempotency_conflict",
                            "The idempotency key was used for different review inputs.",
                        )
                    raise _integrity_error()
                created_at = key_record["created_at"]
            else:
                key_record = {
                    "review_create_key_schema_version": "1.0.0-draft",
                    "key_hash": key_hash,
                    "request_fingerprint": request_fingerprint,
                    "session_id": session_id,
                    "created_at": created_at,
                }
                self._publish_immutable(key_path, key_record)
            session_dir = self.sessions / session_id
            if not _path_exists(session_dir, self.anchor) and len(
                _entry_names(self.sessions, self.anchor)
            ) >= _MAX_SESSIONS:
                raise ReviewError(
                    "review_limit",
                    "This project reached its private review-session limit.",
                )
            revision = ReviewRevision.create(
                session_id=session_id,
                parent_revision_id=None,
                edit_id=None,
                timeline_id=root.id,
            )
            session = ReviewSession(
                id=session_id,
                source_id=source_id,
                analysis_run_id=analysis_run_id,
                base_timeline_id=base.id,
                root_revision_id=revision.id,
                created_at=created_at,
            )
            _ensure_dir(session_dir, self.anchor)
            events_dir = session_dir / "events"
            _ensure_dir(events_dir, self.anchor)
            self._publish_timeline(root)
            self._publish_revision(revision)
            self._publish_immutable(session_dir / "session.json", session.to_record_mapping())
            head = ReviewHead(
                session_id=session.id,
                generation=0,
                revision_id=revision.id,
                redo_revision_ids=(),
                accepted_edit_events=0,
                undo_events=0,
                redo_events=0,
                reset_events=0,
                updated_at=created_at,
            )
            event = _event_mapping(
                head,
                event_kind="genesis",
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            self._publish_immutable(events_dir / "00000000.json", event)
            self._replace_head(session_dir / "head.json", head)
            self._publish_immutable(
                session_dir / "create-idempotency.json",
                key_record,
            )
            self._publish_immutable(session_dir / "visible.json", {"session_id": session.id})
            return session

    def list_sessions(
        self,
        *,
        source_id: str | None = None,
        analysis_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._verify_anchor()
        values = []
        for name in _entry_names(self.sessions, self.anchor):
            if not name.startswith("review_"):
                continue
            path = self.sessions / name
            try:
                _require_dir_anchored(path, self.anchor)
                self._read_json(path / "visible.json")
            except ReviewError:
                continue
            session = self.load_session(path.name)
            if source_id is not None and session.source_id != source_id:
                continue
            if analysis_run_id is not None and session.analysis_run_id != analysis_run_id:
                continue
            head = self.load_head(session.id)
            values.append(
                {
                    **session.to_public_mapping(),
                    "generation": head.generation,
                    "updated_at": head.updated_at,
                }
            )
        if len(values) > 1_000:
            raise ReviewError(
                "review_limit",
                "This project has too many review sessions to list.",
            )
        return sorted(values, key=lambda item: (item["created_at"], item["session_id"]))

    def load_session(self, session_id: str) -> ReviewSession:
        self._verify_anchor()
        _validate_session_id(session_id)
        session_dir = self.sessions / session_id
        _require_dir_anchored(session_dir, self.anchor)
        _require_dir_anchored(session_dir / "events", self.anchor)
        try:
            return session_from_mapping(
                self._read_json(session_dir / "session.json")
            )
        except ReviewError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def load_head(self, session_id: str) -> ReviewHead:
        self._verify_anchor()
        session = self.load_session(session_id)
        session_dir = self.sessions / session.id
        latest = self._latest_event_head(session)
        try:
            cached = head_from_mapping(self._read_json(session_dir / "head.json"))
        except ReviewError:
            self._replace_head(session_dir / "head.json", latest)
            return latest
        except (KeyError, TypeError, ValueError):
            self._replace_head(session_dir / "head.json", latest)
            return latest
        if cached.session_id != session.id or cached.generation > latest.generation:
            raise _integrity_error()
        if cached.generation < latest.generation or cached != latest:
            self._replace_head(session_dir / "head.json", latest)
        return latest

    def current(
        self,
        session_id: str,
        *,
        base: ChordCandidateTimeline,
    ) -> tuple[ReviewSession, ReviewHead, ReviewRevision, ReviewedTimeline]:
        self._verify_anchor()
        session = self.load_session(session_id)
        if session.base_timeline_id != base.id:
            raise _integrity_error()
        head = self.load_head(session.id)
        revision, timeline = self.materialize_revision(session, head.revision_id, base=base)
        return session, head, revision, timeline

    def apply(
        self,
        session_id: str,
        *,
        base: ChordCandidateTimeline,
        expected_token: str,
        idempotency_key: str,
        edit: ReviewEdit,
        created_at: str,
    ) -> tuple[ReviewHead, ReviewRevision, ReviewedTimeline]:
        self._verify_anchor()
        request_fingerprint = hashlib.sha256(
            canonical_request(
                {
                    "action": "edit",
                    "session_id": session_id,
                    "expected_token": expected_token,
                    "edit_id": edit.id,
                }
            )
        ).hexdigest()
        key_hash = _key_hash(idempotency_key)
        with self._claim(self.locks / f"{session_id}.lock"):
            repeated = self._idempotent_head(
                session_id,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            if repeated is not None:
                session = self.load_session(session_id)
                revision, timeline = self.materialize_revision(
                    session,
                    repeated.revision_id,
                    base=base,
                )
                return repeated, revision, timeline
            session, head, _current_revision, current = self.current(
                session_id,
                base=base,
            )
            self._require_token(head, expected_token)
            created_at = _monotonic_timestamp(created_at, head.updated_at)
            if current.phase == "ready_for_approval":
                raise ReviewError(
                    "review_ready",
                    "Undo Finish review before making more corrections.",
                )
            # The replay bound includes the root revision.
            if head.accepted_edit_events >= MAX_REVISIONS - 1:
                raise ReviewError("review_limit", "This review reached its edit limit.")
            if head.generation >= _MAX_HEAD_EVENTS:
                raise ReviewError("review_limit", "This review reached its history limit.")
            raw_root = root_timeline(base)
            timeline = apply_edit(current, edit, base=base, raw_root=raw_root)
            revision = ReviewRevision.create(
                session_id=session.id,
                parent_revision_id=head.revision_id,
                edit_id=edit.id,
                timeline_id=timeline.id,
            )
            self._publish_edit(edit)
            self._publish_timeline(timeline)
            self._publish_revision(revision)
            new_head = ReviewHead(
                session_id=session.id,
                generation=head.generation + 1,
                revision_id=revision.id,
                redo_revision_ids=(),
                accepted_edit_events=head.accepted_edit_events + 1,
                undo_events=head.undo_events,
                redo_events=head.redo_events,
                reset_events=head.reset_events + (edit.kind == "reset_to_raw"),
                updated_at=created_at,
                ready_at=created_at if timeline.phase == "ready_for_approval" else None,
            )
            self._commit_event(
                session,
                new_head,
                event_kind=f"edit:{edit.kind}",
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            return new_head, revision, timeline

    def undo(
        self,
        session_id: str,
        *,
        base: ChordCandidateTimeline,
        expected_token: str,
        idempotency_key: str,
        created_at: str,
    ) -> tuple[ReviewHead, ReviewRevision, ReviewedTimeline]:
        self._verify_anchor()
        return self._navigate(
            session_id,
            base=base,
            expected_token=expected_token,
            idempotency_key=idempotency_key,
            created_at=created_at,
            direction="undo",
            requested_revision_id=None,
        )

    def redo(
        self,
        session_id: str,
        *,
        base: ChordCandidateTimeline,
        expected_token: str,
        idempotency_key: str,
        requested_revision_id: str,
        created_at: str,
    ) -> tuple[ReviewHead, ReviewRevision, ReviewedTimeline]:
        self._verify_anchor()
        return self._navigate(
            session_id,
            base=base,
            expected_token=expected_token,
            idempotency_key=idempotency_key,
            created_at=created_at,
            direction="redo",
            requested_revision_id=requested_revision_id,
        )

    def materialize_revision(
        self,
        session: ReviewSession,
        revision_id: str,
        *,
        base: ChordCandidateTimeline,
    ) -> tuple[ReviewRevision, ReviewedTimeline]:
        self._verify_anchor()
        chain: list[ReviewRevision] = []
        seen: set[str] = set()
        current_id: str | None = revision_id
        while current_id is not None:
            if current_id in seen or len(chain) >= MAX_REVISIONS:
                raise _integrity_error()
            seen.add(current_id)
            revision = self._load_revision(current_id)
            if revision.session_id != session.id:
                raise _integrity_error()
            chain.append(revision)
            current_id = revision.parent_revision_id
        if not chain or chain[-1].id != session.root_revision_id:
            raise _integrity_error()
        timeline = root_timeline(base)
        if chain[-1].timeline_id != timeline.id:
            raise _integrity_error()
        for revision in reversed(chain[:-1]):
            if revision.edit_id is None:
                raise _integrity_error()
            edit = self._load_edit(revision.edit_id)
            timeline = apply_edit(
                timeline,
                edit,
                base=base,
                raw_root=root_timeline(base),
            )
            if timeline.id != revision.timeline_id:
                raise _integrity_error()
        stored = self._load_timeline(timeline.id)
        if stored != timeline:
            raise _integrity_error()
        return chain[0], timeline

    def revision_depth(self, session: ReviewSession, revision_id: str) -> int:
        self._verify_anchor()
        depth = 0
        seen: set[str] = set()
        current_id: str | None = revision_id
        while current_id is not None:
            if current_id in seen or depth >= MAX_REVISIONS:
                raise _integrity_error()
            seen.add(current_id)
            revision = self._load_revision(current_id)
            if revision.session_id != session.id:
                raise _integrity_error()
            current_id = revision.parent_revision_id
            if current_id is not None:
                depth += 1
        if not seen or session.root_revision_id not in seen:
            raise _integrity_error()
        return depth

    def _navigate(
        self,
        session_id: str,
        *,
        base: ChordCandidateTimeline,
        expected_token: str,
        idempotency_key: str,
        created_at: str,
        direction: str,
        requested_revision_id: str | None,
    ) -> tuple[ReviewHead, ReviewRevision, ReviewedTimeline]:
        request_fingerprint = hashlib.sha256(
            canonical_request(
                {
                    "action": direction,
                    "session_id": session_id,
                    "expected_token": expected_token,
                    "requested_revision_id": requested_revision_id,
                }
            )
        ).hexdigest()
        key_hash = _key_hash(idempotency_key)
        with self._claim(self.locks / f"{session_id}.lock"):
            repeated = self._idempotent_head(
                session_id,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            if repeated is not None:
                session = self.load_session(session_id)
                revision, timeline = self.materialize_revision(
                    session,
                    repeated.revision_id,
                    base=base,
                )
                return repeated, revision, timeline
            session, head, revision, _timeline = self.current(session_id, base=base)
            self._require_token(head, expected_token)
            created_at = _monotonic_timestamp(created_at, head.updated_at)
            if head.generation >= _MAX_HEAD_EVENTS:
                raise ReviewError("review_limit", "This review reached its history limit.")
            if direction == "undo":
                if revision.parent_revision_id is None:
                    raise ReviewError("undo_unavailable", "The review is already at its root.")
                target_id = revision.parent_revision_id
                redo = head.redo_revision_ids + (revision.id,)
            else:
                if not head.redo_revision_ids:
                    raise ReviewError("redo_unavailable", "There is no review action to redo.")
                target_id = head.redo_revision_ids[-1]
                if requested_revision_id != target_id:
                    raise ReviewError(
                        "redo_conflict",
                        "The requested redo branch is no longer current.",
                        retryable=True,
                    )
                target = self._load_revision(target_id)
                if target.parent_revision_id != head.revision_id:
                    raise _integrity_error()
                redo = head.redo_revision_ids[:-1]
            target_revision, target_timeline = self.materialize_revision(
                session,
                target_id,
                base=base,
            )
            new_head = ReviewHead(
                session_id=session.id,
                generation=head.generation + 1,
                revision_id=target_id,
                redo_revision_ids=redo,
                accepted_edit_events=head.accepted_edit_events,
                undo_events=head.undo_events + (direction == "undo"),
                redo_events=head.redo_events + (direction == "redo"),
                reset_events=head.reset_events,
                updated_at=created_at,
                ready_at=(
                    created_at
                    if target_timeline.phase == "ready_for_approval"
                    else None
                ),
            )
            self._commit_event(
                session,
                new_head,
                event_kind=direction,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            return new_head, target_revision, target_timeline

    def _commit_event(
        self,
        session: ReviewSession,
        head: ReviewHead,
        *,
        event_kind: str,
        key_hash: str,
        request_fingerprint: str,
    ) -> None:
        event_path = (
            self.sessions
            / session.id
            / "events"
            / f"{head.generation:08d}.json"
        )
        self._publish_immutable(
            event_path,
            _event_mapping(
                head,
                event_kind=event_kind,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            ),
        )
        self._replace_head(self.sessions / session.id / "head.json", head)

    def _latest_event_head(self, session: ReviewSession) -> ReviewHead:
        records = self._validated_events(session)
        return records[-1][1]

    def _idempotent_head(
        self,
        session_id: str,
        *,
        key_hash: str,
        request_fingerprint: str,
    ) -> ReviewHead | None:
        session = self.load_session(session_id)
        for event, head in self._validated_events(session):
            if event["key_hash"] != key_hash:
                continue
            if event["request_fingerprint"] != request_fingerprint:
                raise ReviewError(
                    "idempotency_conflict",
                    "The idempotency key was used for a different review action.",
                )
            return head
        return None

    def _validated_events(
        self,
        session: ReviewSession,
    ) -> list[tuple[dict[str, Any], ReviewHead]]:
        events_dir = self.sessions / session.id / "events"
        _require_dir_anchored(events_dir, self.anchor)
        names = _json_names(events_dir, self.anchor)
        if not names or len(names) > _MAX_HEAD_EVENTS + 1:
            raise _integrity_error()
        records: list[tuple[dict[str, Any], ReviewHead]] = []
        previous: ReviewHead | None = None
        try:
            for name in names:
                path = events_dir / name
                event = self._read_json(path)
                _validate_event(event)
                head = head_from_mapping(event["head"])
                if (
                    head.session_id != session.id
                    or head.generation != (0 if previous is None else previous.generation + 1)
                    or path.name != f"{head.generation:08d}.json"
                ):
                    raise _integrity_error()
                self._validate_head_transition(
                    session,
                    previous,
                    head,
                    event["event_kind"],
                )
                records.append((event, head))
                previous = head
        except ReviewError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None
        return records

    def _validate_head_transition(
        self,
        session: ReviewSession,
        previous: ReviewHead | None,
        head: ReviewHead,
        event_kind: str,
    ) -> None:
        if previous is None:
            if (
                event_kind != "genesis"
                or head.generation != 0
                or head.revision_id != session.root_revision_id
                or head.redo_revision_ids
                or any(
                    (
                        head.accepted_edit_events,
                        head.undo_events,
                        head.redo_events,
                        head.reset_events,
                    )
                )
                or head.updated_at != session.created_at
                or head.ready_at is not None
            ):
                raise _integrity_error()
            return
        if datetime.fromisoformat(head.updated_at) < datetime.fromisoformat(
            previous.updated_at
        ):
            raise _integrity_error()
        unchanged = (
            head.accepted_edit_events,
            head.undo_events,
            head.redo_events,
            head.reset_events,
        )
        prior = (
            previous.accepted_edit_events,
            previous.undo_events,
            previous.redo_events,
            previous.reset_events,
        )
        if event_kind.startswith("edit:"):
            kind = event_kind.removeprefix("edit:")
            revision = self._load_revision(head.revision_id)
            if revision.edit_id is None:
                raise _integrity_error()
            edit = self._load_edit(revision.edit_id)
            timeline = self._load_timeline(revision.timeline_id)
            expected = (
                prior[0] + 1,
                prior[1],
                prior[2],
                prior[3] + (kind == "reset_to_raw"),
            )
            if (
                kind != edit.kind
                or revision.session_id != session.id
                or revision.parent_revision_id != previous.revision_id
                or unchanged != expected
                or head.redo_revision_ids
            ):
                raise _integrity_error()
            self._validate_ready_time(head, timeline)
            return
        if event_kind == "undo":
            current = self._load_revision(previous.revision_id)
            target = self._load_revision(head.revision_id)
            timeline = self._load_timeline(target.timeline_id)
            expected = (prior[0], prior[1] + 1, prior[2], prior[3])
            if (
                current.parent_revision_id != head.revision_id
                or target.session_id != session.id
                or head.redo_revision_ids
                != previous.redo_revision_ids + (previous.revision_id,)
                or unchanged != expected
            ):
                raise _integrity_error()
            self._validate_ready_time(head, timeline)
            return
        if event_kind == "redo":
            if not previous.redo_revision_ids:
                raise _integrity_error()
            target_id = previous.redo_revision_ids[-1]
            target = self._load_revision(target_id)
            timeline = self._load_timeline(target.timeline_id)
            expected = (prior[0], prior[1], prior[2] + 1, prior[3])
            if (
                head.revision_id != target_id
                or target.session_id != session.id
                or target.parent_revision_id != previous.revision_id
                or head.redo_revision_ids != previous.redo_revision_ids[:-1]
                or unchanged != expected
            ):
                raise _integrity_error()
            self._validate_ready_time(head, timeline)
            return
        raise _integrity_error()

    @staticmethod
    def _validate_ready_time(head: ReviewHead, timeline: ReviewedTimeline) -> None:
        expected = head.updated_at if timeline.phase == "ready_for_approval" else None
        if head.ready_at != expected:
            raise _integrity_error()

    def _load_edit(self, edit_id: str) -> ReviewEdit:
        try:
            return edit_from_mapping(self._read_json(self._digest_path(self.edits, edit_id, _EDIT_RE)))
        except ReviewError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def _load_revision(self, revision_id: str) -> ReviewRevision:
        try:
            return revision_from_mapping(
                self._read_json(self._digest_path(self.revisions, revision_id, _REVISION_RE))
            )
        except ReviewError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def _load_timeline(self, timeline_id: str) -> ReviewedTimeline:
        try:
            return timeline_from_mapping(
                self._read_json(self._digest_path(self.timelines, timeline_id, _TIMELINE_RE))
            )
        except ReviewError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def _publish_edit(self, edit: ReviewEdit) -> None:
        self._publish_immutable(
            self._digest_path(self.edits, edit.id, _EDIT_RE, create=True),
            edit.to_record_mapping(),
        )

    def _publish_revision(self, revision: ReviewRevision) -> None:
        self._publish_immutable(
            self._digest_path(
                self.revisions,
                revision.id,
                _REVISION_RE,
                create=True,
            ),
            revision.to_record_mapping(),
        )

    def _publish_timeline(self, timeline: ReviewedTimeline) -> None:
        if len(json.dumps(timeline.to_record_mapping(), allow_nan=False).encode()) > _MAX_TIMELINE_BYTES:
            raise ReviewError(
                "review_limit",
                "The reviewed timeline is too large for this local release.",
            )
        self._publish_immutable(
            self._digest_path(
                self.timelines,
                timeline.id,
                _TIMELINE_RE,
                create=True,
            ),
            timeline.to_record_mapping(),
        )

    def _require_token(self, head: ReviewHead, expected: str) -> None:
        if expected != head.token:
            raise ReviewError(
                "review_precondition_failed",
                "This review changed in another tab. Reload before editing.",
                retryable=True,
            )

    def _digest_path(
        self,
        root: Path,
        identifier: str,
        prefix: str,
        *,
        create: bool = False,
    ) -> Path:
        if not identifier.startswith(prefix) or len(identifier) != len(prefix) + 64:
            raise _integrity_error()
        digest = identifier[len(prefix) :]
        if any(character not in "0123456789abcdef" for character in digest):
            raise _integrity_error()
        parent = root / digest[:2]
        if create:
            _ensure_dir(parent, self.anchor)
        else:
            _require_dir_anchored(parent, self.anchor)
        return parent / f"{digest}.json"

    def _publish_immutable(self, path: Path, value: dict[str, Any]) -> None:
        payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        try:
            create_text_exclusive(
                path,
                payload,
                stage_prefix=".review-",
                mode=0o600,
                sync_directory=True,
                anchor=self.anchor,
            )
        except TargetOccupiedError:
            if self._read_json(path) != value:
                raise _integrity_error()
        except OSError:
            raise _integrity_error() from None

    def _replace_head(self, path: Path, head: ReviewHead) -> None:
        payload = json.dumps(head.to_record_mapping(), indent=2, sort_keys=True) + "\n"
        try:
            create_text_exclusive(
                path,
                payload,
                stage_prefix=".review-head-",
                mode=0o600,
                sync_directory=True,
                anchor=self.anchor,
            )
            return
        except TargetOccupiedError:
            pass
        except OSError:
            raise _integrity_error() from None
        try:
            replace_text(
                path,
                payload,
                stage_prefix=".review-head-",
                prepublish=_guard_leaf,
                sync_directory=True,
                anchor=self.anchor,
            )
        except OSError:
            raise _integrity_error() from None

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            with self.anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
        except OSError:
            raise _integrity_error() from None
        try:
            entry = os.fstat(descriptor)
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o600
                or entry.st_nlink != 1
                or entry.st_size > _MAX_JSON_BYTES
            ):
                raise _integrity_error()
            payload = bytearray()
            while len(payload) <= _MAX_JSON_BYTES:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, _MAX_JSON_BYTES + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            if (
                len(payload) > _MAX_JSON_BYTES
                or (entry.st_dev, entry.st_ino, entry.st_size)
                != (after.st_dev, after.st_ino, after.st_size)
            ):
                raise _integrity_error()
            value = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except (OSError, UnicodeError, ValueError):
            raise _integrity_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(value, dict):
            raise _integrity_error()
        return value

    @contextmanager
    def _claim(self, path: Path) -> Iterator[None]:
        descriptor = _open_lock(path, self.anchor)
        try:
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ReviewError(
                            "review_busy",
                            "Review storage is busy. Retry the operation.",
                            retryable=True,
                        ) from None
                    time.sleep(0.01)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def canonical_request(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _event_mapping(
    head: ReviewHead,
    *,
    event_kind: str,
    key_hash: str,
    request_fingerprint: str,
) -> dict[str, Any]:
    return {
        "review_head_event_schema_version": "1.0.0-draft",
        "event_kind": event_kind,
        "key_hash": key_hash,
        "request_fingerprint": request_fingerprint,
        "head": head.to_record_mapping(),
    }


def _validate_event(value: dict[str, Any]) -> None:
    if set(value) != {
        "review_head_event_schema_version",
        "event_kind",
        "key_hash",
        "request_fingerprint",
        "head",
    }:
        raise _integrity_error()
    if (
        value["review_head_event_schema_version"] != "1.0.0-draft"
        or not isinstance(value["event_kind"], str)
        or not _is_hex(value["key_hash"], 64)
        or not _is_hex(value["request_fingerprint"], 64)
        or not isinstance(value["head"], dict)
    ):
        raise _integrity_error()


def _key_hash(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 8 <= len(value) <= 128
        or not value.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ReviewError("invalid_idempotency_key", "Idempotency key is invalid.")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _validate_session_id(value: str) -> None:
    if not isinstance(value, str) or not value.startswith(_SESSION_RE) or len(value) != 39:
        raise ReviewError("invalid_review_id", "Review session identifier is invalid.")
    if not _is_hex(value[len(_SESSION_RE) :], 32):
        raise ReviewError("invalid_review_id", "Review session identifier is invalid.")


def _is_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _monotonic_timestamp(value: str, previous: str) -> str:
    if not _is_timestamp(value) or not _is_timestamp(previous):
        raise _integrity_error()
    return (
        previous
        if datetime.fromisoformat(value) < datetime.fromisoformat(previous)
        else value
    )


def _guard_leaf(parent_fd: int, leaf: str) -> None:
    try:
        entry = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o600
        or entry.st_nlink != 1
    ):
        raise _integrity_error()


def _open_lock(path: Path, anchor: ProjectAnchor) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    for attempt in range(20):
        try:
            with anchor.parent(path) as (parent_fd, leaf):
                descriptor = os.open(leaf, flags, 0o600, dir_fd=parent_fd)
            break
        except FileNotFoundError:
            # Concurrent O_CREAT calls can transiently report ENOENT on some
            # filesystems. Retry only that benign race; other failures remain
            # fail-closed.
            if attempt < 19:
                time.sleep(0.001)
                continue
            raise _integrity_error() from None
        except OSError:
            raise _integrity_error() from None
    if descriptor < 0:
        raise _integrity_error()
    entry = os.fstat(descriptor)
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o600
        or entry.st_nlink != 1
    ):
        os.close(descriptor)
        raise _integrity_error()
    return descriptor


def _ensure_dir(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path), create=True):
            pass
    except OSError:
        raise _integrity_error() from None


def _require_dir_anchored(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path)):
            pass
    except OSError:
        raise _integrity_error() from None


def _path_exists(path: Path, anchor: ProjectAnchor) -> bool:
    try:
        with anchor.parent(path) as (parent_fd, leaf):
            os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise _integrity_error() from None


def _entry_names(path: Path, anchor: ProjectAnchor) -> tuple[str, ...]:
    try:
        with anchor.directory(anchor.relative(path)) as descriptor:
            names = tuple(sorted(os.listdir(descriptor)))
    except OSError:
        raise _integrity_error() from None
    if any(Path(name).name != name for name in names):
        raise _integrity_error()
    return names


def _json_names(path: Path, anchor: ProjectAnchor) -> tuple[str, ...]:
    names: list[str] = []
    for name in _entry_names(path, anchor):
        if name.startswith(".review-") and name.endswith(".tmp"):
            continue
        if not name.endswith(".json"):
            raise _integrity_error()
        names.append(name)
    return tuple(names)


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def _integrity_error() -> ReviewError:
    return ReviewError(
        "review_storage_integrity",
        "Private review storage failed an integrity check.",
    )
