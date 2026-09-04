"""Interruption + cancellation reconciliation (spec §4 steps 5-6, §2 evidence rule).

Split into two calls on purpose:

    begin()  -> synchronous, immediate. Detect, transition, orphan the old request,
                and hand back the fence token we want the provider to stop.
    finish() -> called by the runtime *after* the provider has acknowledged that audio
                actually stopped (Rime ``clear`` ack / SpeechHandle.interrupt()). This is
                where stop latency is measured, so the number reflects a real stop rather
                than a request to stop.

Latency measured here is `speech stopped - interruption detected` per spec §12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from events import EventKind, EventLog
from state_manager import (
    AgentState,
    PlaybackLedger,
    Policy,
    RequestFence,
    SpeechHandle,
    StateMachine,
)


@dataclass(slots=True)
class Interruption:
    """The in-flight record of one barge-in, from detection to stop confirmation."""

    interruption_id: int
    detected_ms: float
    superseded_request_id: int | None
    speech: SpeechHandle | None
    #: Set by finish().
    stopped_ms: float | None = None
    heard_text: str | None = None
    dropped_backlog_ms: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def stop_latency_ms(self) -> float | None:
        if self.stopped_ms is None:
            return None
        return self.stopped_ms - self.detected_ms


@dataclass(slots=True)
class CancellationPlan:
    """What the runtime must go do. Returned so this module never awaits I/O itself."""

    should_cancel: bool
    context_id: str | None
    reason: str
    request_id: int | None


class InterruptionController:
    """Owns the INTERRUPTED → CANCELLING leg of the machine and the metrics for it."""

    def __init__(
        self,
        *,
        log: EventLog,
        fence: RequestFence,
        machine: StateMachine,
        ledger: PlaybackLedger,
        policy: Policy,
    ) -> None:
        self._log = log
        self._fence = fence
        self._machine = machine
        self._ledger = ledger
        self._policy = policy
        self._counter = 0
        self._open: Interruption | None = None
        #: Every barge-in that reached a confirmed stop, oldest first.
        self.completed: list[Interruption] = []

    @property
    def policy(self) -> Policy:
        return self._policy

    # -- step 1: detection ---------------------------------------------------
    def begin(self, *, now_ms: float, speech: SpeechHandle | None, source: str) -> CancellationPlan:
        """User started talking over the agent (or replaced the request).

        Under NAIVE policy we still *record* the interruption — the naive runtime does
        notice barge-in, it just refuses to invalidate the old request or stop its audio.
        That asymmetry is the whole point of the control arm.
        """
        self._counter += 1
        superseded = self._fence.active_request_id
        self._open = Interruption(
            interruption_id=self._counter,
            detected_ms=now_ms,
            superseded_request_id=superseded,
            speech=speech,
            notes=[f"source={source}"],
        )
        self._log.emit(
            EventKind.INTERRUPTION_DETECTED,
            request_id=superseded,
            interruption_id=self._counter,
            source=source,
            policy=self._policy.value,
            was_speaking=speech is not None,
        )

        if self._policy is Policy.NAIVE:
            # Control arm: no invalidation, no cancel. Old audio keeps playing and its
            # tool result will still be applied when it lands.
            return CancellationPlan(
                should_cancel=False,
                context_id=None,
                reason="naive policy does not cancel queued audio",
                request_id=superseded,
            )

        if superseded is not None:
            self._fence.invalidate(superseded, reason=f"barge-in #{self._counter}")
        if self._machine.state in (AgentState.SPEAKING, AgentState.THINKING):
            self._machine.to(AgentState.INTERRUPTED, request_id=superseded)
        if speech is None:
            # Nothing audible to stop; skip straight back to listening.
            if self._machine.state is AgentState.INTERRUPTED:
                self._machine.to(AgentState.LISTENING)
            self._open.stopped_ms = now_ms
            self.completed.append(self._open)
            self._open = None
            return CancellationPlan(False, None, "no speech in flight", superseded)
        if self._machine.state is AgentState.INTERRUPTED:
            self._machine.to(AgentState.CANCELLING, request_id=superseded)
        return CancellationPlan(
            should_cancel=True,
            context_id=speech.context_id,
            reason="user superseded the request",
            request_id=superseded,
        )

    # -- step 2: confirmed stop ---------------------------------------------
    def finish(
        self,
        *,
        now_ms: float,
        cancelled: bool,
        audible_until_ms: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> Interruption:
        """Called once the provider has actually stopped the audio."""
        intr = self._open
        if intr is None:  # nothing pending (naive arm with no speech, or replay)
            intr = Interruption(
                interruption_id=self._counter,
                detected_ms=now_ms,
                superseded_request_id=None,
                speech=None,
            )
            self.completed.append(intr)
            return intr
        speech = intr.speech
        # Single source of truth for "was a cancel actually issued": the policy, not the
        # caller's claim. The naive arm observes barge-ins but never issues a cancel, so a
        # finish() that reports the audio as stopped must not be allowed to say otherwise.
        cancel_issued = self._policy is Policy.SAFE
        if cancelled and not cancel_issued and speech is not None:
            self._log.emit(
                EventKind.NOTE,
                request_id=speech.request_id,
                t_ms=now_ms,
                severity="defect",
                text=(
                    "barge-in observed but no cancel issued (naive policy): obsolete speech "
                    "keeps playing to completion"
                ),
                stale_speech_remaining_ms=round(
                    max(0.0, speech.queued_backlog_ms - (now_ms - speech.started_ms)), 3
                ),
            )
            cancelled = False

        if cancelled and speech is not None:
            speech.cancelled = True
            # Kernel-owned fields win over caller detail, so a provider can never overwrite
            # the measurement it is being measured by.
            fields: dict[str, Any] = {**(detail or {})}
            fields.update(
                {
                    "speech_id": speech.speech_id,
                    "context_id": speech.context_id,
                    "stop_latency_ms": round(now_ms - intr.detected_ms, 3),
                }
            )
            self._log.emit(
                EventKind.SPEECH_CANCELLED,
                request_id=speech.request_id,
                t_ms=now_ms,
                **fields,
            )
            # Reconcile with what was audible: everything after the cut was never said.
            heard_until = audible_until_ms if audible_until_ms is not None else now_ms
            intr.heard_text = self._ledger.heard(speech.speech_id, until_ms=heard_until)
            intr.dropped_backlog_ms = max(
                0.0, speech.queued_backlog_ms - self._ledger.progress_ms(speech.speech_id, until_ms=heard_until)
            )
            if speech.request_id is not None:
                rec = self._fence.get(speech.request_id)
                rec.heard_text = intr.heard_text
                self._log.emit(
                    EventKind.HEARD_RECONCILED,
                    request_id=speech.request_id,
                    t_ms=now_ms,
                    heard=intr.heard_text,
                    unheard_ms=round(intr.dropped_backlog_ms, 3),
                )
        elif speech is not None and cancel_issued:
            self._log.emit(
                EventKind.NOTE,
                request_id=speech.request_id,
                t_ms=now_ms,
                text="interruption had no audible effect (no queued speech to cancel)",
            )

        if self._machine.state is AgentState.CANCELLING:
            self._machine.to(AgentState.LISTENING)
        self.completed.append(intr)
        self._open = None
        return intr

    @property
    def pending(self) -> Interruption | None:
        return self._open

    def snapshot(self) -> dict[str, Any]:
        return {
            "policy": self._policy.value,
            "interruptions": len(self.completed),
            "stop_latency_ms": [
                round(i.stop_latency_ms, 3) for i in self.completed if i.stop_latency_ms is not None
            ],
            "stale_audio_dropped_ms": [
                round(i.dropped_backlog_ms, 3)
                for i in self.completed
                if i.dropped_backlog_ms
            ],
        }
