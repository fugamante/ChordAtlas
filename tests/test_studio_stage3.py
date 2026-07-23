from __future__ import annotations

import json
import time
from http import HTTPStatus
from pathlib import Path

from test_studio_stage2 import bootstrap, import_source, request, running_server


def succeeded_run(server, cookie: str, source_id: str) -> str:
    payload = b'{"range":null}'
    status, _headers, body = request(
        server,
        "POST",
        f"/api/sources/{source_id}/analysis-runs",
        headers={
            "Cookie": cookie,
            "Origin": server.origin,
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Idempotency-Key": "stage3-analysis-0001",
        },
        body=payload,
    )
    assert status == HTTPStatus.ACCEPTED, body
    run_id = json.loads(body)["run"]["run_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status, _headers, body = request(
            server,
            "GET",
            f"/api/analysis-runs/{run_id}",
            headers={"Cookie": cookie},
        )
        run = json.loads(body)["run"]
        if run["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert run["status"] == "succeeded", run
    return run_id


def review_post(
    server,
    cookie: str,
    path: str,
    payload: dict,
    *,
    key: str,
    token: str | None = None,
):
    body = json.dumps(payload).encode()
    headers = {
        "Cookie": cookie,
        "Origin": server.origin,
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Idempotency-Key": key,
    }
    if token is not None:
        headers["If-Match"] = f'"{token}"'
    return request(server, "POST", path, headers=headers, body=body)


def test_review_api_create_edit_undo_redo_and_private_payload(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        source_id = imported["source"]["id"]
        run_id = succeeded_run(server, cookie, source_id)

        status, headers, body = review_post(
            server,
            cookie,
            f"/api/analysis-runs/{run_id}/review-sessions",
            {},
            key="stage3-review-create-0001",
        )
        assert status == HTTPStatus.CREATED, body
        review = json.loads(body)["review"]
        assert headers["ETag"] == f'"{review["head"]["token"]}"'
        session_id = review["session"]["session_id"]
        segment_id = review["timeline"]["segments"][0]["id"]
        assert review["timeline"]["phase"] == "unreviewed"

        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/edits",
            {"kind": "accept_current", "parameters": {"segment_id": segment_id}},
            key="stage3-review-edit-0001",
            token=review["head"]["token"],
        )
        assert status == HTTPStatus.OK, body
        edited = json.loads(body)["review"]
        assert edited["timeline"]["segments"][0]["label_status"] == "reviewed"
        assert edited["local_measurements"]["accepted_edit_events"] == 1

        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/undo",
            {},
            key="stage3-review-undo-0001",
            token=edited["head"]["token"],
        )
        assert status == HTTPStatus.OK, body
        undone = json.loads(body)["review"]
        assert undone["timeline"]["segments"][0]["label_status"] == "unreviewed"
        assert undone["head"]["can_redo"] is True

        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/redo",
            {"revision_id": undone["head"]["redo_revision_id"]},
            key="stage3-review-redo-0001",
            token=undone["head"]["token"],
        )
        assert status == HTTPStatus.OK, body
        redone = json.loads(body)["review"]
        assert redone["timeline"]["segments"][0]["label_status"] == "reviewed"

        serialized = json.dumps(redone)
        for forbidden in (
            str(tmp_path),
            "base_timeline_id",
            "asset_id",
            "spec_id",
            "model_artifact",
            "private_locator",
            "original_path",
        ):
            assert forbidden not in serialized


def test_review_api_rejects_missing_or_stale_precondition_and_replayed_key(
    tmp_path: Path,
) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        run_id = succeeded_run(server, cookie, imported["source"]["id"])
        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/analysis-runs/{run_id}/review-sessions",
            {},
            key="stage3-review-create-0002",
        )
        assert status == HTTPStatus.CREATED
        review = json.loads(body)["review"]
        session_id = review["session"]["session_id"]
        segment_id = review["timeline"]["segments"][0]["id"]
        payload = {"kind": "accept_current", "parameters": {"segment_id": segment_id}}

        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/edits",
            payload,
            key="stage3-review-edit-0002",
        )
        assert status == HTTPStatus.PRECONDITION_REQUIRED
        assert json.loads(body)["error"]["code"] == "precondition_required"

        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/edits",
            payload,
            key="stage3-review-edit-0002",
            token=review["head"]["token"],
        )
        assert status == HTTPStatus.OK
        changed = json.loads(body)["review"]

        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/edits",
            {"kind": "set_unknown", "parameters": {"segment_id": segment_id}},
            key="stage3-review-edit-0002",
            token=changed["head"]["token"],
        )
        assert status == HTTPStatus.PRECONDITION_FAILED
        assert json.loads(body)["error"]["code"] == "idempotency_conflict"

        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/review-sessions/{session_id}/edits",
            {"kind": "set_unknown", "parameters": {"segment_id": segment_id}},
            key="stage3-review-edit-0003",
            token=review["head"]["token"],
        )
        assert status == HTTPStatus.PRECONDITION_FAILED
        assert json.loads(body)["error"]["code"] == "review_precondition_failed"


def test_review_api_returns_structured_error_for_digit_limit_integer(
    tmp_path: Path,
) -> None:
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        imported = import_source(server, cookie)
        run_id = succeeded_run(server, cookie, imported["source"]["id"])
        status, _headers, body = review_post(
            server,
            cookie,
            f"/api/analysis-runs/{run_id}/review-sessions",
            {},
            key="stage3-review-create-digits",
        )
        assert status == HTTPStatus.CREATED
        review = json.loads(body)["review"]
        huge_integer = "9" * 5_000
        payload = (
            '{"kind":"split","parameters":{"segment_id":"'
            + review["timeline"]["segments"][0]["id"]
            + '","frame":'
            + huge_integer
            + "}}"
        ).encode()
        status, _headers, body = request(
            server,
            "POST",
            f"/api/review-sessions/{review['session']['session_id']}/edits",
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
                "Idempotency-Key": "stage3-review-edit-digits",
                "If-Match": f'"{review["head"]["token"]}"',
            },
            body=payload,
        )
        assert status == HTTPStatus.BAD_REQUEST
        assert json.loads(body)["error"]["code"] == "invalid_json"


def test_review_ui_exposes_accessible_local_controls(
    tmp_path: Path,
) -> None:
    with running_server(tmp_path) as server:
        status, _headers, body = request(server, "GET", "/index.html")
        assert status == HTTPStatus.OK
        html = body.decode()
        assert 'id="review-status"' in html
        assert 'role="status"' in html
        assert "Editable review" in html
        assert "Finish review" in html
        assert "Accept unchanged boundaries" in html
        assert "Retry rejected edit" in html

        status, _headers, body = request(server, "GET", "/app.js")
        assert status == HTTPStatus.OK
        script = body.decode()
        assert "textContent" in script
        assert "innerHTML" not in script
        assert '"If-Match"' in script
        assert "ready_for_approval" in script
        assert "restoreReviewFocus" in script
        assert "Your edit was rejected" in script
        assert "accept_boundaries" in script
