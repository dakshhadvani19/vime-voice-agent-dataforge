"""Core fencing behaviour (spec §6, §19).

These are the tests the brief asks for: "unit/integration tests for stale-result
rejection" (§21). They exercise ``agent/lab.py`` directly — no LiveKit, no Rime, no
network — because the guarantee is a decision, not a network call.
"""

from __future__ import annotations

import pytest
from events import EventKind, EventLog, ManualClock
from lab import InterruptionLab
from state_manager import (
    AgentState,
    IllegalTransition,
    Policy,
    RequestFence,
    StateMachine,
)
from tools import SlowTool


def make_lab(policy: Policy = Policy.SAFE, *, delay: float = 1000.0, ack: float = 40.0):
    clock = ManualClock()
    log = EventLog(clock=clock)
    lab = InterruptionLab(policy=policy, tool=SlowTool(delay), log=log)
    return lab, clock, log, ack


def run_interrupted_pair(lab, clock, *, policy_note: str = "", ack_ms: float = 40.0):
    """Request A → slow tool → barge-in → Request B → A's result lands late.

    Returns (decision_a, decision_b, a_outcome, b_outcome).
    """
    clock.advance_to(0.0)
    lab.start(now_ms=0.0)
    a = lab.begin_request("Book a table for two tomorrow at 8 PM", now_ms=0.0)
    a_outcome = lab.tool.plan(a.request_id, a.text, started_ms=0.0)

    # user barges in while A is still in flight
    clock.advance_to(300.0)
    plan = lab.interrupt(now_ms=300.0, speech=None, source="user_speech")
    lab.confirm_stopped(now_ms=300.0, cancelled=plan.should_cancel)

    clock.advance_to(1400.0)
    b = lab.begin_request("Actually make it four people", now_ms=1400.0)
    b_outcome = lab.tool.plan(b.request_id, b.text, started_ms=1400.0)

    clock.advance_to(1750.0)
    decision_b = lab.deliver_tool_result(b_outcome, now_ms=1750.0)
    # ...and A's result finally arrives, 1.75s after the user moved on
    clock.advance_to(3250.0)
    decision_a = lab.deliver_tool_result(a_outcome, now_ms=3250.0)
    return decision_a, decision_b, a_outcome, b_outcome


class TestStaleResultRejection:
    def test_safe_discards_late_result_of_superseded_request(self):
        lab, clock, _log, _ack = make_lab(Policy.SAFE)
        decision_a, decision_b, *_ = run_interrupted_pair(lab, clock)
        assert decision_b.accepted is True
        assert decision_a.accepted is False
        assert "not active" in decision_a.reason or "authoritative" in decision_a.reason

    def test_stale_result_never_touches_application_state(self):
        lab, _clock, *_ = make_lab(Policy.SAFE)
        run_interrupted_pair(lab, _clock)
        # only B's commit survived
        assert lab.run.store.state["party_size"] == 4
        assert lab.run.store.rejected_commits, "the discarded commit must be recorded"
        assert len(lab.run.store.commits) == 1

    def test_naive_arm_applies_the_same_stale_result(self):
        """The control arm must fail on the identical timeline, or the A/B proves nothing."""
        lab, clock, *_ = make_lab(Policy.NAIVE)
        decision_a, _decision_b, *_ = run_interrupted_pair(lab, clock)
        assert decision_a.accepted is True
        assert lab.run.store.state["party_size"] == 2, "naive clobbers the newer intent"

    def test_timeline_marks_the_verdict_not_just_the_event(self):
        lab, clock, log, _ = make_lab(Policy.SAFE)
        run_interrupted_pair(lab, clock)
        returned = log.find(EventKind.TOOL_RETURNED)
        by_rid = {e.request_id: e.data["verdict"] for e in returned}
        assert by_rid == {1: "discarded", 2: "accepted"}
        stale_flag = {e.request_id: e.data["stale"] for e in returned}
        assert stale_flag[1] is True and stale_flag[2] is False

    def test_stale_speech_is_never_sent_to_tts(self):
        lab, clock, *_ = make_lab(Policy.SAFE)
        a = lab.begin_request("Book a table for two tomorrow at 8 PM", now_ms=0.0)
        clock.advance_to(100.0)
        lab.run.fence.invalidate(a.request_id, reason="superseded")
        clock.advance_to(200.0)
        assert lab.begin_speech("Done. I've noted 2 people.", request_id=a.request_id, now_ms=200.0) is None
        assert not lab.run.log.find(EventKind.SPEECH_STARTED)


