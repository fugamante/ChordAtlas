import json
from pathlib import Path

import pytest

from chordatlas.compare import (
    ComparisonFilters,
    comparison_metadata_to_json,
    comparison_metadata_to_mapping,
    comparison_to_csv,
    comparison_to_json,
    comparison_to_mapping,
    render_comparison_markdown,
    render_comparison_text,
)
from chordatlas.io import load_song_chart
from chordatlas.models import SongChart

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "tests" / "snapshots"
RESEARCH_EXAMPLE = ROOT / "examples" / "research-recording-comparison.yaml"


def _research_snapshot_filters() -> ComparisonFilters:
    return ComparisonFilters.from_values(
        categories=["effects"],
        recording_ids=["remaster"],
        severities=["medium"],
        source_specific_only=True,
    )


def _chart() -> SongChart:
    return SongChart.from_mapping(
        {
            "title": "Comparison Test",
            "artist": "Example Artist",
            "recordings": {
                "studio": "Studio recording",
                "live": "Live performance",
            },
            "structured_recording_notes": {
                "Guitar 1": {
                    "category": "instrumentation",
                    "recording_ids": ["studio", "live"],
                    "notes": ["Open voicings"],
                },
                "Effects": {
                    "category": "effects",
                    "notes": [
                        {
                            "text": "Light chorus",
                            "recording_id": "studio",
                            "severity": "medium",
                        },
                        {
                            "text": "Dryer amp tone",
                            "recording_id": "live",
                            "severity": "low",
                        },
                    ],
                },
                "Estimated tuning": {
                    "category": "tuning",
                    "notes": [
                        {
                            "text": "Approximately 15 cents flat",
                            "recording_id": "studio",
                            "severity": "high",
                        }
                    ],
                },
            },
        }
    )


def _provenance_chart() -> SongChart:
    return SongChart.from_mapping(
        {
            "title": "Provenance Comparison",
            "recordings": {
                "studio": {
                    "title": "Studio recording",
                    "source_url": "https://example.invalid/studio",
                    "version_label": "Studio reference",
                }
            },
            "structured_recording_notes": {
                "Estimated tuning": {
                    "category": "tuning",
                    "notes": [
                        {
                            "text": "Approximately 15 cents flat",
                            "recording_id": "studio",
                            "severity": "high",
                            "confidence": "medium",
                            "claim_origin": "inferred",
                            "provenance": {
                                "source_type": "audio",
                                "source_name": "Studio recording",
                                "source_url": "https://example.invalid/studio",
                                "timestamp_range": "00:00-00:10",
                                "method": "tuner comparison",
                                "confidence": "medium",
                                "verification_status": "verified",
                                "claim_origin": "inferred",
                                "evidence_refs": [
                                    {
                                        "ref_id": "studio-00-00",
                                        "source_name": "Studio recording",
                                        "source_url": "https://example.invalid/studio",
                                        "timestamp_range": "00:00-00:10",
                                    }
                                ],
                            },
                        }
                    ],
                }
            },
        }
    )


def _duplicate_title_chart() -> SongChart:
    return SongChart.from_mapping(
        {
            "title": "Duplicate titles",
            "recordings": {
                "studio": "Reference",
                "live": "Reference",
            },
            "structured_recording_notes": {
                "Effects": {
                    "category": "effects",
                    "notes": [
                        {"text": "Chorus", "recording_id": "studio"},
                        {"text": "Dry", "recording_id": "live"},
                    ],
                }
            },
        }
    )


def _colliding_category_chart() -> SongChart:
    return SongChart.from_mapping(
        {
            "title": "Category labels",
            "recordings": {"studio": "Studio"},
            "structured_recording_notes": [
                {
                    "name": "Underscore",
                    "category": "source_quality",
                    "notes": [{"text": "Clear", "recording_id": "studio"}],
                },
                {
                    "name": "Space",
                    "category": "source quality",
                    "notes": [{"text": "Noisy", "recording_id": "studio"}],
                },
                {
                    "name": "Case",
                    "category": "Source Quality",
                    "notes": [{"text": "Mixed", "recording_id": "studio"}],
                },
            ],
        }
    )


