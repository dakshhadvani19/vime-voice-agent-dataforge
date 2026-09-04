"""Deterministic intent extraction + the deliberately slow tool (spec §11).

Two things live here, and both are *intentionally* not LLM-backed:

  ``parse_intent``  – rule-based extractor with a ground-truth answer, so fixtures in
                      evaluation/cases.json can assert final-state accuracy without a
                      model in the loop making runs non-comparable. In the live path the
                      LLM fills this same slot and returns the same JSON shape.

  ``SlowTool``      – one fake tool whose latency is configurable. §11 prefers this over
                      an unpredictable external API: the late-result race must be
                      reproducible on stage, not dependent on a flaky flight search.

Every result carries the ``request_id`` of the turn that asked for it (spec §6).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

DOMAIN = "booking"

_NUM_WORDS = {
    "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_CITY = re.compile(
    r"\b(?:to|from|in|at|for)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b"
)
_PEOPLE = re.compile(
    r"(?:for\s+)?(\d+|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:people|persons|pax|guests|seats)"
)
#: "Book a table for two" — the brief's own phrasing, which never says "people".
_PEOPLE_FOR = re.compile(r"\bfor\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b")
#: A time needs a meridiem ("9 PM") or an explicit "at" ("at 9"). Without that anchor a
#: bare "4 people" would be read as 04:00, which silently corrupts fixture ground truth.
_TIME_EXPLICIT = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_TIME_AT = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*(?:people|persons|pax|guests|seats))", re.I)
_CANCEL = re.compile(r"\b(forget it|cancel|never ?mind|stop that|scratch that)\b", re.I)
_CHANGE = re.compile(r"\b(actually|instead|change|make it|correction|rather|update)\b", re.I)
#: "Mumbai instead of Delhi" — the destination-correction shape from spec §4 step 4.
_INSTEAD = re.compile(r"\b([A-Z][a-z]{2,})\s+instead of\s+([A-Z][a-z]{2,})")
#: "change the destination to X" / "to X now"
_TO = re.compile(r"\b(?:to|into)\s+([A-Z][a-z]{2,})\b")


@dataclass(slots=True)
class Intent:
    """Normalized final state for one request. Fields are sparse on purpose: an
    interruption only *patches* the fields the user actually re-specified, which is why
    "Mumbai instead of Delhi" must not silently reset the departure time."""

    action: str = DOMAIN
    party_size: int | None = None
    time: str | None = None
    origin: str | None = None
    destination: str | None = None
    cancelled: bool = False
    is_correction: bool = False

    def _resolve_time(self, base: Intent) -> str | None:
        """Bare "at 9" inherits the meridiem already on the booking.

        Deterministic rule, not a guess: users who correct a time say the hour, and the
        only defensible reading is the same half-day as the value being replaced.
        """
        if self.time is None:
            return base.time
        if re.search(r"(am|pm)$", self.time):
            return self.time
        if base.time and re.search(r"(am|pm)$", base.time):
            return f"{self.time} {base.time.split()[-1]}"
        return self.time

    def merged_over(self, base: Intent) -> Intent:
        return Intent(
            action=base.action,
            party_size=self.party_size if self.party_size is not None else base.party_size,
            time=self._resolve_time(base),
            origin=self.origin or base.origin,
            destination=self.destination or base.destination,
            cancelled=self.cancelled or base.cancelled,
            is_correction=self.is_correction,
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "domain": self.action,
            "party_size": self.party_size,
            "time": self.time,
            "origin": self.origin,
            "destination": self.destination,
            "cancelled": self.cancelled,
        }


def parse_intent(text: str) -> Intent:
    """Deterministic stand-in for the LLM slot. Same output contract, no sampling."""
    low = f" {text.lower()} "
    intent = Intent(
        cancelled=bool(_CANCEL.search(low)),
        is_correction=bool(_CHANGE.search(low)),
    )
    m = _PEOPLE.search(low) or _PEOPLE_FOR.search(low)
    if m:
        raw = m.group(1)
        intent.party_size = int(raw) if raw.isdigit() else _NUM_WORDS.get(raw)
    # Prefer an explicit am/pm reading; fall back to a bare "at 9" only when nothing else
    # is stated, then let merged_over() inherit the meridiem of the value being patched.
    explicit = [t for t in _TIME_EXPLICIT.findall(low) if t[0]]
    if explicit:
        hh, mm, ap = explicit[-1]
        intent.time = f"{int(hh)}:{mm or '00'} {ap.lower()}"
    else:
        bare = [t for t in _TIME_AT.findall(low) if t[0]]
        if bare:
            hh, mm = bare[-1]
            intent.time = f"{int(hh)}:{mm or '00'}"
    # Destination: "X instead of Y" is a patch, so X wins regardless of position.
    instead = _INSTEAD.search(text)
    cities = _CITY.findall(text)
    if instead:
        intent.destination = instead.group(1)
        if not intent.is_correction:
            intent.is_correction = True
    elif cities:
        # Order-dependent: "from Ahmedabad to Delhi" → origin, destination.
        if " from " in low:
            intent.origin = cities[0]
            if len(cities) > 1:
                intent.destination = cities[-1]
        else:
            intent.destination = cities[-1]
    if intent.destination is None:
        to = _TO.findall(text)
        if to and intent.is_correction:
            intent.destination = to[-1]
    return intent


def state_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Subset comparison: a fixture asserts only the fields the interruption was meant to
    change. ``None`` on the expected side means "don't care", never "must be empty" —
    comparing prose to prose otherwise fails on every correctly-preserved field."""
    if not expected:
        return True
    for key, want in expected.items():
        if want is None:
            continue
        if actual.get(key) != want:
            return False
    return True


