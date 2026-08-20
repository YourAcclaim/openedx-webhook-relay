"""
Enforce a minimum coverage percentage for every module, not just the total.

The aggregate ``--cov-fail-under`` gate can be comfortably green while one
module is barely tested: this project sat at 94% overall while ``security.py``
-- the code deciding which payload fields leave the LMS -- was at 72%. A total
hides exactly the module you would most want covered.

Usage::

    pytest --cov=openedx_webhook_relay --cov-report=json
    python scripts/check_coverage_floor.py --min 90 coverage.json
"""

import argparse
import json
import sys


def main(argv=None):
    """Report any module below the floor and exit non-zero if there are any."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="Path to a coverage JSON report.")
    parser.add_argument(
        "--min",
        type=float,
        default=90.0,
        dest="minimum",
        help="Minimum percent covered required of each module (default: 90).",
    )
    args = parser.parse_args(argv)

    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)

    files = report.get("files", {})
    if not files:
        print(f"{args.report}: no files in report — did coverage run?", file=sys.stderr)
        return 1

    below = sorted(
        (data["summary"]["percent_covered"], name)
        for name, data in files.items()
        if data["summary"]["percent_covered"] < args.minimum
    )

    for percent, name in below:
        print(f"FAIL {name}: {percent:.0f}% is below the {args.minimum:.0f}% floor")

    if below:
        print(
            f"\n{len(below)} module(s) below the per-module floor. Raise their coverage, "
            "or lower --min deliberately if the gap is genuinely acceptable.",
            file=sys.stderr,
        )
        return 1

    worst = min(
        (data["summary"]["percent_covered"] for data in files.values()),
        default=100.0,
    )
    print(
        f"All {len(files)} module(s) meet the {args.minimum:.0f}% floor "
        f"(lowest: {worst:.0f}%)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
