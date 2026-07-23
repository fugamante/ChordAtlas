from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

from test_studio_stage2 import bootstrap, import_source, request, running_server
from test_studio_stage3 import review_post, succeeded_run


def _ready_review(server, cookie: str, run_id: str) -> dict:
    status, _headers, body = review_post(
        server,
        cookie,
        f"/api/analysis-runs/{run_id}/review-sessions",
        {},
        key="stage4-studio-review-create",
    )
    assert status == HTTPStatus.CREATED, body
    review = json.loads(body)["review"]
    session_id = review["session"]["session_id"]
    for index, segment in enumerate(review["timeline"]["segments"]):
        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/edits",
            {"kind": "accept_current", "parameters": {"segment_id": segment["id"]}},
            key=f"stage4-studio-label-{index}",
            token=review["head"]["token"],
        )
        assert status == HTTPStatus.OK, body
        review = json.loads(body)["review"]
    status, _headers, body = review_post(
        server,
        cookie,
        f"/api/review-sessions/{session_id}/edits",
        {"kind": "accept_boundaries", "parameters": {}},
        key="stage4-studio-boundaries",
        token=review["head"]["token"],
    )
    assert status == HTTPStatus.OK, body
    review = json.loads(body)["review"]
    status, _headers, body = review_post(
        server,
        cookie,
        f"/api/review-sessions/{session_id}/edits",
        {"kind": "finish_review", "parameters": {}},
        key="stage4-studio-finish",
        token=review["head"]["token"],
    )
    assert status == HTTPStatus.OK, body
    return json.loads(body)["review"]


def _normalize(label: str) -> str:
    if label.endswith(":maj"):
        return label[:-4]
    if label.endswith(":min"):
        return f"{label[:-4]}m"
    return label


def _mapping(review: dict) -> dict:
    labels = sorted(
        {
            _normalize(item["label"])
            for item in review["timeline"]["segments"]
            if item["state"] == "chord"
        }
    )
    analyzed = review["timeline"]["analyzed_range"]
    return {
        "mapping_version": "songchart-explicit-grid-v1",
        "title": "Authorized synthetic Studio chart",
        "artist": None,
        "key": None,
        "tuning": "Standard",
        "capo": "None",
        "chart_version": "1.0",
        "meter_numerator": 4,
        "beat_unit": 4,
        "measure_boundaries_frames": [
            analyzed["start_frame"],
            analyzed["end_frame"],
        ],
        "pickup_policy": "full_coverage_confirmed",
        "default_section_name": "Song",
        "notation_mode": "sounding",
        "diagram_policy": "built_in_standard_only",
        "loss_policy": "allow_declared",
        "guitar_decisions": [
            {
                "chord": label,
                "inversion_reviewed": True,
                "voicing": "built_in",
                "playability": "playable",
                "note": None,
            }
            for label in labels
        ],
    }


def _promotion_post(server, cookie: str, path: str, payload: dict, **headers):
    body = json.dumps(payload).encode()
    return request(
        server,
        "POST",
        path,
        headers={
            "Cookie": cookie,
            "Origin": server.origin,
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            **headers,
        },
        body=body,
    )


def test_studio_promotion_preview_approve_export_and_revoke(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        run_id = succeeded_run(server, cookie, imported["source"]["id"])
        review = _ready_review(server, cookie, run_id)
        session_id = review["session"]["session_id"]
        mapping = _mapping(review)
        preview_payload = {
            "revision_id": review["head"]["revision_id"],
            "mapping": mapping,
        }
        status, _headers, body = _promotion_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/promotion-preview",
            preview_payload,
            **{"If-Match": f'"{review["head"]["token"]}"'},
        )
        assert status == HTTPStatus.OK, body
        preview = json.loads(body)["preview"]
        assert "Chord Reference" in preview["renders"]["markdown"]
        material = sorted(
            item["id"]
            for item in preview["issues"]
            if item["severity"] == "material"
        )

        status, headers, body = _promotion_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/approvals",
            {
                **preview_payload,
                "expected_spec_id": preview["spec_id"],
                "expected_result_id": preview["result_id"],
                "expected_issue_digest": preview["issue_digest"],
                "acknowledged_issue_ids": material,
            },
            **{
                "If-Match": f'"{review["head"]["token"]}"',
                "Idempotency-Key": "stage4-studio-approve",
            },
        )
        assert status == HTTPStatus.CREATED, body
        approval = json.loads(body)["approval"]
        approval_id = approval["approval_id"]
        assert headers["ETag"] == f'"{approval["token"]}"'

        status, _headers, body = request(
            server,
            "GET",
            f"/api/approvals/{approval_id}/exports",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK, body
        exports = json.loads(body)["exports"]
        assert json.loads(exports["json"])["schema_version"] == "1.0.0"
        assert str(tmp_path) not in json.dumps(exports)
        assert "review_" not in json.dumps(exports)

        status, _headers, body = _promotion_post(
            server,
            cookie,
            f"/api/approvals/{approval_id}/revoke",
            {"reason": "mistake"},
            **{
                "If-Match": f'"{approval["token"]}"',
                "Idempotency-Key": "stage4-studio-revoke",
            },
        )
        assert status == HTTPStatus.OK, body
        assert json.loads(body)["approval"]["status"] == "revoked"

        status, _headers, body = request(
            server,
            "GET",
            f"/api/approvals/{approval_id}/exports",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.BAD_REQUEST
        assert json.loads(body)["error"]["code"] == "approval_revoked"


def test_stage4_ui_exposes_labeled_grid_guitar_warning_and_revocation_controls(
    tmp_path: Path,
) -> None:
    with running_server(tmp_path) as server:
        status, _headers, body = request(server, "GET", "/index.html")
        assert status == HTTPStatus.OK
        html = body.decode()
        for identifier in (
            'id="promotion-status"',
            'id="measure-boundaries"',
            'id="add-playhead-boundary"',
            'id="confirm-meter"',
            'id="confirm-guitar-setup"',
            'id="guitar-decisions"',
            'id="promotion-issues"',
            'id="promotion-preview-format"',
            'id="approve-promotion"',
            'id="revoke-promotion"',
        ):
            assert identifier in html
        assert "sounding chord symbols" in html
        assert "cannot retract files already written" in html

        status, _headers, body = request(server, "GET", "/app.js")
        assert status == HTTPStatus.OK
        script = body.decode()
        assert "textContent" in script
        assert "innerHTML" not in script
        assert "acknowledged_issue_ids" in script
        assert "expected_result_id" in script
        assert "expected_issue_digest" in script
        assert "inversion_reviewed" in script
        assert "full_coverage_confirmed" in script