def test_comparison_mapping_exposes_summary_counts_and_source_specific_claims() -> None:
    payload = comparison_to_mapping(_chart())

    assert payload["comparison_schema_version"] == "1.0.0"
    assert payload["summary"]["recording_count"] == 2
    assert payload["summary"]["category_count"] == 3
    assert payload["summary"]["claim_count"] == 5
    assert payload["summary"]["shared_claim_count"] == 1
    assert payload["summary"]["source_specific_claim_count"] == 3
    assert payload["summary"]["severity_counts"] == {"high": 1, "medium": 1, "low": 1}
    assert payload["summary"]["recordings"] == [
        {
            "id": "studio",
            "title": "Studio recording",
            "claim_count": 3,
            "source_specific_count": 2,
        },
        {
            "id": "live",
            "title": "Live performance",
            "claim_count": 2,
            "source_specific_count": 1,
        },
    ]

    effects = next(
        category for category in payload["categories"] if category["category"] == "effects"
    )
    assert effects["severity_counts"] == {"medium": 1, "low": 1}
    assert effects["differences"] == [
        {
            "recording_id": "studio",
            "title": "Studio recording",
            "claims": [
                {
                    "group": "Effects",
                    "text": "Light chorus",
                    "source_specific": True,
                    "severity": "medium",
                }
            ],
        },
        {
            "recording_id": "live",
            "title": "Live performance",
            "claims": [
                {
                    "group": "Effects",
                    "text": "Dryer amp tone",
                    "source_specific": True,
                    "severity": "low",
                }
            ],
        },
    ]


def test_duplicate_recording_titles_are_qualified_only_in_human_outputs() -> None:
    chart = _duplicate_title_chart()

    payload = comparison_to_mapping(chart)
    markdown = render_comparison_markdown(chart)
    text = render_comparison_text(chart)

    assert [(row["id"], row["title"]) for row in payload["recordings"]] == [
        ("studio", "Reference"),
        ("live", "Reference"),
    ]
    for rendered in (markdown, text):
        assert "Reference [studio]" in rendered
        assert "Reference [live]" in rendered
    assert "### Reference\n" not in markdown


def test_filtered_unique_recording_title_remains_plain() -> None:
    rendered = render_comparison_markdown(
        _duplicate_title_chart(),
        filters=ComparisonFilters.from_values(recording_ids=["studio"]),
    )

    assert "Reference [studio]" not in rendered
    assert "### Reference\n" in rendered


def test_colliding_category_labels_are_qualified_only_in_human_outputs() -> None:
    chart = _colliding_category_chart()

    payload = comparison_to_mapping(chart)
    markdown = render_comparison_markdown(chart)
    text = render_comparison_text(chart)

    assert [
        (category["category"], category["label"])
        for category in payload["categories"]
    ] == [
        ("source_quality", "Source Quality"),
        ("source quality", "Source Quality"),
        ("Source Quality", "Source Quality"),
    ]
    for rendered in (markdown, text):
        assert "Source Quality [source_quality]" in rendered
        assert "Source Quality [source quality]" in rendered
        assert "Source Quality [Source Quality]" in rendered
    assert "## Source Quality\n" not in markdown
    assert "SOURCE QUALITY [source_quality]" in text.splitlines()
    assert "SOURCE QUALITY [source quality]" in text.splitlines()
    assert "SOURCE QUALITY [Source Quality]" in text.splitlines()


def test_filtered_unique_category_label_remains_plain() -> None:
    rendered = render_comparison_markdown(
        _colliding_category_chart(),
        filters=ComparisonFilters.from_values(categories=["source_quality"]),
    )

    assert "Source Quality [source_quality]" not in rendered
    assert "## Source Quality\n" in rendered


def test_comparison_json_is_machine_readable() -> None:
    payload = json.loads(comparison_to_json(_chart()))

    assert payload["chart_title"] == "Comparison Test"
    assert payload["summary"]["source_specific_claim_count"] == 3


def test_comparison_minimal_mode_keeps_claims_compact() -> None:
    payload = comparison_to_mapping(_provenance_chart())
    claim = payload["categories"][0]["recordings"][0]["claims"][0]

    assert "confidence" not in claim
    assert "claim_origin" not in claim
    assert "provenance_summary" not in claim
    assert "source_url" not in payload["categories"][0]["recordings"][0]


def test_comparison_standard_mode_adds_provenance_summary() -> None:
    payload = comparison_to_mapping(_provenance_chart(), provenance_mode="standard")
    recording = payload["categories"][0]["recordings"][0]
    claim = recording["claims"][0]

    assert recording["source_url"] == "https://example.invalid/studio"
    assert recording["version_label"] == "Studio reference"
    assert claim["confidence"] == "medium"
    assert claim["claim_origin"] == "inferred"
    assert "audio / Studio recording / 00:00-00:10 / tuner comparison" in claim[
        "provenance_summary"
    ]
    assert "provenance" not in claim
    assert "evidence_refs" not in claim


