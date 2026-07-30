from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from chordatlas._fs import (
    ProjectAnchor,
    TargetOccupiedError,
    create_text_exclusive,
    replace_text,
)
from chordatlas.practice.models import (
    PracticeAttempt,
    PracticeError,
    PracticeHead,
    PracticeSession,
    canonical_json,
)

_MAX_RECORD_BYTES = 2 * 1024 * 1024
_MAX_SESSIONS = 1_000
_MAX_ATTEMPTS = 20_000
_MAX_RECEIPTS = 40_000
_MAX_RESET_RECEIPTS = 1_000
_USAGE_SCHEMA_VERSION = "1.0.0-draft"
_RESET_SCHEMA_VERSION = "1.0.0-draft"


class PracticeStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        try:
            self.anchor = ProjectAnchor.for_project(self.project_root)
        except OSError:
            raise _integrity_error() from None
        self.private_root = self.anchor.root / "private"
        self.root = self.private_root / "practice"
        self.sessions = self.root / "sessions"
        self.attempts = self.root / "attempts" / "sha256"
        self.heads = self.root / "heads"
        self.receipts = self.root / "idempotency"
        self.locks = self.root / "locks"
        self.maintenance = self.private_root / "practice-maintenance"
        self.lifecycle_lock = self.maintenance / "lifecycle.lock"
        self.reset_receipts = self.maintenance / "resets"
        self._lifecycle_local = threading.local()

    @classmethod
    def initialize(cls, project_root: Path) -> PracticeStore:
        store = cls(project_root)
        _require_private_directory(store.private_root, store.anchor)
        for path in (
            store.maintenance,
            store.reset_receipts,
        ):
            _ensure_private_directory(path, store.anchor)
        with store.lifecycle(exclusive=True):
            try:
                store._recover_reset_locked()
            except PracticeError as error:
                if error.code != "practice_cleanup_incomplete":
                    raise
            store._ensure_active_tree_locked()
        return store

    @contextmanager
    def lifecycle(self, *, exclusive: bool = False) -> Iterator[None]:
        """Hold the external practice lifecycle lock.

        The lock lives outside the resettable root. Shared ownership spans one
        complete service operation; reset and inventory use exclusive ownership.
        """

        depth = getattr(self._lifecycle_local, "depth", 0)
        mode = getattr(self._lifecycle_local, "mode", None)
        if depth:
            if exclusive and mode != "exclusive":
                raise RuntimeError("practice lifecycle lock cannot be upgraded")
            self._lifecycle_local.depth = depth + 1
            try:
                yield
            finally:
                self._lifecycle_local.depth -= 1
            return

        descriptor = _open_lock(self.lifecycle_lock, self.anchor)
        try:
            _acquire_lock(
                descriptor,
                fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
            )
            self._lifecycle_local.depth = 1
            self._lifecycle_local.mode = "exclusive" if exclusive else "shared"
            try:
                yield
            finally:
                self._lifecycle_local.depth = 0
                self._lifecycle_local.mode = None
        finally:
            os.close(descriptor)

    def creation_time(
        self,
        *,
        idempotency_key: str,
        fingerprint: str,
        recorded_at: str,
    ) -> str:
        key_hash = _idempotency_hash(idempotency_key)
        path = self.receipts / f"{key_hash}.json"
        with self.claim(self.locks / "create.lock"):
            existing = self._optional_json(path)
            if existing is not None:
                _validate_receipt(
                    existing,
                    key_hash=key_hash,
                    action="create",
                    fingerprint=fingerprint,
                )
                return str(existing["recorded_at"])
            if _entry_count(self.receipts, self.anchor) >= _MAX_RECEIPTS:
                raise PracticeError(
                    "practice_limit",
                    "This project reached its private practice-action limit.",
                )
            _publish_json(
                path,
                {
                    "practice_receipt_schema_version": "1.0.0-draft",
                    "key_hash": key_hash,
                    "action": "create",
                    "fingerprint": fingerprint,
                    "recorded_at": recorded_at,
                    "attempt_id": None,
                    "parent_attempt_id": None,
                    "generation": 0,
                },
                self.anchor,
            )
        return recorded_at

    def validate_creation_key(
        self,
        *,
        idempotency_key: str,
        fingerprint: str,
    ) -> None:
        """Validate a Prepare key without consuming a receipt when none exists."""

        key_hash = _idempotency_hash(idempotency_key)
        path = self.receipts / f"{key_hash}.json"
        with self.claim(self.locks / "create.lock"):
            existing = self._optional_json(path)
            if existing is not None:
                _validate_receipt(
                    existing,
                    key_hash=key_hash,
                    action="create",
                    fingerprint=fingerprint,
                )

    def inventory(self) -> dict[str, Any]:
        with self.lifecycle(exclusive=True):
            cleanup_pending = False
            try:
                self._recover_reset_locked()
            except PracticeError as error:
                if error.code != "practice_cleanup_incomplete":
                    raise
                cleanup_pending = True
            usage = self._inventory_locked()
            if cleanup_pending:
                usage["warnings"].append(
                    "Practice history is empty, but private reset cleanup is incomplete"
                )
                usage["reset_allowed"] = False
                usage["capabilities"]["reset_all"] = {
                    "allowed": False,
                    "blocking_resources": ["recovery"],
                }
            return usage

    def reset(
        self,
        *,
        expected_token: str,
        idempotency_key: str,
        confirmation: str,
        recorded_at: str,
    ) -> dict[str, Any]:
        key_hash = _idempotency_hash(idempotency_key)
        fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "action": "reset_practice_history",
                    "expected_token": expected_token,
                    "confirmation": confirmation,
                }
            )
        ).hexdigest()
        receipt_path = self.reset_receipts / f"{key_hash}.json"
        with self.lifecycle(exclusive=True):
            self._recover_reset_locked()
            existing = self._optional_json(receipt_path)
            if existing is not None:
                receipt = _validate_reset_receipt(
                    existing,
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                )
                if receipt["phase"] != "complete":
                    self._recover_one_reset_locked(receipt_path, receipt)
                    receipt = _validate_reset_receipt(
                        _read_json(receipt_path, self.anchor),
                        key_hash=key_hash,
                        fingerprint=fingerprint,
                    )
                return {
                    "removed": receipt["removed"],
                    "usage": self._inventory_locked(),
                    "replayed": True,
                }

            before = self._inventory_locked()
            if expected_token != before["token"]:
                raise PracticeError(
                    "practice_conflict",
                    "Practice usage changed. Refresh and confirm the reset again.",
                    retryable=True,
                )
            if before["categories"]["recovery"]["count"] >= _MAX_RESET_RECEIPTS:
                raise PracticeError(
                    "practice_limit",
                    "This project reached its private practice-maintenance limit.",
                )
            reset_id = hashlib.sha256(
                canonical_json(
                    {
                        "key_hash": key_hash,
                        "fingerprint": fingerprint,
                        "recorded_at": recorded_at,
                    }
                )
            ).hexdigest()
            root_device, root_inode = _directory_identity(self.root, self.anchor)
            receipt = {
                "practice_reset_schema_version": _RESET_SCHEMA_VERSION,
                "key_hash": key_hash,
                "fingerprint": fingerprint,
                "reset_id": reset_id,
                "phase": "prepared",
                "recorded_at": recorded_at,
                "root_device": root_device,
                "root_inode": root_inode,
                "removed": _removed_inventory(before),
            }
            _publish_json(receipt_path, receipt, self.anchor)
            self._recover_one_reset_locked(receipt_path, receipt)
            completed = _validate_reset_receipt(
                _read_json(receipt_path, self.anchor),
                key_hash=key_hash,
                fingerprint=fingerprint,
            )
            return {
                "removed": completed["removed"],
                "usage": self._inventory_locked(),
                "replayed": False,
            }

    def _inventory_locked(self) -> dict[str, Any]:
        categories: dict[str, dict[str, Any]] = {}
        digest = hashlib.sha256()
        definitions = (
            ("sessions", self.sessions, _MAX_SESSIONS),
            ("heads", self.heads, None),
            ("attempts", self.attempts, _MAX_ATTEMPTS),
            ("receipts", self.receipts, _MAX_RECEIPTS),
            ("recovery", self.reset_receipts, _MAX_RESET_RECEIPTS),
        )
        warnings: list[str] = []
        labels = {
            "sessions": "practice-session",
            "attempts": "saved-attempt",
            "receipts": "saved-action",
            "recovery": "reset-recovery",
        }
        for name, path, limit in definitions:
            count, byte_count, leaves = _inventory_directory(path, self.anchor)
            category: dict[str, Any] = {"count": count, "bytes": byte_count}
            if limit is not None:
                remaining = max(limit - count, 0)
                near_limit = count * 10 >= limit * 9
                category.update(
                    {
                        "limit": limit,
                        "remaining": remaining,
                        "near_limit": near_limit,
                    }
                )
                if count >= limit:
                    warnings.append(
                        f"{labels[name]} project limit reached: 0 records remain"
                    )
                elif near_limit:
                    warnings.append(
                        f"Approaching {labels[name]} project limit: "
                        f"{remaining} records remain"
                    )
            categories[name] = category
            for leaf, payload_digest in leaves:
                digest.update(name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(leaf.encode("utf-8"))
                digest.update(b"\0")
                digest.update(payload_digest)
        totals = {
            "count": sum(item["count"] for item in categories.values()),
            "bytes": sum(item["bytes"] for item in categories.values()),
        }
        token = hashlib.sha256(
            canonical_json(
                {
                    "schema_version": _USAGE_SCHEMA_VERSION,
                    "categories": categories,
                    "content_digest": digest.hexdigest(),
                }
            )
        ).hexdigest()
        sessions_open = categories["sessions"]["remaining"] > 0
        attempts_open = categories["attempts"]["remaining"] > 0
        receipts_open = categories["receipts"]["remaining"] > 0
        recovery_open = categories["recovery"]["remaining"] > 0
        return {
            "schema_version": _USAGE_SCHEMA_VERSION,
            "categories": categories,
            "totals": totals,
            "warnings": warnings,
            "capabilities": {
                "prepare": {
                    "allowed": sessions_open and receipts_open,
                    "blocking_resources": [
                        name
                        for name, available in (
                            ("sessions", sessions_open),
                            ("receipts", receipts_open),
                        )
                        if not available
                    ],
                },
                "save": {
                    "allowed": attempts_open and receipts_open,
                    "blocking_resources": [
                        name
                        for name, available in (
                            ("attempts", attempts_open),
                            ("receipts", receipts_open),
                        )
                        if not available
                    ],
                },
                "reset_all": {
                    "allowed": recovery_open,
                    "blocking_resources": [] if recovery_open else ["recovery"],
                },
            },
            "reset_allowed": recovery_open,
            "token": token,
        }

    def _recover_reset_locked(self) -> None:
        pending: list[tuple[Path, dict[str, Any]]] = []
        for name in _json_names(self.reset_receipts, self.anchor):
            path = self.reset_receipts / name
            receipt = _validate_reset_receipt(_read_json(path, self.anchor))
            if receipt["phase"] != "complete":
                pending.append((path, receipt))
        if len(pending) > 1:
            raise _integrity_error()
        if pending:
            self._recover_one_reset_locked(*pending[0])

    def _recover_one_reset_locked(
        self,
        receipt_path: Path,
        receipt: dict[str, Any],
    ) -> None:
        reset_id = str(receipt["reset_id"])
        root_identity = (int(receipt["root_device"]), int(receipt["root_inode"]))
        quarantine = self.private_root / f".practice-quarantine-{reset_id}"
        root_exists = _path_exists(self.root, self.anchor)
        quarantine_exists = _path_exists(quarantine, self.anchor)

        if receipt["phase"] == "prepared":
            if root_exists and not quarantine_exists:
                _require_private_directory(self.root, self.anchor)
                _rename_directory(
                    self.private_root, self.root.name, quarantine.name, self.anchor
                )
                root_exists = False
                quarantine_exists = True
            if quarantine_exists:
                _require_directory_identity(quarantine, root_identity, self.anchor)
                if root_exists:
                    _require_private_directory(self.root, self.anchor)
                self._ensure_active_tree_locked()
                root_exists = True
            if not root_exists or not quarantine_exists:
                raise _integrity_error()
            if not self._active_history_empty_locked():
                raise _integrity_error()
            receipt = {**receipt, "phase": "activated"}
            _replace_json(receipt_path, receipt, self.anchor)

        if receipt["phase"] == "activated":
            if not _path_exists(self.root, self.anchor):
                raise _integrity_error()
            if _path_exists(quarantine, self.anchor):
                try:
                    _remove_private_tree(
                        self.private_root,
                        quarantine.name,
                        expected_identity=root_identity,
                        anchor=self.anchor,
                    )
                except (OSError, PracticeError):
                    raise PracticeError(
                        "practice_cleanup_incomplete",
                        "Practice history is empty, but private cleanup is incomplete. Retry shortly.",
                        retryable=True,
                    ) from None
            receipt = {**receipt, "phase": "complete"}
            _replace_json(receipt_path, receipt, self.anchor)

    def _ensure_active_tree_locked(self) -> None:
        for path in (
            self.root,
            self.sessions,
            self.root / "attempts",
            self.attempts,
            self.heads,
            self.receipts,
            self.locks,
        ):
            _ensure_private_directory(path, self.anchor)
            _sync_directory(path.parent, self.anchor)

    def _active_history_empty_locked(self) -> bool:
        return all(
            _directory_is_empty(path, self.anchor)
            for path in (self.sessions, self.attempts, self.heads, self.receipts, self.locks)
        )

    def publish_initial(
        self,
        session: PracticeSession,
        attempt: PracticeAttempt,
    ) -> tuple[PracticeHead, PracticeAttempt]:
        if attempt.session_id != session.id or attempt.parent_attempt_id is not None:
            raise _integrity_error()
        with self.claim(self.locks / "create.lock"):
            session_path = self.sessions / f"{session.id}.json"
            existing_session = self._optional_json(session_path)
            head_path = self.heads / f"{session.id}.json"
            existing_head = self._optional_json(head_path)
            if existing_session is not None:
                canonical_session = PracticeSession.from_mapping(existing_session)
                if canonical_session.id != session.id:
                    raise _integrity_error()
                attempt = PracticeAttempt.create(
                    session_id=attempt.session_id,
                    parent_attempt_id=attempt.parent_attempt_id,
                    selection_kind=attempt.selection_kind,
                    target_id=attempt.target_id,
                    frame_range=attempt.frame_range,
                    position_frame=attempt.position_frame,
                    loop_enabled=attempt.loop_enabled,
                    rate_milli=attempt.rate_milli,
                    count_in_beats=attempt.count_in_beats,
                    count_in_beat_frames_num=attempt.count_in_beat_frames_num,
                    count_in_beat_frames_den=attempt.count_in_beat_frames_den,
                    recorded_at=canonical_session.created_at,
                )
                self._publish_attempt(attempt)
                head = PracticeHead(
                    session_id=session.id,
                    generation=0,
                    attempt_id=attempt.id,
                    updated_at=attempt.recorded_at,
                )
                if existing_head is None:
                    _publish_json(head_path, head.to_record_mapping(), self.anchor)
                else:
                    loaded = PracticeHead.from_mapping(existing_head)
                    if loaded != head:
                        return loaded, self.load_attempt(loaded.attempt_id)
                return head, attempt

            if _entry_count(self.sessions, self.anchor) >= _MAX_SESSIONS:
                raise PracticeError(
                    "practice_limit",
                    "This project reached its private practice-session limit.",
                )
            if existing_head is not None:
                loaded = PracticeHead.from_mapping(existing_head)
                recovered = self.load_attempt(loaded.attempt_id)
                expected = PracticeAttempt.create(
                    session_id=attempt.session_id,
                    parent_attempt_id=attempt.parent_attempt_id,
                    selection_kind=attempt.selection_kind,
                    target_id=attempt.target_id,
                    frame_range=attempt.frame_range,
                    position_frame=attempt.position_frame,
                    loop_enabled=attempt.loop_enabled,
                    rate_milli=attempt.rate_milli,
                    count_in_beats=attempt.count_in_beats,
                    count_in_beat_frames_num=attempt.count_in_beat_frames_num,
                    count_in_beat_frames_den=attempt.count_in_beat_frames_den,
                    recorded_at=recovered.recorded_at,
                )
                if (
                    loaded.session_id != session.id
                    or recovered.session_id != session.id
                    or recovered.parent_attempt_id is not None
                    or loaded.generation != 0
                    or recovered != expected
                ):
                    raise _integrity_error()
                session = replace(session, created_at=recovered.recorded_at)
                _publish_json(session_path, session.to_record_mapping(), self.anchor)
                return loaded, recovered

            self._publish_attempt(attempt)
            head = PracticeHead(
                session_id=session.id,
                generation=0,
                attempt_id=attempt.id,
                updated_at=attempt.recorded_at,
            )
            _publish_json(head_path, head.to_record_mapping(), self.anchor)
            # The session record is the visibility commit. Its dependencies exist
            # first, so interruption cannot expose an unrestorable session.
            _publish_json(session_path, session.to_record_mapping(), self.anchor)
            return head, attempt

    def record_attempt(
        self,
        session_id: str,
        *,
        expected_token: str,
        idempotency_key: str,
        fingerprint: str,
        recorded_at: str,
        build: Callable[[str, str], PracticeAttempt],
    ) -> tuple[PracticeHead, PracticeAttempt]:
        key_hash = _idempotency_hash(idempotency_key)
        receipt_path = self.receipts / f"{key_hash}.json"
        with self.claim(self.locks / f"{session_id}.lock"):
            session = self.load_session(session_id)
            head = self._reconcile_head_locked(session.id)
            receipt = self._optional_json(receipt_path)
            if receipt is not None:
                _validate_receipt(
                    receipt,
                    key_hash=key_hash,
                    action="update",
                    fingerprint=fingerprint,
                )
                attempt = build(
                    str(receipt["parent_attempt_id"]),
                    str(receipt["recorded_at"]),
                )
                if attempt.id != receipt["attempt_id"]:
                    raise _integrity_error()
                if head.attempt_id == attempt.id:
                    return head, attempt
                if head.token != expected_token:
                    raise PracticeError(
                        "idempotency_expired",
                        "This saved practice action is older than the current state. Refresh.",
                        retryable=True,
                    )
                self._publish_attempt(attempt)
                repaired = PracticeHead(
                    session_id=session.id,
                    generation=int(receipt["generation"]),
                    attempt_id=attempt.id,
                    updated_at=attempt.recorded_at,
                )
                self._replace_head(repaired)
                return repaired, attempt

            if head.token != expected_token:
                raise PracticeError(
                    "practice_conflict",
                    "Practice state changed. Refresh and try again.",
                    retryable=True,
                )
            if _entry_count(self.attempts, self.anchor) >= _MAX_ATTEMPTS:
                raise PracticeError(
                    "practice_limit",
                    "This project reached its private practice-attempt limit.",
                )
            attempt = build(head.attempt_id, recorded_at)
            if attempt.session_id != session.id or attempt.parent_attempt_id != head.attempt_id:
                raise _integrity_error()
            if _entry_count(self.receipts, self.anchor) >= _MAX_RECEIPTS:
                raise PracticeError(
                    "practice_limit",
                    "This project reached its private practice-action limit.",
                )
            receipt_value = {
                "practice_receipt_schema_version": "1.0.0-draft",
                "key_hash": key_hash,
                "action": "update",
                "fingerprint": fingerprint,
                "recorded_at": recorded_at,
                "attempt_id": attempt.id,
                "parent_attempt_id": head.attempt_id,
                "generation": head.generation + 1,
            }
            # The receipt commits deterministic recovery data before visibility changes.
            _publish_json(receipt_path, receipt_value, self.anchor)
            self._publish_attempt(attempt)
            next_head = PracticeHead(
                session_id=session.id,
                generation=head.generation + 1,
                attempt_id=attempt.id,
                updated_at=recorded_at,
            )
            self._replace_head(next_head)
            return next_head, attempt

    def list_sessions(self, *, source_id: str | None = None) -> tuple[PracticeSession, ...]:
        values: list[PracticeSession] = []
        for name in _json_names(self.sessions, self.anchor):
            if not name.startswith("practice_"):
                continue
            path = self.sessions / name
            session = PracticeSession.from_mapping(_read_json(path, self.anchor))
            try:
                head = self.load_head(session.id)
                self.load_attempt(head.attempt_id)
            except PracticeError:
                continue
            if source_id is None or session.source_id == source_id:
                values.append(session)
        return tuple(sorted(values, key=lambda item: (item.created_at, item.id)))

    def load_session(self, session_id: str) -> PracticeSession:
        if not _valid_identifier(session_id, "practice_", 64):
            raise PracticeError(
                "practice_unavailable",
                "The private practice session is unavailable.",
            )
        try:
            session = PracticeSession.from_mapping(
                _read_json(self.sessions / f"{session_id}.json", self.anchor)
            )
        except PracticeError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError):
            raise _integrity_error() from None
        if session.id != session_id:
            raise _integrity_error()
        return session

    def load_attempt(self, attempt_id: str) -> PracticeAttempt:
        if not _valid_identifier(attempt_id, "attempt_", 64):
            raise PracticeError(
                "practice_unavailable",
                "The private practice attempt is unavailable.",
            )
        try:
            attempt = PracticeAttempt.from_mapping(
                _read_json(self.attempts / f"{attempt_id[8:]}.json", self.anchor)
            )
        except PracticeError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError):
            raise _integrity_error() from None
        if attempt.id != attempt_id:
            raise _integrity_error()
        return attempt

    def load_head(self, session_id: str) -> PracticeHead:
        if not _valid_identifier(session_id, "practice_", 64):
            raise PracticeError(
                "practice_unavailable",
                "The private practice session is unavailable.",
            )
        with self.claim(self.locks / f"{session_id}.lock"):
            return self._reconcile_head_locked(session_id)

    def _load_head_record(self, session_id: str) -> PracticeHead:
        if not _valid_identifier(session_id, "practice_", 64):
            raise PracticeError(
                "practice_unavailable",
                "The private practice session is unavailable.",
            )
        try:
            head = PracticeHead.from_mapping(
                _read_json(self.heads / f"{session_id}.json", self.anchor)
            )
        except PracticeError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError):
            raise _integrity_error() from None
        if head.session_id != session_id:
            raise _integrity_error()
        attempt = self.load_attempt(head.attempt_id)
        if attempt.session_id != session_id:
            raise _integrity_error()
        return head

    def current(self, session_id: str) -> tuple[PracticeSession, PracticeHead, PracticeAttempt]:
        if not _valid_identifier(session_id, "practice_", 64):
            raise PracticeError(
                "practice_unavailable",
                "The private practice session is unavailable.",
            )
        with self.claim(self.locks / f"{session_id}.lock"):
            session = self.load_session(session_id)
            head = self._reconcile_head_locked(session.id)
            attempt = self.load_attempt(head.attempt_id)
            return session, head, attempt

    def _reconcile_head_locked(self, session_id: str) -> PracticeHead:
        """Repair the mutable head from the unique complete receipt lineage."""

        head = self._load_head_record(session_id)
        ancestry: list[PracticeAttempt] = []
        seen: set[str] = set()
        attempt = self.load_attempt(head.attempt_id)
        while True:
            if attempt.id in seen or attempt.session_id != session_id:
                raise _integrity_error()
            seen.add(attempt.id)
            ancestry.append(attempt)
            if attempt.parent_attempt_id is None:
                break
            attempt = self.load_attempt(attempt.parent_attempt_id)
        ancestry.reverse()
        if head.generation != len(ancestry) - 1:
            raise _integrity_error()

        children: dict[str, dict[str, tuple[int, PracticeAttempt]]] = {}
        complete_ids: set[str] = set()
        for name in _json_names(self.receipts, self.anchor):
            path = self.receipts / name
            key_hash = path.stem
            value = _read_json(path, self.anchor)
            action = value.get("action")
            fingerprint = value.get("fingerprint")
            if (
                not _valid_hex(key_hash, 64)
                or action not in {"create", "update"}
                or not isinstance(fingerprint, str)
            ):
                raise _integrity_error()
            try:
                _validate_receipt(
                    value,
                    key_hash=key_hash,
                    action=action,
                    fingerprint=fingerprint,
                )
            except PracticeError:
                raise _integrity_error() from None
            if action != "update":
                continue
            try:
                candidate = self.load_attempt(str(value["attempt_id"]))
            except PracticeError as error:
                if error.code == "practice_unavailable":
                    continue
                raise
            if (
                candidate.parent_attempt_id != value["parent_attempt_id"]
                or candidate.recorded_at != value["recorded_at"]
            ):
                raise _integrity_error()
            if candidate.session_id != session_id:
                continue
            generation = int(value["generation"])
            parent_id = str(value["parent_attempt_id"])
            prior = children.setdefault(parent_id, {}).get(candidate.id)
            if prior is not None and prior != (generation, candidate):
                raise _integrity_error()
            children[parent_id][candidate.id] = (generation, candidate)
            complete_ids.add(candidate.id)

        tip = ancestry[0]
        generation = 0
        selected = {tip.id}
        while True:
            options = children.get(tip.id, {})
            if not options:
                break
            if len(options) != 1:
                raise _integrity_error()
            next_generation, next_attempt = next(iter(options.values()))
            if next_generation != generation + 1:
                raise _integrity_error()
            generation = next_generation
            tip = next_attempt
            if tip.id in selected:
                raise _integrity_error()
            selected.add(tip.id)

        if complete_ids != selected - {ancestry[0].id}:
            raise _integrity_error()
        authoritative = PracticeHead(
            session_id=session_id,
            generation=generation,
            attempt_id=tip.id,
            updated_at=tip.recorded_at,
        )
        if head != authoritative:
            self._replace_head(authoritative)
        return authoritative

    def _publish_attempt(self, attempt: PracticeAttempt) -> None:
        path = self.attempts / f"{attempt.id[8:]}.json"
        existing = self._optional_json(path)
        if existing is None:
            _publish_json(path, attempt.to_record_mapping(), self.anchor)
        elif existing != attempt.to_record_mapping():
            raise _integrity_error()

    def _replace_head(self, head: PracticeHead) -> None:
        payload = _json_text(head.to_record_mapping())
        try:
            replace_text(
                self.heads / f"{head.session_id}.json",
                payload,
                stage_prefix=".practice-head-",
                prepublish=_guard_leaf,
                sync_directory=True,
                anchor=self.anchor,
            )
        except OSError:
            raise _integrity_error() from None

    def _optional_json(self, path: Path) -> dict[str, Any] | None:
        try:
            with self.anchor.parent(path) as (parent_fd, leaf):
                os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            raise _integrity_error() from None
        return _read_json(path, self.anchor)

    @contextmanager
    def claim(self, path: Path) -> Iterator[None]:
        descriptor = _open_lock(path, self.anchor)
        try:
            _acquire_lock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _publish_json(
    path: Path,
    value: dict[str, Any],
    anchor: ProjectAnchor,
) -> None:
    content = _json_text(value)
    if len(content.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise PracticeError(
            "practice_limit",
            "The private practice record exceeds its safe storage limit.",
        )
    try:
        create_text_exclusive(
            path,
            content,
            stage_prefix=".practice-record-",
            mode=0o600,
            sync_directory=True,
            anchor=anchor,
        )
    except TargetOccupiedError:
        if _read_json(path, anchor) != value:
            raise _integrity_error() from None
    except OSError:
        raise _integrity_error() from None


def _replace_json(
    path: Path,
    value: dict[str, Any],
    anchor: ProjectAnchor,
) -> None:
    content = _json_text(value)
    if len(content.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise PracticeError(
            "practice_limit",
            "The private practice record exceeds its safe storage limit.",
        )
    try:
        replace_text(
            path,
            content,
            stage_prefix=".practice-reset-",
            prepublish=_guard_leaf,
            sync_directory=True,
            anchor=anchor,
        )
    except OSError:
        raise _integrity_error() from None


def _read_json(path: Path, anchor: ProjectAnchor) -> dict[str, Any]:
    try:
        with anchor.parent(path) as (parent_fd, leaf):
            descriptor = os.open(
                leaf,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
    except FileNotFoundError:
        raise PracticeError(
            "practice_unavailable",
            "The private practice record is unavailable.",
        ) from None
    except OSError:
        raise _integrity_error() from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size <= 0
            or info.st_size > _MAX_RECORD_BYTES
        ):
            raise _integrity_error()
        payload = bytearray()
        while len(payload) <= _MAX_RECORD_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, _MAX_RECORD_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) > _MAX_RECORD_BYTES
            or (info.st_dev, info.st_ino, info.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
        ):
            raise _integrity_error()
    finally:
        os.close(descriptor)
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise _integrity_error() from None
    if not isinstance(value, dict):
        raise _integrity_error()
    return value


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _guard_leaf(parent_fd: int, leaf: str) -> None:
    try:
        info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise _integrity_error()


def _ensure_private_directory(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path), create=True):
            pass
    except OSError:
        raise _integrity_error() from None


