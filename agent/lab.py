"""The lab orchestrator: wires fence + state machine + interruption + tool into one
explicit sequence, with **no I/O and no sleeps**.

This is the seam that makes the numbers honest. The live runtime (`main.py`) and the
deterministic simulator (`evaluation/simulate.py`) call the *same* methods in the *same*
order, only differing in where the timestamps come from:

  live  -> ``time.monotonic()`` at the real moment each thing happened
  sim   -> a virtual clock advanced by a discrete-event scheduler

so a simulated pass and a staged pass exercise identical decision logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from events import EventKind, EventLog
from interruption import CancellationPlan, InterruptionController
from metrics import Metric, compute_metrics
from state_manager import (
    AgentState,
    IllegalTransition,
    PlaybackLedger,
    Policy,
    RequestFence,
    RequestRecord,
    SpeechHandle,
    StateMachine,
)
from tools import BookingStore, Intent, SlowTool, ToolOutcome, parse_intent


@dataclass(slots=True)
class Decision:
    """What the fence decided about a late-arriving result."""

    request_id: int
    accepted: bool
    reason: str
    speak_text: str | None = None
    state: dict[str, Any] | None = None


@dataclass(slots=True)
class LabRun:
    """One scenario replay, with everything needed for the report."""

    policy: Policy
    log: EventLog
    fence: RequestFence
    machine: StateMachine
    controller: InterruptionController
    store: BookingStore
    ledger: PlaybackLedger
    decisions: list[Decision] = field(default_factory=list)
    speech: dict[str, SpeechHandle] = field(default_factory=dict)
    #: final intent the agent actually committed (ground truth target for accuracy)
    base_intent: Intent = field(default_factory=Intent)

    def snapshot(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "state": self.machine.state.value,
            "active_request_id": self.fence.active_request_id,
            "final_state": self.store.snapshot(),
            "requests": [
                {
                    "request_id": r.request_id,
                    "text": r.text,
                    "invalidated": r.invalidated,
                    "completed": r.completed,
                    "generation": r.generation,
                    "fence": r.fence_token,
                    "discarded_results": len(r.discarded_results),
                    "accepted_result": r.accepted_result,
                    "heard_text": r.heard_text,
                }
                for r in self.fence.requests
            ],
            "events": [e.to_dict() for e in self.log.events],
        }


class InterruptionLab:
    """Owns one live conversation's orchestration. One instance per session."""

    def __init__(
        self,
        *,
        policy: Policy = Policy.SAFE,
        tool: SlowTool | None = None,
        log: EventLog | None = None,
        interrupted_sessions_expected: int = 0,
    ) -> None:
        self.policy = policy
        log = log or EventLog()
        fence = RequestFence(log, policy=policy)
        machine = StateMachine(log)
        ledger = PlaybackLedger()
        self.run = LabRun(
            policy=policy,
            log=log,
            fence=fence,
            machine=machine,
            controller=InterruptionController(
                log=log,
                fence=fence,
                machine=machine,
                ledger=ledger,
                policy=policy,
            ),
            store=BookingStore(),
            ledger=ledger,
        )
        self.tool = tool or SlowTool()
        self._interrupted_sessions = interrupted_sessions_expected

    # -- turn intake ---------------------------------------------------------
    def start(self, *, now_ms: float) -> None:
        if self.run.machine.state is AgentState.IDLE:
            self.run.machine.to(AgentState.LISTENING)

    def begin_request(self, text: str, *, now_ms: float, delay_override_ms: float | None = None) -> RequestRecord:
        """User turn is finalized (end-of-turn detected by LiveKit/VAD)."""
        if self.run.machine.state is AgentState.IDLE:
            self.run.machine.to(AgentState.LISTENING)
        if self.run.machine.state in (AgentState.SPEAKING, AgentState.THINKING):
            # New work while speaking *is* an interruption. In SAFE the runtime must have
            # called interrupt() first — a silent overlap is exactly the bug class this lab
            # hunts, so it raises instead of quietly recovering.
            if self.policy is Policy.SAFE:
                raise IllegalTransition(
                    f"begin_request while {self.run.machine.state.value}: call interrupt() first"
                )
            self.run.log.emit(
                EventKind.NOTE,
                t_ms=now_ms,
                severity="defect",
                text=(
                    f"new turn began while {self.run.machine.state.value}: previous request's "
                    "work was never cancelled, so its late result can still mutate state"
                ),
            )
        elif self.run.machine.state in (AgentState.CANCELLING, AgentState.INTERRUPTED):
            # The user finished their replacement before the cancel round trip landed.
            self.run.machine.to(AgentState.LISTENING)
        rec = self.run.fence.begin(text)
        self.run.machine.to(AgentState.THINKING, request_id=rec.request_id)
        if delay_override_ms is not None:
            self.tool.set_delay(delay_override_ms)
        return rec

    # -- tool completion -----------------------------------------------------
    def deliver_tool_result(self, outcome: ToolOutcome, *, now_ms: float) -> Decision:
        """A tool result lands — possibly long after its request was superseded."""
        rid = outcome.request_id
        started = self.run.log.first(EventKind.TOOL_STARTED, rid)
        if started is None:
            self.run.log.emit(
                EventKind.TOOL_STARTED,
                request_id=rid,
                t_ms=outcome.started_ms,
                delay_ms=outcome.delay_ms,
            )
        accepted = self.run.fence.owns(rid, policy=self.policy)
        stale = self.run.fence.is_stale(rid)
        reason = (
            "request is active"
            if accepted and not stale
            else (
                f"a newer request #{self.run.fence.latest_request_id} is authoritative"
                if stale
                else f"request #{rid} is not active (active=#{self.run.fence.active_request_id})"
            )
        )
        self.run.log.emit(
            EventKind.TOOL_RETURNED,
            request_id=rid,
            t_ms=now_ms,
            verdict="accepted" if accepted else "discarded",
            stale=stale,
            active_request_id=self.run.fence.latest_request_id,
            reason=reason,
            latency_ms=round(now_ms - outcome.started_ms, 3),
            payload=outcome.result,
        )
        decision = Decision(request_id=rid, accepted=accepted, reason=reason)
        if not accepted:
            self.run.fence.record_result(rid, outcome.result, accepted=False)
            # Recorded on the store, not only in the log: "state remained consistent" is a
            # claim about the state object, so the state object has to carry the evidence.
            self.run.store.commit(rid, outcome.result, allowed=False)
            self.run.decisions.append(decision)
            return decision

        # Accepted: commit app state, then speak it. Under NAIVE a stale result can reach
        # this line, which is the defect the fixture is designed to expose — record the
        # clobber rather than hiding it, so the metric and the timeline agree.
        if stale and self.run.store.state != outcome.result:
            self.run.log.emit(
                EventKind.NOTE,
                request_id=rid,
                t_ms=now_ms,
                text=f"stale result for #{rid} clobbered state owned by #{self.run.fence.latest_request_id}",
                severity="defect",
                previous=self.run.store.snapshot(),
                overwritten=outcome.result,
            )
        committed = self.run.store.commit(rid, outcome.result, allowed=True)
        intent = Intent(
            action=str(outcome.result.get("domain", "booking")),
            party_size=outcome.result.get("party_size"),
            time=outcome.result.get("time"),
            origin=outcome.result.get("origin"),
            destination=outcome.result.get("destination"),
            cancelled=bool(outcome.result.get("cancelled")),
        )
        self.run.base_intent = intent
        decision.state = outcome.result if committed else None
        decision.speak_text = self._speakable(outcome.result)
        self.run.fence.record_result(rid, outcome.result, accepted=True)
        self.run.decisions.append(decision)
        return decision

    # -- speech --------------------------------------------------------------
    def begin_speech(
        self,
        text: str,
        *,
        request_id: int,
        now_ms: float,
        speech_id: str | None = None,
        queued_backlog_ms: float = 0.0,
    ) -> SpeechHandle | None:
        """Ask Rime to speak ``text``. Returns None if the fence rejects the generation."""
        rec = self.run.fence.get(request_id)
        if not self.run.fence.owns_generation(request_id, rec.generation, policy=self.policy):
            self.run.log.emit(
                EventKind.NOTE,
                request_id=request_id,
                t_ms=now_ms,
                text=f"speech suppressed before send: generation {rec.generation} is fenced",
            )
            return None
        sid = speech_id or f"speech-{request_id}-{rec.generation}"
        handle = SpeechHandle(
            speech_id=sid,
            request_id=request_id,
            generation=rec.generation,
            text=text,
            started_ms=now_ms,
            context_id=rec.fence_token,
            queued_backlog_ms=queued_backlog_ms,
        )
        self.run.speech[sid] = handle
        self.run.ledger.register(sid)
        if self.run.machine.state in (AgentState.THINKING, AgentState.LISTENING, AgentState.CANCELLING):
            if self.run.machine.state is AgentState.CANCELLING:
                self.run.machine.to(AgentState.LISTENING)
            if self.run.machine.state in (AgentState.LISTENING, AgentState.THINKING):
                if self.run.machine.state is AgentState.LISTENING:
                    self.run.machine.to(AgentState.THINKING, request_id=request_id)
                self.run.machine.to(AgentState.SPEAKING, request_id=request_id)
        self.run.log.emit(
            EventKind.SPEECH_STARTED,
            request_id=request_id,
            t_ms=now_ms,
            speech_id=sid,
            context_id=handle.context_id,
            stale=self.run.fence.is_stale(request_id),
            active_request_id=self.run.fence.latest_request_id,
            text=text,
        )
        return handle

    def note_words(
        self,
        handle: SpeechHandle,
        words: list[str],
        starts_ms: list[float],
        ends_ms: list[float],
    ) -> None:
        self.run.ledger.note(handle.speech_id, words, starts_ms, ends_ms)
        handle.spoken_words.extend(words)

    def end_speech(self, handle: SpeechHandle, *, now_ms: float) -> None:
        handle.completed = True
        self.run.log.emit(
            EventKind.SPEECH_COMPLETED,
            request_id=handle.request_id,
            t_ms=now_ms,
            speech_id=handle.speech_id,
            duration_ms=round(now_ms - handle.started_ms, 3),
        )
        if self.run.fence.active_request_id == handle.request_id:
            self.run.fence.complete(handle.request_id)
        if self.run.machine.state is AgentState.SPEAKING:
            self.run.machine.to(AgentState.LISTENING, request_id=handle.request_id)

    # -- interruption --------------------------------------------------------
    def interrupt(self, *, now_ms: float, speech: SpeechHandle | None, source: str = "user_speech") -> CancellationPlan:
        self._interrupted_sessions += 1 if source != "false_interruption" else 0
        plan = self.run.controller.begin(now_ms=now_ms, speech=speech, source=source)
        return plan

    def confirm_stopped(
        self,
        *,
        now_ms: float,
        cancelled: bool,
        audible_until_ms: float | None = None,
        detail: dict[str, Any] | None = None,
    ):
        return self.run.controller.finish(
            now_ms=now_ms, cancelled=cancelled, audible_until_ms=audible_until_ms, detail=detail
        )

    # -- reporting -----------------------------------------------------------
    def metrics(
        self,
        *,
        expected_final_state: dict[str, Any] | None = None,
        source: str = "simulated",
    ) -> dict[str, Metric]:
        return compute_metrics(
            self.run.log,
            interrupted_sessions=self._interrupted_sessions,
            final_state_expected=expected_final_state,
            final_state_actual=self.run.store.snapshot() if expected_final_state is not None else None,
            source=source,
        )

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _speakable(state: dict[str, Any]) -> str:
        """Plain, speakable sentence. Rime reads everything verbatim, so no markdown."""
        if state.get("cancelled"):
            return "Okay, I've cancelled that for you."
        bits: list[str] = []
        if state.get("party_size"):
            bits.append(f"{state['party_size']} people")
        if state.get("time"):
            bits.append(f"at {state['time']}")
        if state.get("destination"):
            origin = state.get("origin")
            bits.append(f"from {origin} to {state['destination']}" if origin else f"to {state['destination']}")
        if not bits:
            return "I didn't catch anything to change. What would you like?"
        return "Done. I've noted " + ", ".join(bits) + "."


def expected_state_from_case(case: dict[str, Any]) -> dict[str, Any]:
    """Ground truth for a fixture: parse the expected string with the same extractor the
    tool uses, so the assertion compares like-for-like rather than prose-vs-prose."""
    return parse_intent(case["expected"]).to_state()
