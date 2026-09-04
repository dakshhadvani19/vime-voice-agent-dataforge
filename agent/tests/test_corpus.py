"""Corpus-level integration tests (spec §10, §19, §22).

The most important test here is ``test_acceptance_test_clauses_hold``: it takes the eight
numbered clauses of §19 and asserts each one against the recorded timeline, so the
acceptance criterion in the brief is literally executable rather than prose.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "evaluation"))

from simulate import plan_turns, run_case  # noqa: E402
from state_manager import Policy  # noqa: E402

CORPUS = json.loads((ROOT / "evaluation" / "cases.json").read_text())
CASES = CORPUS["cases"]
CASE_IDS = [c["id"] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_safe_arm_passes_every_case(case):
    res = run_case(case, Policy.SAFE)
    assert res.passed, f"{case['id']}: {res.failures}"
    assert res.metrics["stale_output_rate"]["numerator"] == 0


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_naive_arm_matches_its_declared_expectation(case):
    res = run_case(case, Policy.NAIVE)
    if case.get("naive_expected", "defect") == "defect":
        assert res.exhibited_defect, f"{case['id']}: fixture does not discriminate"
    else:
        assert not res.exhibited_defect, f"{case['id']}: guard fixture should be clean in both arms"


def test_both_arms_receive_the_identical_schedule():
    """§5 requires the same scenario, same delayed tool and same interruption in both modes."""
    from config import LabConfig

    cfg = LabConfig()
    for case in CASES:
        a = plan_turns(case, cfg)
        b = plan_turns(case, cfg)
        assert a == b, f"{case['id']}: schedule differs between runs"


def test_simulation_is_bit_reproducible():
    for case in CASES:
        first = run_case(case, Policy.SAFE).to_dict()
        second = run_case(case, Policy.SAFE).to_dict()
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_corpus_covers_every_required_scenario():
    """§10 coverage list, asserted against the fixture tags so the corpus cannot rot."""
    tags = {t for c in CASES for t in c.get("tags", [])}
    required = {
        "parameter_correction",
        "time_correction",
        "destination_correction",
        "cancellation",
        "mid_sentence_interruption",
        "late_old_tool_result",
        "double_interruption",
        "false_or_background_speech",
    }
    missing = required - tags
    assert not missing, f"corpus is missing §10 coverage: {sorted(missing)}"


class TestAcceptanceTestClauses:
    """§19, clause by clause, on the canonical fixture."""

    @pytest.fixture()
    def safe_run(self):
        case = next(c for c in CASES if c["id"] == "late_result_01")
        return run_case(case, Policy.SAFE)

    @pytest.fixture()
    def speaking_run(self):
        case = next(c for c in CASES if c["id"] == "booking_01")
        return run_case(case, Policy.SAFE)

    def test_1_obsolete_rime_audio_stops_promptly(self, speaking_run):
        # Asserted on the serialized events, because that exact shape is what the browser
        # timeline renders — the UI cannot be trusted to be right if it reads a different
        # structure than the one the kernel measured.
        ev = next(e for e in speaking_run.events if e["kind"] == "speech_cancelled")
        assert ev["data"]["stop_latency_ms"] <= 100.0, "modelled clear round trip; see assumptions"

    def test_2_and_3_old_request_invalidated_new_one_active(self, safe_run):
        invalid = [e for e in safe_run.events if e["kind"] == "request_invalidated"]
        assert invalid and invalid[0]["request_id"] == 1, "A must be declared obsolete"
        created = [e for e in safe_run.events if e["kind"] == "request_created"]
        assert [e["request_id"] for e in created] == [1, 2], "A then B must be created"
        assert safe_run.metrics["final_state_accuracy"]["value"] == 1.0

    def test_4_late_result_from_a_is_not_applied(self, safe_run):
        stale = [
            e
            for e in safe_run.events
            if e["kind"] == "tool_returned" and e["data"].get("verdict") == "discarded"
        ]
        assert len(stale) == 1
        assert stale[0]["request_id"] == 1
        assert safe_run.final_state["party_size"] == 4

    def test_5_and_6_result_b_accepted_and_spoken(self, safe_run):
        accepted = [
            e for e in safe_run.events if e["kind"] == "tool_returned" and e["data"].get("verdict") == "accepted"
        ]
        assert [e["request_id"] for e in accepted] == [2]
        speech = [e for e in safe_run.events if e["kind"] == "speech_started"]
        assert [e["request_id"] for e in speech] == [2], "only B may be spoken"

    def test_7_event_log_proves_the_sequence(self, safe_run):
        labels = safe_run.timeline
        wanted = ["DISCARDED", "ACCEPTED"]
        joined = " | ".join(labels)
        for token in wanted:
            assert token in joined
        invalidated_at = next(line for line in labels if "INVALIDATED" in line)
        discarded_at = next(line for line in labels if "RETURNED → DISCARDED" in line)
        assert labels.index(invalidated_at) < labels.index(discarded_at), (
            "the fence must reject the late result *after* declaring the request obsolete"
        )

    def test_8_recovery_and_stale_outcome_recorded(self, safe_run):
        assert safe_run.metrics["recovery_latency"]["samples"], "recovery latency must be measured"
        assert safe_run.metrics["stale_output_rate"]["numerator"] == 0
        assert safe_run.metrics["stale_session_rate"]["value"] == 0.0
        assert safe_run.metrics["fencing_correctness"]["value"] == 1.0

    def test_naive_arm_fails_clause_4_on_the_same_fixture(self):
        case = next(c for c in CASES if c["id"] == "late_result_01")
        res = run_case(case, Policy.NAIVE)
        assert res.final_state["party_size"] == 2, "naive must show the clobber"
        assert res.metrics["stale_output_rate"]["numerator"] > 0
        assert res.metrics["fencing_correctness"]["value"] == 0.0


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_no_metric_is_invented(case):
    """Every reported latency figure must be derivable from the event list in the same
    result file. If a number cannot be recomputed from the timeline, it does not ship."""
    for policy in (Policy.SAFE, Policy.NAIVE):
        res = run_case(case, policy)
        stops = res.metrics["interruption_stop_latency"].get("samples", [])
        recorded = [
            round(e["data"]["stop_latency_ms"], 3)
            for e in res.events
            if e["kind"] == "speech_cancelled" and "stop_latency_ms" in e["data"]
        ]
        assert stops == recorded, f"{case['id']}/{policy.value}: stop latency not derived from events"
        rec = res.metrics["recovery_latency"].get("samples", [])
        for value in rec:
            assert any(
                e["kind"] == "interruption_detected" for e in res.events
            ), "recovery reported without a detected interruption"
            assert value > 0, "recovery latency must be a positive delta"


class TestSecretHygiene:
    """§22: 'No secrets are committed.' Cheap to enforce, embarrassing to miss."""

    SUSPICIOUS = re.compile(
        r"(?:sk-[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{8,}|api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,})"
    )
    SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "__pycache__", "results"}

    def test_tracked_source_files_contain_no_key_shapes(self):
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or any(part in self.SKIP_DIRS for part in path.parts):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".json", ".md", ".sh", ".toml", ".yaml", ".yml", ".example", ".env"}:
                continue
            text = path.read_text(errors="ignore")
            for m in self.SUSPICIOUS.finditer(text):
                # placeholders in docs are fine; anything that is not obviously a
                # placeholder is not.
                if "YOUR_" in text[max(0, m.start() - 40): m.start() + 60] or "<" in m.group(0):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {m.group(0)[:32]}")
        assert not offenders, f"possible secrets committed: {offenders}"

    def test_env_example_placeholders_for_every_credential(self):
        """The invariant is narrow and checkable: anything that *is* a secret-shaped key
        must be empty or an obvious placeholder. Config-shaped keys may carry defaults."""
        env = (ROOT / ".env.example").read_text()
        secret_keys = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)$")
        for line in env.splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if secret_keys.search(key.strip()):
                assert value == "" or re.search(r"(your[-_]|REPLACE|<|\.\.\.)", value, re.I), (
                    f".env.example {key} must be empty or a placeholder, not {value!r}"
                )
        assert "RIME_API_KEY=" in env and "LIVEKIT_API_SECRET=" in env

    @pytest.mark.parametrize("doc", ["README.md", "RIME_EVIDENCE.md"])
    def test_docs_do_not_pin_a_stale_run_id(self, doc: str):
        """A run_id pasted into prose goes stale the moment anyone re-runs the evaluation.

        Cite `evaluation/results/latest.json` (which always carries the current handle), or
        cite the exact run_id that file holds — never an older one.
        """
        latest = json.loads((ROOT / "evaluation/results/latest.json").read_text())
        current = latest["run_id"]
        for cited in set(re.findall(r"\b\d{8}T\d{6}Z-(?:simulated|live)\b", (ROOT / doc).read_text())):
            assert cited == current, f"{doc} cites run_id {cited}, but latest.json holds {current}"

    def test_readme_quotes_no_number_absent_from_results(self):
        """§12: unverified numbers get no credit. The README may only cite latency figures
        that appear in a committed results file."""
        readme = (ROOT / "README.md").read_text() if (ROOT / "README.md").exists() else ""
        blob = json.dumps(json.loads((ROOT / "evaluation/results/latest.json").read_text()))
        cited = set(re.findall(r"\b(\d{2,5})(?:\.\d+)?\s*ms\b", readme))
        for num in cited:
            assert num in blob, (
                f"README cites {num} ms, which appears nowhere in evaluation/results/latest.json. "
                "Quote numbers a run produced, or label them as assumptions (spec §12)."
            )
        assert cited, "README stopped citing any measured figures: the guard would be vacuous"