class BookingStore:
    """The application state that must stay consistent with what the user heard (§2).

    Mutated only by ``commit``, which callers may invoke solely for an accepted,
    fence-passing result. The count of rejected commits is what makes stale application
    visible instead of merely "probably fine".
    """

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"domain": DOMAIN, "cancelled": False}
        self.commits: list[dict[str, Any]] = []
        self.rejected_commits: list[dict[str, Any]] = []

    def commit(self, request_id: int, state: dict[str, Any], *, allowed: bool) -> bool:
        entry = {"request_id": request_id, "state": dict(state)}
        if allowed:
            self.state = state
            self.commits.append(entry)
            return True
        self.rejected_commits.append(entry)
        return False

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)


@dataclass(slots=True)
class ToolOutcome:
    request_id: int
    started_ms: float
    finished_ms: float
    result: dict[str, Any]
    delay_ms: float

    @property
    def latency_ms(self) -> float:
        return self.finished_ms - self.start_ms_ms if hasattr(self, "start_ms_ms") else (
            self.finished_ms - self.started_ms
        )


class SlowTool:
    """Configurable-latency fake tool.

    ``plan()`` returns the result *and* the virtual finish offset so the simulator can
    schedule it on a deterministic clock, while ``run()`` is the same computation against
    a real event loop for the live path. One source of truth for the delay.
    """

    def __init__(self, delay_ms: float = 1500.0, *, sleeper: Callable[[float], Awaitable[None]] | None = None) -> None:
        self.delay_ms = float(delay_ms)
        self._sleeper = sleeper

    async def _sleep(self, seconds: float) -> None:
        if self._sleeper is not None:
            await self._sleeper(seconds)
        else:
            await asyncio.sleep(seconds)

    def plan(self, request_id: int, text: str, *, started_ms: float, base: Intent | None = None) -> ToolOutcome:
        intent = parse_intent(text)
        if base is not None and intent.is_correction:
            intent = intent.merged_over(base)
        result = {
            "request_id": request_id,
            "tool": "booking.lookup",
            **intent.to_state(),
        }
        return ToolOutcome(
            request_id=request_id,
            started_ms=started_ms,
            finished_ms=started_ms + self.delay_ms,
            result=result,
            delay_ms=self.delay_ms,
        )

    async def run(self, request_id: int, text: str, *, base: Intent | None = None) -> dict[str, Any]:
        await self._sleep(self.delay_ms / 1000.0)
        intent = parse_intent(text)
        if base is not None and intent.is_correction:
            intent = intent.merged_over(base)
        return {"request_id": request_id, "tool": "booking.lookup", **intent.to_state()}

    def set_delay(self, delay_ms: float) -> None:
        """Exposed at runtime so the demo can dial the race window on stage."""
        self.delay_ms = float(delay_ms)
