from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Confidence(Enum):
    """Normalized confidence values for uncertain chart claims."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def label(self) -> str:
        return self.value.title()


class SourceType(Enum):
    """Evidence source categories for chart claims."""

    AUDIO = "audio"
    VIDEO = "video"
    STEM = "stem"
    SCORE = "score"
    TAB = "tab"
    LINER_NOTES = "liner_notes"
    INTERVIEW = "interview"
    SOFTWARE = "software"
    CONTRIBUTOR = "contributor"
    REFERENCE = "reference"
    UNKNOWN = "unknown"


class ClaimOrigin(Enum):
    """How a chart claim entered the system."""

    OBSERVED = "observed"
    COMPUTED = "computed"
    INFERRED = "inferred"
    VERIFIED = "verified"
    USER_ENTERED = "user_entered"
    UNKNOWN = "unknown"


class VerificationStatus(Enum):
    """Verification state of a provenance record."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")


@dataclass(frozen=True)
class EvidenceReference:
    """A concrete citation or time-bound evidence pointer."""

    ref_id: str
    source_name: str | None = None
    source_url: str | None = None
    timestamp_range: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        _validate_timestamp_range(self.timestamp_range)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | str) -> EvidenceReference:
        if isinstance(value, str):
            return cls(ref_id=value)
        return cls(
            ref_id=str(value["ref_id"]),
            source_name=_optional_str(value.get("source_name")),
            source_url=_optional_str(value.get("source_url")),
            timestamp_range=_optional_str(value.get("timestamp_range")),
            notes=_optional_str(value.get("notes")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return _drop_none(
            {
                "ref_id": self.ref_id,
                "source_name": self.source_name,
                "source_url": self.source_url,
                "timestamp_range": self.timestamp_range,
                "notes": self.notes,
            }
        )


@dataclass(frozen=True)
class ProvenanceRecord:
    """Answers how ChordAtlas knows a specific chart claim."""

    source_type: SourceType = SourceType.UNKNOWN
    source_name: str | None = None
    source_url: str | None = None
    timestamp_range: str | None = None
    method: str | None = None
    contributor: str | None = None
    confidence: Confidence | None = None
    created_at: str | None = None
    notes: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNKNOWN
    evidence_refs: tuple[EvidenceReference, ...] = ()
    claim_origin: ClaimOrigin = ClaimOrigin.UNKNOWN

    def __post_init__(self) -> None:
        _validate_timestamp_range(self.timestamp_range)
        if self.claim_origin is ClaimOrigin.INFERRED and self.confidence is None:
            raise ValueError("Inferred provenance records must include confidence")
        if self.verification_status is VerificationStatus.VERIFIED and not self.evidence_refs:
            raise ValueError("Verified provenance records must include at least one evidence reference")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ProvenanceRecord:
        evidence_refs = tuple(
            EvidenceReference.from_mapping(ref) for ref in value.get("evidence_refs", ())
        )
        return cls(
            source_type=_enum_value(SourceType, value.get("source_type"), SourceType.UNKNOWN),
            source_name=_optional_str(value.get("source_name")),
            source_url=_optional_str(value.get("source_url")),
            timestamp_range=_optional_str(value.get("timestamp_range")),
            method=_optional_str(value.get("method")),
            contributor=_optional_str(value.get("contributor")),
            confidence=_confidence_value(value.get("confidence")),
            created_at=_optional_str(value.get("created_at")),
            notes=_optional_str(value.get("notes")),
            verification_status=_enum_value(
                VerificationStatus,
                value.get("verification_status"),
                VerificationStatus.UNKNOWN,
            ),
            evidence_refs=evidence_refs,
            claim_origin=_enum_value(ClaimOrigin, value.get("claim_origin"), ClaimOrigin.UNKNOWN),
        )

    def to_mapping(self) -> dict[str, Any]:
        return _drop_none(
            {
                "source_type": self.source_type.value,
                "source_name": self.source_name,
                "source_url": self.source_url,
                "timestamp_range": self.timestamp_range,
                "method": self.method,
                "contributor": self.contributor,
                "confidence": self.confidence.value if self.confidence else None,
                "created_at": self.created_at,
                "notes": self.notes,
                "verification_status": self.verification_status.value,
                "evidence_refs": [ref.to_mapping() for ref in self.evidence_refs],
                "claim_origin": self.claim_origin.value,
            }
        )

    @property
    def is_unknown(self) -> bool:
        return self.claim_origin is ClaimOrigin.UNKNOWN or self.source_type is SourceType.UNKNOWN


@dataclass(frozen=True)
class ProvenanceMixin:
    """Reusable optional provenance carrier for chart entities."""

    provenance: tuple[ProvenanceRecord, ...] = field(default_factory=tuple)


def parse_provenance(value: Any) -> tuple[ProvenanceRecord, ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        return (ProvenanceRecord.from_mapping(value),)
    return tuple(ProvenanceRecord.from_mapping(item) for item in value)


def provenance_to_mapping(records: tuple[ProvenanceRecord, ...]) -> list[dict[str, Any]]:
    return [record.to_mapping() for record in records]


def provenance_warnings(records: tuple[ProvenanceRecord, ...], *, claim: str) -> tuple[str, ...]:
    warnings: list[str] = []
    for record in records:
        if record.is_unknown:
            warnings.append(f"{claim} has unknown provenance")
    return tuple(warnings)


def format_confidence(value: Confidence | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Confidence):
        return value.label
    normalized = _confidence_value(value)
    return normalized.label if normalized else str(value)


def _confidence_value(value: Any) -> Confidence | None:
    if value is None:
        return None
    if isinstance(value, Confidence):
        return value
    return _enum_value(Confidence, value, None)


def _enum_value(enum_type: type[Enum], value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, enum_type):
        return value
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    for member in enum_type:
        if normalized in {member.value, member.name.lower()}:
            return member
    allowed = ", ".join(member.value for member in enum_type)
    raise ValueError(f"Invalid {enum_type.__name__}: {value!r}. Expected one of: {allowed}")


def _validate_timestamp_range(value: str | None) -> None:
    if value is None:
        return
    parts = re.split(r"\s*[-–]\s*", value)
    if len(parts) != 2 or not all(_TIMESTAMP_RE.match(part) for part in parts):
        raise ValueError(f"Invalid timestamp range: {value!r}")
    if _timestamp_seconds(parts[0]) > _timestamp_seconds(parts[1]):
        raise ValueError(f"Timestamp range starts after it ends: {value!r}")


def _timestamp_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None and item != []}
