#!/usr/bin/env python3
"""
EF Memory — Deployment Version Check

Compares EFM library versions across deployed projects to detect drift.
Usage: python3 .memory/scripts/deploy_check.py [--projects /path1 /path2 ...]

If no --projects specified, scans ~/Projects/ for .memory/ directories.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

# Key files that indicate EFM version
KEY_LIB_FILES = [
    "search.py", "scanner.py", "working_memory.py",
    "auto_sync.py", "init.py", "evolution.py",
    "compaction.py", "vectordb.py", "config_presets.py",
    "events_io.py", "reasoning.py",
]

KEY_DIRS = ["lib", "scripts", "hooks", "tests"]


def md5_file(path: Path) -> str:
    """Compute MD5 of a file."""
    if not path.exists():
        return "MISSING"
    return hashlib.md5(path.read_bytes()).hexdigest()


def get_efm_fingerprint(memory_dir: Path) -> dict:
    """Get version fingerprint for an EFM installation."""
    result = {
        "path": str(memory_dir),
        "lib_hashes": {},
        "test_count": 0,
        "config_version": None,
        "schema_size": 0,
    }

    # Hash key lib files
    lib_dir = memory_dir / "lib"
    for fname in KEY_LIB_FILES:
        result["lib_hashes"][fname] = md5_file(lib_dir / fname)

    # Count tests
    test_dir = memory_dir / "tests"
    if test_dir.exists():
        result["test_count"] = len(list(test_dir.glob("test_*.py")))

    # Config version
    config_path = memory_dir / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            result["config_version"] = cfg.get("version")
        except Exception:
            pass

    # Schema size (proxy for version)
    schema_path = memory_dir / "config.schema.json"
    if schema_path.exists():
        result["schema_size"] = schema_path.stat().st_size

    return result


def find_efm_projects(base_dir: Path, exclude_source: bool = False) -> list[Path]:
    """Find all directories containing .memory/ under base_dir."""
    results = []
    for d in sorted(base_dir.iterdir()):
        if not d.is_dir():
            continue
        memory_dir = d / ".memory"
        if memory_dir.exists() and (memory_dir / "lib").exists():
            if exclude_source and d.name in ("EFM", "EF-Memory-for-Claude"):
                continue
            results.append(memory_dir)
    return results


def compare_installations(source_fp: dict, target_fp: dict) -> dict:
    """Compare source fingerprint with target, return drift info."""
    drift = {
        "matching": [],
        "different": [],
        "missing_in_target": [],
    }
    for fname, src_hash in source_fp["lib_hashes"].items():
        tgt_hash = target_fp["lib_hashes"].get(fname, "MISSING")
        if tgt_hash == "MISSING":
            drift["missing_in_target"].append(fname)
        elif tgt_hash == src_hash:
            drift["matching"].append(fname)
        else:
            drift["different"].append(fname)

    drift["test_count_match"] = source_fp["test_count"] == target_fp["test_count"]
    drift["schema_match"] = source_fp["schema_size"] == target_fp["schema_size"]
    drift["is_current"] = (
        len(drift["different"]) == 0
        and len(drift["missing_in_target"]) == 0
        and drift["test_count_match"]
        and drift["schema_match"]
    )
    return drift


def main():
    parser = argparse.ArgumentParser(description="Check EFM deployment versions")
    parser.add_argument(
        "--projects", nargs="*",
        help="Specific project .memory paths to check",
    )
    parser.add_argument(
        "--base", default=str(Path.home() / "Projects"),
        help="Base directory to scan (default: ~/Projects)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Find source repo (support both old and new directory names)
    base = Path(args.base)
    source_dir = base / "EFM" / ".memory"
    if not source_dir.exists():
        source_dir = base / "EF-Memory-for-Claude" / ".memory"
    if not source_dir.exists():
        print(f"ERROR: Source repo not found at {base}/EFM or {base}/EF-Memory-for-Claude", file=sys.stderr)
        sys.exit(1)

    source_fp = get_efm_fingerprint(source_dir)

    # Find target projects
    if args.projects:
        targets = [Path(p) for p in args.projects]
    else:
        targets = find_efm_projects(base, exclude_source=True)

    if not targets:
        print("No EFM deployments found.")
        sys.exit(0)

    # Compare
    report = {
        "timestamp": datetime.now().isoformat(),
        "source": str(source_dir),
        "deployments": [],
    }

    for target_dir in targets:
        target_fp = get_efm_fingerprint(target_dir)
        drift = compare_installations(source_fp, target_fp)
        project_name = target_dir.parent.name

        entry = {
            "project": project_name,
            "path": str(target_dir),
            "is_current": drift["is_current"],
            "different_files": drift["different"],
            "missing_files": drift["missing_in_target"],
            "test_files": target_fp["test_count"],
            "source_test_files": source_fp["test_count"],
        }
        report["deployments"].append(entry)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"EFM Deployment Check — {report['timestamp']}")
        print(f"Source: {report['source']}")
        print(f"{'=' * 60}")
        for dep in report["deployments"]:
            status = "✅ CURRENT" if dep["is_current"] else "⚠️  OUTDATED"
            print(f"\n{dep['project']}: {status}")
            print(f"  Path: {dep['path']}")
            print(f"  Test files: {dep['test_files']}/{dep['source_test_files']}")
            if dep["different_files"]:
                print(f"  Drift: {', '.join(dep['different_files'])}")
            if dep["missing_files"]:
                print(f"  Missing: {', '.join(dep['missing_files'])}")

        all_current = all(d["is_current"] for d in report["deployments"])
        print(f"\n{'=' * 60}")
        if all_current:
            print("All deployments are current. ✅")
        else:
            outdated = [d["project"] for d in report["deployments"] if not d["is_current"]]
            print(f"Outdated: {', '.join(outdated)}")


if __name__ == "__main__":
    main()