def _require_private_directory(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path)):
            pass
    except OSError:
        raise _integrity_error() from None


def _directory_identity(path: Path, anchor: ProjectAnchor) -> tuple[int, int]:
    try:
        with anchor.directory(anchor.relative(path)) as descriptor:
            opened = os.fstat(descriptor)
            return opened.st_dev, opened.st_ino
    except OSError:
        raise _integrity_error() from None


def _require_directory_identity(
    path: Path,
    expected: tuple[int, int],
    anchor: ProjectAnchor,
) -> None:
    if _directory_identity(path, anchor) != expected:
        raise _integrity_error()


def _open_lock(path: Path, anchor: ProjectAnchor) -> int:
    try:
        with anchor.parent(path) as (parent_fd, leaf):
            descriptor = os.open(
                leaf,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
    except OSError:
        raise _integrity_error() from None
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        os.close(descriptor)
        raise _integrity_error()
    return descriptor


def _acquire_lock(descriptor: int, mode: int) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise PracticeError(
                    "practice_busy",
                    "Practice storage is busy. Retry shortly.",
                    retryable=True,
                ) from None
            time.sleep(0.01)


def _inventory_directory(
    path: Path,
    anchor: ProjectAnchor,
) -> tuple[int, int, tuple[tuple[str, bytes], ...]]:
    try:
        context = anchor.directory(anchor.relative(path))
        descriptor = context.__enter__()
    except OSError:
        raise _integrity_error() from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise _integrity_error()
        names = sorted(os.listdir(descriptor))
        count = 0
        byte_count = 0
        leaves: list[tuple[str, bytes]] = []
        for name in names:
            if Path(name).name != name or not name.endswith(".json"):
                raise _integrity_error()
            try:
                leaf = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
            except OSError:
                raise _integrity_error() from None
            try:
                before = os.fstat(leaf)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != os.getuid()
                    or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_size <= 0
                    or before.st_size > _MAX_RECORD_BYTES
                ):
                    raise _integrity_error()
                payload = bytearray()
                while len(payload) <= _MAX_RECORD_BYTES:
                    chunk = os.read(
                        leaf,
                        min(64 * 1024, _MAX_RECORD_BYTES + 1 - len(payload)),
                    )
                    if not chunk:
                        break
                    payload.extend(chunk)
                after = os.fstat(leaf)
                named = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                identity = (before.st_dev, before.st_ino, before.st_size)
                if (
                    len(payload) > _MAX_RECORD_BYTES
                    or identity != (after.st_dev, after.st_ino, after.st_size)
                    or identity != (named.st_dev, named.st_ino, named.st_size)
                ):
                    raise _integrity_error()
            finally:
                os.close(leaf)
            count += 1
            byte_count += len(payload)
            leaves.append((name, hashlib.sha256(payload).digest()))
        return count, byte_count, tuple(leaves)
    finally:
        context.__exit__(None, None, None)


