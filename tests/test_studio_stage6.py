from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

from test_practice_stage6 import approved_fixture
from test_studio_stage1 import bootstrap, request, running_server


def practice_post(
    server,
    cookie: str,
    path: str,
    payload: dict | None,
    *,
    key: str,
    token: str | None = None,
):
    body = b"" if payload is None else json.dumps(payload).encode()
    headers = {
        "Cookie": cookie,
        "Origin": server.origin,
        "Content-Length": str(len(body)),
        "Idempotency-Key": key,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["If-Match"] = f'"{token}"'
    return request(server, "POST", path, headers=headers, body=body)


def test_studio_create_save_list_and_restore_private_practice(tmp_path: Path) -> None:
    _practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        status, headers, body = practice_post(
            server,
            cookie,
            f"/api/approvals/{approval_id}/practice-sessions",
            None,
            key="stage6-studio-create",
        )
        assert status == HTTPStatus.CREATED, body
        value = json.loads(body)["practice"]
        assert headers["ETag"] == f'"{value["head"]["token"]}"'
        section = next(
            item
            for item in value["practice_session"]["targets"]
            if item["kind"] == "section" and item["label"] == "Chorus"
        )
        status, headers, body = practice_post(
            server,
            cookie,
            (
                f"/api/practice-sessions/"
                f"{value['practice_session']['practice_session_id']}/attempts"
            ),
            {
                "target_id": section["target_id"],
                "custom_range": None,
                "position_frame": section["range"]["start_frame"],
                "loop_enabled": True,
                "rate_milli": 750,
                "count_in": "one_approved_bar",
            },
            key="stage6-studio-save",
            token=value["head"]["token"],
        )
        assert status == HTTPStatus.OK, body
        saved = json.loads(body)["practice"]
        assert headers["ETag"] == f'"{saved["head"]["token"]}"'
        assert saved["attempt"]["restored_playing"] is False
        assert saved["source_authorization"]["status"] == "local_authorized"

        status, _headers, body = request(
            server,
            "GET",
            f"/api/sources/{source_id}/practice-sessions",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK, body
        listed = json.loads(body)["practice_sessions"]
        assert listed == [saved]
        assert str(tmp_path) not in body.decode()
        assert "play_" not in body.decode()


def test_studio_restart_restores_paused_and_rotates_media_capability(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    created = practice.create(approval_id, idempotency_key="stage6-restart-create")
    with running_server(tmp_path) as first:
        first_cookie = bootstrap(first)
        status, _headers, body = request(
            first,
            "GET",
            "/api/sources",
            headers={"Cookie": first_cookie},
        )
        assert status == HTTPStatus.OK
        first_handle = json.loads(body)["sources"][0]["playback_url"]

    with running_server(tmp_path) as second:
        second_cookie = bootstrap(second)
        status, _headers, body = request(
            second,
            "GET",
            f"/api/sources/{source_id}/practice-sessions",
            headers={"Cookie": second_cookie},
        )
        assert status == HTTPStatus.OK
        restored = json.loads(body)["practice_sessions"][0]
        assert restored["practice_session"]["practice_session_id"] == (
            created["practice_session"]["practice_session_id"]
        )
        assert restored["attempt"]["restored_playing"] is False

        status, _headers, body = request(
            second,
            "GET",
            "/api/sources",
            headers={"Cookie": second_cookie},
        )
        second_handle = json.loads(body)["sources"][0]["playback_url"]
        assert second_handle != first_handle
        status, _headers, _body = request(
            second,
            "GET",
            first_handle,
            headers={"Cookie": second_cookie},
        )
        assert status == HTTPStatus.NOT_FOUND


def test_studio_restart_rediscovers_active_approval_before_practice_creation(
    tmp_path: Path,
) -> None:
    _practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        status, _headers, body = request(
            server,
            "GET",
            f"/api/sources/{source_id}/approvals",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK, body
        approvals = json.loads(body)["approvals"]
        assert [item["approval_id"] for item in approvals] == [approval_id]
        assert approvals[0]["status"] == "active"

        status, _headers, body = practice_post(
            server,
            cookie,
            f"/api/approvals/{approval_id}/practice-sessions",
            None,
            key="stage6-after-restart-create",
        )
        assert status == HTTPStatus.CREATED, body


def test_practice_mutations_require_auth_origin_etag_and_idempotency(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-auth-create")
    session_id = value["practice_session"]["practice_session_id"]
    path = f"/api/practice-sessions/{session_id}/attempts"
    payload = json.dumps(
        {
            "target_id": value["attempt"]["target_id"],
            "custom_range": None,
            "position_frame": 0,
            "loop_enabled": True,
            "rate_milli": 1_000,
            "count_in": "off",
        }
    ).encode()
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        for headers in (
            {
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
                "Idempotency-Key": "stage6-no-cookie",
                "If-Match": f'"{value["head"]["token"]}"',
            },
            {
                "Cookie": cookie,
                "Origin": "http://attacker.invalid",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
                "Idempotency-Key": "stage6-wrong-origin",
                "If-Match": f'"{value["head"]["token"]}"',
            },
        ):
            status, _headers, _body = request(
                server,
                "POST",
                path,
                headers=headers,
                body=payload,
            )
            assert status == HTTPStatus.FORBIDDEN
        status, _headers, body = request(
            server,
            "POST",
            path,
            headers={
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
                "Idempotency-Key": "stage6-no-etag",
            },
            body=payload,
        )
        assert status == HTTPStatus.PRECONDITION_REQUIRED, body


def test_practice_json_rejects_duplicate_keys_and_nonfinite_values(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    value = practice.create(approval_id, idempotency_key="stage6-json-create")
    session_id = value["practice_session"]["practice_session_id"]
    path = f"/api/practice-sessions/{session_id}/attempts"
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        for index, payload in enumerate(
            (
                b'{"rate_milli":1000,"rate_milli":750}',
                b'{"rate_milli":NaN}',
            )
        ):
            status, _headers, body = request(
                server,
                "POST",
                path,
                headers={
                    "Cookie": cookie,
                    "Origin": server.origin,
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                    "Idempotency-Key": f"stage6-invalid-json-{index}",
                    "If-Match": f'"{value["head"]["token"]}"',
                },
                body=payload,
            )
            assert status == HTTPStatus.BAD_REQUEST
            assert json.loads(body)["error"]["code"] == "invalid_json"


def test_stage6_ui_exposes_accessible_private_practice_controls(tmp_path: Path) -> None:
    with running_server(tmp_path) as server:
        status, _headers, body = request(server, "GET", "/index.html")
        assert status == HTTPStatus.OK
        html = body.decode()
        for identifier in (
            'id="practice-panel"',
            'id="practice-status"',
            'id="practice-target"',
            'id="practice-speed"',
            'id="practice-count-in"',
            'id="practice-save"',
            'id="practice-start"',
            'id="practice-clear"',
            'id="practice-storage-usage"',
            'id="practice-history-clear"',
            'id="practice-history-dialog"',
            'id="practice-history-confirmation"',
            'id="practice-history-submit"',
        ):
            assert identifier in html
        assert "Pitch preservation is not provided" in html
        assert "interaction-synchronized" in html
        assert 'value="one_approved_bar"' in html
        assert "CLEAR ALL PRACTICE HISTORY" in html
        assert "They are not a storage quota" in html

        status, _headers, body = request(server, "GET", "/app.js")
        assert status == HTTPStatus.OK
        script = body.decode()
        assert "localStorage.getItem" not in script
        assert "localStorage.setItem" not in script
        assert "localStorage.removeItem" in script
        assert "audio.playbackRate" in script
        assert "attempt.count_in.beats" in script
        assert "Saved setup restored paused" in script
        assert "seekFrame(attempt.position_frame)" in script
        assert "Paused · range complete" in script
        assert "audio.currentTime = frame / sampleRate" in script
        assert script.count("&& loopPlaybackRequested") >= 2
        assert script.count("loopPlaybackRequested = false") >= 4
        assert "/approvals`" in script
        assert 'request("/api/practice-history")' in script
        assert '"/api/practice-history/reset"' in script
        assert "practiceHistoryDialog.showModal()" in script
        assert "if (practiceHistoryDialog.open) return" in script
        assert "if (practiceHistoryResetPending) return" in script
        assert "practiceHistoryConfirmation.disabled = true" in script
        assert "practiceHistoryResetUnresolved" in script
        assert "renderPracticeHistoryDialogCounts()" in script
        assert """for (const control of [
      practiceSaveButton,
      practicePauseButton,
      practiceClearButton
    ]) {
      control.disabled = !canSave;
    }
    practiceStartButton.disabled = !practiceSaveAllowed || practiceStartPending;""" in script
        assert "practiceSaveAllowed = canSave" in script
        assert 'name === "attempts" ? "saved-attempt" : "saved-action"' in script
        assert "if (!practiceSaveAllowed)" in script
        assert "else if (practiceSaveAllowed) pausePractice()" in script
        assert "announcePracticeSaveLimit({pausedWithoutSaving: true})" in script
        assert "if (practiceStartPending) return" in script
        assert "practiceStartPending = true" in script
        assert "practiceStartPending = false" in script
        assert """practiceStartButton.disabled =
        currentPractice?.availability.status !== "ready"
        || !practiceSaveAllowed;""" in script
        assert "practiceRange = null" in script
        assert "audio.playbackRate = 1" in script
        assert ".trim()" not in script[
            script.index("const clearPracticeHistory"):
            script.index("const loadPracticeSessions")
        ]
        assert "innerHTML" not in script


def test_studio_practice_usage_is_authenticated_bounded_and_private(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, _source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-usage-api-create")
    with running_server(tmp_path) as server:
        status, _headers, _body = request(server, "GET", "/api/practice-history")
        assert status == HTTPStatus.FORBIDDEN
        cookie = bootstrap(server)
        status, headers, body = request(
            server,
            "GET",
            "/api/practice-history",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK, body
        value = json.loads(body)["practice_history"]
        assert headers["Cache-Control"] == "no-store"
        assert headers["ETag"] == f'"{value["token"]}"'
        assert set(value["categories"]) == {
            "sessions",
            "heads",
            "attempts",
            "receipts",
            "recovery",
        }
        public_text = body.decode()
        assert str(tmp_path) not in public_text
        assert approval_id not in public_text
        assert "authorized-synthetic.wav" not in public_text
        assert "source_id" not in public_text
        assert "attempt_id" not in public_text


def test_studio_clear_all_practice_history_requires_origin_etag_and_phrase(
    tmp_path: Path,
) -> None:
    practice, _promotion, _media, source_id, approval_id = approved_fixture(tmp_path)
    practice.create(approval_id, idempotency_key="stage6-reset-api-create")
    with running_server(tmp_path) as server:
        cookie = bootstrap(server)
        status, _headers, body = request(
            server,
            "GET",
            "/api/practice-history",
            headers={"Cookie": cookie},
        )
        usage = json.loads(body)["practice_history"]
        payload = json.dumps(
            {"confirmation": "CLEAR ALL PRACTICE HISTORY"}
        ).encode()
        base_headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Idempotency-Key": "stage6-reset-api",
            "If-Match": f'"{usage["token"]}"',
        }
        status, _headers, _body = request(
            server,
            "POST",
            "/api/practice-history/reset",
            headers={**base_headers, "Cookie": cookie},
            body=payload,
        )
        assert status == HTTPStatus.FORBIDDEN
        status, _headers, body = request(
            server,
            "POST",
            "/api/practice-history/reset",
            headers={
                **base_headers,
                "Cookie": cookie,
                "Origin": server.origin,
                "If-Match": f'"{"0" * 64}"',
            },
            body=payload,
        )
        assert status == HTTPStatus.PRECONDITION_FAILED, body
        wrong = json.dumps({"confirmation": " CLEAR ALL PRACTICE HISTORY"}).encode()
        status, _headers, body = request(
            server,
            "POST",
            "/api/practice-history/reset",
            headers={
                **base_headers,
                "Cookie": cookie,
                "Origin": server.origin,
                "Content-Length": str(len(wrong)),
            },
            body=wrong,
        )
        assert status == HTTPStatus.BAD_REQUEST, body

        status, headers, body = request(
            server,
            "POST",
            "/api/practice-history/reset",
            headers={
                **base_headers,
                "Cookie": cookie,
                "Origin": server.origin,
            },
            body=payload,
        )
        assert status == HTTPStatus.OK, body
        result = json.loads(body)["practice_history_reset"]
        assert result["removed"]["categories"]["sessions"]["count"] == 1
        assert result["usage"]["categories"]["sessions"]["count"] == 0
        assert headers["Cache-Control"] == "no-store"
        assert approval_id not in body.decode()
        assert str(tmp_path) not in body.decode()

        status, _headers, body = request(
            server,
            "GET",
            f"/api/sources/{source_id}/approvals",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK, body
        assert json.loads(body)["approvals"][0]["approval_id"] == approval_id
