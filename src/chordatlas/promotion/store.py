from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from chordatlas._fs import TargetOccupiedError, create_text_exclusive
from chordatlas.promotion.models import (
    ApprovalRecord,
    MappingConfig,
    PromotionError,
    PromotionResult,
    PromotionSpec,
    RevocationRecord,
    canonical_json,
)

_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_EVENTS = 256
_MAX_RECEIPTS = 10_000


class PromotionStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.root = self.project_root / ".chordatlas" / "promotion"
        self.configs = self.root / "configs" / "sha256"
        self.results = self.root / "results" / "sha256"
        self.specs = self.root / "specs" / "sha256"
        self.approvals = self.root / "approvals"
        self.revocations = self.root / "revocations"
        self.events = self.root / "events"
        self.receipts = self.root / "idempotency"
        self.locks = self.root / "locks"

    @classmethod
    def initialize(cls, project_root: Path) -> PromotionStore:
        store = cls(project_root)
        for path in (
            store.root,
            store.root / "configs",
            store.configs,
            store.root / "results",
            store.results,
            store.root / "specs",
            store.specs,
            store.approvals,
            store.revocations,
            store.events,
            store.receipts,
            store.locks,
        ):
            _ensure_dir(path)
        return store

    def claim_receipt(
        self,
        *,
        idempotency_key: str,
        action: str,
        fingerprint: str,
        recorded_at: str,
    ) -> tuple[str, bool, str]:
        if (
            not isinstance(idempotency_key, str)
            or not 8 <= len(idempotency_key.encode("utf-8")) <= 200
        ):
            raise PromotionError(
                "invalid_idempotency_key",
                "Use an Idempotency-Key containing 8–200 UTF-8 bytes.",
            )
        if (
            action not in {"approve", "revoke"}
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
            or not _is_timestamp(recorded_at)
        ):
            raise PromotionError(
                "promotion_integrity",
                "The promotion action failed an integrity check.",
            )
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        receipt_id = f"rcp_{key_hash}"
        path = self.receipts / f"{key_hash}.json"
        with self.claim(self.locks / "idempotency.lock"):
            if path.exists():
                value = self._read_json(path)
                if (
                    set(value)
                    != {
                        "promotion_receipt_schema_version",
                        "key_hash",
                        "action",
                        "fingerprint",
                        "recorded_at",
                    }
                    or value.get("promotion_receipt_schema_version")
                    != "1.0.0-draft"
                    or value.get("key_hash") != key_hash
                    or value.get("fingerprint") != fingerprint
                    or value.get("action") != action
                    or not _is_timestamp(value.get("recorded_at"))
                ):
                    raise PromotionError(
                        "idempotency_conflict",
                        "The idempotency key was used for a different promotion action.",
                    )
                return str(value["recorded_at"]), True, receipt_id
            receipt_count = sum(1 for _ in self.receipts.glob("*.json"))
            if receipt_count >= _MAX_RECEIPTS:
                raise PromotionError(
                    "promotion_limit",
                    "This project reached its private promotion-receipt limit.",
                )
            self._publish(
                path,
                {
                    "promotion_receipt_schema_version": "1.0.0-draft",
                    "key_hash": key_hash,
                    "action": action,
                    "fingerprint": fingerprint,
                    "recorded_at": recorded_at,
                },
            )
        return recorded_at, False, receipt_id

    def publish_approval(
        self,
        *,
        config: MappingConfig,
        spec: PromotionSpec,
        result: PromotionResult,
        approval: ApprovalRecord,
        allow_existing: bool,
    ) -> None:
        with self.claim(self.locks / f"{approval.session_id}.lock"):
            if (
                allow_existing
                and self.revocation_for(approval.session_id, approval.id) is not None
            ):
                raise PromotionError(
                    "idempotency_expired",
                    "This idempotent approval was already revoked.",
                )
            active = self.active_for_session(approval.session_id)
            if active is not None:
                if active.id == approval.id and allow_existing:
                    return
                raise PromotionError(
                    "active_approval_exists",
                    "Revoke the current approval before approving another chart.",
                )
            self._publish(
                self.configs / f"{config.id[7:]}.json",
                {"id": config.id, **config.to_mapping()},
            )
            self._publish(
                self.results / f"{result.id[4:]}.json",
                result.to_record_mapping(),
            )
            self._publish(
                self.specs / f"{spec.id[7:]}.json",
                spec.to_record_mapping(),
            )
            self._publish(
                self.approvals / f"{approval.id}.json",
                approval.to_record_mapping(),
            )
            self._append_event(
                approval.session_id,
                {
                    "kind": "approved",
                    "approval_id": approval.id,
                    "at": approval.approved_at,
                },
            )

    def revoke(
        self,
        *,
        session_id: str,
        approval_id: str,
        expected_token: str,
        revocation: RevocationRecord,
    ) -> None:
        with self.claim(self.locks / f"{session_id}.lock"):
            active = self.active_for_session(session_id)
            if active is None or active.id != approval_id:
                prior = self.revocation_for(session_id, approval_id)
                if prior is not None and prior.id == revocation.id:
                    return
                raise PromotionError(
                    "approval_not_active",
                    "The selected approval is no longer active.",
                    retryable=True,
                )
            if expected_token != self.approval_token(active):
                raise PromotionError(
                    "promotion_conflict",
                    "The approval state changed. Refresh and try again.",
                    retryable=True,
                )
            self._publish(
                self.revocations / f"{revocation.id}.json",
                revocation.to_record_mapping(),
            )
            self._append_event(
                session_id,
                {
                    "kind": "revoked",
                    "approval_id": approval_id,
                    "revocation_id": revocation.id,
                    "at": revocation.revoked_at,
                },
            )

    def load_approval(self, approval_id: str) -> ApprovalRecord:
        if (
            len(approval_id) != 68
            or not approval_id.startswith("apr_")
            or any(character not in "0123456789abcdef" for character in approval_id[4:])
        ):
            raise PromotionError(
                "promotion_unavailable",
                "The private promotion record is unavailable.",
            )
        try:
            record = ApprovalRecord.from_mapping(
                self._read_json(self.approvals / f"{approval_id}.json")
            )
            if record.id != approval_id:
                raise ValueError("approval path does not match record")
            return record
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def load_result(self, result_id: str) -> PromotionResult:
        if (
            len(result_id) != 68
            or not result_id.startswith("pro_")
            or any(character not in "0123456789abcdef" for character in result_id[4:])
        ):
            raise PromotionError(
                "promotion_unavailable",
                "The private promotion record is unavailable.",
            )
        try:
            record = PromotionResult.from_mapping(
                self._read_json(self.results / f"{result_id[4:]}.json")
            )
            if record.id != result_id:
                raise ValueError("result path does not match record")
            return record
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def load_config(self, config_id: str) -> MappingConfig:
        if not _valid_id(config_id, "sha256:", 64):
            raise PromotionError(
                "promotion_unavailable",
                "The private promotion record is unavailable.",
            )
        try:
            value = self._read_json(self.configs / f"{config_id[7:]}.json")
            if value.get("id") != config_id:
                raise ValueError("config path does not match record")
            mapping = dict(value)
            mapping.pop("id")
            config = MappingConfig.from_mapping(mapping)
            if config.id != config_id:
                raise ValueError("config id does not match content")
            return config
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def load_spec(self, spec_id: str) -> PromotionSpec:
        if not _valid_id(spec_id, "sha256:", 64):
            raise PromotionError(
                "promotion_unavailable",
                "The private promotion record is unavailable.",
            )
        try:
            record = PromotionSpec.from_mapping(
                self._read_json(self.specs / f"{spec_id[7:]}.json")
            )
            if record.id != spec_id:
                raise ValueError("spec path does not match record")
            return record
        except (KeyError, TypeError, ValueError):
            raise _integrity_error() from None

    def active_for_session(self, session_id: str) -> ApprovalRecord | None:
        active_id: str | None = None
        for event in self._events_for(session_id):
            kind = event.get("kind")
            if kind == "approved":
                if active_id is not None:
                    raise _integrity_error()
                active_id = str(event["approval_id"])
            elif kind == "revoked":
                if active_id != event.get("approval_id"):
                    raise _integrity_error()
                active_id = None
            else:
                raise _integrity_error()
        return None if active_id is None else self.load_approval(active_id)

    def revocation_for(
        self,
        session_id: str,
        approval_id: str,
    ) -> RevocationRecord | None:
        for event in self._events_for(session_id):
            if event["kind"] != "revoked" or event["approval_id"] != approval_id:
                continue
            revocation_id = str(event["revocation_id"])
            try:
                record = RevocationRecord.from_mapping(
                    self._read_json(self.revocations / f"{revocation_id}.json")
                )
            except (KeyError, TypeError, ValueError):
                raise _integrity_error() from None
            if (
                record.id != revocation_id
                or record.approval_id != approval_id
                or record.revoked_at != event["at"]
            ):
                raise _integrity_error()
            return record
        return None

    def require_active(self, approval_id: str) -> ApprovalRecord:
        approval = self.load_approval(approval_id)
        active = self.active_for_session(approval.session_id)
        if active is None or active.id != approval.id:
            raise PromotionError(
                "approval_revoked",
                (
                    "This approval no longer authorizes new exports. Previously "
                    "written files are not retracted."
                ),
            )
        return approval

    @staticmethod
    def approval_token(approval: ApprovalRecord) -> str:
        return f"approval_{approval.id[-24:]}"

    def latest_event_at(self, session_id: str) -> str | None:
        events = self._events_for(session_id)
        return None if not events else str(events[-1]["at"])

    def _append_event(self, session_id: str, event: dict[str, Any]) -> None:
        directory = self.events / session_id
        _ensure_dir(directory)
        paths = sorted(directory.glob("*.json"))
        if len(paths) >= _MAX_EVENTS:
            raise PromotionError(
                "promotion_limit",
                "This review session reached its approval event limit.",
            )
        event = {
            "promotion_event_schema_version": "1.0.0-draft",
            "generation": len(paths),
            **event,
        }
        self._publish(directory / f"{len(paths):08d}.json", event)

    def _events_for(self, session_id: str) -> tuple[dict[str, Any], ...]:
        directory = self.events / session_id
        if not directory.exists():
            return ()
        _require_dir(directory)
        paths = sorted(directory.glob("*.json"))
        if len(paths) > _MAX_EVENTS:
            raise _integrity_error()
        values = []
        seen_approvals: set[str] = set()
        for generation, path in enumerate(paths):
            value = self._read_json(path)
            kind = value.get("kind")
            expected_keys = (
                {
                    "promotion_event_schema_version",
                    "generation",
                    "kind",
                    "approval_id",
                    "at",
                }
                if kind == "approved"
                else {
                    "promotion_event_schema_version",
                    "generation",
                    "kind",
                    "approval_id",
                    "revocation_id",
                    "at",
                }
            )
            if (
                set(value) != expected_keys
                or kind not in {"approved", "revoked"}
                or value.get("promotion_event_schema_version") != "1.0.0-draft"
                or value.get("generation") != generation
                or path.name != f"{generation:08d}.json"
                or not _valid_id(value.get("approval_id"), "apr_", 64)
                or not _is_timestamp(value.get("at"))
                or (
                    kind == "revoked"
                    and not _valid_id(value.get("revocation_id"), "rev_", 64)
                )
            ):
                raise _integrity_error()
            approval_id = str(value["approval_id"])
            if kind == "approved":
                if approval_id in seen_approvals:
                    raise _integrity_error()
                seen_approvals.add(approval_id)
            values.append(value)
        return tuple(values)

    def _publish(self, path: Path, value: dict[str, Any]) -> None:
        content = canonical_json(value).decode("utf-8") + "\n"
        if len(content.encode("utf-8")) > _MAX_JSON_BYTES:
            raise PromotionError(
                "promotion_limit",
                "The private promotion record exceeds the local size limit.",
            )
        try:
            create_text_exclusive(
                path,
                content,
                stage_prefix=".promotion-",
                mode=0o600,
            )
        except TargetOccupiedError:
            if self._read_json(path) != value:
                raise _integrity_error() from None

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            info = path.lstat()
        except OSError:
            raise PromotionError(
                "promotion_unavailable",
                "The private promotion record is unavailable.",
            ) from None
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size <= 0
            or info.st_size > _MAX_JSON_BYTES
        ):
            raise _integrity_error()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _integrity_error() from None
        if not isinstance(value, dict):
            raise _integrity_error()
        return value

    @contextmanager
    def claim(self, path: Path) -> Iterator[None]:
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
        except OSError:
            raise _integrity_error() from None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise _integrity_error()
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise PromotionError(
                            "promotion_busy",
                            "Promotion storage is busy. Retry shortly.",
                            retryable=True,
                        ) from None
                    time.sleep(0.01)
            yield
        finally:
            os.close(descriptor)


def _ensure_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    _require_dir(path)
    os.chmod(path, 0o700)


def _require_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise _integrity_error() from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise _integrity_error()


def _integrity_error() -> PromotionError:
    return PromotionError(
        "promotion_storage_integrity",
        "Private promotion storage failed an integrity check.",
    )


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_id(value: Any, prefix: str, digest_length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len(prefix) + digest_length
        and value.startswith(prefix)
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )
