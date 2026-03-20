"""TPM (Tokens Per Minute) dispatcher with priority queues and metrics.

Maintains a sliding 60-second token window.  Incoming requests are queued
and dispatched in priority + wait-time order, fitting as many as possible
into the available budget before waiting.

Priority levels (lower = higher priority):
  0 — high   (live user dialogue)
  1 — normal (cron jobs, default)
  2 — low    (batch / background tasks)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from uuid import uuid4

logger = logging.getLogger(__name__)

# Public constants for callers
PRIORITY_HIGH = 0
PRIORITY_NORMAL = 1
PRIORITY_LOW = 2

_PRIORITY_LABELS = {0: "high", 1: "normal", 2: "low"}


# ── internal data ──────────────────────────────────────────────────────

@dataclass(slots=True)
class _Record:
    """One entry in the sliding token-usage window."""
    ts: float
    tokens: int
    ticket_id: str  # links reservation → settle


@dataclass
class _Ticket:
    """A queued request waiting for TPM budget."""
    id: str
    estimated: int
    priority: int
    enqueued: float
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False


# ── dispatcher ─────────────────────────────────────────────────────────

class TPMDispatcher:
    """Single-process, in-memory TPM tracker with priority queue."""

    def __init__(self, limit: int, timeout: float = 120.0) -> None:
        self.limit = limit
        self.timeout = timeout
        self._window: deque[_Record] = deque()
        self._queue: list[_Ticket] = []
        self._retry_handle: asyncio.TimerHandle | None = None
        self._started_at: float = time.monotonic()
        # ── metrics ──
        self._total_requests: int = 0
        self._total_tokens: int = 0
        self._total_timeouts: int = 0
        self._per_priority: dict[int, int] = {0: 0, 1: 0, 2: 0}
        self._wait_times: deque[tuple[float, float]] = deque()  # (ts, seconds)
        self._max_queue_depth: int = 0
        self._max_wait_time: float = 0.0
        # dispatch→settle tracking for latency
        self._dispatch_times: dict[str, float] = {}  # ticket_id → dispatch mono

    # ── window bookkeeping ─────────────────────────────────────────

    def _purge(self) -> int:
        """Drop records older than 60 s, return current usage."""
        cutoff = time.monotonic() - 60
        while self._window and self._window[0].ts < cutoff:
            self._window.popleft()
        return sum(r.tokens for r in self._window)

    @property
    def used(self) -> int:
        return self._purge()

    @property
    def available(self) -> int:
        return max(self.limit - self._purge(), 0)

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    # ── core dispatch logic ────────────────────────────────────────

    def _try_dispatch(self) -> None:
        """Pick the best fitting candidate(s) from the queue and wake them."""
        while True:
            avail = self.limit - self._purge()

            # Find candidates that fit in the remaining budget
            candidates = [
                t for t in self._queue
                if t.estimated <= avail and not t.cancelled
            ]
            if not candidates:
                self._schedule_retry()
                return

            # Best candidate: highest priority, then longest wait
            candidates.sort(key=lambda t: (t.priority, t.enqueued))
            winner = candidates[0]

            # Reserve budget in the window
            self._queue.remove(winner)
            now = time.monotonic()
            self._window.append(
                _Record(now, winner.estimated, winner.id)
            )

            # ── metrics: record wait time ──
            wait = now - winner.enqueued
            self._wait_times.append((now, wait))
            if wait > self._max_wait_time:
                self._max_wait_time = wait
            self._dispatch_times[winner.id] = now

            logger.info(
                "TPM: dispatch %s [%s] est=%d wait=%.1fs (avail=%d, queued=%d)",
                winner.id,
                _PRIORITY_LABELS.get(winner.priority, "?"),
                winner.estimated,
                wait,
                avail,
                len(self._queue),
            )
            winner.ready.set()
            # loop back — maybe another small request fits too

    def _schedule_retry(self) -> None:
        """Set a timer to re-dispatch when the oldest record expires."""
        if not self._queue or not self._window:
            return
        # Cancel any existing timer
        if self._retry_handle is not None:
            self._retry_handle.cancel()
        oldest_ts = self._window[0].ts
        delay = max((oldest_ts + 60.0) - time.monotonic() + 0.1, 0.1)
        try:
            loop = asyncio.get_running_loop()
            self._retry_handle = loop.call_later(delay, self._on_retry)
        except RuntimeError:
            pass  # no running loop (e.g. unit tests)

    def _on_retry(self) -> None:
        self._retry_handle = None
        self._try_dispatch()

    # ── public API ─────────────────────────────────────────────────

    async def acquire(
        self, estimated: int, priority: int = PRIORITY_NORMAL
    ) -> str:
        """Queue a request and wait until TPM budget is available.

        Returns a *ticket_id* that must be passed to :meth:`settle`
        when the actual token count is known.

        Raises ``TimeoutError`` after *self.timeout* seconds.
        """
        self._total_requests += 1
        self._per_priority[priority] = self._per_priority.get(priority, 0) + 1

        ticket = _Ticket(
            id=uuid4().hex[:8],
            estimated=estimated,
            priority=priority,
            enqueued=time.monotonic(),
        )
        self._queue.append(ticket)
        # track max queue depth
        if len(self._queue) > self._max_queue_depth:
            self._max_queue_depth = len(self._queue)

        self._try_dispatch()

        try:
            await asyncio.wait_for(ticket.ready.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            ticket.cancelled = True
            self._total_timeouts += 1
            try:
                self._queue.remove(ticket)
            except ValueError:
                pass
            raise TimeoutError(
                f"TPM budget timeout after {self.timeout}s "
                f"(used={self.used}, est={estimated}, limit={self.limit})"
            )

        return ticket.id

    def settle(self, ticket_id: str, actual: int) -> None:
        """Replace the reservation with actual token usage, re-dispatch."""
        self._total_tokens += actual
        self._dispatch_times.pop(ticket_id, None)
        for i, rec in enumerate(self._window):
            if rec.ticket_id == ticket_id:
                # Update in place: keep original timestamp, replace tokens
                self._window[i] = _Record(rec.ts, actual, "")
                break
        logger.info("TPM: settle %s actual=%d", ticket_id, actual)
        # Budget may have freed up — try to dispatch waiting requests
        self._try_dispatch()

    def get_stats(self) -> dict:
        """Return collected metrics snapshot."""
        now = time.monotonic()
        uptime = now - self._started_at

        # purge old wait times (keep last 5 minutes for percentiles)
        cutoff = now - 300
        while self._wait_times and self._wait_times[0][0] < cutoff:
            self._wait_times.popleft()

        waits = [w for _, w in self._wait_times]
        waits_sorted = sorted(waits) if waits else []

        def percentile(data: list[float], p: float) -> float:
            if not data:
                return 0.0
            idx = int(len(data) * p / 100)
            return data[min(idx, len(data) - 1)]

        # tokens per minute over last 60s
        self._purge()
        tokens_last_60s = sum(r.tokens for r in self._window)

        return {
            "uptime_seconds": round(uptime),
            "tpm_limit": self.limit,
            "tpm_used": tokens_last_60s,
            "tpm_available": max(self.limit - tokens_last_60s, 0),
            "tpm_utilization_pct": round(tokens_last_60s / self.limit * 100, 1) if self.limit else 0,
            "queue_depth": len(self._queue),
            "queue_max_depth": self._max_queue_depth,
            "requests": {
                "total": self._total_requests,
                "by_priority": {
                    "high": self._per_priority.get(0, 0),
                    "normal": self._per_priority.get(1, 0),
                    "low": self._per_priority.get(2, 0),
                },
                "timeouts": self._total_timeouts,
            },
            "tokens": {
                "total_settled": self._total_tokens,
                "avg_per_request": round(self._total_tokens / self._total_requests) if self._total_requests else 0,
            },
            "wait_time": {
                "avg": round(sum(waits) / len(waits), 2) if waits else 0,
                "max": round(self._max_wait_time, 2),
                "p50": round(percentile(waits_sorted, 50), 2),
                "p95": round(percentile(waits_sorted, 95), 2),
                "p99": round(percentile(waits_sorted, 99), 2),
                "samples": len(waits),
            },
            "window_records": len(self._window),
        }

    # ── estimation helpers ─────────────────────────────────────────

    @staticmethod
    def estimate_input(body: dict) -> int:
        """Rough input-token estimate from the request body."""
        chars = 0
        for msg in body.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        chars += len(part.get("text", ""))
        # ~3 chars per token (conservative for mixed languages) + overhead
        return chars // 3 + 500

    @staticmethod
    def estimate_total(body: dict, limit: int = 0) -> int:
        """Estimate input + output tokens for budget check.

        If *limit* is given, cap the estimate so it never exceeds the
        TPM window (otherwise a huge prompt would be stuck forever).
        """
        input_est = TPMDispatcher.estimate_input(body)
        max_out = body.get("max_tokens") or body.get("max_completion_tokens") or 4096
        total = input_est + max_out
        if limit > 0:
            total = min(total, limit)
        return max(total, 1)
