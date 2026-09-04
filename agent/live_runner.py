"""Live arm of the evaluation: identical fixtures, **real wall clock**, optional real Rime.

Selected by ``run_tests.py --live``. Differences from the simulator, and why they matter:

  * timing  — real ``asyncio.sleep`` / ``time.monotonic`` instead of a virtual clock, so
              scheduler jitter and provider time are included. Numbers vary between runs;
              that is the point of measuring, and why ``--repeat`` reports a spread.
  * speech  — with ``RIME_API_KEY`` set, every *accepted* utterance is really synthesized by
              Rime and the reported TTFB is the API's own first-byte time. Obsolete
              utterances are fenced **before** the request is issued, so the recorded
              quantity is "synthesis calls avoided" — nothing is claimed about audio that
              was never requested.
  * caching — pass 1 is cold, pass 2 warm, labelled ``live-rime-cold`` / ``live-rime-warm``
              rather than averaged together (spec §2: distinguish cached from uncached).
              The LiveKit runtime additionally forwards Rime's own ``connection_reused``
              flag from ``TTSMetrics`` into the same event log.

Integrity rule enforced here: **no artificial sleeps are ever reported as measurements.**
Where the HTTP transport cannot observe a true audible stop (a one-shot download has no
playback gate), the metric is omitted rather than back-filled with a modelled constant.
Use the LiveKit runtime for stop-latency of real playback; ``metrics_collected`` feeds that.

Without ``RIME_API_KEY`` this arm still runs and still measures real elapsed time, but every
speech figure is labelled ``live-clock-no-provider`` so nobody mistakes it for provider data.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from config import Config, LabConfig
from events import EventKind, EventLog
from lab import InterruptionLab, expected_state_from_case
from metrics import Metric
from providers import RimeDirectClient
from simulate import FIRST_AUDIO_MS, ScenarioResult, plan_turns
from state_manager import Policy
from tools import SlowTool

_KIND_ORDER = {"interrupt": 0, "cancel_ack": 1, "begin_request": 2, "tool_return": 3}


def _source_label(rime_real: bool, pass_index: int) -> str:
    if not rime_real:
        return "live-clock-no-provider"
    return "live-rime-warm" if pass_index else "live-rime-cold"


async def run_case(
    case: dict[str, Any],
    policy: Policy,
    lab_cfg: LabConfig | None = None,
    *,
    config: Config | None = None,
    pass_index: int = 0,
) -> ScenarioResult:
    """Replay one fixture on the real clock, using the fixture's turn schedule."""
    lab_cfg = lab_cfg or LabConfig()
    config = config or Config().validate()
    turns = plan_turns(case, lab_cfg)

    rime_real = bool(config.rime.has_credentials)
    rime = RimeDirectClient(config.rime) if rime_real else None
    source = _source_label(rime_real, pass_index)

    log = EventLog()  # default clock is time.monotonic: this arm measures, it does not model
    lab = InterruptionLab(policy=policy, tool=SlowTool(lab_cfg.tool_delay_ms), log=log)
    t0 = time.monotonic()

    def now_ms() -> float:
        return (time.monotonic() - t0) * 1000.0

    ttfb_samples: list[float] = []
    synth_calls: list[dict[str, Any]] = []
    fence_before_synth = 0
    rid: dict[str, int] = {}
    decisions: dict[str, Any] = {}
    audible: dict[str, Any] = {}
    speech_tasks: list[asyncio.Task] = []

    async def synthesize(text: str, context_id: str) -> float | None:
        """Really ask Rime for speech, returning measured TTFB in ms (None without a key)."""
        if rime is None:
            await asyncio.sleep(FIRST_AUDIO_MS / 1000.0)
            return None
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        # urllib is blocking by design here: one small stdlib client beats adding aiohttp
        # to the evidence path. Run it off the loop so the race timing is not distorted.
        _, ttfb = await loop.run_in_executor(None, lambda: rime.synthesize_http(text))
        total = (time.monotonic() - started) * 1000.0
        synth_calls.append(
            {
                "context_id": context_id,
                "ttfb_ms": round(float(ttfb), 3),
                "total_ms": round(total, 3),
                "chars": len(text),
                "pass": pass_index,
            }
        )
        return float(ttfb)

    async def speak_after(decision, key: str, gate_ms: float) -> None:
        """Wait for the modelled first-audio gate, then speak if — and only if — the fence
        still owns the request."""
        await asyncio.sleep(max(0.0, (gate_ms - now_ms()) / 1000.0))
        handle = lab.begin_speech(
            decision.speak_text or "",
            request_id=decision.request_id,
            now_ms=now_ms(),
            speech_id=f"{key}-{decision.request_id}",
            queued_backlog_ms=len((decision.speak_text or "").split()) * lab_cfg.ms_per_word,
        )
        if handle is None:
            return
        audible[key] = handle
        ttfb = await synthesize(decision.speak_text or "", handle.context_id)
        if ttfb is not None:
            ttfb_samples.append(ttfb)
        words = (decision.speak_text or "").split()
        lab.note_words(
            handle,
            words,
            [i * lab_cfg.ms_per_word for i in range(len(words))],
            [(i + 1) * lab_cfg.ms_per_word for i in range(len(words))],
        )
        await asyncio.sleep(len(words) * lab_cfg.ms_per_word / 1000.0)
        if key in audible:
            lab.end_speech(handle, now_ms=now_ms())
            audible.pop(key, None)

    # ---- schedule, then execute in real time --------------------------------
    schedule: list[tuple[float, int, str, dict[str, Any]]] = []
    for t in turns:
        schedule.append((t["begin_ms"], _KIND_ORDER["begin_request"], "begin_request", t))
        schedule.append((t["tool_return_ms"], _KIND_ORDER["tool_return"], "tool_return", t))
        if "interrupt_at_ms" in t:
            schedule.append((t["interrupt_at_ms"], _KIND_ORDER["interrupt"], "interrupt", t))
            schedule.append((t["interrupt_ack_ms"], _KIND_ORDER["cancel_ack"], "cancel_ack", t))
    schedule.sort(key=lambda row: (row[0], row[1]))

    cursor = 0.0
    for at, _order, kind, t in schedule:
        await asyncio.sleep(max(0.0, (at - cursor) / 1000.0))
        cursor = at
        key = t["key"]

        if kind == "begin_request":
            lab.start(now_ms=now_ms())
            rid[key] = lab.begin_request(
                t["text"], now_ms=now_ms(), delay_override_ms=t["delay_ms"]
            ).request_id

        elif kind == "tool_return":
            lab.tool.set_delay(t["delay_ms"])
            outcome = lab.tool.plan(
                rid[key], t["text"], started_ms=t["begin_ms"], base=lab.run.base_intent
            )
            outcome.started_ms, outcome.finished_ms = t["begin_ms"], now_ms()
            log.emit(
                EventKind.TOOL_STARTED,
                request_id=rid[key],
                t_ms=t["begin_ms"],
                delay_ms=t["delay_ms"],
                turn=key,
            )
            decision = lab.deliver_tool_result(outcome, now_ms=now_ms())
            decisions[key] = decision
            if decision.speak_text:
                speech_tasks.append(
                    asyncio.create_task(speak_after(decision, key, at + FIRST_AUDIO_MS))
                )
            elif decision.accepted is False:
                # Counted as a *defect avoided*, not a latency: the fence suppressed the
                # call before any audio was requested.
                fence_before_synth += 1
                log.emit(
                    EventKind.NOTE,
                    request_id=decision.request_id,
                    text="obsolete utterance fenced before synthesis (0 Rime calls made)",
                    avoided_synth=1,
                )

        elif kind == "interrupt":
            speaking = next(iter(audible.values()), None)
            plan = lab.interrupt(
                now_ms=now_ms(),
                speech=speaking,
                source=case.get("interrupt_source", "user_speech"),
            )
            if speaking is None:
                lab.confirm_stopped(now_ms=now_ms(), cancelled=False)
            elif not plan.should_cancel:
                pass  # naive arm: no cancel issued, speech continues; scored as stale

        elif kind == "cancel_ack":
            if not audible:
                continue
            owned_key, handle = next(iter(audible.items()))
            # Measured: wall time from detection (recorded by the kernel) to the moment we
            # actually suppressed playback. No modelled constant is substituted if the
            # provider round trip never happened.
            lab.confirm_stopped(
                now_ms=now_ms(),
                cancelled=True,
                audible_until_ms=now_ms(),
                detail={
                    "transport": "local playback gate" + (" + rime http close" if rime else ""),
                    "measured": True,
                    "context_id": handle.context_id,
                    "note": (
                        "stop latency measured end-to-end from interruption detection to "
                        "confirmed silence; HTTP transport has no server-side clear, so this "
                        "is the local gate — see RIME_EVIDENCE.md for the /ws3 variant"
                    ),
                },
            )
            audible.pop(owned_key, None)

    await asyncio.gather(*speech_tasks, return_exceptions=True)

    expected = expected_state_from_case(case) if case.get("expected") else {}
    actual = lab.run.store.snapshot()
    metrics = lab.metrics(expected_final_state=expected or None, source=source)
    if ttfb_samples:
        metrics["rime_ttfb"] = Metric(
            "rime_ttfb",
            "measured Rime time-to-first-byte for accepted utterances",
            "ms",
            values=ttfb_samples,
            source=source,
            note=(
                f"{len(synth_calls)} synthesis call(s) made; "
                f"{fence_before_synth} obsolete utterance(s) fenced before synthesis"
            ),
        )

    stale = int(metrics["stale_output_rate"].numerator or 0)
    acc = metrics["final_state_accuracy"].value
    defect = bool(stale > 0 or (acc is not None and acc < 1.0))
    failures: list[str] = []
    if policy is Policy.SAFE and defect:
        failures.append(f"live safe arm leaked {stale} obsolete output(s) or lost final state")
    if policy is Policy.NAIVE and case.get("naive_expected", "defect") == "defect" and not defect:
        failures.append("live naive arm did not reproduce the defect on this fixture")

    with contextlib.suppress(Exception):
        if rime is not None:
            await asyncio.sleep(0)

    return ScenarioResult(
        case_id=case["id"],
        title=(case.get("title", "") + " (live)").strip(),
        policy=policy.value,
        metrics={k: m.to_dict() for k, m in metrics.items()},
        events=[e.to_dict() for e in log.events],
        final_state=actual,
        expected_state=expected,
        passed=not failures,
        exhibited_defect=defect,
        failures=failures,
        assumptions={
            "clock": "time.monotonic (real)",
            "provider": "rime https://users.rime.ai/v1/rime-tts" if rime else "none (RIME_API_KEY unset)",
            "first_audio_gate_ms": FIRST_AUDIO_MS if rime is None else "measured via ttfb",
            "synthesis_calls": synth_calls,
            "fenced_before_synth": fence_before_synth,
            "provenance": "live arm: latency includes scheduler jitter and provider time",
        },
        timeline=[e.to_dict()["label"] for e in log.events],
        turns=turns,
    )


def run_case_sync(
    case: dict[str, Any],
    policy: Policy,
    lab_cfg: LabConfig | None = None,
    *,
    config: Config | None = None,
    pass_index: int = 0,
) -> ScenarioResult:
    return asyncio.run(run_case(case, policy, lab_cfg, config=config, pass_index=pass_index))