def _directory_is_empty(path: Path, anchor: ProjectAnchor) -> bool:
    try:
        with anchor.directory(anchor.relative(path)) as descriptor:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise _integrity_error()
            return not os.listdir(descriptor)
    except OSError:
        raise _integrity_error() from None


def _removed_inventory(before: dict[str, Any]) -> dict[str, Any]:
    categories = {
        name: {
            "count": 0 if name == "recovery" else int(value["count"]),
            "bytes": 0 if name == "recovery" else int(value["bytes"]),
        }
        for name, value in before["categories"].items()
    }
    totals = {
        "count": before["totals"]["count"] - before["categories"]["recovery"]["count"],
        "bytes": before["totals"]["bytes"] - before["categories"]["recovery"]["bytes"],
    }
    return {
        "schema_version": _USAGE_SCHEMA_VERSION,
        "categories": categories,
        "totals": totals,
    }


def _path_exists(path: Path, anchor: ProjectAnchor) -> bool:
    try:
        with anchor.parent(path) as (parent_fd, leaf):
            os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise _integrity_error() from None
    return True


def _sync_directory(path: Path, anchor: ProjectAnchor) -> None:
    try:
        with anchor.directory(anchor.relative(path)) as descriptor:
            os.fsync(descriptor)
    except OSError:
        raise _integrity_error() from None


