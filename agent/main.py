"""LiveKit runtime: STT → LLM → fenced tool → Rime TTS (spec §15).

Every LiveKit symbol used here was checked against livekit-agents 1.7.1 installed in this
environment (per spec §21 — no invented methods):

    AgentServer() + @server.rtc_session(agent_name=...)          # 1.x entrypoint
    cli.run_app(server: AgentServer | WorkerOptions)             # argv subcommands: start|dev
    AgentSession(stt=, llm=, tts=, vad=, turn_handling=, allow_interruptions=)
    AgentSession.start(room=ctx.room, agent=...)  /  .interrupt(force=False)
    AgentSession.generate_reply(instructions=|user_input=) -> SpeechHandle
    Agent(instructions=, tools=[...])  +  @function_tool
    session.on("agent_state_changed" | "user_state_changed" | "speech_created"
               | "metrics_collected")
    ctx.connect()  /  ctx.room.local_participant.publish_data(payload, topic=, reliable=)
    TTSMetrics fields: ttfb, cancelled, streamed, connection_reused

Division of labour, deliberately:
  * LiveKit owns transport, VAD, turn detection and audio-level barge-in.
  * ``agent/lab.py`` owns request identity, invalidation and the accept/discard decision.
    That is the part no framework does for you: LiveKit can stop the audio, but it has no
    idea that a tool result belonging to the previous turn must not be applied.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config
from events import EventKind
from lab import InterruptionLab
from providers import build_livekit_tts
from state_manager import AgentState, Policy
from tools import SlowTool, ToolOutcome

CFG = Config().validate()


def load_env() -> None:
    """Tiny .env loader — 15 lines is not worth a dependency."""
    for candidate in (".env", "agent/.env", ".env.local"):
        path = Path(candidate)
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return


class FencedAssistant:
    """One kernel instance per room. The UI's truth comes from here only, so what the
    timeline shows cannot drift from what the fence decided (spec §7)."""

    def __init__(self, *, room=None, session=None, policy: Policy, tool: SlowTool) -> None:
        self.room = room
        self.session = session
        self.policy = policy
        self.tool = tool
        self.lab = InterruptionLab(policy=policy, tool=tool)
        self._t0 = time.monotonic()
        self._sent = 0
        self._current_speech = None

    @property
    def now_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    # -- telemetry to the browser -------------------------------------------
    def publish(self, **extra) -> None:
        events = self.lab.run.log.since(self._sent)
        if not events and not extra:
            return
        payload = {
            "type": "timeline",
            "policy": self.policy.value,
            "state": self.lab.run.machine.state.value,
            "active_request_id": self.lab.run.fence.active_request_id,
            "latest_request_id": self.lab.run.fence.latest_request_id,
            "events": [e.to_dict() for e in events],
            "final_state": self.lab.run.store.snapshot(),
            "metrics": {k: m.to_dict() for k, m in self.lab.metrics(source="live").items()},
            **extra,
        }
        self._sent = len(self.lab.run.log.events)
        if self.room is None:
            return
        with contextlib.suppress(Exception):
            self.room.local_participant.publish_data(
                json.dumps(payload, default=str), topic=CFG.lab.publish_topic, reliable=True
            )

    # -- LiveKit event wiring -------------------------------------------------
    def attach(self) -> None:
        session = self.session

        @session.on("agent_state_changed")
        def _on_agent_state(ev) -> None:
            self.lab.run.log.emit(
                EventKind.STATE_CHANGED,
                state=str(getattr(ev, "new_state", "")),
                request_id=self.lab.run.fence.latest_request_id,
                origin="livekit",
            )
            self.publish()

        @session.on("user_state_changed")
        def _on_user_state(ev) -> None:
            # Barge-in: the user started talking while we were audible.
            if str(getattr(ev, "new_state", "")) != "speaking":
                return
            if self.lab.run.machine.state is not AgentState.SPEAKING:
                return
            plan = self.lab.interrupt(
                now_ms=self.now_ms, speech=self._current_speech, source="livekit_user_speech"
            )
            if plan.should_cancel:
                with contextlib.suppress(NoRunningLoop):
                    asyncio.get_running_loop().create_task(self._confirm_stopped())

        @session.on("metrics_collected")
        def _on_metrics(ev) -> None:
            metrics = getattr(ev, "metrics", None)
            if metrics is None or type(metrics).__name__ != "TTSMetrics":
                return
            # This is where live mode's numbers come from: Rime's own reported TTFB and
            # whether the synthesis was cancelled. No estimated constants in the live arm.
            self.lab.run.log.emit(
                EventKind.MEASUREMENT,
                request_id=self.lab.run.fence.latest_request_id,
                name="rime_ttfb",
                value_ms=round(float(getattr(metrics, "ttfb", 0.0) or 0.0) * 1000.0, 3),
                cancelled=bool(getattr(metrics, "cancelled", False)),
                streamed=bool(getattr(metrics, "streamed", False)),
                connection_reused=bool(getattr(metrics, "connection_reused", False)),
                provider="rime",
            )
            self.publish()

    async def _confirm_stopped(self) -> None:
        """Cut the audio, then stamp the confirmed stop so latency measures a real stop."""
        if self.session is None:
            return
        try:
            await self.session.interrupt(force=False)
        except Exception as exc:  # pragma: no cover - live only
            self.lab.run.log.emit(EventKind.NOTE, text=f"session.interrupt() failed: {exc}", severity="error")
            return
        self.lab.confirm_stopped(
            now_ms=self.now_ms,
            cancelled=True,
            detail={"transport": "livekit session.interrupt + rime /ws3 clear", "measured": True},
        )
        self.publish()

    # -- turn + tool ----------------------------------------------------------
    async def on_user_turn(self, text: str) -> None:
        if self.lab.run.machine.state in (AgentState.SPEAKING, AgentState.THINKING):
            self.lab.interrupt(now_ms=self.now_ms, speech=self._current_speech, source="superseding_turn")
            await self._confirm_stopped()
        self.lab.start(now_ms=self.now_ms)
        self.lab.begin_request(text, now_ms=self.now_ms)
        self.publish()

    async def run_tool(self, query: str) -> str:
        """Body of the function tool. Fenced: a superseded call returns a suppression
        marker instead of data, so the model cannot speak a stale result."""
        rid = self.lab.run.fence.latest_request_id
        if rid is None:
            return "NO_ACTIVE_REQUEST: result suppressed, do not speak."
        started = self.now_ms
        self.lab.run.log.emit(
            EventKind.TOOL_STARTED, request_id=rid, t_ms=started, delay_ms=self.tool.delay_ms
        )
        self.publish()
        result = await self.tool.run(rid, query, base=self.lab.run.base_intent)
        if self.lab.run.machine.state is AgentState.SPEAKING:
            self.lab.interrupt(now_ms=self.now_ms, speech=self._current_speech, source="tool_completed_mid_speech")
        outcome = ToolOutcome(
            request_id=rid,
            started_ms=started,
            finished_ms=self.now_ms,
            result=result,
            delay_ms=self.tool.delay_ms,
        )
        decision = self.lab.deliver_tool_result(outcome, now_ms=self.now_ms)
        self.publish()
        if not decision.accepted:
            return (
                f"SUPERSEDED_REQUEST #{rid}: the user replaced this request. "
                "Do not speak any part of this result."
            )
        return json.dumps(decision.state or {})

    async def speak(self, text: str) -> None:
        """Speak a line through the fence. Used by Lab mode and scripted demos."""
        rid = self.lab.run.fence.latest_request_id or 0
        handle = self.lab.begin_speech(text, request_id=rid, now_ms=self.now_ms)
        if handle is None or self.session is None:
            return
        self._current_speech = handle
        self.publish()
        speech = self.session.say(text, allow_interruptions=True)
        with contextlib.suppress(Exception):
            await speech.wait_for_playout()
        self.lab.end_speech(handle, now_ms=self.now_ms)
        self._current_speech = None
        self.publish()


class NoRunningLoop(RuntimeError):
    pass


def build_agent_class():
    """Imported lazily so `--check` and the eval harness never need the LiveKit stack."""
    from livekit.agents import Agent as LKAgent
    from livekit.agents import function_tool

    class BreakMyVoiceAgent(LKAgent):
        """Persona + the one tool. All sequencing lives in FencedAssistant/the kernel."""

        def __init__(self, assistant: FencedAssistant) -> None:
            self.assistant = assistant

            @function_tool
            async def lookup_availability(query: str) -> str:
                """Look up availability for the user's current request."""
                return await assistant.run_tool(query)

            super().__init__(
                instructions=(
                    "You are a voice agent being tested for interruption safety. "
                    "Answer in one or two short speakable sentences. No lists, no markdown, "
                    "no emojis: everything you write is read aloud by Rime. "
                    "If a tool result says SUPERSEDED_REQUEST, do not repeat the old request "
                    "or mention that result at all — the user already replaced it."
                ),
                tools=[lookup_availability],
            )

        async def on_enter(self) -> None:
            self.session.generate_reply(
                instructions=(
                    "Introduce yourself in one sentence, then tell the user this is a lab: "
                    "they should interrupt you while you are speaking."
                )
            )

    return BreakMyVoiceAgent


