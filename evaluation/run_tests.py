#!/usr/bin/env python3
"""Replay the stress corpus through the fencing kernel and emit real numbers (spec §10, §12).

    python evaluation/run_tests.py                 # deterministic virtual-clock A/B
    python evaluation/run_tests.py --live          # real clock; real Rime if keyed
    python evaluation/run_tests.py --only late_result_01 --verbose
    python evaluation/run_tests.py --live --repeat 5

Exit code is non-zero if any SAFE-arm assertion fails, so CI can gate on it.

Every run writes evaluation/results/<run_id>.json containing: provenance (git sha, python,
platform, harness version, exact Rime config), the declared latency assumptions, per-case
metrics with raw samples, and the full event timeline. Nothing in the README or in
RIME_EVIDENCE.md is allowed to cite a figure that is not in one of those files.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(HERE))

from config import Config, LabConfig
from simulate import ScenarioResult, run_case
from state_manager import Policy

HARNESS_VERSION = "1.0.0"
METRIC_ORDER = [
    "interruption_stop_latency",
    "recovery_latency",
    "stale_output_rate",
    "stale_session_rate",
    "final_state_accuracy",
    "fencing_correctness",
]
RATE_METRICS = {"stale_output_rate", "stale_session_rate", "final_state_accuracy", "fencing_correctness"}


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def load_cases(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def pool(results: list[ScenarioResult]) -> dict[str, dict[str, Any]]:
    """Aggregate across cases. Rates pool numerator/denominator rather than averaging
    ratios, so a 1-case and a 4-case run stay comparable."""
    agg: dict[str, dict[str, Any]] = {}
    for name in METRIC_ORDER:
        vals: list[float] = []
        num = den = 0
        sources: set[str] = set()
        for r in results:
            m = r.metrics.get(name)
            if not m:
                continue
            sources.add(m.get("source", "simulated"))
            vals.extend(m.get("samples", []))
            if "numerator" in m:
                num += m["numerator"]
                den += m["denominator"]
        entry: dict[str, Any] = {"n_samples": len(vals), "source": "+".join(sorted(sources)) or "n/a"}
        if name in RATE_METRICS and den:
            entry["value"] = round(num / den, 6)
            entry["numerator"] = num
            entry["denominator"] = den
        elif vals:
            entry["value"] = round(statistics.fmean(vals), 3)
            entry["mean_ms"] = round(statistics.fmean(vals), 3)
            entry["median_ms"] = round(statistics.median(vals), 3)
            entry["min_ms"] = round(min(vals), 3)
            entry["max_ms"] = round(max(vals), 3)
            if len(vals) >= 2:
                entry["stdev_ms"] = round(statistics.stdev(vals), 3)
            entry["samples_ms"] = [round(v, 3) for v in vals]
        else:
            entry["value"] = None
        agg[name] = entry
    return agg


def fmt(name: str, entry: dict[str, Any]) -> str:
    v = entry.get("value")
    if v is None:
        return f"{name:<26} {'n/a':>10}  ({entry['source']})"
    if name in RATE_METRICS:
        return (
            f"{name:<26} {v:>10.3f}  "
            f"[{entry.get('numerator', 0)}/{entry.get('denominator', 0)}]  ({entry['source']})"
        )
    return f"{name:<26} {v:>9.1f}ms  ({entry['source']})"


def markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Evaluation run `{payload['run_id']}`",
        "",
        f"- mode: **{payload['mode']}** ({payload['timing']})",
        f"- generated: {payload['generated_at']}",
        f"- git: `{payload['provenance']['git_sha']}` · python {payload['provenance']['python']} · {payload['provenance']['platform']}",
        f"- fixtures: `evaluation/cases.json` v{payload['corpus_version']} · {payload['case_count']} cases × {payload['arms']} arms",
        f"- Rime config: `{json.dumps(payload['provenance']['rime_config'])}`",
        "",
        "Latency constants below are **declared inputs** for `simulated` mode, not measurements.",
        "Only a `live` mode row may be quoted as measured behaviour.",
        "",
        "## Aggregated",
        "",
        "| metric | naive (control arm) | safe (this project) |",
        "| --- | ---: | ---: |",
    ]
    for name in METRIC_ORDER:
        naive_row = payload["aggregate"]["naive"].get(name, {})
        safe_row = payload["aggregate"]["safe"].get(name, {})

        def cell(e: dict[str, Any], metric: str = name) -> str:
            # `metric` is a default arg so the closure binds *this* iteration's name;
            # a late-binding bug here would silently mislabel every row of the report.
            v = e.get("value")
            if v is None:
                return "n/a"
            if metric in RATE_METRICS:
                return f"{v:.3f} ({e.get('numerator', 0)}/{e.get('denominator', 0)})"
            return f"{v:.1f} ms"

        lines.append(f"| `{name}` | {cell(naive_row)} | {cell(safe_row)} |")
    lines += [
        "",
        "## Per case",
        "",
        "| case | arm | stop latency | recovery | stale outputs | final state | fencing | verdict |",
        "| --- | --- | ---: | ---: | ---: | :---: | ---: | :---: |",
    ]
    for r in payload["results"]:
        def lat(name: str, metrics: dict[str, Any] = r["metrics"]) -> str:
            v = (metrics.get(name) or {}).get("mean")
            return "n/a" if v is None else f"{v:.1f} ms"

        def rate(name: str, metrics: dict[str, Any] = r["metrics"]) -> str:
            v = (metrics.get(name) or {}).get("mean")
            return "n/a" if v is None else f"{v:.0%}"

        verdict = "PASS" if r["passed"] else "FAIL"
        lines.append(
            f"| `{r['case_id']}` | {r['policy']} | {lat('interruption_stop_latency')} | "
            f"{lat('recovery_latency')} | {rate('stale_output_rate')} | "
            f"{'✓' if (r['metrics'].get('final_state_accuracy') or {}).get('value', 1) == 1.0 else '✗'} | "
            f"{rate('fencing_correctness')} | {verdict} |"
        )
    lines += ["", "## Event timelines", ""]
    for r in payload["results"]:
        lines.append(f"### `{r['case_id']}` — {r['policy']}")
        lines.append("")
        lines.append("```")
        lines.extend(r["timeline"])
        lines.append("```")
        lines.append("")
        if r["failures"]:
            lines.append("Failures: " + "; ".join(r["failures"]))
            lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    corpus = load_cases(Path(args.cases))
    cfg = Config().validate()
    lab_cfg = LabConfig.from_env()
    cases = corpus["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted or wanted & set(c.get("tags", []))]
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    mode = "live" if args.live else "simulated"
    results: list[ScenarioResult] = []
    started = time.time()
    for rep in range(args.repeat):
        for case in cases:
            for policy in (Policy.NAIVE, Policy.SAFE):
                if args.live:
                    res = run_case_live(case, policy, lab_cfg, cfg, pass_index=rep)
                else:
                    res = run_case(case, policy, lab_cfg)
                if args.repeat > 1:
                    res.case_id = f"{res.case_id}#r{rep + 1}"
                results.append(res)

    by_policy: dict[str, list[ScenarioResult]] = {"naive": [], "safe": []}
    for r in results:
        by_policy[r.policy].append(r)
    aggregate = {p: pool(rs) for p, rs in by_policy.items()}

    safe_failures = [r for r in by_policy["safe"] if not r.passed]
    naive_reproduced = sum(1 for r in by_policy["naive"] if r.exhibited_defect)
    # Guard fixtures (naive_expected=clean) are *supposed* to stay clean in both arms, so
    # counting them in the denominator would understate how discriminating the corpus is.
    defect_expected = [
        r for r in by_policy["naive"]
        if next((c for c in cases if c["id"].split("#")[0] == r.case_id.split("#")[0]), {}).get("naive_expected", "defect") == "defect"
    ]
    guards = [r for r in by_policy["naive"] if r not in defect_expected]

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{mode}"
    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "wall_clock_seconds": round(time.time() - started, 3),
        "mode": mode,
        "cached_passes": {"cold": 1 if args.live else 0, "warm": max(0, args.repeat - 1) if args.live else 0},
        "timing": (
            "real clock (time.monotonic); provider latency included"
            if args.live
            else "virtual clock (discrete-event); reproducible, provider latency modelled"
        ),
        "corpus_version": corpus.get("version"),
        "case_count": len(cases),
        "arms": 2,
        "repeat": args.repeat,
        "provenance": {
            "git_sha": _git_sha(),
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "harness_version": HARNESS_VERSION,
            "rime_config": cfg.rime.as_dict(),
            "lab_config": {
                "tool_delay_ms": lab_cfg.tool_delay_ms,
                "rime_clear_rtt_ms": lab_cfg.rime_clear_rtt_ms,
                "interruption_detection_latency_ms": lab_cfg.interruption_detection_latency_ms,
                "ms_per_word": lab_cfg.ms_per_word,
            },
            "credentials_present": {
                "RIME_API_KEY": bool(os.environ.get("RIME_API_KEY")),
                "LIVEKIT_URL": bool(os.environ.get("LIVEKIT_URL")),
            },
        },
        "aggregate": aggregate,
        "summary": {
            "safe_cases": len(by_policy["safe"]),
            "safe_failures": len(safe_failures),
            "naive_cases": len(by_policy["naive"]),
            "naive_cases_reproducing_defect": naive_reproduced,
        },
        "results": [r.to_dict() for r in results],
    }

    out_dir = Path(args.out) if args.out else HERE / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out_dir / "latest.md").write_text(markdown_report(payload))

    # ---- console report -----------------------------------------------------
    print(f"\nDataForge × Rime — interruption-safety evaluation ({mode})")
    print("=" * 78)
    print(f"corpus v{corpus.get('version')}: {len(cases)} cases × 2 arms · run {run_id}")
    print(f"provenance: git {_git_sha()[:8]} · python {payload['provenance']['python']}")
    if not args.live:
        print("NOTE: virtual clock. Latency constants are declared inputs, NOT measurements.")
    for policy in ("naive", "safe"):
        print(f"\n[{policy.upper()}]" + ("  ← control arm, expected to fail" if policy == "naive" else "  ← this project"))
        for name in METRIC_ORDER:
            print("  " + fmt(name, aggregate[policy].get(name, {"source": mode})))
    print(f"\nsafe arm: {len(by_policy['safe']) - len(safe_failures)}/{len(by_policy['safe'])} cases pass")
    guard_clean = sum(1 for r in guards if not r.exhibited_defect)
    print(
        f"naive arm reproduced the predicted defect in {sum(1 for r in defect_expected if r.exhibited_defect)}"
        f"/{len(defect_expected)} discriminating fixtures"
    )
    if guards:
        print(f"guard fixtures (both arms must stay clean): {guard_clean}/{len(guards)} clean in naive")
    for r in safe_failures:
        print(f"  FAIL {r.case_id}: " + "; ".join(r.failures), file=sys.stderr)
    if args.verbose:
        for r in results:
            print(f"\n--- {r.case_id} [{r.policy}] ---")
            for line in r.timeline:
                print(f"  {line}")
    print(f"\nwrote {out_dir / (run_id + '.json')}")
    print(f"wrote {out_dir / 'latest.md'}")
    return 1 if safe_failures else 0


def run_case_live(
    case: dict[str, Any],
    policy: Policy,
    lab_cfg: LabConfig,
    cfg: Config,
    *,
    pass_index: int = 0,
) -> ScenarioResult:  # pragma: no cover - needs credentials for the provider arm
    """Live arm: same fixtures, real wall clock, real Rime synthesis when keyed.

    Imported lazily so the deterministic path keeps zero provider dependencies.
    ``pass_index`` 0 is the cold pass, >=1 the warm one: cached vs uncached stay separate
    numbers instead of collapsing into one mean (spec §2).
    """
    from live_runner import run_case_sync

    return run_case_sync(case, policy, lab_cfg, config=cfg, pass_index=pass_index)


def main() -> int:
    p = argparse.ArgumentParser(description="Run the interruption-safety stress corpus.")
    p.add_argument("--cases", default=str(HERE / "cases.json"))
    p.add_argument("--only", help="comma-separated case ids or tags")
    p.add_argument("--out", help="results directory (default evaluation/results)")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--live", action="store_true", help="real clock + real providers where keyed")
    p.add_argument("--verbose", action="store_true", help="print event timelines")
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
