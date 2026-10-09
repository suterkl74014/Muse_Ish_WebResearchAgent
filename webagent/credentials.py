from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_API_PROVIDERS = ("groq", "gemini", "openrouter")
SERVICE_NAME = "WebAgent.APIKeys"


class CredentialStoreError(RuntimeError):
    pass


class CredentialBackendUnavailable(CredentialStoreError):
    pass


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


class KeyringBackend:
    """Small adapter so the credential store can be tested without a real OS keyring."""

    def __init__(self) -> None:
        try:
            import keyring  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on installation
            raise CredentialBackendUnavailable(
                "The 'keyring' package is not installed. Re-run the WebAgent installer or install dependencies."
            ) from exc
        self._keyring = keyring

    def set_password(self, service: str, username: str, password: str) -> None:
        try:
            self._keyring.set_password(service, username, password)
        except Exception as exc:  # pragma: no cover - backend/OS specific
            raise CredentialBackendUnavailable(f"OS credential storage is unavailable: {exc}") from exc

    def get_password(self, service: str, username: str) -> str | None:
        try:
            return self._keyring.get_password(service, username)
        except Exception as exc:  # pragma: no cover - backend/OS specific
            raise CredentialBackendUnavailable(f"OS credential storage is unavailable: {exc}") from exc

    def delete_password(self, service: str, username: str) -> None:
        try:
            self._keyring.delete_password(service, username)
        except self._keyring.errors.PasswordDeleteError:
            return
        except Exception as exc:  # pragma: no cover - backend/OS specific
            raise CredentialBackendUnavailable(f"OS credential storage is unavailable: {exc}") from exc


class CredentialStore:
    """
    WebAgent-owned API credential registry.

    Only non-secret metadata is written to ``metadata_path``. Raw API keys are
    stored via the operating-system credential backend (Windows Credential
    Manager through ``keyring`` on the supported Windows build).

    There is intentionally no WebAgent key-count limit.
    """

    def __init__(self, metadata_path: Path, backend: Any | None = None) -> None:
        self.metadata_path = Path(metadata_path)
        self.backend = backend
        self._lock = threading.RLock()
        self._data = self._load()

    def _backend(self):
        if self.backend is None:
            self.backend = KeyringBackend()
        return self.backend

    def _load(self) -> dict[str, Any]:
        if not self.metadata_path.exists():
            return {"version": 1, "credentials": []}
        try:
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            rows = raw.get("credentials") if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                rows = []
            clean = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                provider = str(row.get("provider") or "").lower()
                rid = str(row.get("id") or "")
                fp = str(row.get("fingerprint") or "")
                if provider in SUPPORTED_API_PROVIDERS and rid and fp:
                    clean.append({
                        "id": rid,
                        "provider": provider,
                        "fingerprint": fp,
                        "label": str(row.get("label") or ""),
                        "source": str(row.get("source") or "user"),
                        "created_at": str(row.get("created_at") or ""),
                    })
            return {"version": 1, "credentials": clean}
        except Exception:
            # A corrupt metadata file should not expose or destroy OS-stored secrets.
            return {"version": 1, "credentials": []}

    def _save(self) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.metadata_path.with_suffix(self.metadata_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.metadata_path)

    @staticmethod
    def _username(provider: str, record_id: str) -> str:
        return f"{provider}:{record_id}"

    def records(self, provider: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in self._data["credentials"]]
        if provider:
            p = provider.lower()
            rows = [r for r in rows if r["provider"] == p]
        return rows

    def add(self, provider: str, secret: str, *, label: str = "", source: str = "user") -> dict[str, Any]:
        result = self.add_many(provider, [secret], label_prefix=label, source=source)
        if result["added"]:
            return result["records"][0]
        # Duplicate: return the existing record.
        fp = fingerprint(secret.strip())
        return next(r for r in self.records(provider) if r["fingerprint"] == fp)

    def add_many(
        self,
        provider: str,
        secrets: Iterable[str],
        *,
        label_prefix: str = "",
        source: str = "user",
    ) -> dict[str, Any]:
        provider = provider.strip().lower()
        if provider not in SUPPORTED_API_PROVIDERS:
            raise ValueError(f"Unsupported API provider: {provider}")

        # Preserve input order while removing blanks and repeated lines.
        values: list[str] = []
        seen_values: set[str] = set()
        for value in secrets:
            key = str(value or "").strip()
            if key and key not in seen_values:
                values.append(key)
                seen_values.add(key)
        if not values:
            return {"added": 0, "duplicates": 0, "records": []}

        backend = self._backend()
        with self._lock:
            existing = {r["fingerprint"] for r in self._data["credentials"] if r["provider"] == provider}
            pending: list[tuple[dict[str, Any], str]] = []
            duplicates = 0
            base_count = len(existing)
            for pos, key in enumerate(values, start=1):
                fp = fingerprint(key)
                if fp in existing:
                    duplicates += 1
                    continue
                rid = uuid.uuid4().hex
                number = base_count + len(pending) + 1
                if label_prefix.strip():
                    label = label_prefix.strip() if len(values) == 1 else f"{label_prefix.strip()} {pos}"
                else:
                    label = f"{provider.title()} key {number}"
                row = {
                    "id": rid,
                    "provider": provider,
                    "fingerprint": fp,
                    "label": label,
                    "source": source,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                pending.append((row, key))
                existing.add(fp)

            created_usernames: list[str] = []
            try:
                for row, key in pending:
                    username = self._username(provider, row["id"])
                    backend.set_password(SERVICE_NAME, username, key)
                    created_usernames.append(username)
                if pending:
                    self._data["credentials"].extend(row for row, _ in pending)
                    self._save()
            except Exception:
                # Keep metadata and OS storage consistent if a bulk write fails midway.
                pending_ids = {row["id"] for row, _ in pending}
                self._data["credentials"] = [r for r in self._data["credentials"] if r["id"] not in pending_ids]
                for username in created_usernames:
                    try:
                        backend.delete_password(SERVICE_NAME, username)
                    except Exception:
                        pass
                raise
            rows = [dict(row) for row, _ in pending]
            return {"added": len(rows), "duplicates": duplicates, "records": rows}

    def keys(self, provider: str) -> list[str]:
        provider = provider.strip().lower()
        rows = self.records(provider)
        if not rows:
            return []
        backend = self._backend()
        out: list[str] = []
        for row in rows:
            value = backend.get_password(SERVICE_NAME, self._username(provider, row["id"]))
            if value:
                out.append(value)
        return out

    def remove(self, record_id: str) -> bool:
        with self._lock:
            row = next((r for r in self._data["credentials"] if r["id"] == record_id), None)
            if row is None:
                return False
            self._backend().delete_password(SERVICE_NAME, self._username(row["provider"], row["id"]))
            self._data["credentials"] = [r for r in self._data["credentials"] if r["id"] != record_id]
            self._save()
            return True

    def remove_many(self, record_ids: Iterable[str]) -> int:
        ids = set(record_ids)
        removed = 0
        # Use remove() so each OS credential is deleted before metadata is forgotten.
        for rid in list(ids):
            if self.remove(rid):
                removed += 1
        return removed

    def count(self, provider: str | None = None) -> int:
        return len(self.records(provider))
