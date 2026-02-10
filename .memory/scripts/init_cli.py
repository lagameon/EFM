#!/usr/bin/env python3
"""
EF Memory V3 — Project Init CLI

Initialize EF Memory auto-startup for a project. Generates CLAUDE.md,
.claude/rules/, hooks.json, and settings.local.json.

Usage:
    python3 .memory/scripts/init_cli.py                    # Init current project
    python3 .memory/scripts/init_cli.py --preset standard   # Init with preset
    python3 .memory/scripts/init_cli.py --dry-run           # Preview without writing
    python3 .memory/scripts/init_cli.py --force             # Overwrite existing EF Memory sections
    python3 .memory/scripts/init_cli.py --upgrade           # Upgrade hooks/rules without overwriting user content
    python3 .memory/scripts/init_cli.py --target /path/to   # Init a different project
    python3 .memory/scripts/init_cli.py --help              # Show help

Presets:
    minimal   — Human review ON, no embeddings, basic rules only (try EFM)
    standard  — Auto-harvest ON, human review OFF, sync + rules (most projects)
    full      — All features including embeddings + LLM reasoning (requires API keys)
"""

import json
import logging
import sys
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR))

from lib.config_presets import VALID_PRESET_NAMES, describe_preset, load_config
from lib.init import run_init


def _parse_args(argv: list) -> dict:
    """Simple argument parser."""
    args = {
        "dry_run": False,
        "force": False,
        "upgrade": False,
        "target": None,
        "preset": None,
        "help": False,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            args["help"] = True
        elif arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--force":
            args["force"] = True
        elif arg == "--upgrade":
            args["upgrade"] = True
        elif arg == "--target":
            if i + 1 < len(argv):
                args["target"] = argv[i + 1]
                i += 1
            else:
                print("ERROR: --target requires a path argument")
                sys.exit(1)
        elif arg.startswith("--target="):
            args["target"] = arg.split("=", 1)[1]
        elif arg == "--preset":
            if i + 1 < len(argv):
                args["preset"] = argv[i + 1]
                i += 1
            else:
                print("ERROR: --preset requires a value (minimal|standard|full)")
                sys.exit(1)
        elif arg.startswith("--preset="):
            args["preset"] = arg.split("=", 1)[1]
        elif arg.startswith("--"):
            print(f"ERROR: Unknown option: {arg}")
            sys.exit(1)
        i += 1
    return args


def _print_report(report, preset_name=None):
    """Print the init report."""
    mode = "[DRY RUN] " if report.dry_run else ""

    if report.files_created:
        print(f"\n{mode}Created:")
        for f in report.files_created:
            print(f"  + {f}")

    if report.files_merged:
        print(f"\n{mode}Merged:")
        for f in report.files_merged:
            print(f"  ~ {f}")

    if report.files_skipped:
        print(f"\n{mode}Skipped (already exists):")
        for f in report.files_skipped:
            print(f"  - {f}")

    if report.warnings:
        print(f"\nWarnings:")
        for w in report.warnings:
            print(f"  ! {w}")

    if report.suggestions:
        print(f"\nSuggestions:")
        for s in report.suggestions:
            print(f"  > {s}")

    total = len(report.files_created) + len(report.files_merged) + len(report.files_skipped)
    print(f"\n{mode}Done ({total} files processed, {report.duration_ms:.0f}ms)")

    # Getting Started section
    print("\nGetting Started:")
    print("  Core commands:")
    print("    /memory-search <query>  — Find relevant project knowledge")
    print("    /memory-save            — Capture a lesson / decision / constraint")
    print("    /memory-plan            — Start a working memory session")
    if preset_name:
        print(f"  Preset: {preset_name} ({describe_preset(preset_name)})")
        print(f'  Change: edit .memory/config.json → "preset": "minimal"|"standard"|"full"')
    else:
        print('  Tip: use --preset standard|minimal|full for quick config')
    print("  All commands: see CLAUDE.md or /memory-init --help")


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    args = _parse_args(sys.argv[1:])

    if args["help"]:
        print(__doc__.strip())
        sys.exit(0)

    if args["force"] and args["upgrade"]:
        print("ERROR: --force and --upgrade are mutually exclusive")
        sys.exit(1)

    # Resolve project root
    if args["target"]:
        project_root = Path(args["target"]).resolve()
        if not project_root.is_dir():
            print(f"ERROR: Target directory does not exist: {project_root}")
            sys.exit(1)
    else:
        project_root = _MEMORY_DIR.parent

    # Load config
    config_path = _MEMORY_DIR / "config.json"
    preset_name = args["preset"]

    # Validate preset name early
    if preset_name and preset_name not in VALID_PRESET_NAMES:
        print(f"ERROR: Unknown preset '{preset_name}'. "
              f"Valid presets: {', '.join(sorted(VALID_PRESET_NAMES))}")
        sys.exit(1)

    # Apply --preset: write preset into config.json before loading
    if preset_name and not args["dry_run"]:
        try:
            if config_path.exists():
                raw = json.loads(config_path.read_text())
            else:
                raw = {"version": 3}
            raw["preset"] = preset_name
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(raw, indent=2) + "\n")
            print(f"Preset '{preset_name}' saved to config.json")
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: Could not update config.json with preset: {exc}")

    # Load config (with preset resolution)
    config = load_config(config_path)

    # Track effective preset name for report
    if not preset_name:
        preset_name = config.get("preset")

    if args["upgrade"]:
        from lib.init import run_upgrade
        print(f"EF Memory Upgrade — {project_root}")
        if args["dry_run"]:
            print("(dry run — no files will be written)")
        report = run_upgrade(
            project_root=project_root,
            config=config,
            dry_run=args["dry_run"],
        )
        _print_report(report, preset_name=preset_name)
        return

    # Run init
    print(f"EF Memory Init — {project_root}")
    if args["dry_run"]:
        print("(dry run — no files will be written)")

    report = run_init(
        project_root=project_root,
        config=config,
        force=args["force"],
        dry_run=args["dry_run"],
    )

    _print_report(report, preset_name=preset_name)


if __name__ == "__main__":
    main()
