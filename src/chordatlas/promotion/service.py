from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from chordatlas.media import FrameRange, ProjectMediaStore, Timebase
from chordatlas.promotion.mapper import build_draft
from chordatlas.promotion.models import (
    ApprovalRecord,
    MappingConfig,
    PromotionError,
    PromotionResult,
    PromotionSpec,
    RevocationRecord,
    canonical_json,
    issues_digest,
)
from chordatlas.promotion.store import PromotionStore
from chordatlas.render import render_markdown, render_text
from chordatlas.review import ReviewService
from chordatlas.schema import validate_chart_schema


@dataclass(frozen=True)
class PromotionPracticeBasis:
    """Trusted private Stage 4 seam; never include this object in an export."""

    approval_id: str
    promotion_result_id: str
    review_session_id: str
    review_revision_id: str
    reviewed_timeline_id: str
    source_id: str
    asset_id: str
    timebase: Timebase
    analyzed_range: FrameRange
    timing_map: tuple
    meter_numerator: int
    beat_unit: int
    approval_active: bool
    review_current: bool


class PromotionService:
    """Trusted Stage 4 coordinator for exact-review approval and chart export."""

    def __init__(
        self,
        project_root: Path,
        *,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ) -> None:
        self.review = ReviewService(project_root)
        self.store = PromotionStore.initialize(project_root)
        self._clock = clock

    def preview(
        self,
        session_id: str,
        revision_id: str,
        *,
        expected_head_token: str,
        config_mapping: Mapping[str, Any],
    ) -> dict[str, Any]:
        config = MappingConfig.from_mapping(config_mapping)
        session, revision, timeline, candidates = self.review.load_ready_revision(
            session_id,
            revision_id,
            expected_head_token=expected_head_token,
            require_current=True,
        )
        return public_preview(
            self._preview_value(
                session.id,
                revision.id,
                timeline.id,
                timeline,
                candidates,
                config,
            )
        )

    def approve(
        self,
        session_id: str,
        revision_id: str,
        *,
        expected_head_token: str,
        idempotency_key: str,
        config_mapping: Mapping[str, Any],
        expected_spec_id: str,
        expected_result_id: str,
        expected_issue_digest: str,
        acknowledged_issue_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        config = MappingConfig.from_mapping(config_mapping)
        if (
            len(acknowledged_issue_ids) > 2_000
            or acknowledged_issue_ids != tuple(sorted(set(acknowledged_issue_ids)))
        ):
            raise PromotionError(
                "invalid_acknowledgements",
                "Issue acknowledgements must be unique and sorted.",
            )
        with self.review.claim_for_approval(session_id):
            session, revision, timeline, candidates = self.review.load_ready_revision(
                session_id,
                revision_id,
                expected_head_token=expected_head_token,
                require_current=True,
            )
            preview = self._preview_value(
                session.id,
                revision.id,
                timeline.id,
                timeline,
                candidates,
                config,
            )
            if (
                expected_spec_id != preview["spec_id"]
                or expected_result_id != preview["result_id"]
                or expected_issue_digest != preview["issue_digest"]
            ):
                raise PromotionError(
                    "preview_mismatch",
                    "The exact promotion preview changed. Build and review it again.",
                    retryable=True,
                )
            required = tuple(
                item["id"]
                for item in preview["issues"]
                if item["severity"] == "material"
            )
            if acknowledged_issue_ids != tuple(sorted(required)):
                raise PromotionError(
                    "acknowledgement_required",
                    "Acknowledge every material mapping issue from this exact preview.",
                )
            fingerprint = hashlib.sha256(
                canonical_json(
                    {
                        "action": "approve",
                        "session_id": session.id,
                        "revision_id": revision.id,
                        "timeline_id": timeline.id,
                        "config_id": config.id,
                        "spec_id": preview["spec_id"],
                        "result_id": preview["result_id"],
                        "issue_digest": preview["issue_digest"],
                        "acknowledged_issue_ids": list(acknowledged_issue_ids),
                    }
                )
            ).hexdigest()
            approved_at, repeated, receipt_id = self.store.claim_receipt(
                idempotency_key=idempotency_key,
                action="approve",
                fingerprint=fingerprint,
                recorded_at=self._event_time(session.id),
            )
            self._require_current_event_time(session.id, approved_at)
            approval = ApprovalRecord.create(
                session_id=session.id,
                review_revision_id=revision.id,
                reviewed_timeline_id=timeline.id,
                mapping_config_id=config.id,
                spec_id=preview["spec_id"],
                result_id=preview["result_id"],
                receipt_id=receipt_id,
                issue_digest=preview["issue_digest"],
                acknowledged_issue_ids=acknowledged_issue_ids,
                approved_at=approved_at,
            )
            result = self._result_for_preview(preview)
            spec = self._spec_for_preview(preview)
            self.store.publish_approval(
                config=config,
                spec=spec,
                result=result,
                approval=approval,
                allow_existing=repeated,
            )
        return self.get(approval.id)

    def get(self, approval_id: str) -> dict[str, Any]:
        approval, result = self._validated_bundle(approval_id)
        active = self.store.active_for_session(approval.session_id)
        revocation = self.store.revocation_for(approval.session_id, approval.id)
        return {
            "approval": {
                "approval_id": approval.id,
                "review_revision_id": approval.review_revision_id,
                "result_id": approval.result_id,
                "approved_at": approval.approved_at,
                "status": "active" if active and active.id == approval.id else "revoked",
                "token": (
                    self.store.approval_token(approval)
                    if active and active.id == approval.id
                    else None
                ),
                "revocation": (
                    None
                    if revocation is None
                    else {
                        "revoked_at": revocation.revoked_at,
                        "reason": revocation.reason,
                        "prior_exports_not_retracted": True,
                    }
                ),
            },
            "result": result.to_public_mapping(),
        }

    def list_active_for_source(self, source_id: str) -> list[dict[str, Any]]:
        """Return validated active approvals for one local Studio source."""

        values: list[dict[str, Any]] = []
        for session in self.review.list(source_id=source_id):
            active = self.store.active_for_session(str(session["session_id"]))
            if active is None:
                continue
            value = self.get(active.id)
            if value["approval"]["status"] != "active":
                raise PromotionError(
                    "promotion_storage_integrity",
                    "Private promotion storage failed an integrity check.",
                )
            values.append(value)
        return sorted(
            values,
            key=lambda item: (
                str(item["approval"]["approved_at"]),
                str(item["approval"]["approval_id"]),
            ),
        )

    def load_practice_basis(
        self,
        approval_id: str,
        *,
        require_active: bool,
    ) -> PromotionPracticeBasis:
        """Validate the exact private approval/timing/media chain for Stage 6."""

        approval, result = self._validated_bundle(approval_id)
        active = self.store.active_for_session(approval.session_id)
        approval_active = active is not None and active.id == approval.id
        if require_active and not approval_active:
            raise PromotionError(
                "approval_revoked",
                "This approval no longer authorizes a new or changed practice session.",
            )
        session, revision, timeline, candidates = self.review.load_ready_revision(
            approval.session_id,
            approval.review_revision_id,
            expected_head_token=None,
            require_current=False,
        )
        head = self.review.store.load_head(session.id)
        review_current = head.revision_id == revision.id
        if require_active and not review_current:
            raise PromotionError(
                "review_changed",
                "The reviewed chart changed. Create a new approval before practicing it.",
            )
        config = self.store.load_config(approval.mapping_config_id)
        analysis_spec = self.review.analysis.load_spec(candidates.spec_id)
        asset = ProjectMediaStore.initialize(
            self.review.store.project_root
        ).asset_for_source(session.source_id)
        if (
            approval.reviewed_timeline_id != timeline.id
            or result.spec_id != approval.spec_id
            or analysis_spec.asset_id != asset.id
            or analysis_spec.timebase != asset.timebase
            or candidates.timebase != asset.timebase
            or timeline.timebase != asset.timebase
            or timeline.analyzed_range != analysis_spec.analyzed_range
        ):
            raise PromotionError(
                "promotion_storage_integrity",
                "The private promotion timing chain failed an integrity check.",
            )
        _validate_practice_timing_map(
            result.timing_map,
            analyzed_range=timeline.analyzed_range,
        )
        return PromotionPracticeBasis(
            approval_id=approval.id,
            promotion_result_id=result.id,
            review_session_id=session.id,
            review_revision_id=revision.id,
            reviewed_timeline_id=timeline.id,
            source_id=session.source_id,
            asset_id=asset.id,
            timebase=asset.timebase,
            analyzed_range=timeline.analyzed_range,
            timing_map=result.timing_map,
            meter_numerator=config.meter_numerator,
            beat_unit=config.beat_unit,
            approval_active=approval_active,
            review_current=review_current,
        )

    def revoke(
        self,
        approval_id: str,
        *,
        expected_token: str,
        idempotency_key: str,
        reason: str,
    ) -> dict[str, Any]:
        approval = self.store.load_approval(approval_id)
        fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "action": "revoke",
                    "approval_id": approval.id,
                    "reason": reason,
                }
            )
        ).hexdigest()
        revoked_at, _repeated, _receipt_id = self.store.claim_receipt(
            idempotency_key=idempotency_key,
            action="revoke",
            fingerprint=fingerprint,
            recorded_at=self._event_time(approval.session_id),
        )
        self._require_current_event_time(approval.session_id, revoked_at)
        revocation = RevocationRecord.create(
            approval_id=approval.id,
            revoked_at=revoked_at,
            reason=reason,
        )
        self.store.revoke(
            session_id=approval.session_id,
            approval_id=approval.id,
            expected_token=expected_token,
            revocation=revocation,
        )
        return self.get(approval.id)

    def exports(self, approval_id: str) -> dict[str, str]:
        approval = self.store.require_active(approval_id)
        validated, result = self._validated_bundle(approval_id)
        if validated.id != approval.id:
            raise PromotionError(
                "promotion_storage_integrity",
                "Private promotion storage failed an integrity check.",
            )
        validation = validate_chart_schema(result.chart)
        if not validation.passed:
            raise PromotionError(
                "chart_validation_failed",
                "The approved SongChart no longer passes schema validation.",
            )
        return {
            "json": json.dumps(
                result.chart.to_mapping(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            "markdown": render_markdown(result.chart),
            "text": render_text(result.chart),
        }

    def _validated_bundle(
        self,
        approval_id: str,
    ) -> tuple[ApprovalRecord, PromotionResult]:
        approval = self.store.load_approval(approval_id)
        result = self.store.load_result(approval.result_id)
        spec = self.store.load_spec(approval.spec_id)
        config = self.store.load_config(approval.mapping_config_id)
        if (
            spec.session_id != approval.session_id
            or spec.review_revision_id != approval.review_revision_id
            or spec.reviewed_timeline_id != approval.reviewed_timeline_id
            or spec.mapping_config_id != approval.mapping_config_id
            or spec.id != result.spec_id
            or config.id != spec.mapping_config_id
        ):
            raise PromotionError(
                "promotion_storage_integrity",
                "Private promotion storage failed an integrity check.",
            )
        return approval, result

    def _event_time(self, session_id: str) -> str:
        current = self._clock()
        previous = self.store.latest_event_at(session_id)
        if previous is None:
            return current
        try:
            current_value = datetime.fromisoformat(current)
            previous_value = datetime.fromisoformat(previous)
        except (TypeError, ValueError):
            raise PromotionError(
                "promotion_integrity",
                "The promotion clock failed an integrity check.",
            ) from None
        try:
            return previous if current_value < previous_value else current
        except TypeError:
            raise PromotionError(
                "promotion_integrity",
                "The promotion clock failed an integrity check.",
            ) from None

    def _require_current_event_time(self, session_id: str, value: str) -> None:
        latest = self.store.latest_event_at(session_id)
        if latest is None:
            return
        try:
            stale = datetime.fromisoformat(value) < datetime.fromisoformat(latest)
        except (TypeError, ValueError):
            raise PromotionError(
                "promotion_integrity",
                "The promotion clock failed an integrity check.",
            ) from None
        if stale:
            raise PromotionError(
                "idempotency_expired",
                "This idempotent action predates the current approval state.",
            )

    def _preview_value(
        self,
        session_id: str,
        revision_id: str,
        timeline_id: str,
        timeline,
        candidates,
        config: MappingConfig,
    ) -> dict[str, Any]:
        spec = PromotionSpec.create(
            session_id=session_id,
            review_revision_id=revision_id,
            reviewed_timeline_id=timeline_id,
            mapping_config_id=config.id,
        )
        draft = build_draft(timeline, candidates, config)
        validation = validate_chart_schema(draft.chart)
        if not validation.passed:
            raise PromotionError(
                "chart_validation_failed",
                "The promotion preview does not satisfy SongChart 1.0.0.",
            )
        # Rendering now makes renderer compatibility part of the approved preview.
        markdown = render_markdown(draft.chart)
        text = render_text(draft.chart)
        json_output = json.dumps(
            draft.chart.to_mapping(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        result = PromotionResult.create(
            spec_id=spec.id,
            chart=draft.chart,
            issues=draft.issues,
            timing_map=draft.timing_map,
        )
        return {
            "spec_id": spec.id,
            "result_id": result.id,
            "issue_digest": issues_digest(draft.issues),
            "chart": draft.chart.to_mapping(),
            "issues": [item.to_mapping() for item in draft.issues],
            "timing_map": [item.to_mapping() for item in draft.timing_map],
            "renders": {
                "markdown": markdown,
                "text": text,
                "json": json_output,
            },
            "_spec": spec,
            "_result": result,
        }

    @staticmethod
    def _result_for_preview(preview: dict[str, Any]) -> PromotionResult:
        result = preview.get("_result")
        if not isinstance(result, PromotionResult):
            raise PromotionError(
                "promotion_integrity",
                "The promotion preview failed an integrity check.",
            )
        return result

    @staticmethod
    def _spec_for_preview(preview: dict[str, Any]) -> PromotionSpec:
        spec = preview.get("_spec")
        if not isinstance(spec, PromotionSpec):
            raise PromotionError(
                "promotion_integrity",
                "The promotion preview failed an integrity check.",
            )
        return spec


def public_preview(value: dict[str, Any]) -> dict[str, Any]:
    """Strip trusted in-process objects before returning a preview over HTTP."""

    return {key: item for key, item in value.items() if not key.startswith("_")}


def _validate_practice_timing_map(timing_map, *, analyzed_range: FrameRange) -> None:
    if not timing_map:
        raise PromotionError(
            "promotion_storage_integrity",
            "The private promotion timing map is empty.",
        )
    previous_end = analyzed_range.start_frame
    prior_section = None
    closed_sections: set[tuple[int, str]] = set()
    for number, measure in enumerate(timing_map, start=1):
        section = (measure.section_ordinal, measure.section_name)
        if (
            measure.measure_number != number
            or measure.start_frame != previous_end
            or measure.end_frame > analyzed_range.end_frame
        ):
            raise PromotionError(
                "promotion_storage_integrity",
                "The private promotion timing map is not contiguous.",
            )
        if prior_section is not None and section != prior_section:
            closed_sections.add(prior_section)
        if section in closed_sections:
            raise PromotionError(
                "promotion_storage_integrity",
                "A promoted section is split into noncontiguous frame ranges.",
            )
        previous_end = measure.end_frame
        prior_section = section
    if (
        timing_map[0].start_frame != analyzed_range.start_frame
        or previous_end != analyzed_range.end_frame
    ):
        raise PromotionError(
            "promotion_storage_integrity",
            "The private promotion timing map does not cover the reviewed range.",
        )
