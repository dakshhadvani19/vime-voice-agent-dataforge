"""Request/generation fencing + explicit state machine — the core of the project (spec §6, §7).

Design rules, straight from the brief:
  * §7  "Keep the state machine explicit and small. Avoid hidden orchestration logic
          spread across UI components."  → all sequencing lives here, nowhere else.
  * §21 "Keep the interruption/state manager isolated and testable."  → this module
          imports nothing from LiveKit or Rime. Providers are injected as callables.

The kernel has one job: decide, for every late-arriving piece of work (tool result,
LLM text, queued TTS audio), whether it belongs to the request the user currently owns.

    active_request_id == result.request_id  -> ACCEPT
    otherwise                               -> DISCARD, and never speak it

``Policy.SAFE`` enforces that. ``Policy.NAIVE`` is the deliberate control arm for the
A/B demo in §5: it accepts whatever arrives last, which is exactly the failure the
project claims to fix. Both arms run the *same* scenario fixture, so the delta is
observable rather than asserted.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from events import EventKind, EventLog


class AgentState(str, Enum):
    """Spec §7 state machine.

    ``initializing``/``idle``/``listening``/``thinking``/``speaking`` are the names
    LiveKit's own ``AgentState`` literal uses (livekit.agents.voice.events:306), so the
    lab states map 1:1 onto the live runtime. ``INTERRUPTED`` and ``CANCELLING`` are the
    two states the naive runtime does not expose, and are where the bug lives.
    """

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CANCELLING = "cancelling"


#: Legal transitions only. Anything else raises — a silent illegal transition in the
#: runtime is precisely the class of bug this lab is designed to surface.
_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.IDLE: frozenset({AgentState.LISTENING}),
    AgentState.LISTENING: frozenset({AgentState.THINKING, AgentState.IDLE}),
    AgentState.THINKING: frozenset(
        {AgentState.SPEAKING, AgentState.INTERRUPTED, AgentState.LISTENING}
    ),
    # SPEAKING → THINKING only ever happens in the naive arm: the framework restarted the
    # turn without our cancellation leg running. Kept legal so the control arm stays
    # runnable, and logged as a defect by InterruptionLab.begin_request.
    AgentState.SPEAKING: frozenset({AgentState.INTERRUPTED, AgentState.LISTENING, AgentState.THINKING}),
    AgentState.INTERRUPTED: frozenset({AgentState.CANCELLING, AgentState.LISTENING}),
    AgentState.CANCELLING: frozenset({AgentState.LISTENING, AgentState.IDLE}),
}


class IllegalTransition(RuntimeError):
    pass


class Policy(str, Enum):
    NAIVE = "naive"
    SAFE = "safe"


@dataclass(slots=True)
class RequestRecord:
    """A single user turn, and the authority token everything downstream must carry."""

    request_id: int
    text: str
    #: Monotonic fence token for *speech work* spawned by this request. Bumped whenever
    #: we cancel, so a queued-but-unplayed audio batch can be identified as stale even
    #: if it shares a request_id with the accepted one.
    generation: int = 1
    invalidated: bool = False
    completed: bool = False
    superseded_by: int | None = None
    invalidate_reason: str | None = None
    #: Words the user actually heard before we cut the audio (spec §2: state must stay
    #: consistent with what the user actually heard, not what we intended to say).
    heard_text: str = ""
    accepted_result: dict[str, Any] | None = None
    discarded_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fence_token(self) -> str:
        """Value sent to Rime as ``contextId`` so returned audio chunks can be fenced."""
        return f"req-{self.request_id}-gen-{self.generation}"

    @property
    def is_live(self) -> bool:
        return not self.invalidated and not self.completed


@dataclass(slots=True)
class SpeechHandle:
    """Handle to one utterance we asked Rime to speak."""

    speech_id: str
    request_id: int
    generation: int
    text: str
    started_ms: float
    context_id: str
    spoken_words: list[str] = field(default_factory=list)
    cancelled: bool = False
    completed: bool = False
    #: ms of synthesized audio that was still queued when the barge-in landed. This is
    #: the quantity that makes naive agents "keep talking" after being interrupted.
    queued_backlog_ms: float = 0.0


class RequestFence:
    """Monotonic request IDs + the accept/discard decision (spec §6)."""

    def __init__(self, log: EventLog, *, policy: Policy = Policy.SAFE) -> None:
        self._log = log
        self._policy = policy
        self._counter = itertools.count(1)
        self._requests: dict[int, RequestRecord] = {}
        self.active_request_id: int | None = None
        #: Most recently created request. Never cleared, so staleness can be judged for
        #: an event that lands after the new request already finished. The naive arm has
        #: no invalidation records to consult, so this — not `invalidated` — is what
        #: makes "obsolete" observable in both arms of the A/B.
        self.latest_request_id: int | None = None

    # -- lifecycle -----------------------------------------------------------
    def begin(self, text: str) -> RequestRecord:
        # Creating a new request implicitly supersedes whatever was live. This is the
        # single chokepoint: no path can start new work without invalidating old work.
        # NAIVE skips the invalidation, which is the bug being demonstrated: it moves on
        # to the new request while leaving the old one's work able to mutate state.
        if self._policy is Policy.SAFE and self.active_request_id is not None:
            prev = self._requests[self.active_request_id]
            if prev.is_live:
                self.invalidate(prev.request_id, reason="superseded")
        rec = RequestRecord(request_id=next(self._counter), text=text)
        self._requests[rec.request_id] = rec
        self.active_request_id = rec.request_id
        self.latest_request_id = rec.request_id
        self._log.emit(
            EventKind.REQUEST_CREATED,
            request_id=rec.request_id,
            text=text,
            fence=rec.fence_token,
        )
        return rec

    def invalidate(self, request_id: int, *, reason: str) -> RequestRecord:
        rec = self._requests[request_id]
        if rec.invalidated:
            return rec
        rec.invalidated = True
        rec.generation += 1  # orphans every audio batch already in flight
        rec.invalidate_reason = reason
        self._log.emit(
            EventKind.REQUEST_INVALIDATED,
            request_id=request_id,
            reason=reason,
            new_fence=rec.fence_token,
        )
        return rec

    def complete(self, request_id: int) -> None:
        rec = self._requests[request_id]
        rec.completed = True
        if self.active_request_id == request_id:
            self.active_request_id = None
        self._log.emit(EventKind.REQUEST_COMPLETED, request_id=request_id)

    # -- the decision --------------------------------------------------------
    def owns(self, request_id: int, *, policy: Policy) -> bool:
        """The one question the whole product asks.

        NAIVE always answers True — that is not an oversight, it is the control arm.
        """
        if policy is Policy.NAIVE:
            return True
        rec = self._requests.get(request_id)
        return bool(rec and rec.is_live and self.active_request_id == request_id)

    def owns_generation(self, request_id: int, generation: int, *, policy: Policy) -> bool:
        """Audio is fenced per *generation*, so a stale in-flight TTS batch is rejected
        even when its request_id still matches."""
        if not self.owns(request_id, policy=policy):
            return False
        rec = self._requests.get(request_id)
        if rec is None:
            return False
        return policy is Policy.NAIVE or generation == rec.generation

    def is_stale(self, request_id: int) -> bool:
        """True when a *newer* request exists, i.e. this work is obsolete.

        Deliberately independent of the policy so both arms of the A/B can be scored:
        the naive runtime emits no invalidation record, yet its stale outputs are exactly
        what the evaluation must count.
        """
        return self.latest_request_id is not None and request_id != self.latest_request_id

    def record_result(self, request_id: int, result: dict[str, Any], *, accepted: bool) -> None:
        rec = self._requests[request_id]
        if accepted:
            rec.accepted_result = result
        else:
            rec.discarded_results.append(result)

    def get(self, request_id: int) -> RequestRecord:
        return self._requests[request_id]

    @property
    def requests(self) -> Iterable[RequestRecord]:
        return self._requests.values()


class StateMachine:
    """Tiny explicit machine (spec §7). Emits STATE_CHANGED for the timeline."""

    def __init__(self, log: EventLog) -> None:
        self._log = log
        self._state = AgentState.IDLE

    @property
    def state(self) -> AgentState:
        return self._state

    def to(self, new: AgentState, *, request_id: int | None = None) -> None:
        if new is self._state:
            return
        legal = _TRANSITIONS[self._state]
        if new not in legal:
            raise IllegalTransition(
                f"illegal transition {self._state.value} → {new.value}; allowed: "
                f"{sorted(s.value for s in legal)}"
            )
        old, self._state = self._state, new
        self._log.emit(
            EventKind.STATE_CHANGED, request_id=request_id, state=new.value, from_=old.value
        )

    def reset(self) -> None:
        self._state = AgentState.IDLE


class PlaybackLedger:
    """Tracks which words the user actually heard, from Rime word-level timestamps.

    Rime's ``/ws3`` (and ``/ws2``) emit ``timestamps`` events carrying
    ``word_timestamps.{words,start,end}`` plus the echoed ``contextId``. We convert that
    into a "heard prefix" at the moment speech stops, so final application state can be
    reconciled against what was audible rather than what we meant to say.
    """

    def __init__(self) -> None:
        self._by_speech: dict[str, list[tuple[str, float, float]]] = {}

    def register(self, speech_id: str) -> None:
        self._by_speech.setdefault(speech_id, [])

    def note(self, speech_id: str, words: Iterable[str], starts: Iterable[float], ends: Iterable[float]) -> None:
        bucket = self._by_speech.setdefault(speech_id, [])
        # strict=True: a truncated timestamps frame is a provider bug we want to see,
        # not a silently short utterance that mis-reports what the user heard.
        for w, s, e in zip(words, starts, ends, strict=True):
            bucket.append((w, float(s), float(e)))

    def heard(self, speech_id: str, *, until_ms: float) -> str:
        """Words whose *end* timestamp had already passed when audio stopped."""
        bucket = self._by_speech.get(speech_id, [])
        return " ".join(w for w, _s, e in bucket if e <= until_ms)

    def progress_ms(self, speech_id: str, *, until_ms: float) -> float:
        bucket = self._by_speech.get(speech_id, [])
        played = [e for _w, _s, e in bucket if e <= until_ms]
        return played[-1] if played else 0.0
