"""Validated provider configuration.

Why a validator instead of passing strings straight through: Rime's docs state plainly
that an unsupported ``speaker``/``modelId``/``lang`` combination *"is not reliably
rejected"* by the API — it can silently fall back to a different voice. For a project
whose deliverable is a reproducible measured speech pipeline, a silent voice swap would
poison both the demo and the latency numbers. So we fail loudly at startup instead,
against the catalog in docs.rime.ai.

Verified against the live docs on 2026-09-04:
  * Models available to the LiveKit plugin: ``TTSModels = Literal['mistv2','mistv3','coda']``
    (livekit-plugins-rime 1.7.1, ``livekit/plugins/rime/models.py``)
  * ``coda`` + ``astra`` + ``en``: Rime "Streaming TTS" example request body
  * Coda serves 253 voices, each exactly one language; Mist v3 serves 78.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Rime voice catalog (English) — subset of the public catalog, enough to catch
# the mistakes that actually happen: reusing a voice name across models.
# Source: https://docs.rime.ai/docs/voices-coda and /docs/voices-mist-v3
# ---------------------------------------------------------------------------
CODA_EN: frozenset[str] = frozenset(
    {
        "astra", "bancroft", "beatty", "clementine", "cupola", "eyre", "godfrey",
        "hesse", "lawton", "lintel", "luna", "lyra", "marlu", "masonry", "parapet",
        "vespera", "adeline", "albion", "alma", "ana", "andromeda", "arcade", "argon",
    }
)
MIST_V3_EN: frozenset[str] = frozenset(
    {
        "astra", "cove", "estelle", "lagoon", "luna", "mari", "moon", "moraine", "peak",
        "sirius", "summit", "talon", "thunder", "tundra", "vespera", "wildflower",
        "alexis", "alpine", "bayou", "blaze", "blossom", "boulder", "brook", "cedar",
        "creek", "ember", "falcon", "hawk", "iris", "ironwood", "jose", "river",
    }
)
MIST_V2_VOICES: frozenset[str] = frozenset(
    {"astra", "cove", "luna", "peak", "river", "vespera", "ana", "orion", "ursa"}
)
#: model -> language -> voices
CATALOG: dict[str, dict[str, frozenset[str]]] = {
    "coda": {"en": CODA_EN},
    "mistv3": {"en": MIST_V3_EN},
    "mistv2": {"en": MIST_V2_VOICES},
}

#: Which model to run in the judged path. Coda is Rime's default production model;
#: mistv3 is documented as the lowest time-to-first-audio, which is what a latency
#: contest wants. Both are current production models.
DEFAULT_MODEL = os.environ.get("RIME_MODEL", "coda")
DEFAULT_SPEAKER = os.environ.get("RIME_SPEAKER", "astra")
DEFAULT_LANG = os.environ.get("RIME_LANG", "en")


class ConfigError(RuntimeError):
    pass


@dataclass(slots=True)
class RimeConfig:
    """Exact spoken-output configuration. Printed verbatim into RIME_EVIDENCE.md."""

    model: str = DEFAULT_MODEL
    speaker: str = DEFAULT_SPEAKER
    lang: str = DEFAULT_LANG
    sample_rate: int = int(os.environ.get("RIME_SAMPLE_RATE", "22050"))
    speed_alpha: float = float(os.environ.get("RIME_SPEED_ALPHA", "1.0"))
    reduce_latency: bool = os.environ.get("RIME_REDUCE_LATENCY", "1") == "1"
    use_websocket: bool = os.environ.get("RIME_USE_WEBSOCKET", "1") == "1"
    segment: str = os.environ.get("RIME_SEGMENT", "bySentence")
    api_key_env: str = "RIME_API_KEY"
    http_endpoint: str = "https://users.rime.ai/v1/rime-tts"
    ws_endpoint: str = "wss://users-ws.rime.ai/ws3"
    regional: str = os.environ.get("RIME_REGION", "global")

    def validate(self) -> None:
        if self.model not in CATALOG:
            raise ConfigError(
                f"unknown Rime model {self.model!r}. livekit-plugins-rime 1.7.1 accepts "
                f"one of {sorted(CATALOG)}"
            )
        if self.lang not in CATALOG[self.model]:
            raise ConfigError(
                f"Rime model {self.model!r} has no language catalog entry for {self.lang!r} "
                f"in this build. Known: {sorted(CATALOG[self.model])}"
            )
        if self.speaker not in CATALOG[self.model][self.lang]:
            raise ConfigError(
                f"voice {self.speaker!r} is not published for model={self.model!r} "
                f"lang={self.lang!r}. Rime does not reliably reject bad triples, so this "
                f"is validated locally. Try one of: {sorted(CATALOG[self.model][self.lang])[:6]}"
            )
        if self.segment not in {"bySentence", "immediate", "never"}:
            raise ConfigError(
                f"segment must be one of bySentence|immediate|never (got {self.segment!r})"
            )

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key)

    def as_dict(self) -> dict[str, Any]:
        """Everything except the secret — this dict is safe to commit into evidence."""
        return {
            "model": self.model,
            "speaker": self.speaker,
            "lang": self.lang,
            "sample_rate": self.sample_rate,
            "speed_alpha": self.speed_alpha,
            "reduce_latency": self.reduce_latency,
            "transport": "websocket /ws3 (JSON streaming)" if self.use_websocket else "http streaming",
            "segment": self.segment,
            "endpoint": self.ws_endpoint if self.use_websocket else self.http_endpoint,
            "region": self.regional,
            "credential": f"env:{self.api_key_env}",
            "package": "livekit-plugins-rime==1.7.1",
        }


@dataclass(slots=True)
class LiveKitConfig:
    url: str = field(default_factory=lambda: os.environ.get("LIVEKIT_URL", "ws://localhost:7880"))
    api_key: str | None = field(default_factory=lambda: os.environ.get("LIVEKIT_API_KEY"))
    api_secret: str | None = field(default_factory=lambda: os.environ.get("LIVEKIT_API_SECRET"))

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)


@dataclass(slots=True)
class LabConfig:
    """Knob settings for the experiment itself."""
    tool_delay_ms: float = field(default_factory=lambda: float(os.environ.get("TOOL_DELAY_MS", "1500")))
    #: Modelled round-trip for Rime's ``clear`` op in simulated runs. Labeled as an
    #: assumption in the report, never presented as a measurement of the network.
    rime_clear_rtt_ms: float = field(default_factory=lambda: float(os.environ.get("RIME_CLEAR_RTT_MS", "40")))
    #: LiveKit's own endpointing/VAD must hear the barge-in before we can act on it.
    interruption_detection_latency_ms: float = field(
        default_factory=lambda: float(os.environ.get("INTERRUPTION_DETECT_MS", "120"))
    )
    #: ms of already-synthesized audio sitting in the browser buffer at barge-in time.
    playback_backlog_ms: float = field(default_factory=lambda: float(os.environ.get("PLAYBACK_BACKLOG_MS", "600")))
    #: Approximate ms of speech per word, used to size the queued backlog in sim.
    ms_per_word: float = field(default_factory=lambda: float(os.environ.get("MS_PER_WORD", "225")))
    publish_topic: str = os.environ.get("TELEMETRY_TOPIC", "vime.timeline")

    @classmethod
    def from_env(cls) -> LabConfig:
        return cls()


@dataclass(slots=True)
class Config:
    rime: RimeConfig = field(default_factory=RimeConfig)
    livekit: LiveKitConfig = field(default_factory=LiveKitConfig)
    lab: LabConfig = field(default_factory=LabConfig.from_env)
    log_level: str = os.environ.get("LOG_LEVEL", "info")

    def validate(self) -> Config:
        self.rime.validate()
        return self

    @property
    def providers_ready(self) -> bool:
        return self.rime.has_credentials is not None and self.livekit.has_credentials