def build_session(config: Config):
    """AgentSession wiring. Optional plugins degrade with an actionable message."""
    from livekit.agents import AgentSession

    try:
        from livekit.plugins import openai as lk_openai
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("missing dependency: pip install 'livekit-plugins-openai==1.7.1'") from exc

    stt: object | None = None
    if os.environ.get("DEEPGRAM_API_KEY"):
        with contextlib.suppress(ImportError):
            from livekit.plugins import deepgram

            stt = deepgram.STT(
                model=os.environ.get("DEEPGRAM_STT_MODEL", "nova-3"), language="en"
            )
    if stt is None:
        # LiveKit Inference descriptor form (string shorthand, verified in docs).
        stt = "deepgram/nova-3:multi"

    vad = None
    with contextlib.suppress(ImportError):
        from livekit.plugins import silero

        vad = silero.VAD.load()

    turn_handling = None
    with contextlib.suppress(ImportError):
        from livekit.agents import TurnHandlingOptions
        from livekit.plugins.turn_detector.multilingual import MultilingualModel

        turn_handling = TurnHandlingOptions(turn_detection=MultilingualModel())

    return AgentSession(
        stt=stt,
        llm=lk_openai.LLM(model=os.environ.get("LLM_MODEL", "gpt-4.1-mini")),
        tts=build_livekit_tts(config.rime),
        vad=vad,
        turn_handling=turn_handling,
        allow_interruptions=True,
    )


