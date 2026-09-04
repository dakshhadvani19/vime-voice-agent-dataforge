"""Local dev/lab server: LiveKit token minting + deterministic Lab-mode replay (stdlib only).

Why it exists:
  * The browser must never hold a Rime or LiveKit secret, so tokens are minted here
    (Rime's docs say the same about the WebSocket handshake: the browser cannot set an
    Authorization header).
  * A hackathon demo on a stage must not depend on room acoustics. `--lab` streams the
    *same* fixtures the evaluation harness scores through the *same* kernel, so the timeline
    the judges watch and the numbers in results/*.json come from one execution rather than
    a hand-waved re-enactment.

It is a dev surface, not a product feature: no auth, no accounts, no database (§17).
Run:  python agent/dev_server.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parent
for p in (str(AGENT_DIR), str(ROOT / "evaluation")):
    if p not in sys.path:
        sys.path.insert(0, p)

from config import Config  # noqa: E402
from main import load_env  # noqa: E402
from state_manager import Policy  # noqa: E402

load_env()
CFG = Config().validate()
CASES_PATH = ROOT / "evaluation" / "cases.json"
RESULTS_PATH = ROOT / "evaluation" / "results" / "latest.json"
_stop = threading.Event()


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text())["cases"]


def replay(case_id: str, policy: Policy, speed: float):
    """Run one fixture through the kernel, then yield (delay_s, snapshot) for real-time playback.

    The kernel finishes instantly on its virtual clock; we re-space the recorded millisecond
    offsets so the UI can watch causality unfold, and every figure stays exactly what the
    deterministic run produced (no second, divergent execution).
    """
    from simulate import run_case

    res = run_case({"id": case_id, **next(c for c in _cases() if c["id"] == case_id)}, policy)
    events = res.events
    prev_t = 0.0
    state = "idle"
    active: int | None = None
    latest: int | None = None
    for ev in events:
        gap = max(0.0, ev["t_ms"] - prev_t) / 1000.0 / max(0.25, speed)
        prev_t = ev["t_ms"]
        if ev["kind"] == "state_changed" and ev.get("state"):
            state = ev["state"]
        if ev["kind"] == "request_created":
            # a new request is simultaneously the newest and the active one
            active = latest = ev["request_id"]
        elif ev["kind"] == "request_invalidated":
            # fenced: nothing is active until the replacement turn is created
            active = None
        yield gap, {
            "type": "timeline",
            "policy": policy.value,
            "state": state,
            "active_request_id": active,
            "latest_request_id": latest,
            "scenario": case_id,
            "events": [ev],
            "final_state": {},
            "metrics": {},
        }
    yield 0.2 / max(0.25, speed), {
        "type": "timeline",
        "policy": policy.value,
        "state": state,
        "active_request_id": active,
        "latest_request_id": latest,
        "scenario": case_id,
        "events": [],
        "final_state": res.final_state,
        "metrics": res.metrics,
    }
    yield 0.0, {"type": "end", "events": [], "metrics": res.metrics}


class Handler(BaseHTTPRequestHandler):
    server_version = "VimeDataForge/0.1"

    # -- plumbing ------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, indent=2, default=str).encode())

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "content-type")
        self.send_header("access-control-allow-methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "expected a JSON body"}, 400)
        if path == "/api/token":
            return self._token(body)
        self._json({"error": "not found"}, 404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == "/health":
            return self._json(
                {
                    "ok": True,
                    "rime": CFG.rime.as_dict(),
                    "credentials": {
                        "RIME_API_KEY": bool(CFG.rime.api_key),
                        "LIVEKIT_URL": bool(os.environ.get("LIVEKIT_URL")),
                        "LIVEKIT_API_KEY": bool(CFG.livekit.api_key),
                    },
                    "corpus": {"cases": len(_cases()), "path": "evaluation/cases.json"},
                }
            )
        if path == "/api/scenarios":
            return self._json(
                {"cases": [{"id": c["id"], "title": c.get("title", ""), "tags": c.get("tags", [])} for c in _cases()]}
            )
        if path == "/api/results":
            if not RESULTS_PATH.exists():
                return self._json(
                    {"error": "no results yet — run: python evaluation/run_tests.py"}, 503
                )
            return self._send(200, RESULTS_PATH.read_bytes())
        if path == "/lab/stream":
            return self._stream(query)
        self._json({"error": "not found"}, 404)

    # -- endpoints -----------------------------------------------------------
    def _token(self, body: dict) -> None:
        """Mint a single-use room token. Refuses to fake one when keys are absent."""
        if not CFG.livekit.has_credentials:
            return self._json(
                {
                    "error": "LIVEKIT_API_KEY / LIVEKIT_API_SECRET are not set",
                    "hint": (
                        "either run the agent in dev mode (python agent/main.py dev, which "
                        "starts livekit-cli and prints a room URL), or set the three LIVEKIT_* "
                        "vars in .env. Lab replay needs none of this and works right now."
                    ),
                    "fallback": {"endpoint": "/lab/stream?scenario=booking_01&policy=safe"},
                },
                501,
            )
        try:
            from livekit import api
        except ImportError:
            return self._json({"error": "pip install livekit-agents[rime] to mint tokens"}, 500)
        identity = str(body.get("identity") or "visitor")
        room = str(body.get("room") or os.environ.get("DEMO_ROOM", "break-my-voice-agent"))
        token = (
            api.AccessToken(CFG.livekit.api_key, CFG.livekit.api_secret)
            .with_identity("agent-side-token")
            .with_ttl(600)
            .with_grants(api.VideoGrants(room_join=True, room=room))
        )
        self._json({"token": token.to_jwt(), "url": CFG.livekit.url, "room": room, "identity": identity})

    def _stream(self, query: dict) -> None:
        case_id = (query.get("scenario") or ["booking_01"])[0]
        policy = Policy("naive" if (query.get("policy") or ["safe"])[0] == "naive" else "safe")
        speed = float((query.get("speed") or ["1"])[0])
        if not any(c["id"] == case_id for c in _cases()):
            return self._json({"error": f"unknown scenario {case_id}"}, 404)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        # A lab replay is finite, so we close the socket when the last frame is out.
        # `Connection: close` also stops EventSource's auto-reconnect from silently
        # re-running the scenario forever (the client closes on the end frame too).
        self.send_header("connection", "close")
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.close_connection = True

        def write(payload: dict) -> None:
            self.wfile.write(f"data: {json.dumps(payload, default=str)}\n\n".encode())
            self.wfile.flush()

        try:
            write({"type": "hello", "scenario": case_id, "policy": policy.value, "speed": speed,
                   "timing": "virtual clock replay of the deterministic run"})
            for gap, snap in replay(case_id, policy, speed):
                if _stop.is_set():
                    return
                if gap:
                    time.sleep(gap)
                write(snap)
        except (BrokenPipeError, ConnectionResetError):
            return  # browser tab closed; nothing to fix
        except Exception as exc:  # pragma: no cover - surfaced to the client
            with __import__("contextlib").suppress(Exception):
                write({"type": "timeline", "error": f"{type(exc).__name__}: {exc}", "events": [], "metrics": {}})

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("LOG_LEVEL", "info") == "debug":
            super().log_message(fmt, *args)


def main() -> int:
    p = argparse.ArgumentParser(description="Break My Voice Agent — dev/lab server")
    p.add_argument("--host", default=os.environ.get("AGENT_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", "8080")))
    p.add_argument("--check", action="store_true", help="validate config and exit")
    args = p.parse_args()

    print("Rime configuration:")
    print(json.dumps(CFG.rime.as_dict(), indent=2))
    if args.check:
        print("✓ config valid; scenarios:", [c["id"] for c in _cases()])
        snap = next(iter(replay("booking_01", Policy.SAFE, 1000)), None)
        print(f"✓ lab replay produces events: {len(snap[1]['events']) if snap else 0} in first frame")
        return 0

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\nlab + token server on http://{args.host}:{args.port}")
    print("  GET  /health  /api/scenarios  /api/results  /lab/stream?scenario=booking_01&policy=safe")
    print("  POST /api/token   (needs LIVEKIT_* keys; lab mode does not)")
    print("Ctrl-C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _stop.set()
        httpd.shutdown()
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
