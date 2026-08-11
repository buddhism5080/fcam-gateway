from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.config import SchedulingConfig


@dataclass
class RuntimeScheduling:
    """
    In-process overrides for scientific key selection.

    Updated via admin API without process restart. Falls back to AppConfig.scheduling
    values when a field is None.
    """

    freshness_half_life_seconds: int | None = None
    unknown_credit_baseline: float | None = None
    credit_workers: int | None = None


class RuntimeSettings:
    def __init__(self, *, scheduling: SchedulingConfig | None = None) -> None:
        self._lock = threading.RLock()
        base = scheduling or SchedulingConfig()
        self._scheduling = RuntimeScheduling(
            freshness_half_life_seconds=int(base.freshness_half_life_seconds),
            unknown_credit_baseline=float(base.unknown_credit_baseline),
            credit_workers=None,
        )

    def get_scheduling(self) -> RuntimeScheduling:
        with self._lock:
            return RuntimeScheduling(
                freshness_half_life_seconds=self._scheduling.freshness_half_life_seconds,
                unknown_credit_baseline=self._scheduling.unknown_credit_baseline,
                credit_workers=self._scheduling.credit_workers,
            )

    def patch_scheduling(
        self,
        *,
        freshness_half_life_seconds: int | None = None,
        unknown_credit_baseline: float | None = None,
        credit_workers: int | None = None,
        unset_credit_workers: bool = False,
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
            return self.get_scheduling()

    def effective_half_life(self, fallback: int) -> float:
        with self._lock:
            v = self._scheduling.freshness_half_life_seconds
            return float(v if v is not None else fallback)

    def effective_unknown_baseline(self, fallback: float) -> float:
        with self._lock:
            v = self._scheduling.unknown_credit_baseline
            return float(v if v is not None else fallback)

    def effective_credit_workers(self, fallback: int) -> int:
        with self._lock:
            v = self._scheduling.credit_workers
            return int(v if v is not None else fallback)