def make_server(config: Config):
    from livekit import agents
    from livekit.agents import AgentServer

    server = AgentServer()
    Agent = build_agent_class()

    @server.rtc_session(agent_name="break-my-voice-agent")
    async def entrypoint(ctx: agents.JobContext) -> None:
        await ctx.connect()
        policy = Policy(os.environ.get("DEMO_POLICY", "safe"))
        assistant = FencedAssistant(
            room=ctx.room,
            policy=policy,
            tool=SlowTool(config.lab.tool_delay_ms),
        )
        session = build_session(config)
        assistant.session = session
        assistant.attach()
        await session.start(room=ctx.room, agent=Agent(assistant))
        session.generate_reply(instructions="Greet the user and invite them to interrupt.")

    return server


def print_config(config: Config) -> None:
    print("Rime configuration (mirrored verbatim into RIME_EVIDENCE.md):")
    print(json.dumps(config.rime.as_dict(), indent=2))
    print(f"LiveKit url: {config.livekit.url}")
    print(
        "credentials: "
        f"RIME_API_KEY={'set' if config.rime.api_key else 'MISSING'}, "
        f"LIVEKIT_API_KEY={'set' if config.livekit.api_key else 'MISSING'}, "
        f"DEEPGRAM_API_KEY={'set' if os.environ.get('DEEPGRAM_API_KEY') else 'MISSING'}, "
        f"OPENAI_API_KEY={'set' if os.environ.get('OPENAI_API_KEY') else 'MISSING'}"
    )


def main() -> int:
    load_env()
    argv = sys.argv[1:]

    if "--check" in argv:
        print_config(CFG)
        try:
            from livekit import agents
            from livekit.plugins import rime  # noqa: F401
        except ImportError as exc:
            print(f"\n✗ livekit stack unavailable in this interpreter: {exc}")
            return 2
        make_server(CFG)
        print("\n✓ config validated; AgentServer, Agent and session wiring resolve cleanly")
        print("✓ start it with: python agent/main.py dev   (or: python agent/main.py start)")
        return 0

    from livekit import agents

    print_config(CFG)
    # cli.run_app owns argv subcommands: `dev` (ephemeral room via livekit-cli) or `start`.
    agents.cli.run_app(make_server(CFG))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