def _rename_directory(
    parent: Path,
    source: str,
    target: str,
    anchor: ProjectAnchor,
) -> None:
    try:
        with anchor.directory(anchor.relative(parent)) as descriptor:
            try:
                os.stat(target, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _integrity_error()
            os.rename(source, target, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            os.fsync(descriptor)
    except PracticeError:
        raise
    except OSError:
        raise _integrity_error() from None


def _remove_private_tree(
    parent: Path,
    leaf: str,
    *,
    expected_identity: tuple[int, int],
    anchor: ProjectAnchor,
) -> None:
    try:
        context = anchor.directory(anchor.relative(parent))
        parent_fd = context.__enter__()
    except OSError:
        raise _integrity_error() from None
    try:
        parent_info = os.fstat(parent_fd)
        if expected_identity[0] != parent_info.st_dev:
            raise _integrity_error()
        _remove_tree_at(
            parent_fd,
            leaf,
            expected_identity=expected_identity,
            expected_device=parent_info.st_dev,
        )
        os.fsync(parent_fd)
    finally:
        context.__exit__(None, None, None)


def _remove_tree_at(
    parent_fd: int,
    leaf: str,
    *,
    expected_identity: tuple[int, int] | None,
    expected_device: int,
) -> None:
    if not leaf or Path(leaf).name != leaf:
        raise _integrity_error()
    try:
        descriptor = os.open(
            leaf,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError:
        raise _integrity_error() from None
    try:
        info = os.fstat(descriptor)
        named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_dev != expected_device
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
            or (
                expected_identity is not None
                and (info.st_dev, info.st_ino) != expected_identity
            )
        ):
            raise _integrity_error()
        for name in sorted(os.listdir(descriptor)):
            if Path(name).name != name:
                raise _integrity_error()
            item = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(item.st_mode):
                _remove_tree_at(
                    descriptor,
                    name,
                    expected_identity=(item.st_dev, item.st_ino),
                    expected_device=expected_device,
                )
            elif (
                stat.S_ISREG(item.st_mode)
                and item.st_uid == os.getuid()
                and item.st_nlink == 1
                and stat.S_IMODE(item.st_mode) == 0o600
                and item.st_dev == expected_device
            ):
                record = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
                try:
                    opened = os.fstat(record)
                    named_record = os.stat(
                        name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        (opened.st_dev, opened.st_ino)
                        != (named_record.st_dev, named_record.st_ino)
                        or opened.st_nlink != 1
                        or opened.st_dev != expected_device
                    ):
                        raise _integrity_error()
                    os.unlink(name, dir_fd=descriptor)
                finally:
                    os.close(record)
                os.fsync(descriptor)
            else:
                raise _integrity_error()
    finally:
        os.close(descriptor)
    try:
        remaining = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if (remaining.st_dev, remaining.st_ino) != (info.st_dev, info.st_ino):
            raise _integrity_error()
        os.rmdir(leaf, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError:
        raise _integrity_error() from None


def _idempotency_hash(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 8 <= len(value.encode("utf-8")) <= 200
    ):
        raise PracticeError(
            "invalid_idempotency_key",
            "Use an Idempotency-Key containing 8–200 UTF-8 bytes.",
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_reset_receipt(
    value: dict[str, Any],
    *,
    key_hash: str | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    expected = {
        "practice_reset_schema_version",
        "key_hash",
        "fingerprint",
        "reset_id",
        "phase",
        "recorded_at",
        "root_device",
        "root_inode",
        "removed",
    }
    if (
        set(value) != expected
        or value.get("practice_reset_schema_version") != _RESET_SCHEMA_VERSION
        or not _valid_hex(value.get("key_hash"), 64)
        or not _valid_hex(value.get("fingerprint"), 64)
        or not _valid_hex(value.get("reset_id"), 64)
        or value.get("phase") not in {"prepared", "activated", "complete"}
        or not _is_timestamp(value.get("recorded_at"))
        or type(value.get("root_device")) is not int
        or value["root_device"] <= 0
        or type(value.get("root_inode")) is not int
        or value["root_inode"] <= 0
        or not _valid_removed_inventory(value.get("removed"))
    ):
        raise _integrity_error()
    if (
        (key_hash is not None and value["key_hash"] != key_hash)
        or (fingerprint is not None and value["fingerprint"] != fingerprint)
    ):
        raise PracticeError(
            "idempotency_conflict",
            "The idempotency key was used for a different practice action.",
        )
    return value


def _valid_removed_inventory(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "categories",
        "totals",
    }:
        return False
    if value["schema_version"] != _USAGE_SCHEMA_VERSION:
        return False
    categories = value["categories"]
    if not isinstance(categories, dict) or set(categories) != {
        "sessions",
        "heads",
        "attempts",
        "receipts",
        "recovery",
    }:
        return False
    for category in categories.values():
        if not isinstance(category, dict) or set(category) != {"count", "bytes"}:
            return False
        if any(type(category[key]) is not int or category[key] < 0 for key in category):
            return False
    totals = value["totals"]
    if not isinstance(totals, dict) or set(totals) != {"count", "bytes"}:
        return False
    if any(type(totals[key]) is not int or totals[key] < 0 for key in totals):
        return False
    return (
        totals["count"] == sum(item["count"] for item in categories.values())
        and totals["bytes"] == sum(item["bytes"] for item in categories.values())
        and categories["recovery"] == {"count": 0, "bytes": 0}
    )


def _validate_receipt(
    value: dict[str, Any],
    *,
    key_hash: str,
    action: str,
    fingerprint: str,
) -> None:
    expected = {
        "practice_receipt_schema_version",
        "key_hash",
        "action",
        "fingerprint",
        "recorded_at",
        "attempt_id",
        "parent_attempt_id",
        "generation",
    }
    if (
        set(value) != expected
        or value.get("practice_receipt_schema_version") != "1.0.0-draft"
        or value.get("key_hash") != key_hash
        or value.get("action") != action
        or value.get("fingerprint") != fingerprint
        or not _is_timestamp(value.get("recorded_at"))
        or type(value.get("generation")) is not int
        or value["generation"] < 0
    ):
        raise PracticeError(
            "idempotency_conflict",
            "The idempotency key was used for a different practice action.",
        )
    if action == "create":
        if (
            value["attempt_id"] is not None
            or value["parent_attempt_id"] is not None
            or value["generation"] != 0
        ):
            raise _integrity_error()
    elif (
        not _valid_identifier(value.get("attempt_id"), "attempt_", 64)
        or not _valid_identifier(value.get("parent_attempt_id"), "attempt_", 64)
        or value["generation"] <= 0
    ):
        raise _integrity_error()


def _json_names(path: Path, anchor: ProjectAnchor) -> tuple[str, ...]:
    try:
        with anchor.directory(anchor.relative(path)) as descriptor:
            return tuple(
                sorted(name for name in os.listdir(descriptor) if name.endswith(".json"))
            )
    except OSError:
        raise _integrity_error() from None


def _entry_count(path: Path, anchor: ProjectAnchor) -> int:
    return len(_json_names(path, anchor))


def _valid_identifier(value: str, prefix: str, digest_length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len(prefix) + digest_length
        and value.startswith(prefix)
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )


def _valid_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _reject_duplicate_keys(pairs):
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def _integrity_error() -> PracticeError:
    return PracticeError(
        "practice_storage_integrity",
        "Private practice storage failed an integrity check.",
    )
