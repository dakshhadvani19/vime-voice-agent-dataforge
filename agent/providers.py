"""Provider adapters. LiveKit + Rime touch the outside world *only* through this file,
so the kernel (state_manager / lab / metrics) stays importable with no providers
installed and no credentials set.

Two integration paths, deliberately:

  1. ``build_livekit_tts``  → the official ``livekit-plugins-rime`` TTS used by the real
     voice agent. Preferred for the judged path: it gives LiveKit's turn handling,
     interruption plumbing and ``TTSMetrics`` (which reports ``ttfb`` and ``cancelled``).

  2. ``RimeDirectClient``   → a thin /ws3 client used by Lab mode and by
     ``scripts/proof_of_voice.py`` to demonstrate that speech actually starts and
     actually stops on command. It uses Rime's documented ``contextId`` echo for fencing
     and the documented ``clear`` operation for cancellation.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from config import RimeConfig


# ---------------------------------------------------------------------------
# 1. LiveKit plugin
# ---------------------------------------------------------------------------
def build_livekit_tts(cfg: RimeConfig):
    """Instantiate the official Rime TTS plugin with the validated config.

    Signature verified against the installed package (livekit-plugins-rime 1.7.1):
        rime.TTS(*, base_url, model, speaker, lang, repetition_penalty, temperature,
                 top_p, max_tokens, time_scale_factor, speed_alpha, sample_rate,
                 reduce_latency, pause_between_brackets, phonemize_between_brackets,
                 api_key, http_session, use_websocket, segment, tokenizer)
    ``lang`` here is the plugin's own literal (``eng``, not ``en``) — the plugin converts.
    """
    from livekit.plugins import rime  # imported lazily: not needed for tests/sim

    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "speaker": cfg.speaker,
        "lang": _plugin_lang(cfg.lang),
        "sample_rate": cfg.sample_rate,
        "speed_alpha": cfg.speed_alpha,
        "use_websocket": cfg.use_websocket,
    }
    if cfg.reduce_latency:
        kwargs["reduce_latency"] = True
    if cfg.segment:
        kwargs["segment"] = cfg.segment
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    return rime.TTS(**kwargs)


def _plugin_lang(lang: str) -> str:
    """Rime's HTTP/WS API takes ``en``; the LiveKit plugin's TTSLangs literal takes ISO-3."""
    return {"en": "eng", "hi": "hin", "es": "spa", "fr": "fra", "de": "deu", "pt": "por", "ja": "jpn", "ar": "arb"}.get(
        lang, lang
    )


def build_stt(cfg: RimeConfig | None = None):
    """Deepgram streaming STT per spec §8 — the "don't build STT" slot."""
    from livekit.agents import AgentSession  # noqa: F401  (keeps import error local)
    from livekit.plugins import deepgram

    return deepgram.STT(model=os.environ.get("DEEPGRAM_STT_MODEL", "nova-3"), language="en")


# ---------------------------------------------------------------------------
# 2. Direct Rime client (Lab mode + voice proof)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class RimeEvent:
    type: str
    context_id: str | None
    audio: bytes | None = None
    words: list[str] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    t_ms: float = 0.0


class RimeHttpError(RuntimeError):
    pass


@dataclass(slots=True)
class RimeDirectClient:
    """Minimal, honest Rime client.

    HTTP path uses stdlib only so `proof_of_voice.py` runs on a bare machine. WebSocket
    path uses the ``websockets`` package when present (it is the documented route for
    interruption handling: ``clear`` + context IDs).
    """

    config: RimeConfig
    on_event: Callable[[RimeEvent], None] | None = None

    # -- HTTP streaming ------------------------------------------------------
    def synthesize_http(self, text: str, *, out_path: str | None = None, accept: str = "audio/wav") -> tuple[bytes, float]:
        """One POST, audio streamed back. Returns (bytes, ttfb_ms)."""
        key = self.config.api_key
        if not key:
            raise RimeHttpError(f"{self.config.api_key_env} is not set")
        body = json.dumps(
            {
                "text": text,
                "modelId": self.config.model,
                "speaker": self.config.speaker,
                "lang": self.config.lang,
                "speedAlpha": self.config.speed_alpha,
            }
        ).encode()
        req = urllib.request.Request(
            self.config.http_endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": accept,
            },
            method="POST",
        )
        t0 = time.monotonic()
        ttfb: float | None = None
        chunks: list[bytes] = []
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    if ttfb is None:
                        ttfb = (time.monotonic() - t0) * 1000.0
                    chunks.append(chunk)
        except urllib.error.HTTPError as e:  # pragma: no cover - network dependent
            raise RimeHttpError(f"Rime HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from e
        except urllib.error.URLError as e:  # pragma: no cover - network dependent
            raise RimeHttpError(f"Rime unreachable: {e.reason}") from e
        audio = b"".join(chunks)
        if out_path:
            with open(out_path, "wb") as f:
                f.write(audio)
        return audio, (ttfb if ttfb is not None else 0.0)

    # -- WebSocket streaming -------------------------------------------------
    async def stream_ws(self, text: str, *, context_id: str) -> AsyncIterator[RimeEvent]:  # pragma: no cover
        """Documented /ws3 protocol:
            connect wss://users-ws.rime.ai/ws3?speaker=&modelId=&audioFormat=
            Authorization: Bearer header (browser WebSocket cannot set headers → server-side)
            send {"text": ..., "contextId": ...}, {"operation":"eos"}
            recv chunk | timestamps | done | error
            {"operation":"clear"} discards the queued buffer — the cancel primitive.
        """
        import asyncio  # local import; only needed for the ws path

        try:
            import websockets
        except ImportError as e:  # pragma: no cover
            raise RimeHttpError(
                "the /ws3 path needs the `websockets` package: pip install 'websockets>=12'"
            ) from e
        if not self.config.api_key:
            raise RimeHttpError(f"{self.config.api_key_env} is not set")
        from urllib.parse import urlencode

        qs = urlencode(
            {"speaker": self.config.speaker, "modelId": self.config.model, "audioFormat": "pcm"}
        )
        url = f"{self.config.ws_endpoint}?{qs}"
        async with websockets.connect(  # type: ignore[attr-defined]
            url, additional_headers={"Authorization": f"Bearer {self.config.api_key}"}
        ) as ws:
            await ws.send(json.dumps({"text": text, "contextId": context_id}))
            await ws.send(json.dumps({"operation": "eos"}))
            async for message in ws:
                ev = json.loads(message)
                out = RimeEvent(
                    type=ev.get("type", "unknown"),
                    context_id=ev.get("contextId"),
                    t_ms=time.monotonic() * 1000.0,
                    raw=ev,
                )
                if out.type == "chunk" and ev.get("data"):
                    out.audio = base64.b64decode(ev["data"])
                elif out.type == "timestamps":
                    wt = ev.get("word_timestamps", {})
                    out.words = wt.get("words", [])
                    out.starts = [v * 1000.0 for v in wt.get("start", [])]
                    out.ends = [v * 1000.0 for v in wt.get("end", [])]
                yield out
                if out.type == "done":
                    return
        await asyncio.sleep(0)  # keep the coroutine honest for type checkers

    async def clear(self, ws) -> None:  # pragma: no cover - requires live connection
        """Send the documented clear operation for an interrupted utterance."""
        await ws.send(json.dumps({"operation": "clear"}))


# ---------------------------------------------------------------------------
# Offline double: lets tests + simulator assert the fencing behaviour without a network.
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class NullRime:
    """Deterministic speech double. Emits word timestamps at ``ms_per_word`` cadence and
    honours ``clear`` by truncating the remaining words — same contract as /ws3."""

    ms_per_word: float = 225.0
    first_audio_ms: float = 180.0
    cleared: bool = False
    spoken_ms: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def synthesize(self, text: str, *, context_id: str, start_ms: float):
        words = [w for w in text.split() if w]
        out = []
        t = start_ms + self.first_audio_ms
        for w in words:
            if self.cleared:
                self.events.append({"type": "cleared", "contextId": context_id, "t_ms": t})
                break
            out.append((w, t - start_ms, t - start_ms + self.ms_per_word))
            self.events.append({"type": "word", "word": w, "contextId": context_id, "t_ms": t})
            t += self.ms_per_word
        self.spoken_ms = (t - start_ms) if out else self.first_audio_ms
        if not self.cleared:
            self.events.append({"type": "done", "contextId": context_id, "t_ms": t})
        return out

    def clear(self, at_ms: float) -> None:
        self.cleared = True
        self.spoken_ms = at_ms

    def reset(self) -> None:
        self.cleared = False
        self.spoken_ms = 0.0
        self.events.clear()
