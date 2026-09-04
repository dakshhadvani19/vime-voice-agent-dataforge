"""Rime/LiveKit configuration validation.

Rime documents that an unsupported speaker/modelId/lang triple "is not reliably rejected"
by the API — it can silently serve a different voice. For a measured pipeline that would be
fatal (the config in RIME_EVIDENCE.md would describe speech that did not happen), so the
combination is validated locally at startup and these tests pin that behaviour.
"""

from __future__ import annotations

import pytest
from config import CATALOG, ConfigError, LabConfig, RimeConfig
from providers import NullRime


class TestRimeConfig:
    """Startup validation of the exact provider configuration (spec §2, §21)."""

    def test_default_config_is_valid(self):
        cfg = RimeConfig()
        cfg.validate()
        assert cfg.model in CATALOG
        assert cfg.speaker in CATALOG[cfg.model][cfg.lang]

    def test_mistv3_low_latency_option_is_valid(self):
        RimeConfig(model="mistv3", speaker="astra", lang="en").validate()

    def test_unknown_model_rejected(self):
        with pytest.raises(ConfigError):
            RimeConfig(model="arcana").validate()

    def test_voice_not_published_for_model_is_rejected(self):
        """`cove` is a Mist voice; asking for it on Coda must fail loudly."""
        with pytest.raises(ConfigError):
            RimeConfig(model="coda", speaker="cove", lang="en").validate()

    def test_unsupported_language_rejected(self):
        with pytest.raises(ConfigError):
            RimeConfig(model="coda", speaker="astra", lang="gu").validate()

    def test_bad_segment_rejected(self):
        with pytest.raises(ConfigError):
            RimeConfig(segment="byWord").validate()

    def test_evidence_dict_never_contains_the_api_key(self, monkeypatch):
        """The evidence doc must be shareable. The *value* may never appear; naming the
        environment variable is required, since §2 asks for the exact configuration."""
        monkeypatch.setenv("RIME_API_KEY", "dummy-value-not-a-real-key")
        cfg = RimeConfig()
        blob = str(cfg.as_dict())
        assert "dummy-value-not-a-real-key" not in blob
        assert "api_key" not in cfg.as_dict()
        assert cfg.as_dict()["credential"] == "env:RIME_API_KEY"

    def test_plugin_parameter_names_are_real(self):
        """Guard against invented kwargs, checked against the installed signature.

        Spec §21: "Do not invent APIs, package names, Rime model IDs, voices, or LiveKit
        methods." This test makes that a build failure rather than a code-review hope.
        Skips cleanly when livekit-plugins-rime is not installed (e.g. the eval-only venv).
        """
        import inspect
        import re

        rime = pytest.importorskip("livekit.plugins.rime")
        from providers import build_livekit_tts

        allowed = set(inspect.signature(rime.TTS.__init__).parameters) - {"self"}
        src = inspect.getsource(build_livekit_tts)
        used = set(re.findall(r'"([a-z_]+)"\s*:', src)) | set(re.findall(r'kwargs\["([a-z_]+)"\]', src))
        assert used, "factory no longer passes any named options; evidence docs would be wrong"
        assert used <= allowed, f"invented rime.TTS kwargs: {sorted(used - allowed)}"


def test_config_error_message_names_a_valid_alternative():
    """A rejection that does not say what *would* work just sends the developer to the docs."""
    with pytest.raises(ConfigError) as exc:
        RimeConfig(model="coda", speaker="cove", lang="en").validate()
    msg = str(exc.value)
    assert "cove" in msg and "coda" in msg
    assert any(v in msg for v in CATALOG["coda"]["en"]), "error should suggest real voices"


class TestLabConfig:
    def test_defaults_are_declared_and_typed(self):
        cfg = LabConfig()
        assert cfg.tool_delay_ms > 0
        assert cfg.rime_clear_rtt_ms > 0
        assert cfg.ms_per_word > 0
        assert cfg.publish_topic, "the frontend subscribes to this topic"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TOOL_DELAY_MS", "777")
        assert LabConfig.from_env().tool_delay_ms == 777


class TestNullRime:
    """The offline speech double must obey the same contract as /ws3, or the sim lies."""

    def test_emits_word_timestamps_then_done(self):
        rime = NullRime(ms_per_word=200.0, first_audio_ms=150.0)
        words = rime.synthesize("one two three", context_id="req-1-gen-1", start_ms=1000.0)
        assert [w[0] for w in words] == ["one", "two", "three"]
        assert words[0][1] == pytest.approx(150.0)
        assert rime.events[-1]["type"] == "done"
        assert all(e["contextId"] == "req-1-gen-1" for e in rime.events)

    def test_clear_truncates_instead_of_lying_about_completing(self):
        rime = NullRime()
        rime.clear(at_ms=1100.0)
        words = rime.synthesize("one two three", context_id="req-1-gen-2", start_ms=1000.0)
        assert words == []
        assert rime.events[-1]["type"] == "cleared"

    def test_reset_restores_state(self):
        rime = NullRime()
        rime.clear(at_ms=0.0)
        rime.reset()
        assert rime.synthesize("one", context_id="x", start_ms=0.0)