def test_comparison_research_mode_adds_full_provenance_and_evidence_refs() -> None:
    payload = comparison_to_mapping(_provenance_chart(), provenance_mode="research")
    claim = payload["categories"][0]["recordings"][0]["claims"][0]

    assert claim["evidence_refs"][0]["ref_id"] == "studio-00-00"
    assert claim["provenance"][0]["method"] == "tuner comparison"


def test_duplicate_claims_merge_distinct_provenance_in_stable_order() -> None:
    source_a = {
        "source_type": "audio",
        "source_name": "Source A",
        "verification_status": "verified",
        "claim_origin": "observed",
        "evidence_refs": [{"ref_id": "source-a"}],
    }
    source_b = {
        "source_type": "audio",
        "source_name": "Source B",
        "verification_status": "verified",
        "claim_origin": "observed",
        "evidence_refs": [{"ref_id": "source-b"}],
    }
    chart = SongChart.from_mapping(
        {
            "title": "Duplicate Evidence",
            "recordings": {"studio": "Studio recording"},
            "structured_recording_notes": {
                "Effects": {
                    "category": "effects",
                    "recording_id": "studio",
                    "notes": [
                        {"text": "Light chorus", "severity": "medium", "provenance": source_a},
                        {"text": "Light chorus", "severity": "medium", "provenance": source_b},
                        {"text": "Light chorus", "severity": "medium", "provenance": source_a},
                        {"text": "Light chorus", "severity": "high", "provenance": source_b},
                    ],
                }
            },
        }
    )

    minimal = comparison_to_mapping(chart)
    research = comparison_metadata_to_mapping(chart)
    claim = research["comparison"]["categories"][0]["recordings"][0]["claims"][0]

    assert minimal["summary"]["claim_count"] == 1
    assert minimal["categories"][0]["recordings"][0]["claims"] == [
        {
            "group": "Effects",
            "text": "Light chorus",
            "source_specific": False,
            "severity": "medium",
        }
    ]
    assert [record["source_name"] for record in claim["provenance"]] == [
        "Source A",
        "Source B",
    ]
    assert [ref["ref_id"] for ref in claim["evidence_refs"]] == ["source-a", "source-b"]
    assert [record["source_name"] for record in research["provenance_records"]] == [
        "Source A",
        "Source B",
    ]
    assert [ref["ref_id"] for ref in research["evidence_refs"]] == [
        "source-a",
        "source-b",
    ]


def test_single_claim_deduplicates_identical_group_and_note_provenance() -> None:
    provenance = {
        "source_type": "audio",
        "source_name": "Shared source",
        "verification_status": "verified",
        "claim_origin": "observed",
        "evidence_refs": [{"ref_id": "shared-source"}],
    }
    chart = SongChart.from_mapping(
        {
            "title": "Duplicate Group Evidence",
            "recordings": {"studio": "Studio recording"},
            "structured_recording_notes": {
                "Effects": {
                    "category": "effects",
                    "recording_id": "studio",
                    "provenance": provenance,
                    "notes": [{"text": "Light chorus", "provenance": provenance}],
                }
            },
        }
    )

    payload = comparison_metadata_to_mapping(chart)
    claim = payload["comparison"]["categories"][0]["recordings"][0]["claims"][0]

    assert len(claim["provenance"]) == 1
    assert [record["source_name"] for record in payload["provenance_records"]] == [
        "Shared source"
    ]
    assert [ref["ref_id"] for ref in payload["evidence_refs"]] == ["shared-source"]


def test_delimiter_collisions_remain_distinct_within_one_recording() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Claim Identity",
            "recordings": {"studio": "Studio recording"},
            "structured_recording_notes": [
                {
                    "name": "A: B",
                    "category": "effects",
                    "recording_ids": ["studio"],
                    "notes": ["C"],
                },
                {
                    "name": "A",
                    "category": "effects",
                    "recording_ids": ["studio"],
                    "notes": ["B: C"],
                },
            ],
        }
    )

    payload = comparison_to_mapping(chart)
    claims = payload["categories"][0]["recordings"][0]["claims"]

    assert payload["summary"]["claim_count"] == 2
    assert [(claim["group"], claim["text"]) for claim in claims] == [
        ("A: B", "C"),
        ("A", "B: C"),
    ]