class TestGenerationFence:
    def test_queued_audio_from_previous_generation_is_blocked(self):
        """A result carrying the right request_id but a stale generation must still be
        dropped — otherwise 'stop obsolete Rime audio' is unenforceable."""
        log = EventLog()
        fence = RequestFence(log, policy=Policy.SAFE)
        rec = fence.begin("hello")
        assert fence.owns_generation(rec.request_id, rec.generation, policy=Policy.SAFE)
        fence.invalidate(rec.request_id, reason="barge-in")
        assert not fence.owns_generation(rec.request_id, rec.generation - 1, policy=Policy.SAFE)

    def test_fence_token_shape_matches_context_id_contract(self):
        log = EventLog()
        fence = RequestFence(log)
        rec = fence.begin("hello")
        assert rec.fence_token == "req-1-gen-1"
        fence.invalidate(rec.request_id, reason="x")
        assert rec.fence_token == "req-1-gen-2", "cancelling must bump the generation"

    def test_naive_policy_accepts_anything(self):
        log = EventLog()
        fence = RequestFence(log, policy=Policy.NAIVE)
        rec = fence.begin("hello")
        fence.invalidate(rec.request_id, reason="manual")
        assert fence.owns(rec.request_id, policy=Policy.NAIVE) is True
        assert fence.owns(rec.request_id, policy=Policy.SAFE) is False


class TestStateMachine:
    def test_interruption_path(self):
        log = EventLog()
        m = StateMachine(log)
        for state in (
            AgentState.LISTENING,
            AgentState.THINKING,
            AgentState.SPEAKING,
            AgentState.INTERRUPTED,
            AgentState.CANCELLING,
            AgentState.LISTENING,
        ):
            m.to(state)
        assert m.state is AgentState.LISTENING

    def test_illegal_transition_raises(self):
        m = StateMachine(EventLog())
        with pytest.raises(IllegalTransition):
            m.to(AgentState.SPEAKING)

    def test_begin_request_while_speaking_requires_interrupt_first(self):
        lab, clock, *_ = make_lab(Policy.SAFE)
        lab.begin_request("hello", now_ms=0.0)
        clock.advance_to(1000.0)
        lab.deliver_tool_result(lab.tool.plan(1, "hello", started_ms=0.0), now_ms=1000.0)
        lab.begin_speech("Done.", request_id=1, now_ms=1200.0)
        with pytest.raises(IllegalTransition):
            lab.begin_request("second", now_ms=1300.0)

    def test_naive_logs_the_overlap_instead_of_raising(self):
        lab, clock, *_ = make_lab(Policy.NAIVE)
        lab.begin_request("hello", now_ms=0.0)
        clock.advance_to(1000.0)
        lab.deliver_tool_result(lab.tool.plan(1, "hello", started_ms=0.0), now_ms=1000.0)
        lab.begin_speech("Done.", request_id=1, now_ms=1200.0)
        lab.begin_request("second", now_ms=1300.0)  # must not raise: this is the bug being shown
        notes = [e for e in lab.run.log.events if e.kind is EventKind.NOTE]
        assert any(e.data.get("severity") == "defect" for e in notes)


