"""Event log + timeline event model (spec §13).

Stdlib only, no LiveKit/Rime imports: this module is the observability backbone and
must stay importable inside the evaluation harness and unit tests with zero provider
credentials configured.

Every event carries the ``request_id`` it belongs to so the timeline is itself a
proof of the fencing decision, not decoration.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    """Canonical event names. These strings are the public telemetry contract
    (consumed by the frontend timeline and asserted on by the test suite)."""

    # request lifecycle
    REQUEST_CREATED = "request_created"
    REQUEST_INVALIDATED = "request_invalidated"
    REQUEST_COMPLETED = "request_completed"
    # tool lifecycle
    TOOL_STARTED = "tool_started"
    TOOL_RETURNED = "tool_returned"  # .data.verdict in {accepted, discarded}
    # speech lifecycle (Rime)
    SPEECH_STARTED = "speech_started"
    SPEECH_COMPLETED = "speech_completed"
    SPEECH_CANCELLED = "speech_cancelled"
    SPEECH_WORD = "speech_word"  # word-level timestamp, for the heard ledger
    # interruption
    INTERRUPTION_DETECTED = "interruption_detected"
    HEARD_RECONCILED = "heard_reconciled"
    # orchestration
    STATE_CHANGED = "state_changed"
    MEASUREMENT = "measurement"
    NOTE = "note"


@dataclass(slots=True)
class TimelineEvent:
    """One row of the event timeline."""

    seq: int
    kind: EventKind
    request_id: int | None
    # Monotonic wall clock (ms). In the deterministic simulator this is the virtual
    # clock, which is what makes cross-mode latency numbers comparable.
    t_ms: float
    state: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Human label used verbatim by the UI (mirrors spec §13 wording)."""
        rid = f"#{self.request_id}" if self.request_id is not None else ""
        verdict = self.data.get("verdict")
        return {
            EventKind.REQUEST_CREATED: f"REQUEST {rid} CREATED",
            EventKind.REQUEST_INVALIDATED: f"REQUEST {rid} INVALIDATED",
            EventKind.REQUEST_COMPLETED: f"REQUEST {rid} COMPLETED",
            EventKind.TOOL_STARTED: f"TOOL {rid} STARTED",
            EventKind.TOOL_RETURNED: (
                f"TOOL {rid} RETURNED → "
                + ("ACCEPTED ✓" if verdict == "accepted" else "DISCARDED ✓")
            ),
            EventKind.SPEECH_STARTED: f"RIME RESPONSE {rid} STARTED",
            EventKind.SPEECH_COMPLETED: f"RIME RESPONSE {rid} COMPLETED",
            EventKind.SPEECH_CANCELLED: f"RIME SPEECH CANCELLED ✓ {rid}",
            EventKind.INTERRUPTION_DETECTED: "USER INTERRUPTION DETECTED",
            EventKind.HEARD_RECONCILED: f"HEARD STATE RECONCILED {rid}",
            EventKind.STATE_CHANGED: f"STATE → {self.state}",
            EventKind.MEASUREMENT: f"METRIC {self.data.get('name')}",
            EventKind.NOTE: str(self.data.get("text", "")),
        }[self.kind]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["label"] = self.label
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), default=str)


class ManualClock:
    """A clock someone else advances. Used by the discrete-event simulator so that every
    timestamp in the log — including ones emitted deep inside the kernel, where passing a
    time argument through would be noise — comes from the virtual clock and not the wall."""

    def __init__(self) -> None:
        self._ms = 0.0

    def advance_to(self, t_ms: float) -> None:
        if t_ms < self._ms:
            raise ValueError(f"clock went backwards: {t_ms} < {self._ms}")
        self._ms = t_ms

    def __call__(self) -> float:
        return self._ms / 1000.0


class EventLog:
    """Append-only event sink with fan-out to live subscribers (data channel / SSE).

    ``clock`` is the *only* time source the kernel sees. Live runs pass
    ``time.monotonic``; the simulator passes a :class:`ManualClock`.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._events: list[TimelineEvent] = []
        self._subs: list[Callable[[TimelineEvent], None]] = []
        self._clock = clock

    def set_clock(self, clock: Callable[[], float]) -> None:
        self._clock = clock

    # -- write ---------------------------------------------------------------
    def emit(
        self,
        kind: EventKind,
        *,
        request_id: int | None = None,
        state: str | None = None,
        t_ms: float | None = None,
        **data: Any,
    ) -> TimelineEvent:
        ev = TimelineEvent(
            seq=len(self._events),
            kind=kind,
            request_id=request_id,
            t_ms=self._clock() * 1000.0 if t_ms is None else t_ms,
            state=state,
            data=data,
        )
        self._events.append(ev)
        for sub in list(self._subs):
            sub(ev)
        return ev

    # -- read ----------------------------------------------------------------
    @property
    def events(self) -> list[TimelineEvent]:
        return list(self._events)

    def find(self, kind: EventKind, request_id: int | None = None) -> list[TimelineEvent]:
        return [
            e
            for e in self._events
            if e.kind == kind and (request_id is None or e.request_id == request_id)
        ]

    def first(self, kind: EventKind, request_id: int | None = None) -> TimelineEvent | None:
        hits = self.find(kind, request_id)
        return hits[0] if hits else None

    def since(self, seq: int) -> list[TimelineEvent]:
        return self._events[seq:]

    def to_jsonl(self) -> str:
        return "\n".join(e.to_json() for e in self._events)

    def subscribe(self, callback: Callable[[TimelineEvent], None]) -> Callable[[], None]:
        self._subs.append(callback)

        def _unsub() -> None:
            if callback in self._subs:
                self._subs.remove(callback)

        return _unsub

    def clear(self) -> None:
        self._events.clear()
