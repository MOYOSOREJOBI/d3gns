from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import config as _cfg

logger = logging.getLogger(__name__)


@dataclass
class SchedulerJob:
    name: str
    fn: Callable[[], Any]
    interval_s: float
    jitter_s: float = 0.0
    run_immediately: bool = False
    last_run_at: float | None = None
    last_error: str | None = None
    run_count: int = 0
    error_count: int = 0


@dataclass
class BotScheduleEntry:
    bot_id: str
    fn: Callable[[], Any]
    platform: str
    interval_seconds: int = 60
    enabled: bool = False
    last_run_at: float | None = None
    next_run_at: float | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    error_count: int = 0
    run_count: int = 0


class Scheduler:
    def __init__(self):
        self._jobs: dict[str, SchedulerJob] = {}
        self._job_threads: dict[str, threading.Thread] = {}
        self._job_stops: dict[str, threading.Event] = {}
        self._bot_entries: dict[str, BotScheduleEntry] = {}
        self._bot_threads: dict[str, threading.Thread] = {}
        self._bot_stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._started = False
        self._db = None
        self._owner_state_getter: Callable[[], dict[str, Any]] | None = None
        self._budget = None
        self._proposal_runner: Callable[[str], dict[str, Any] | None] | None = None

    def configure(
        self,
        *,
        db_module: Any = None,
        owner_state_getter: Callable[[], dict[str, Any]] | None = None,
        quota_budget: Any = None,
        proposal_runner: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> None:
        self._db = db_module
        self._owner_state_getter = owner_state_getter
        self._budget = quota_budget
        self._proposal_runner = proposal_runner

    def add_job(
        self,
        name: str,
        fn: Callable[[], Any],
        interval_s: float,
        jitter_s: float = 5.0,
        run_immediately: bool = False,
        replace: bool = False,
    ) -> None:
        with self._lock:
            if name in self._jobs and not replace:
                return
            self._jobs[name] = SchedulerJob(
                name=name,
                fn=fn,
                interval_s=interval_s,
                jitter_s=jitter_s,
                run_immediately=run_immediately,
            )
            if self._started:
                self._launch_job(name)

    def register_bot(
        self,
        bot_id: str,
        fn: Callable[[], Any],
        *,
        platform: str,
        interval_seconds: int,
        enabled: bool = False,
    ) -> None:
        with self._lock:
            if self._db and hasattr(self._db, "get_bot_schedule_config"):
                stored = self._db.get_bot_schedule_config(bot_id)
                if stored:
                    enabled = bool(stored.get("enabled", enabled))
                    interval_seconds = int(stored.get("interval_seconds", interval_seconds))
            entry = BotScheduleEntry(
                bot_id=bot_id,
                fn=fn,
                platform=platform,
                interval_seconds=interval_seconds,
                enabled=enabled,
                next_run_at=time.time() + interval_seconds,
            )
            self._bot_entries[bot_id] = entry
            if self._db and hasattr(self._db, "set_bot_schedule_config"):
                self._db.set_bot_schedule_config(
                    bot_id,
                    enabled=enabled,
                    interval_seconds=interval_seconds,
                )
            if self._started and _cfg.ENABLE_EXPANSION_SCHEDULER and enabled:
                self._launch_bot(bot_id)

    def enable_bot(self, bot_id: str) -> bool:
        with self._lock:
            entry = self._bot_entries.get(bot_id)
            if not entry:
                return False
            entry.enabled = True
            entry.next_run_at = time.time() + entry.interval_seconds
            if self._db and hasattr(self._db, "set_bot_schedule_config"):
                self._db.set_bot_schedule_config(bot_id, enabled=True, interval_seconds=entry.interval_seconds)
            if self._started and _cfg.ENABLE_EXPANSION_SCHEDULER:
                self._launch_bot(bot_id)
            return True

    def disable_bot(self, bot_id: str) -> bool:
        with self._lock:
            entry = self._bot_entries.get(bot_id)
            if not entry:
                return False
            entry.enabled = False
            stop_event = self._bot_stops.pop(bot_id, None)
            if stop_event:
                stop_event.set()
            self._bot_threads.pop(bot_id, None)
            if self._db and hasattr(self._db, "set_bot_schedule_config"):
                self._db.set_bot_schedule_config(bot_id, enabled=False, interval_seconds=entry.interval_seconds)
            return True

    def set_interval(self, bot_id: str, interval_seconds: int) -> bool:
        with self._lock:
            entry = self._bot_entries.get(bot_id)
            if not entry:
                return False
            entry.interval_seconds = max(5, int(interval_seconds))
            entry.next_run_at = time.time() + entry.interval_seconds
            if self._db and hasattr(self._db, "set_bot_schedule_config"):
                self._db.set_bot_schedule_config(bot_id, enabled=entry.enabled, interval_seconds=entry.interval_seconds)
            return True

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            for name in list(self._jobs):
                self._launch_job(name)
            if _cfg.ENABLE_EXPANSION_SCHEDULER:
                for bot_id, entry in self._bot_entries.items():
                    if entry.enabled:
                        self._launch_bot(bot_id)
        logger.info(
            "Scheduler started with %s maintenance job(s); expansion scheduler enabled=%s",
            len(self._jobs),
            _cfg.ENABLE_EXPANSION_SCHEDULER,
        )

    def stop(self) -> None:
        with self._lock:
            self._started = False
            job_threads = list(self._job_threads.values())
            bot_threads = list(self._bot_threads.values())
            for stop in self._job_stops.values():
                stop.set()
            for stop in self._bot_stops.values():
                stop.set()
            self._job_stops.clear()
            self._bot_stops.clear()
            self._job_threads.clear()
            self._bot_threads.clear()
        for thread in job_threads + bot_threads:
            try:
                if thread.is_alive():
                    thread.join(timeout=0.5)
            except Exception:
                logger.debug("Scheduler thread join skipped", exc_info=True)
        logger.info("Scheduler stopped")

    def _launch_job(self, name: str) -> None:
        job = self._jobs.get(name)
        thread = self._job_threads.get(name)
        if thread and not thread.is_alive():
            self._job_threads.pop(name, None)
        if not job or name in self._job_threads:
            return
        stop = threading.Event()
        self._job_stops[name] = stop
        thread = threading.Thread(target=self._run_job_loop, args=(job, stop), name=f"sched-{name}", daemon=True)
        self._job_threads[name] = thread
        thread.start()

    def _launch_bot(self, bot_id: str) -> None:
        thread = self._bot_threads.get(bot_id)
        if thread and not thread.is_alive():
            self._bot_threads.pop(bot_id, None)
        if bot_id in self._bot_threads:
            return
        entry = self._bot_entries.get(bot_id)
        if not entry:
            return
        stop = threading.Event()
        self._bot_stops[bot_id] = stop
        thread = threading.Thread(target=self._run_bot_loop, args=(entry, stop), name=f"bot-sched-{bot_id}", daemon=True)
        self._bot_threads[bot_id] = thread
        thread.start()

    def _run_job_loop(self, job: SchedulerJob, stop: threading.Event) -> None:
        if not job.run_immediately:
            stop.wait(job.interval_s + random.uniform(0, job.jitter_s))
        while not stop.is_set():
            try:
                job.fn()
                job.last_run_at = time.time()
                job.run_count += 1
            except Exception as exc:
                job.last_error = str(exc)
                job.error_count += 1
                logger.warning("Scheduler job '%s' failed: %s", job.name, exc)
            stop.wait(job.interval_s + random.uniform(0, job.jitter_s))

    def _run_bot_loop(self, entry: BotScheduleEntry, stop: threading.Event) -> None:
        while not stop.is_set():
            sleep_for = max(1.0, (entry.next_run_at or time.time()) - time.time())
            if stop.wait(sleep_for):
                return
            self._run_bot_cycle(entry)

    def _run_bot_cycle(self, entry: BotScheduleEntry) -> None:
        now = time.time()
        state = self._owner_state_getter() if self._owner_state_getter else {}
        if state and not state.get("owned_by_self", False):
            result = {
                "state": "skipped",
                "degraded_reason": "Execution authority not held by this process.",
                "skipped": True,
            }
            self._record_bot_result(entry, result, now)
            return
        if self._budget and entry.platform and not self._budget.can_request(entry.platform):
            result = {
                "state": "quota_blocked",
                "degraded_reason": self._budget.degraded_reason(entry.platform) or "Quota budget exhausted.",
                "skipped": True,
            }
            self._record_bot_result(entry, result, now)
            return
        try:
            if self._proposal_runner:
                proposal_result = self._proposal_runner(entry.bot_id) or {}
                if proposal_result:
                    if self._budget and entry.platform:
                        self._budget.record(entry.platform, cost=1)
                    self._record_bot_result(entry, proposal_result, now)
                    return
            result = entry.fn() or {}
            if self._budget and entry.platform:
                self._budget.record(entry.platform, cost=int(result.get("quota_cost", 1)))
            self._record_bot_result(entry, result, now)
        except Exception as exc:
            entry.error_count += 1
            entry.last_error = str(exc)
            result = {
                "state": "error",
                "degraded_reason": str(exc),
                "skipped": True,
            }
            self._record_bot_result(entry, result, now)
            if self._db and hasattr(self._db, "save_failure_event"):
                self._db.save_failure_event(entry.bot_id, "scheduler_cycle_failed", "warning", str(exc), {"platform": entry.platform})

    def _record_bot_result(self, entry: BotScheduleEntry, result: dict[str, Any], now: float) -> None:
        entry.last_run_at = now
        entry.next_run_at = now + entry.interval_seconds
        entry.last_result = result
        entry.run_count += 1
        entry.last_error = result.get("degraded_reason", "") if result.get("state") == "error" else ""
        if self._db and hasattr(self._db, "set_bot_schedule_config"):
            self._db.set_bot_schedule_config(
                entry.bot_id,
                enabled=entry.enabled,
                interval_seconds=entry.interval_seconds,
                last_run_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                last_result=result,
                error_count=entry.error_count,
                last_error=entry.last_error,
            )

    def trigger_now(self, name: str) -> bool:
        if name in self._jobs:
            job = self._jobs[name]
            try:
                job.fn()
                job.last_run_at = time.time()
                job.run_count += 1
                return True
            except Exception as exc:
                job.last_error = str(exc)
                job.error_count += 1
                return False
        entry = self._bot_entries.get(name)
        if not entry:
            return False
        self._run_bot_cycle(entry)
        return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._started,
                "expansion_enabled": _cfg.ENABLE_EXPANSION_SCHEDULER,
                "maintenance_jobs": {
                    name: {
                        "interval_s": job.interval_s,
                        "last_run_at": job.last_run_at,
                        "run_count": job.run_count,
                        "error_count": job.error_count,
                        "last_error": job.last_error,
                        "alive": name in self._job_threads and self._job_threads[name].is_alive(),
                    }
                    for name, job in self._jobs.items()
                },
                "bots": {
                    bot_id: {
                        "enabled": entry.enabled,
                        "interval_seconds": entry.interval_seconds,
                        "last_run_at": entry.last_run_at,
                        "next_run_at": entry.next_run_at,
                        "error_count": entry.error_count,
                        "last_error": entry.last_error,
                        "last_result": entry.last_result,
                        "platform": entry.platform,
                        "alive": bot_id in self._bot_threads and self._bot_threads[bot_id].is_alive(),
                    }
                    for bot_id, entry in self._bot_entries.items()
                },
            }


_SCHEDULER: Scheduler | None = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> Scheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        with _scheduler_lock:
            if _SCHEDULER is None:
                _SCHEDULER = Scheduler()
    return _SCHEDULER