def test_delimiter_collisions_are_source_specific_across_recordings() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Claim Identity",
            "recordings": {"studio": "Studio recording", "live": "Live recording"},
            "structured_recording_notes": [
                {
                    "name": "A: B",
                    "category": "effects",
                    "recording_ids": ["studio"],
                    "notes": ["C"],
                },
                {
                    "name": "A",
                    "category": "effects",
                    "recording_ids": ["live"],
                    "notes": ["B: C"],
                },
            ],
        }
    )

    payload = comparison_to_mapping(chart)
    rows = payload["categories"][0]["recordings"]

    assert payload["summary"]["shared_claim_count"] == 0
    assert payload["summary"]["source_specific_claim_count"] == 2
    assert [row["source_specific_count"] for row in rows] == [1, 1]
    assert [row["claims"][0]["source_specific"] for row in rows] == [True, True]


def test_conflicting_claim_semantics_keep_the_first_row() -> None:
    chart = SongChart.from_mapping(
        {
            "title": "Conflict Policy",
            "recordings": {"studio": "Studio recording"},
            "structured_recording_notes": {
                "Effects": {
                    "category": "effects",
                    "recording_ids": ["studio"],
                    "notes": [
                        {
                            "text": "Light chorus",
                            "severity": "medium",
                            "confidence": "medium",
                            "claim_origin": "inferred",
                        },
                        {
                            "text": "Light chorus",
                            "severity": "high",
                            "confidence": "high",
                            "claim_origin": "observed",
                        },
                    ],
                }
            },
        }
    )

    payload = comparison_to_mapping(chart, provenance_mode="standard")
    claims = payload["categories"][0]["recordings"][0]["claims"]

    assert payload["summary"]["claim_count"] == 1
    assert claims == [
        {
            "group": "Effects",
            "text": "Light chorus",
            "source_specific": False,
            "severity": "medium",
            "confidence": "medium",
            "claim_origin": "inferred",
        }
    ]


def test_comparison_filters_category_recording_severity_and_source_specific_claims() -> None:
    payload = comparison_to_mapping(
        _chart(),
        filters=ComparisonFilters.from_values(
            categories=["effects"],
            recording_ids=["studio"],
            severities=["medium"],
            source_specific_only=True,
        ),
    )

    assert payload["recordings"] == [{"id": "studio", "title": "Studio recording"}]
    assert payload["summary"]["category_count"] == 1
    assert payload["summary"]["claim_count"] == 1
    assert payload["summary"]["source_specific_claim_count"] == 1
    assert payload["categories"][0]["category"] == "effects"
    assert payload["categories"][0]["recordings"][0]["claims"] == [
        {
            "group": "Effects",
            "text": "Light chorus",
            "source_specific": True,
            "severity": "medium",
        }
    ]


def test_comparison_filter_rejects_unknown_recording_ids() -> None:
    try:
        comparison_to_mapping(
            _chart(),
            filters=ComparisonFilters.from_values(recording_ids=["missing"]),
        )
    except ValueError as error:
        assert "Unknown recording filter(s): missing" in str(error)
    else:
        raise AssertionError("unknown recording filter did not fail")


def test_comparison_csv_is_tabular() -> None:
    rendered = comparison_to_csv(
        _chart(),
        filters=ComparisonFilters.from_values(categories=["effects"]),
    )

    assert rendered.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific"
    )
    assert "effects,Effects,studio,Studio recording,Effects,Light chorus,medium,true" in rendered
    assert "effects,Effects,live,Live performance,Effects,Dryer amp tone,low,true" in rendered


def test_comparison_csv_standard_mode_uses_extended_columns() -> None:
    rendered = comparison_to_csv(_provenance_chart(), provenance_mode="standard")

    assert rendered.splitlines()[0] == (
        "category,category_label,recording_id,recording_title,group,text,"
        "severity,source_specific,confidence,claim_origin,provenance_summary,"
        "evidence_refs,recording_source_url,recording_version_label"
    )
    assert "medium,inferred," in rendered
    assert "https://example.invalid/studio,Studio reference" in rendered


def test_comparison_csv_research_mode_includes_evidence_refs() -> None:
    rendered = comparison_to_csv(_provenance_chart(), provenance_mode="research")

    assert "studio-00-00" in rendered


