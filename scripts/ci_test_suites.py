"""Run one explicit CI suite, rejecting missing/duplicate test assignments and skips."""

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree


def load_suites(root: Path, manifest: Path) -> dict[str, list[str]]:
    suites = json.loads(manifest.read_text())
    if not isinstance(suites, dict) or not suites:
        raise ValueError("Expected a nonempty suite manifest")
    for name, files in suites.items():
        if not isinstance(name, str) or not isinstance(files, list) or not files:
            raise ValueError("Each suite must contain test paths")
        if any(not isinstance(f, str) for f in files):
            raise ValueError("Test paths must be strings")
    assigned = Counter(f for files in suites.values() for f in files)
    discovered = {str(p.relative_to(root)) for p in (root / "tests").rglob("test_*.py")}
    missing = discovered - assigned.keys()
    stale = assigned.keys() - discovered
    duplicate = {f for f, count in assigned.items() if count != 1}
    if missing or stale or duplicate:
        raise ValueError(
            f"Invalid suite partition: missing={sorted(missing)}, "
            f"stale={sorted(stale)}, duplicate={sorted(duplicate)}"
        )
    return suites


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite")
    parser.add_argument("--manifest", type=Path, default=Path("configs/test-suites.json"))
    parser.add_argument("--report-dir", type=Path, default=Path("test-results"))
    args = parser.parse_args()
    suites = load_suites(Path.cwd(), args.manifest)
    if args.suite is None:
        print(json.dumps({s: len(files) for s, files in suites.items()}, indent=2))
        return 0
    if args.suite not in suites:
        parser.error(f"Unknown suite: {args.suite}")
    report = args.report_dir / f"{args.suite}.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", f"--junitxml={report}", *suites[args.suite]],
        check=False,
    )
    if result.returncode:
        return result.returncode
    if ElementTree.parse(report).findall(".//skipped"):
        print("CI requires zero skipped tests; check TEST_DATABASE_URL", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