class TestCancellationAccounting:
    def test_naive_cannot_claim_it_stopped_the_audio(self):
        """A caller reporting 'stopped' must not overwrite the policy's truth."""
        lab, clock, log, _ = make_lab(Policy.NAIVE)
        lab.begin_request("Book a table for two tomorrow at 8 PM", now_ms=0.0)
        clock.advance_to(1000.0)
        d = lab.deliver_tool_result(lab.tool.plan(1, "Book a table for two tomorrow at 8 PM", started_ms=0.0), now_ms=1000.0)
        handle = lab.begin_speech(d.speak_text, request_id=1, now_ms=1100.0, queued_backlog_ms=900.0)
        lab.note_words(handle, ["Done.", "I've", "noted"], [1100.0, 1300.0, 1500.0], [1250.0, 1450.0, 1650.0])
        lab.interrupt(now_ms=1400.0, speech=handle)
        lab.confirm_stopped(now_ms=1440.0, cancelled=True, audible_until_ms=1400.0)
        assert not log.find(EventKind.SPEECH_CANCELLED), "naive never issued a cancel"
        assert handle.cancelled is False
        assert any(e.data.get("severity") == "defect" for e in log.find(EventKind.NOTE))

    def test_stop_latency_measured_from_detection_to_confirmed_stop(self):
        lab, clock, log, _ = make_lab(Policy.SAFE)
        lab.begin_request("Book a table for two tomorrow at 8 PM", now_ms=0.0)
        clock.advance_to(1000.0)
        d = lab.deliver_tool_result(lab.tool.plan(1, "Book a table for two tomorrow at 8 PM", started_ms=0.0), now_ms=1000.0)
        handle = lab.begin_speech(d.speak_text, request_id=1, now_ms=1100.0, queued_backlog_ms=900.0)
        lab.note_words(handle, ["Done.", "I've", "noted"], [1100.0, 1300.0, 1500.0], [1250.0, 1450.0, 1650.0])
        lab.interrupt(now_ms=1400.0, speech=handle)
        lab.confirm_stopped(now_ms=1437.5, cancelled=True, audible_until_ms=1400.0)
        cancel = log.first(EventKind.SPEECH_CANCELLED)
        assert cancel is not None
        assert cancel.data["stop_latency_ms"] == pytest.approx(37.5)

    def test_heard_ledger_keeps_only_audible_words(self):
        lab, clock, log, _ = make_lab(Policy.SAFE)
        lab.begin_request("Book a table for two tomorrow at 8 PM", now_ms=0.0)
        clock.advance_to(1000.0)
        d = lab.deliver_tool_result(lab.tool.plan(1, "Book a table for two tomorrow at 8 PM", started_ms=0.0), now_ms=1000.0)
        handle = lab.begin_speech(d.speak_text, request_id=1, now_ms=1100.0, queued_backlog_ms=900.0)
        words = d.speak_text.split()
        lab.note_words(
            handle,
            words,
            [1100.0 + i * 200 for i in range(len(words))],
            [1300.0 + i * 200 for i in range(len(words))],
        )
        lab.interrupt(now_ms=1500.0, speech=handle)
        intr = lab.confirm_stopped(now_ms=1540.0, cancelled=True, audible_until_ms=1500.0)
        assert intr.heard_text == " ".join(words[:2]), "only words that finished before the cut"
        reconciled = log.first(EventKind.HEARD_RECONCILED, 1)
        assert reconciled is not None and reconciled.data["heard"] == intr.heard_text


class TestProvenance:
    def test_events_use_the_injected_clock_not_the_wall_clock(self):
        clock = ManualClock()
        log = EventLog(clock=clock)
        clock.advance_to(5_000.0)
        log.emit(EventKind.NOTE, text="hi")
        assert log.events[0].t_ms == 5000.0

    def test_manual_clock_refuses_to_go_backwards(self):
        clock = ManualClock()
        clock.advance_to(10.0)
        with pytest.raises(ValueError):
            clock.advance_to(5.0)

    def test_metric_with_no_samples_is_none_not_zero(self):
        """Zero would silently read as 'perfect' in a report."""
        lab, _clock, *_ = make_lab(Policy.SAFE)
        lab.begin_request("hello", now_ms=0.0)
        metrics = lab.metrics()
        assert metrics["interruption_stop_latency"].value is None
        assert metrics["interruption_stop_latency"].values == []
