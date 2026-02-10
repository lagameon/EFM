"""Tests for config_presets module."""

import json
import pytest
from pathlib import Path

from lib.config_presets import (
    PRESETS,
    VALID_PRESET_NAMES,
    _deep_merge,
    describe_preset,
    load_config,
    resolve_config,
)


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_flat_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        assert _deep_merge(base, override) == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 99}}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 99}}

    def test_override_replaces_non_dict(self):
        base = {"x": {"a": 1}}
        override = {"x": "flat"}
        assert _deep_merge(base, override) == {"x": "flat"}

    def test_override_adds_nested(self):
        base = {"x": {"a": 1}}
        override = {"x": {"a": 1, "b": 2}, "y": 3}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 2}, "y": 3}

    def test_neither_mutated(self):
        base = {"x": {"a": 1}}
        override = {"x": {"b": 2}}
        _deep_merge(base, override)
        assert base == {"x": {"a": 1}}
        assert override == {"x": {"b": 2}}

    def test_empty_base(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}

    def test_empty_override(self):
        assert _deep_merge({"a": 1}, {}) == {"a": 1}

    def test_both_empty(self):
        assert _deep_merge({}, {}) == {}


# ---------------------------------------------------------------------------
# resolve_config
# ---------------------------------------------------------------------------


class TestResolveConfig:
    def test_no_preset_returns_raw(self):
        raw = {"version": "1.5", "automation": {"human_review_required": True}}
        result = resolve_config(raw)
        assert result is raw  # should be identity when no preset

    def test_preset_none_returns_raw(self):
        raw = {"preset": None, "version": "1.5"}
        result = resolve_config(raw)
        assert result is raw

    def test_preset_empty_string_returns_raw(self):
        raw = {"preset": "", "version": "1.5"}
        result = resolve_config(raw)
        assert result is raw

    def test_invalid_preset_raises(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            resolve_config({"preset": "turbo"})

    def test_minimal_preset_defaults(self):
        result = resolve_config({"preset": "minimal"})
        assert result["embedding"]["enabled"] is False
        assert result["reasoning"]["enabled"] is False
        assert result["automation"]["human_review_required"] is True
        assert result["automation"]["pipeline_steps"] == ["generate_rules"]
        assert result["v3"]["auto_harvest_on_stop"] is False

    def test_standard_preset_defaults(self):
        result = resolve_config({"preset": "standard"})
        assert result["automation"]["human_review_required"] is False
        assert result["v3"]["auto_harvest_on_stop"] is True
        assert "sync_embeddings" in result["automation"]["pipeline_steps"]
        assert "generate_rules" in result["automation"]["pipeline_steps"]

    def test_full_preset_defaults(self):
        result = resolve_config({"preset": "full"})
        assert result["embedding"]["enabled"] is True
        assert result["reasoning"]["enabled"] is True
        assert "evolution_check" in result["automation"]["pipeline_steps"]

    def test_explicit_override_wins(self):
        """User setting always beats preset default."""
        raw = {
            "preset": "standard",
            "automation": {"human_review_required": True},  # override standard's False
        }
        result = resolve_config(raw)
        assert result["automation"]["human_review_required"] is True

    def test_nested_override_preserves_preset_siblings(self):
        """Override one nested key, keep others from preset."""
        raw = {
            "preset": "full",
            "automation": {"human_review_required": True},
        }
        result = resolve_config(raw)
        # Override wins
        assert result["automation"]["human_review_required"] is True
        # Preset sibling preserved
        assert result["automation"]["dedup_check_on_capture"] is True

    def test_extra_fields_preserved(self):
        """Fields not in the preset pass through unchanged."""
        raw = {
            "preset": "minimal",
            "version": "1.5",
            "paths": {"CODE_ROOTS": ["src/"]},
        }
        result = resolve_config(raw)
        assert result["version"] == "1.5"
        assert result["paths"]["CODE_ROOTS"] == ["src/"]

    def test_preset_field_preserved(self):
        result = resolve_config({"preset": "standard"})
        assert result["preset"] == "standard"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_config(tmp_path / "nonexistent.json")
        assert result == {}

    def test_invalid_json_returns_empty(self, tmp_path):
        bad = tmp_path / "config.json"
        bad.write_text("not json{{{")
        result = load_config(bad)
        assert result == {}

    def test_loads_and_resolves(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"preset": "minimal", "version": "1.5"}))
        result = load_config(cfg)
        assert result["embedding"]["enabled"] is False
        assert result["version"] == "1.5"

    def test_loads_without_preset(self, tmp_path):
        """Backward compat: config without preset key."""
        cfg = tmp_path / "config.json"
        data = {"version": "1.5", "automation": {"human_review_required": True}}
        cfg.write_text(json.dumps(data))
        result = load_config(cfg)
        assert result == data


# ---------------------------------------------------------------------------
# describe_preset
# ---------------------------------------------------------------------------


class TestDescribePreset:
    def test_known_presets(self):
        for name in VALID_PRESET_NAMES:
            desc = describe_preset(name)
            assert isinstance(desc, str)
            assert len(desc) > 5

    def test_unknown_returns_custom(self):
        assert describe_preset("unknown") == "custom configuration"


# ---------------------------------------------------------------------------
# Preset completeness
# ---------------------------------------------------------------------------


class TestPresetCompleteness:
    def test_all_presets_have_embedding(self):
        for name, preset in PRESETS.items():
            assert "embedding" in preset, f"Preset '{name}' missing 'embedding'"

    def test_all_presets_have_automation(self):
        for name, preset in PRESETS.items():
            assert "automation" in preset, f"Preset '{name}' missing 'automation'"

    def test_all_presets_have_v3(self):
        for name, preset in PRESETS.items():
            assert "v3" in preset, f"Preset '{name}' missing 'v3'"

    def test_valid_preset_names_matches(self):
        assert VALID_PRESET_NAMES == frozenset(PRESETS.keys())


# ---------------------------------------------------------------------------
# Preset compaction
# ---------------------------------------------------------------------------


class TestPresetCompaction:
    def test_all_presets_have_compaction(self):
        """Every preset must include a compaction section."""
        for name, preset in PRESETS.items():
            assert "compaction" in preset, f"Preset '{name}' missing compaction config"
            assert "auto_suggest_threshold" in preset["compaction"], \
                f"Preset '{name}' missing auto_suggest_threshold"

    def test_compaction_thresholds_ordered(self):
        """Minimal < standard < full for compaction threshold."""
        assert PRESETS["minimal"]["compaction"]["auto_suggest_threshold"] < \
            PRESETS["standard"]["compaction"]["auto_suggest_threshold"]
        assert PRESETS["standard"]["compaction"]["auto_suggest_threshold"] < \
            PRESETS["full"]["compaction"]["auto_suggest_threshold"]