def test_comparison_metadata_sidecar_contains_filters_versions_and_evidence() -> None:
    payload = comparison_metadata_to_mapping(
        _provenance_chart(),
        filters=ComparisonFilters.from_values(
            categories=["tuning"],
            recording_ids=["studio"],
        ),
    )

    assert payload["metadata_schema_version"] == "1.0.0"
    assert payload["comparison_schema_version"] == "1.0.0"
    assert payload["chart_schema_version"] == "1.0.0"
    assert payload["export"]["format"] == "csv"
    assert payload["export"]["provenance_mode"] == "research"
    assert payload["export"]["filters"] == {
        "categories": ["tuning"],
        "recording_ids": ["studio"],
        "severities": [],
        "source_specific_only": False,
    }
    assert "recording_source_url" in payload["export"]["csv_columns"]
    assert payload["recordings"][0]["source_url"] == "https://example.invalid/studio"
    assert payload["provenance_records"][0]["method"] == "tuner comparison"
    assert payload["evidence_refs"][0]["ref_id"] == "studio-00-00"
    assert payload["comparison"]["categories"][0]["category"] == "tuning"


def test_comparison_metadata_json_is_machine_readable() -> None:
    payload = json.loads(comparison_metadata_to_json(_provenance_chart()))

    assert payload["metadata_schema_version"] == "1.0.0"
    assert payload["evidence_refs"][0]["ref_id"] == "studio-00-00"


@pytest.mark.parametrize("renderer", [comparison_to_json, comparison_metadata_to_json])
def test_comparison_json_rejects_nonfinite_direct_construction(renderer) -> None:
    chart = SongChart(title="Test", artist=float("nan"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Out of range float values"):
        renderer(chart)


def test_comparison_markdown_renders_summary_and_differences() -> None:
    rendered = render_comparison_markdown(_chart())

    assert rendered.startswith("# Recording Comparison: Comparison Test")
    assert "## Summary" in rendered
    assert "- Source-specific claims: 3" in rendered
    assert "## Effects" in rendered
    assert "- Studio recording:\n  - Effects: Light chorus [medium severity]" in rendered


def test_comparison_text_renders_summary_and_differences() -> None:
    rendered = render_comparison_text(_chart())

    assert rendered.startswith("Recording Comparison: Comparison Test")
    assert "Summary" in rendered
    assert "- Source-specific claims: 3" in rendered
    assert "EFFECTS" in rendered
    assert "- Live performance:\n  - Effects: Dryer amp tone [low severity]" in rendered


def test_comparison_markdown_standard_mode_renders_claim_provenance_details() -> None:
    rendered = render_comparison_markdown(_provenance_chart(), provenance_mode="standard")

    assert "Estimated tuning: Approximately 15 cents flat [high severity]" in rendered
    assert "inferred; medium confidence; audio / Studio recording" in rendered


def test_comparison_export_is_empty_when_chart_has_no_recordings() -> None:
    payload = comparison_to_mapping(SongChart.from_mapping({"title": "No Recordings"}))

    assert payload["recordings"] == []
    assert payload["summary"]["recording_count"] == 0
    assert payload["summary"]["category_count"] == 0
    assert payload["categories"] == []


def test_research_comparison_csv_matches_golden_snapshot() -> None:
    chart = load_song_chart(RESEARCH_EXAMPLE)

    assert comparison_to_csv(
        chart,
        filters=_research_snapshot_filters(),
        provenance_mode="research",
    ) == (SNAPSHOTS / "research-recording-comparison.effects-remaster.csv").read_text(
        encoding="utf-8"
    )


def test_research_comparison_json_matches_golden_snapshot() -> None:
    chart = load_song_chart(RESEARCH_EXAMPLE)

    assert comparison_to_json(
        chart,
        filters=_research_snapshot_filters(),
        provenance_mode="research",
    ) == (SNAPSHOTS / "research-recording-comparison.effects-remaster.json").read_text(
        encoding="utf-8"
    )


def test_research_comparison_metadata_matches_golden_snapshot() -> None:
    chart = load_song_chart(RESEARCH_EXAMPLE)

    assert comparison_metadata_to_json(
        chart,
        filters=_research_snapshot_filters(),
        provenance_mode="research",
    ) == (
        SNAPSHOTS / "research-recording-comparison.effects-remaster.metadata.json"
    ).read_text(encoding="utf-8")
