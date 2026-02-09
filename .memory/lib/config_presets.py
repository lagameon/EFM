"""
EF Memory — Configuration Presets & Loader

Provides three named presets (minimal, standard, full) that set sensible
defaults for different usage profiles.  Individual settings in config.json
always override preset defaults (deep merge, user wins).

Usage:
    from .config_presets import load_config
    config = load_config(config_path)

Or for in-process resolution:
    from .config_presets import resolve_config
    config = resolve_config(raw_config)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("efm.config_presets")

# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

PRESETS: Dict[str, Dict[str, Any]] = {
    "minimal": {
        # Try EF Memory with least friction.
        # Human review ON, no embeddings, no reasoning, pipeline = rules only.
        "embedding": {"enabled": False},
        "reasoning": {"enabled": False},
        "automation": {
            "human_review_required": True,
            "pipeline_steps": ["generate_rules"],
            "dedup_check_on_capture": True,
        },
        "v3": {
            "auto_startup": True,
            "auto_start_on_plan": True,
            "auto_harvest_on_stop": False,
            "auto_draft_from_conversation": True,
            "prefill_on_plan_start": True,
        },
    },
    "standard": {
        # Best for most projects.
        # Human review OFF, auto-harvest ON, pipeline = sync + rules.
        "embedding": {"enabled": False},
        "reasoning": {"enabled": False},
        "automation": {
            "human_review_required": False,
            "pipeline_steps": ["sync_embeddings", "generate_rules"],
            "dedup_check_on_capture": True,
        },
        "v3": {
            "auto_startup": True,
            "auto_start_on_plan": True,
            "auto_harvest_on_stop": True,
            "auto_draft_from_conversation": True,
            "prefill_on_plan_start": True,
        },
    },
    "full": {
        # All features enabled — requires embedding + LLM API keys.
        "embedding": {"enabled": True},
        "reasoning": {"enabled": True},
        "automation": {
            "human_review_required": False,
            "pipeline_steps": [
                "sync_embeddings",
                "generate_rules",
                "evolution_check",
                "reasoning_check",
            ],
            "dedup_check_on_capture": True,
        },
        "v3": {
            "auto_startup": True,
            "auto_start_on_plan": True,
            "auto_harvest_on_stop": True,
            "auto_draft_from_conversation": True,
            "prefill_on_plan_start": True,
        },
    },
}

VALID_PRESET_NAMES = frozenset(PRESETS.keys())


# ---------------------------------------------------------------------------
# Deep merge helper
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*.

    - Dict values are merged recursively.
    - All other types in *override* replace *base*.
    - Neither input is mutated; returns a new dict.
    """
    merged: Dict[str, Any] = {}
    all_keys = set(base) | set(override)
    for key in all_keys:
        if key in override and key in base:
            bv, ov = base[key], override[key]
            if isinstance(bv, dict) and isinstance(ov, dict):
                merged[key] = _deep_merge(bv, ov)
            else:
                merged[key] = ov  # override wins
        elif key in override:
            merged[key] = override[key]
        else:
            merged[key] = base[key]
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_config(raw_config: dict) -> dict:
    """Merge preset defaults under explicit user overrides.

    Algorithm:
      1. If ``raw_config`` has a ``"preset"`` key, load that preset's defaults.
      2. Deep-merge ``raw_config`` on top (user settings always win).
      3. Return the merged dict.

    If ``"preset"`` is absent or ``None``, return ``raw_config`` unchanged
    (backward compatible).

    Raises ``ValueError`` for unknown preset names.
    """
    preset_name = raw_config.get("preset")
    if not preset_name:
        return raw_config

    if preset_name not in VALID_PRESET_NAMES:
        raise ValueError(
            f"Unknown preset '{preset_name}'. "
            f"Valid presets: {', '.join(sorted(VALID_PRESET_NAMES))}"
        )

    preset_defaults = PRESETS[preset_name]
    # Preset is the base; user config is the override (user wins)
    merged = _deep_merge(preset_defaults, raw_config)
    return merged


def load_config(config_path: Path) -> dict:
    """Load ``config.json``, resolve presets, and return the merged dict.

    If the file doesn't exist or can't be parsed, returns ``{}``.
    """
    if not config_path.exists():
        return {}
    try:
        raw = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not parse %s: %s", config_path, exc)
        return {}
    return resolve_config(raw)


def describe_preset(name: str) -> str:
    """Return a one-line human description of a preset."""
    descriptions = {
        "minimal": "human review on, no embeddings, basic rules only",
        "standard": "auto-harvest on, human review off, sync + rules",
        "full": "all features including embeddings + LLM reasoning",
    }
    return descriptions.get(name, "custom configuration")
