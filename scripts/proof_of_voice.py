#!/usr/bin/env python3
"""Prove Rime is really speaking, and really stops — with numbers this script measured.

Run this before recording the demo and paste its output into RIME_EVIDENCE.md.

    python scripts/proof_of_voice.py                 # synthesize the current config
    python scripts/proof_of_voice.py --repeat 3      # cold vs warm pass (cached/uncached)
    python scripts/proof_of_voice.py --interrupt-at 250   # abort mid-stream, measure it

Behaviour without a key: prints the exact reproducible request and exits 0 as SKIP, so CI
never needs credentials and nobody is tempted to commit one.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from config import Config, ConfigError  # noqa: E402
from providers import RimeDirectClient, RimeHttpError  # noqa: E402

DEFAULT_LINE = (
    "This sentence is synthesized by Rime right now. Interrupt me while I am speaking, "
    "and the obsolete audio should stop instead of finishing."
)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def synthesize_once(client: RimeDirectClient, text: str, out: Path) -> dict:
    started = time.monotonic()
    audio, ttfb = client.synthesize_http(text, out_path=str(out))
    total = (time.monotonic() - started) * 1000.0
    return {
        "bytes": len(audio),
        "ttfb_ms": round(ttfb, 1),
        "total_ms": round(total, 1),
        "file": str(out.relative_to(ROOT)),
        "wav_header": audio[:4].decode("latin-1"),
    }


def abort_at(client: RimeDirectClient, text: str, out: Path, cutoff_ms: float) -> dict:
    """Start streaming, close the connection mid-response, and report what we actually got.

    Rime's documented HTTP interruption is exactly this — "Close the connection" — and the
    /ws3 one is `{"operation": "clear"}`. This measures that aborting is observable from the
    client side, which is the property §19.1 depends on.
    """
    body = json.dumps(
        {
            "text": text,
            "modelId": client.config.model,
            "speaker": client.config.speaker,
            "lang": client.config.lang,
        }
    ).encode()
    req = urllib.request.Request(
        client.config.http_endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {client.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        },
        method="POST",
    )
    got = bytearray()
    t0 = time.monotonic()
    ttfb = None
    closed_at = None
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, open(out, "wb") as f:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                if ttfb is None:
                    ttfb = (time.monotonic() - t0) * 1000.0
                got.extend(chunk)
                f.write(chunk)
                if (time.monotonic() - t0) * 1000.0 >= cutoff_ms:
                    closed_at = (time.monotonic() - t0) * 1000.0
                    break
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}"}
    except urllib.error.URLError as exc:
        return {"error": f"unreachable: {exc.reason}"}
    return {
        "cutoff_ms": cutoff_ms,
        "bytes_before_abort": len(got),
        "ttfb_ms": round(ttfb, 1) if ttfb is not None else None,
        "aborted_at_ms": round(closed_at, 1) if closed_at is not None else "stream ended first",
        "truncated_file": str(out.relative_to(ROOT)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Rime speech proof")
    ap.add_argument("--text", default=DEFAULT_LINE)
    ap.add_argument("--repeat", type=int, default=1, help="1 = single pass; 3+ shows cold vs warm")
    ap.add_argument("--interrupt-at", type=float, default=0.0, help="abort the stream after N ms")
    ap.add_argument("--out-dir", default="agent/media")
    ap.add_argument("--json", action="store_true", help="machine-readable output for RIME_EVIDENCE.md")
    args = ap.parse_args()

    try:
        cfg = Config().validate()
    except ConfigError as exc:
        print(f"✗ invalid Rime configuration: {exc}", file=sys.stderr)
        print("  fix RIME_MODEL / RIME_SPEAKER / RIME_LANG in .env against docs.rime.ai", file=sys.stderr)
        return 2

    print(f"model={cfg.rime.model} speaker={cfg.rime.speaker} lang={cfg.rime.lang} "
          f"endpoint={cfg.rime.http_endpoint}")

    if not cfg.rime.has_credentials:
        query = json.dumps(
            {"text": args.text, "modelId": cfg.rime.model, "speaker": cfg.rime.speaker, "lang": cfg.rime.lang}
        )
        print("\nSKIP: RIME_API_KEY is not set (it must never be committed — spec §22).")
        print("Reproduce this proof yourself with:\n")
        print("  curl --request POST \\")
        print(f"    --url {cfg.rime.http_endpoint} \\")
        print("    --header \"Authorization: Bearer $RIME_API_KEY\" \\")
        print("    --header 'Content-Type: application/json' \\")
        print("    --header 'Accept: audio/wav' \\")
        print(f"    --data '{query}' \\")
        print("    --output agent/media/rime-proof.wav\n")
        print("Then play agent/media/rime-proof.wav and record it in RIME_EVIDENCE.md.")
        return 0

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    client = RimeDirectClient(cfg.rime)
    report: dict = {
        "config": cfg.rime.as_dict(),
        "text_sha": hex(abs(hash(args.text)) % (10**12)),
        "passes": [],
    }

    try:
        for i in range(max(1, args.repeat)):
            out = out_dir / f"rime-proof-{i + 1}.wav"
            res = synthesize_once(client, args.text, out)
            res["pass"] = "cold" if i == 0 else "warm"
            report["passes"].append(res)
            print(f"  pass {i + 1} ({res['pass']}): ttfb={res['ttfb_ms']}ms total={res['total_ms']}ms "
                  f"{human(res['bytes'])} → {out.name}")
        if args.interrupt_at > 0:
            out = out_dir / "rime-proof-aborted.wav"
            res = abort_at(client, args.text, out, args.interrupt_at)
            report["abort"] = res
            if "error" in res:
                print(f"  abort: {res['error']}")
            else:
                print(f"  abort@{res['cutoff_ms']:.0f}ms: received {human(res['bytes_before_abort'])} "
                      f"then closed the stream (ttfb={res['ttfb_ms']}ms)")
    except RimeHttpError as exc:
        print(f"✗ Rime call failed: {exc}", file=sys.stderr)
        return 1

    report_path = out_dir / "rime-proof.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {report_path.relative_to(ROOT)}")
    if args.json:
        print(json.dumps(report, indent=2))
    print("\nPaste the block above into RIME_EVIDENCE.md. Numbers there must come from this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
