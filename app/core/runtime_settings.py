from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import SchedulingConfig

logger = logging.getLogger(__name__)

# Keys written to runtime_settings.json (hot overrides only; None = follow file/env).
_PERSIST_FIELDS = (
    "freshness_half_life_seconds",
    "unknown_credit_baseline",
    "credit_workers",
    "http_connection_pool_enabled",
    "credit_batch_size",
    "credit_batch_delay_seconds",
    "credit_refresh_check_interval_seconds",
    "credit_retry_delay_minutes",
    "epsilon_greedy",
)


@dataclass
class RuntimeScheduling:
    """
    In-process overrides for scientific key selection + credit refresh pacing.

    Updated via admin API without process restart. Falls back to AppConfig values
    when a field is None. When a persist path is configured, overrides survive restart.
    """

    freshness_half_life_seconds: int | None = None
    unknown_credit_baseline: float | None = None
    credit_workers: int | None = None
    # None = follow file/env config; True/False = hot override for UpstreamHttpPool
    http_connection_pool_enabled: bool | None = None
    credit_batch_size: int | None = None
    credit_batch_delay_seconds: int | None = None
    credit_refresh_check_interval_seconds: int | None = None
    credit_retry_delay_minutes: int | None = None
    # Explore probability for ε-greedy / rotation (0..1). None = follow file default.
    epsilon_greedy: float | None = None


def default_runtime_settings_path(*, database_path: str | None = None) -> Path:
    """
    Resolve persist file path.

    Priority:
    1) FCAM_RUNTIME_SETTINGS_PATH
    2) sibling of SQLite DB file: <db_dir>/runtime_settings.json
    3) ./data/runtime_settings.json
    """
    env = (os.environ.get("FCAM_RUNTIME_SETTINGS_PATH") or "").strip()
    if env:
        return Path(env).expanduser()

    if database_path:
        p = Path(database_path).expanduser()
        try:
            parent = p.resolve().parent
        except Exception:
            parent = p.parent
        return parent / "runtime_settings.json"

    return Path("./data/runtime_settings.json")


