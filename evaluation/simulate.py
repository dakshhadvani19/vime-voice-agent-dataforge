"""Discrete-event replay of the fencing kernel on a **virtual clock**.

Why a virtual clock instead of ``asyncio.sleep``: the object under test is a race. Wall
clock timings jitter per machine and per load, which would make the naive-vs-safe
comparison — the headline of spec §5 — irreproducible. Every timestamp here is computed
from the fixture's declared latencies, so the same ``cases.json`` yields the same numbers
on any machine, in any order, under any load.

The two arms are fed the **same** schedule. The simulator does not "make naive fail": it
builds one timeline, replays it through ``InterruptionLab`` with a different policy flag,
and the divergence falls out of ``fence.owns()`` / ``fence.is_stale()``.

What is real vs modelled:
  * REAL   — request IDs, invalidation, accept/discard, state commits, event ordering and
             every metric, all executed by ``agent/lab.py`` — the same module the LiveKit
             runtime drives in production.
  * MODEL  — the latency constants (Rime first-audio, ``clear`` round trip, barge-in
             detection). Declared per run and stamped ``source: "simulated"`` into every
             result, so they can never be misread as measured provider latency.

``run_tests.py --live`` replays the identical fixtures against the real clock and, when
credentials exist, real Rime synthesis, re-stamping results as ``live`` / ``live-cached``.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

from config import LabConfig
from events import EventKind, EventLog, ManualClock
from lab import InterruptionLab, expected_state_from_case
from state_manager import Policy
from tools import SlowTool

#: Rime's documented cloud figure is sub-200ms end-to-end ("Streaming TTS", docs.rime.ai),
#: with mistv3 well under 100ms to first byte. Used as the modelled constant so results are
#: stable across machines; ``--live`` replaces it with what the API actually did.
FIRST_AUDIO_MS = 180.0

# Tie-break at an identical timestamp: a barge-in is observed before the cancellation it
# triggers, and both before the request that supersedes the interrupted one.
_ORDER = {
    "interrupt": 0,
    "cancel_ack": 1,
    "begin_request": 2,
    "tool_return": 3,
    "speech_start": 4,
    "speech_complete": 5,
}


@dataclass(order=True)
class Scheduled:
    t_ms: float
    order: int
    seq: int
    kind: str = field(compare=False, default="")
    turn: str = field(compare=False, default="")


@dataclass(slots=True)
class ScenarioResult:
    case_id: str
    title: str
    policy: str
    metrics: dict[str, Any]
    events: list[dict[str, Any]]
    final_state: dict[str, Any]
    expected_state: dict[str, Any]
    passed: bool
    #: For the naive arm, passing means it reproduced the defect exactly as predicted.
    exhibited_defect: bool = False
    failures: list[str] = field(default_factory=list)
    assumptions: dict[str, Any] = field(default_factory=dict)
    timeline: list[str] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "policy": self.policy,
            "passed": self.passed,
            "exhibited_defect": self.exhibited_defect,
            "failures": self.failures,
            "final_state": self.final_state,
            "expected_state": self.expected_state,
            "assumptions": self.assumptions,
            "turns": self.turns,
            "metrics": self.metrics,
            "timeline": self.timeline,
            "events": self.events,
        }


def plan_turns(case: dict[str, Any], cfg: LabConfig) -> list[dict[str, Any]]:
    """Turns + interruption moments in ms on the virtual clock.

    Shared shape with the live runner so both replay the same scenario: same delayed tool,
    same interruption, same interruption moment — only the policy differs (spec §5).
    """
    detect = cfg.interruption_detection_latency_ms
    ms_per_word = cfg.ms_per_word
    first_delay = float(case.get("initial_tool_delay_ms", cfg.tool_delay_ms))
    patch_delay = float(case.get("patch_tool_delay_ms", 350.0))
    int_speech = float(case.get("interrupt_speech_ms", 900.0))
    phase = case.get("interrupt_phase", "speaking")

    turns: list[dict[str, Any]] = []

    def add(key: str, text: str, delay: float, begin: float, interrupt_at: float | None) -> dict[str, Any]:
        t: dict[str, Any] = {
            "key": key,
            "text": text,
            "delay_ms": delay,
            "begin_ms": begin,
            "tool_return_ms": begin + delay,
            "interrupt_at_ms": interrupt_at,
        }
        t["speech_start_ms"] = t["tool_return_ms"] + FIRST_AUDIO_MS
        t["interrupt_ack_ms"] = (interrupt_at + cfg.rime_clear_rtt_ms) if interrupt_at is not None else None
        t["replacement_speech_ms"] = int_speech if interrupt_at is not None else None
        turns.append(t)
        return t

    a = add("A", case["initial"], first_delay, 0.0, None)
    if phase == "tool":
        t_int = float(case.get("interrupt_after_ms", round(first_delay * 0.25)))
    else:
        words_in = float(case.get("interrupt_after_words", 2))
        t_int = float(case.get("interrupt_after_ms", a["speech_start_ms"] + words_in * ms_per_word))
    a["interrupt_at_ms"], a["interrupt_ack_ms"] = t_int, t_int + cfg.rime_clear_rtt_ms

    prev = a
    if case.get("interrupt"):
        prev = add("B", case["interrupt"], patch_delay, t_int + detect + int_speech, None)
    if case.get("interrupt2"):
        after = float(
            case.get(
                "interrupt2_after_ms",
                prev["speech_start_ms"] + 1.5 * ms_per_word,
            )
        )
        prev["interrupt_at_ms"], prev["interrupt_ack_ms"] = after, after + cfg.rime_clear_rtt_ms
        prev["replacement_speech_ms"] = int_speech
        add("C", case["interrupt2"], patch_delay, after + detect + int_speech, None)

    for t in turns:
        if t["interrupt_at_ms"] is None:
            t.pop("interrupt_at_ms", None)
            t.pop("interrupt_ack_ms", None)
            t.pop("replacement_speech_ms", None)
    return turns


def run_case(case: dict[str, Any], policy: Policy, cfg: LabConfig | None = None) -> ScenarioResult:
    cfg = cfg or LabConfig()
    # One time source for the whole run: the kernel's own events and the schedule's
    # timestamps come from the same virtual clock, so no wall-clock value can leak in.
    clock = ManualClock()
    log = EventLog(clock=clock)
    lab = InterruptionLab(policy=policy, tool=SlowTool(cfg.tool_delay_ms), log=log)
    turns = plan_turns(case, cfg)
    by_key = {t["key"]: t for t in turns}

    heap: list[Scheduled] = []
    seq = 0

    def push(t_ms: float, kind: str, turn: str) -> None:
        nonlocal seq
        seq += 1
        heapq.heappush(heap, Scheduled(t_ms=t_ms, order=_ORDER.get(kind, 9), seq=seq, kind=kind, turn=turn))

    for t in turns:
        push(t["begin_ms"], "begin_request", t["key"])
        push(t["tool_return_ms"], "tool_return", t["key"])
        push(t["speech_start_ms"], "speech_start", t["key"])
        if "interrupt_at_ms" in t:
            push(t["interrupt_at_ms"], "interrupt", t["key"])
            push(t["interrupt_ack_ms"], "cancel_ack", t["key"])

    rid: dict[str, int] = {}
    open_speech: dict[str, Any] = {}
    audible: dict[str, Any] = {}
    decisions: dict[str, Any] = {}
    interrupted: dict[str, Any] = {}
    finished: set[int] = set()

    def speech_len(text: str) -> float:
        return max(1, len(text.split())) * cfg.ms_per_word

    while heap:
        item = heapq.heappop(heap)
        now, kind, key = item.t_ms, item.kind, item.turn
        clock.advance_to(now)
        t = by_key[key]

        if kind == "begin_request":
            lab.start(now_ms=now)
            rid[key] = lab.begin_request(t["text"], now_ms=now, delay_override_ms=t["delay_ms"]).request_id

        elif kind == "tool_return":
            lab.tool.set_delay(t["delay_ms"])
            outcome = lab.tool.plan(rid[key], t["text"], started_ms=t["begin_ms"], base=lab.run.base_intent)
            outcome.finished_ms = now
            lab.run.log.emit(
                EventKind.TOOL_STARTED,
                request_id=rid[key],
                t_ms=t["begin_ms"],
                delay_ms=t["delay_ms"],
                turn=key,
            )
            decisions[key] = lab.deliver_tool_result(outcome, now_ms=now)

        elif kind == "speech_start":
            d = decisions.get(key)
            if d is None or not d.speak_text or d.request_id in finished:
                continue
            handle = lab.begin_speech(
                d.speak_text,
                request_id=d.request_id,
                now_ms=now,
                speech_id=f"{key}-{d.request_id}",
                queued_backlog_ms=speech_len(d.speak_text),
            )
            if handle is None:
                continue
            words = d.speak_text.split()
            lab.note_words(
                handle,
                words,
                [now + i * cfg.ms_per_word for i in range(len(words))],
                [now + (i + 1) * cfg.ms_per_word for i in range(len(words))],
            )
            open_speech[key] = handle
            audible[key] = handle
            push(now + speech_len(d.speak_text), "speech_complete", key)

        elif kind == "speech_complete":
            handle = open_speech.pop(key, None)
            if handle is None or handle.cancelled:
                continue  # already cut short by cancellation
            lab.end_speech(handle, now_ms=now)
            finished.add(handle.request_id)
            audible.pop(key, None)

        elif kind == "interrupt":
            if case.get("interrupt_source") == "false_speech":
                # Background speech never becomes an interruption: turn detection rejects it
                # above this layer, so the kernel must not see it. Both arms therefore stay
                # clean here — that is what makes this a guard fixture rather than a
                # free pass. The NOTE is what the timeline shows the rejection as.
                log_note = lab.run.log
                log_note.emit(
                    EventKind.NOTE,
                    request_id=lab.run.fence.latest_request_id,
                    t_ms=now,
                    text="background speech below interruption threshold: request NOT invalidated",
                    handled_by="turn-detection",
                )
                continue
            # The user's barge-in lands on whichever speech is audible right now.
            speaking = next(iter(audible.values()), None)
            interrupted[key] = speaking
            plan = lab.interrupt(
                now_ms=now,
                speech=speaking,
                source=case.get("interrupt_source", "user_speech"),
            )
            if speaking is None:
                # Nothing audible to stop (interrupt during a slow tool call): the
                # cancellation leg resolves immediately rather than waiting on a clear ack.
                lab.confirm_stopped(now_ms=now, cancelled=False)
                interrupted[key] = None
            elif not plan.should_cancel:
                pass  # naive arm: the caller keeps its speech handle, which is the defect

        elif kind == "cancel_ack":
            handle = interrupted.get(key)
            if handle is None:
                continue
            still_audible = any(h is handle for h in audible.values())
            res = lab.confirm_stopped(
                now_ms=now,
                cancelled=still_audible,
                audible_until_ms=t["interrupt_at_ms"],
                detail={
                    "transport": "rime /ws3 clear (modelled round trip)",
                    "rtt_ms": cfg.rime_clear_rtt_ms,
                    "context_id": handle.context_id,
                },
            )
            # Only retire the utterance if the kernel actually stopped it. Under NAIVE the
            # cancel is never issued, so the speech stays open and plays to completion —
            # retiring it here would hide the very defect this arm exists to show.
            if res.speech is not None and res.speech.cancelled:
                for k, h in list(open_speech.items()):
                    if h is handle:
                        open_speech.pop(k, None)
                for k, h in list(audible.items()):
                    if h is handle:
                        audible.pop(k, None)
            interrupted[key] = None

    # ---- scoring ------------------------------------------------------------
    expected = expected_state_from_case(case) if case.get("expected") else {}
    actual = lab.run.store.snapshot()
    metrics = lab.metrics(expected_final_state=expected or None, source="simulated")

    stale_m = metrics["stale_output_rate"]
    acc_m = metrics["final_state_accuracy"]
    stale_count = int(stale_m.numerator or 0)
    wrong_final = acc_m.value is not None and acc_m.value < 1.0
    defect = bool(stale_count > 0 or wrong_final)

    real_barge_in = (
        case.get("interrupt_source") != "false_speech"
        and any("interrupt_at_ms" in t for t in turns)
    )
    audible_barge_in = real_barge_in and case.get("interrupt_phase", "speaking") == "speaking"

    failures: list[str] = []
    if policy is Policy.SAFE:
        if stale_count > 0:
            failures.append(f"obsolete output reached the user {stale_count}× (§19.4 violated)")
        if wrong_final:
            failures.append(f"final state {actual} does not satisfy expected {expected}")
        has_cancel_event = bool([e for e in lab.run.log.events if e.kind is EventKind.SPEECH_CANCELLED])
        if audible_barge_in and not has_cancel_event:
            # §19.1: obsolete Rime audio must stop, and the stop must be *confirmed*, not
            # merely requested — hence a required event plus a required measurement.
            failures.append("no speech_cancelled event: obsolete audio was never stopped (§19.1)")
        stop = metrics["interruption_stop_latency"]
        if audible_barge_in and stop.value is None:
            failures.append("stop latency unmeasured: cancellation was never confirmed")
        fencing = metrics["fencing_correctness"]
        if fencing.denominator and fencing.numerator != fencing.denominator:
            failures.append(f"obsolete tool results not all rejected ({fencing.numerator}/{fencing.denominator})")
    else:
        # Control arm: it "passes" by reproducing exactly what the fixture predicts for a
        # runtime with no fencing. `naive_expected` is declared in the corpus, so a
        # non-discriminating fixture is caught rather than quietly inflating the win.
        want = case.get("naive_expected", "defect")
        if want == "defect" and not defect:
            failures.append("naive arm did not reproduce the stale-output defect: fixture is not discriminating")
        if want == "clean" and defect:
            failures.append(f"naive arm misbehaved on a guard fixture: {actual} vs {expected}")

    return ScenarioResult(
        case_id=case["id"],
        title=case.get("title", ""),
        policy=policy.value,
        metrics={k: m.to_dict() for k, m in metrics.items()},
        events=[e.to_dict() for e in lab.run.log.events],
        final_state=actual,
        expected_state=expected,
        passed=not failures,
        exhibited_defect=defect,
        failures=failures,
        assumptions={
            "clock": "virtual (discrete-event), milliseconds",
            "rime_first_audio_ms": FIRST_AUDIO_MS,
            "rime_clear_rtt_ms": cfg.rime_clear_rtt_ms,
            "interruption_detection_ms": cfg.interruption_detection_latency_ms,
            "ms_per_word": cfg.ms_per_word,
            "turn_delays_ms": {x["key"]: x["delay_ms"] for x in turns},
            "provenance": "latency constants are declared inputs, not measurements; use --live for measured values",
        },
        timeline=[e.to_dict()["label"] for e in lab.run.log.events],
        turns=turns,
    )
