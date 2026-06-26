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


def test_chart_serialization_preserves_complete_provenance() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Test",
            "sections": [{"name": "Intro", "bars": [["Cadd9"]]}],
            "chord_provenance": {
                "Cadd9": {
                    "source_type": "audio",
                    "source_name": "Example recording",
                    "source_url": "https://youtube.com/example",
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