class RuntimeSettings:
    def __init__(
        self,
        *,
        scheduling: SchedulingConfig | None = None,
        http_connection_pool_enabled: bool | None = None,
        persist_path: str | Path | None = None,
        load_persisted: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._persist_path: Path | None = Path(persist_path).expanduser() if persist_path else None
        base = scheduling or SchedulingConfig()
        self._scheduling = RuntimeScheduling(
            freshness_half_life_seconds=int(base.freshness_half_life_seconds),
            unknown_credit_baseline=float(base.unknown_credit_baseline),
            credit_workers=None,
            http_connection_pool_enabled=http_connection_pool_enabled,
            credit_batch_size=None,
            credit_batch_delay_seconds=None,
            credit_refresh_check_interval_seconds=None,
            credit_retry_delay_minutes=None,
            epsilon_greedy=float(getattr(base, "epsilon_greedy", 0.1) or 0.1),
        )
        if load_persisted and self._persist_path is not None:
            self._load_from_disk()

    @property
    def persist_path(self) -> Path | None:
        return self._persist_path

    def set_persist_path(self, path: str | Path | None) -> None:
        with self._lock:
            self._persist_path = Path(path).expanduser() if path else None

    def get_scheduling(self) -> RuntimeScheduling:
        with self._lock:
            return RuntimeScheduling(
                freshness_half_life_seconds=self._scheduling.freshness_half_life_seconds,
                unknown_credit_baseline=self._scheduling.unknown_credit_baseline,
                credit_workers=self._scheduling.credit_workers,
                http_connection_pool_enabled=self._scheduling.http_connection_pool_enabled,
                credit_batch_size=self._scheduling.credit_batch_size,
                credit_batch_delay_seconds=self._scheduling.credit_batch_delay_seconds,
                credit_refresh_check_interval_seconds=self._scheduling.credit_refresh_check_interval_seconds,
                credit_retry_delay_minutes=self._scheduling.credit_retry_delay_minutes,
                epsilon_greedy=self._scheduling.epsilon_greedy,
            )

    def patch_scheduling(
        self,
        *,
        freshness_half_life_seconds: int | None = None,
        unknown_credit_baseline: float | None = None,
        credit_workers: int | None = None,
        unset_credit_workers: bool = False,
        http_connection_pool_enabled: bool | None = None,
        clear_http_connection_pool_override: bool = False,
        credit_batch_size: int | None = None,
        clear_credit_batch_size: bool = False,
        credit_batch_delay_seconds: int | None = None,
        clear_credit_batch_delay_seconds: bool = False,
        credit_refresh_check_interval_seconds: int | None = None,
        clear_credit_refresh_check_interval_seconds: bool = False,
        credit_retry_delay_minutes: int | None = None,
        clear_credit_retry_delay_minutes: bool = False,
        epsilon_greedy: float | None = None,
        clear_epsilon_greedy: bool = False,
        persist: bool = True,
    ) -> RuntimeScheduling:
        with self._lock:
            if freshness_half_life_seconds is not None:
                self._scheduling.freshness_half_life_seconds = max(int(freshness_half_life_seconds), 1)
            if unknown_credit_baseline is not None:
                self._scheduling.unknown_credit_baseline = max(float(unknown_credit_baseline), 0.0)
            if unset_credit_workers:
                self._scheduling.credit_workers = None
            elif credit_workers is not None:
                self._scheduling.credit_workers = max(int(credit_workers), 1)
            if clear_http_connection_pool_override:
                self._scheduling.http_connection_pool_enabled = None
            elif http_connection_pool_enabled is not None:
                self._scheduling.http_connection_pool_enabled = bool(http_connection_pool_enabled)

            if clear_credit_batch_size:
                self._scheduling.credit_batch_size = None
            elif credit_batch_size is not None:
                self._scheduling.credit_batch_size = max(int(credit_batch_size), 1)

            if clear_credit_batch_delay_seconds:
                self._scheduling.credit_batch_delay_seconds = None
            elif credit_batch_delay_seconds is not None:
                self._scheduling.credit_batch_delay_seconds = max(int(credit_batch_delay_seconds), 0)

            if clear_credit_refresh_check_interval_seconds:
                self._scheduling.credit_refresh_check_interval_seconds = None
            elif credit_refresh_check_interval_seconds is not None:
                self._scheduling.credit_refresh_check_interval_seconds = max(
                    int(credit_refresh_check_interval_seconds), 1
                )

            if clear_credit_retry_delay_minutes:
                self._scheduling.credit_retry_delay_minutes = None
            elif credit_retry_delay_minutes is not None:
                self._scheduling.credit_retry_delay_minutes = max(int(credit_retry_delay_minutes), 1)

            if clear_epsilon_greedy:
                self._scheduling.epsilon_greedy = None
            elif epsilon_greedy is not None:
                self._scheduling.epsilon_greedy = min(max(float(epsilon_greedy), 0.0), 1.0)

            if persist:
                self._save_to_disk_unlocked()
            return self.get_scheduling()

    def effective_half_life(self, fallback: int) -> float:
        with self._lock:
            v = self._scheduling.freshness_half_life_seconds
            return float(v if v is not None else fallback)

    def effective_unknown_baseline(self, fallback: float) -> float:
        with self._lock:
            v = self._scheduling.unknown_credit_baseline
            return float(v if v is not None else fallback)

    def effective_epsilon_greedy(self, fallback: float) -> float:
        with self._lock:
            v = self._scheduling.epsilon_greedy
            x = float(v if v is not None else fallback)
            return min(max(x, 0.0), 1.0)

    def effective_credit_workers(self, fallback: int) -> int:
        with self._lock:
            v = self._scheduling.credit_workers
            return int(v if v is not None else fallback)

    def effective_http_connection_pool_enabled(self, fallback: bool) -> bool:
        with self._lock:
            v = self._scheduling.http_connection_pool_enabled
            return bool(fallback if v is None else v)

    def effective_credit_batch_size(self, fallback: int) -> int:
        with self._lock:
            v = self._scheduling.credit_batch_size
            return max(int(v if v is not None else fallback), 1)

    def effective_credit_batch_delay_seconds(self, fallback: int) -> int:
        with self._lock:
            v = self._scheduling.credit_batch_delay_seconds
            return max(int(v if v is not None else fallback), 0)

    def effective_credit_refresh_check_interval_seconds(self, fallback: int) -> int:
        with self._lock:
            v = self._scheduling.credit_refresh_check_interval_seconds
            return max(int(v if v is not None else fallback), 1)

    def effective_credit_retry_delay_minutes(self, fallback: int) -> int:
        with self._lock:
            v = self._scheduling.credit_retry_delay_minutes
            return max(int(v if v is not None else fallback), 1)

    def _overrides_dict_unlocked(self) -> dict[str, Any]:
        """Only fields that are intentional hot overrides (not pure file mirrors for half-life)."""
        s = self._scheduling
        out: dict[str, Any] = {}
        # Always persist current effective scheduling knobs that UI edits, including
        # half-life / baseline (initialized from file but treated as editable runtime state).
        for name in _PERSIST_FIELDS:
            val = getattr(s, name, None)
            if val is not None:
                out[name] = val
        return out

    def _load_from_disk(self) -> None:
        path = self._persist_path
        if path is None or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("runtime_settings.load_failed", extra={"fields": {"path": str(path)}})
            return
        if not isinstance(raw, dict):
            return
        data = raw.get("overrides") if isinstance(raw.get("overrides"), dict) else raw
        if not isinstance(data, dict):
            return
        try:
            if "freshness_half_life_seconds" in data and data["freshness_half_life_seconds"] is not None:
                self._scheduling.freshness_half_life_seconds = max(int(data["freshness_half_life_seconds"]), 1)
            if "unknown_credit_baseline" in data and data["unknown_credit_baseline"] is not None:
                self._scheduling.unknown_credit_baseline = max(float(data["unknown_credit_baseline"]), 0.0)
            if "credit_workers" in data:
                v = data["credit_workers"]
                self._scheduling.credit_workers = None if v is None else max(int(v), 1)
            if "http_connection_pool_enabled" in data:
                v = data["http_connection_pool_enabled"]
                self._scheduling.http_connection_pool_enabled = None if v is None else bool(v)
            if "credit_batch_size" in data:
                v = data["credit_batch_size"]
                self._scheduling.credit_batch_size = None if v is None else max(int(v), 1)
            if "credit_batch_delay_seconds" in data:
                v = data["credit_batch_delay_seconds"]
                self._scheduling.credit_batch_delay_seconds = None if v is None else max(int(v), 0)
            if "credit_refresh_check_interval_seconds" in data:
                v = data["credit_refresh_check_interval_seconds"]
                self._scheduling.credit_refresh_check_interval_seconds = (
                    None if v is None else max(int(v), 1)
                )
            if "credit_retry_delay_minutes" in data:
                v = data["credit_retry_delay_minutes"]
                self._scheduling.credit_retry_delay_minutes = None if v is None else max(int(v), 1)
            if "epsilon_greedy" in data and data["epsilon_greedy"] is not None:
                self._scheduling.epsilon_greedy = min(max(float(data["epsilon_greedy"]), 0.0), 1.0)
            logger.info(
                "runtime_settings.loaded",
                extra={"fields": {"path": str(path), "keys": sorted(self._overrides_dict_unlocked().keys())}},
            )
        except Exception:
            logger.exception("runtime_settings.load_parse_failed", extra={"fields": {"path": str(path)}})

    def _save_to_disk_unlocked(self) -> None:
        path = self._persist_path
        if path is None:
            return
        payload = {
            "version": 1,
            "overrides": self._overrides_dict_unlocked(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            logger.info(
                "runtime_settings.saved",
                extra={"fields": {"path": str(path), "keys": sorted(payload["overrides"].keys())}},
            )
        except Exception:
            logger.exception("runtime_settings.save_failed", extra={"fields": {"path": str(path)}})

    def save_to_disk(self) -> None:
        with self._lock:
            self._save_to_disk_unlocked()
