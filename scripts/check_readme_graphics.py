"""README graphics preservation check.

Milestone 2 must preserve the README's existing architectural graphics
*byte-for-byte*: the Milestone-1 ingestion workflow (an ASCII diagram) and the
three Mermaid diagrams (enterprise architecture, dimensional star schema, Airflow
orchestration). This script fingerprints every fenced code block in the README and
asserts that each protected block from the recorded baseline still appears,
unchanged, in the current file.

Usage::

    # Record the baseline (run once, before editing the README):
    python scripts/check_readme_graphics.py --record

    # Verify nothing protected has changed (CI + pre-commit):
    python scripts/check_readme_graphics.py

Protected blocks are identified by stable content signatures so the check is
insensitive to where in the document a block appears — only its exact bytes
matter. A newly added, recognized block (e.g. the Milestone-2 diagram) is folded
into the baseline on the next ``--record``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
BASELINE = REPO_ROOT / "docs" / "readme_graphics_baseline.json"

# Content signatures -> friendly name for each protected graphic. A block is
# "protected" iff its body contains the signature substring. Order matters only
# for naming; hashes are what is enforced.
SIGNATURES: list[tuple[str, str]] = [
    ("USAspending bulk_download API", "milestone_1_ingestion_workflow"),
    ("AWS S3 data lake", "enterprise_system_architecture"),
    ("erDiagram", "dimensional_warehouse_star_schema"),
    ("Check source freshness", "apache_airflow_orchestration"),
    ("Partitioned Parquet Data Lake", "milestone_2_historical_architecture"),
    ("Storage Abstraction Layer", "milestone_3_s3_cloud_architecture"),
]

_FENCE = re.compile(r"(^```[^\n]*\n.*?^```)", re.MULTILINE | re.DOTALL)


def extract_blocks(md_text: str) -> list[str]:
    """Return every fenced code block, including its opening/closing fences."""
    return [m.group(1) for m in _FENCE.finditer(md_text)]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def protected_blocks(md_text: str) -> dict[str, str]:
    """Map friendly name -> SHA-256 for each recognized protected graphic."""
    found: dict[str, str] = {}
    for block in extract_blocks(md_text):
        for signature, name in SIGNATURES:
            if signature in block:
                found[name] = sha256(block)
                break
    return found


def record() -> int:
    md = README.read_text(encoding="utf-8")
    blocks = protected_blocks(md)
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(blocks, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Recorded {len(blocks)} protected graphic(s) to {BASELINE.relative_to(REPO_ROOT)}:")
    for name, digest in sorted(blocks.items()):
        print(f"  {name:40s} {digest}")
    return 0


def verify() -> int:
    if not BASELINE.exists():
        print(f"ERROR: baseline not found at {BASELINE}. Run with --record first.", file=sys.stderr)
        return 2
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = protected_blocks(README.read_text(encoding="utf-8"))

    ok = True
    print("README graphics preservation check")
    print("=" * 70)
    for name, expected in sorted(baseline.items()):
        actual = current.get(name)
        if actual == expected:
            status = "PRESERVED"
        elif actual is None:
            status, ok = "MISSING", False
        else:
            status, ok = "CHANGED", False
        print(f"  [{status:9s}] {name}")
        if actual is not None and actual != expected:
            print(f"      expected {expected}")
            print(f"      actual   {actual}")
    # Any newly added, recognized graphic not yet in the baseline is informational.
    for name in sorted(set(current) - set(baseline)):
        print(f"  [NEW      ] {name} ({current[name]})  -- run --record to adopt")
    print("=" * 70)
    print("RESULT:", "ALL PROTECTED GRAPHICS PRESERVED" if ok else "GRAPHICS REGRESSION DETECTED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="README graphics preservation check.")
    parser.add_argument("--record", action="store_true", help="Record the current baseline.")
    args = parser.parse_args(argv)
    return record() if args.record else verify()


if __name__ == "__main__":
    raise SystemExit(main())
