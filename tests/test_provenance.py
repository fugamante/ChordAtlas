import json

import pytest

from chordatlas.io import song_chart_to_json
from chordatlas.models import SongChart
from chordatlas.provenance import (
    ClaimOrigin,
    Confidence,
    EvidenceReference,
    ProvenanceRecord,
    SourceType,
    VerificationStatus,
    parse_provenance,
)
from chordatlas.render import render_markdown


def test_provenance_model_creation_normalizes_values() -> None:
    record = ProvenanceRecord.from_mapping(
        {
            "source_type": "Audio",
            "timestamp_range": "00:13-00:18",
            "method": "human-ear transcription",
            "confidence": "Medium",
            "verification_status": "unverified",
            "claim_origin": "inferred",
        }
    )

    assert record.source_type is SourceType.AUDIO
    assert record.confidence is Confidence.MEDIUM
    assert record.claim_origin is ClaimOrigin.INFERRED


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EvidenceReference(ref_id=""),
        lambda: EvidenceReference.from_mapping(""),
        lambda: EvidenceReference.from_mapping({"ref_id": ""}),
    ],
)
def test_evidence_reference_rejects_empty_ref_id(factory) -> None:
    with pytest.raises(ValueError, match=r"^EvidenceReference.ref_id must not be empty$"):
        factory()


def test_evidence_reference_preserves_whitespace_ref_id() -> None:
    assert EvidenceReference(ref_id=" ").ref_id == " "


def test_scalar_evidence_ref_matches_one_item_list() -> None:
    scalar = ProvenanceRecord.from_mapping({"evidence_refs": "source-a"})
    listed = ProvenanceRecord.from_mapping({"evidence_refs": ["source-a"]})

    assert scalar == listed


def test_empty_scalar_evidence_ref_preserves_no_refs() -> None:
    assert ProvenanceRecord.from_mapping({"evidence_refs": ""}).evidence_refs == ()


@pytest.mark.parametrize("value", [{"ref_id": "nested"}, 1])
def test_evidence_refs_reject_wrong_containers(value) -> None:
    with pytest.raises(ValueError, match=r"^evidence_refs must be a string or list$"):
        ProvenanceRecord.from_mapping({"evidence_refs": value})


@pytest.mark.parametrize("item", [1, ["nested"]])
def test_evidence_refs_reject_wrong_items(item) -> None:
    with pytest.raises(ValueError, match=r"^Evidence references must be strings or mappings$"):
        ProvenanceRecord.from_mapping({"evidence_refs": [item]})


@pytest.mark.parametrize("value", ["audio", 1])
def test_provenance_rejects_wrong_containers(value) -> None:
    with pytest.raises(ValueError, match=r"^Provenance must be a mapping or list of mappings$"):
        parse_provenance(value)


def test_provenance_list_items_require_mappings() -> None:
    with pytest.raises(ValueError, match=r"^Provenance records must be mappings$"):
        parse_provenance(["audio"])


def test_evidence_reference_requires_ref_id_before_other_validation() -> None:
    with pytest.raises(ValueError, match=r"^Evidence references must include a ref_id$"):
        EvidenceReference.from_mapping({"timestamp_range": "invalid"})


def test_chart_serialization_preserves_complete_provenance() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "sections": [{"name": "Intro", "bars": [["Cadd9"]]}],
            "chord_provenance": {
                "Cadd9": {
                    "source_type": "audio",
                    "source_name": "Example recording",
                    "source_url": "https://example.invalid/recording",
                    "timestamp_range": "00:13-00:18",
                    "method": "human-ear transcription",
                    "contributor": "Example Contributor",
                    "confidence": "medium",
                    "verification_status": "unverified",
                    "notes": "Chord sounds like Cadd9, but Cmaj7 is possible.",
                    "claim_origin": "inferred",
                }
            },
        }
    )

    payload = json.loads(song_chart_to_json(chart))

    assert payload["chord_provenance"]["Cadd9"][0]["source_name"] == "Example recording"
    assert payload["chord_provenance"]["Cadd9"][0]["confidence"] == "medium"
    assert payload["chord_provenance"]["Cadd9"][0]["notes"].startswith("Chord sounds")


def test_inferred_claim_requires_confidence() -> None:
    with pytest.raises(ValueError, match="Inferred provenance"):
        ProvenanceRecord(claim_origin=ClaimOrigin.INFERRED)


def test_verified_claim_requires_evidence() -> None:
    with pytest.raises(ValueError, match="Verified provenance"):
        ProvenanceRecord(verification_status=VerificationStatus.VERIFIED)


def test_verified_claim_accepts_evidence_reference() -> None:
    record = ProvenanceRecord(
        verification_status=VerificationStatus.VERIFIED,
        evidence_refs=(EvidenceReference(ref_id="stem-bridge", timestamp_range="00:13-00:18"),),
    )

    assert record.verification_status is VerificationStatus.VERIFIED


def test_invalid_timestamp_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="starts after it ends"):
        ProvenanceRecord(timestamp_range="00:18-00:13")


def test_unknown_provenance_is_allowed_but_flagged() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "provenance": {"source_type": "unknown", "claim_origin": "unknown"},
        }
    )

    assert chart.provenance_warnings() == ("chart has unknown provenance",)


def test_markdown_provenance_modes() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "sections": [{"name": "Intro", "bars": [["Cadd9"]]}],
            "chord_provenance": {
                "Cadd9": {
                    "source_type": "audio",
                    "timestamp_range": "00:13-00:18",
                    "method": "human-ear transcription",
                    "confidence": "medium",
                    "verification_status": "unverified",
                    "claim_origin": "inferred",
                }
            },
        }
    )

    minimal = render_markdown(chart, provenance_mode="minimal")
    standard = render_markdown(chart, provenance_mode="standard")
    research = render_markdown(chart, provenance_mode="research")

    assert "Provenance: Unverified, 00:13-00:18, human-ear transcription" in minimal
    assert "Confidence: Medium" in standard
    assert "Status: Unverified" in standard
    assert "Source type: audio" in research
    assert "Method: human-ear transcription" in research
