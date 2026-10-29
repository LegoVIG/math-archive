"""Math Archive E2E Test Runner.

Executes test tiers (Tiers 1-4) with structured reporting, timing, and exit codes.
Usage:
    python run_tests.py              # Run all tiers (1-4)
    python run_tests.py --tier 1     # Run Tier 1: Isolated Feature Coverage
    python run_tests.py --tier 2     # Run Tier 2: Boundary & Corner Cases
    python run_tests.py --tier 3     # Run Tier 3: Cross-Feature Combinations
    python run_tests.py --tier 4     # Run Tier 4: Real-World Scenarios
    python run_tests.py --json-report results.json  # Save JSON report
"""

import argparse
import importlib
import json
import sys
import time
import unittest
from pathlib import Path

# Ensure UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add tests directory to sys.path
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

TIERS = {
    1: {
        "name": "Tier 1: Feature Coverage (Isolation)",
        "module": "test_tier1_features",
        "min_tests": 110,
    },
    2: {
        "name": "Tier 2: Boundary & Corner Cases",
        "module": "test_tier2_boundaries",
        "min_tests": 110,
    },
    3: {
        "name": "Tier 3: Cross-Feature Combinations",
        "module": "test_tier3_combinations",
        "min_tests": 10,
    },
    4: {
        "name": "Tier 4: Real-World Application Scenarios",
        "module": "test_tier4_scenarios",
        "min_tests": 4,
    },
}


def _filter_suite(suite, pattern):
    new_suite = unittest.TestSuite()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            sub = _filter_suite(item, pattern)
            if sub.countTestCases() > 0:
                new_suite.addTest(sub)
        elif pattern.lower() in item.id().lower():
            new_suite.addTest(item)
    return new_suite


def run_tier(tier_num: int, verbose: bool = False, keyword: str = None):
    """Run a single test tier and return metric dictionary."""
    tier_info = TIERS[tier_num]
    mod_name = tier_info["module"]

    try:
        module = importlib.import_module(mod_name)
    except Exception as e:
        return {
            "tier": tier_num,
            "name": tier_info["name"],
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "skipped": 0,
            "elapsed": 0.0,
            "load_error": str(e),
        }

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(module)
    if keyword:
        suite = _filter_suite(suite, keyword)

    runner = unittest.TextTestRunner(
        verbosity=2 if verbose else 1,
        stream=sys.stdout if verbose else None,
    )

    start_time = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_time

    total = result.testsRun
    failed = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    passed = total - failed - errors - skipped

    return {
        "tier": tier_num,
        "name": tier_info["name"],
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "elapsed": round(elapsed, 3),
        "min_tests": tier_info["min_tests"],
        "meets_threshold": (total >= tier_info["min_tests"]) if not keyword else True,
    }


def print_summary(tier_results: list):
    """Print structured summary table to console."""
    print("\n" + "=" * 80)
    print(f"{'MATH ARCHIVE E2E TEST RUNNER SUMMARY':^80}")
    print("=" * 80)
    print(f"{'Tier':<4} {'Name':<38} {'Total':<7} {'Pass':<6} {'Fail':<6} {'Skip':<6} {'Time(s)':<8}")
    print("-" * 80)

    total_all = 0
    total_passed = 0
    total_failed = 0
    total_errors = 0
    total_skipped = 0
    total_time = 0.0

    for r in tier_results:
        t_num = f"T{r['tier']}"
        name = r["name"][:36]
        tot = r["total"]
        p = r["passed"]
        f = r["failed"] + r.get("errors", 0)
        s = r["skipped"]
        el = f"{r['elapsed']:.2f}"

        total_all += tot
        total_passed += p
        total_failed += r["failed"]
        total_errors += r.get("errors", 0)
        total_skipped += s
        total_time += r["elapsed"]

        print(f"{t_num:<4} {name:<38} {tot:<7} {p:<6} {f:<6} {s:<6} {el:<8}")

    print("-" * 80)
    print(
        f"{'ALL':<4} {'Total Suite Execution':<38} {total_all:<7} {total_passed:<6} "
        f"{total_failed + total_errors:<6} {total_skipped:<6} {total_time:.2f}s"
    )
    print("=" * 80)

    # Check overall quality gate
    passed_all = (total_failed + total_errors) == 0 and total_all > 0
    status_str = "SUCCESS (PASS)" if passed_all else "FAILED / INCOMPLETE"
    print(f"Overall Status: {status_str}")
    print("=" * 80 + "\n")

    return passed_all


def main():
    parser = argparse.ArgumentParser(description="Math Archive E2E Test Suite Runner")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="Run specific tier (1, 2, 3, or 4)")
    parser.add_argument("-k", "--keyword", type=str, default=None, help="Filter tests by keyword/pattern")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose test execution output")
    parser.add_argument("--json-report", type=str, help="Save structured test execution report to JSON file")
    args = parser.parse_args()

    selected_tiers = [args.tier] if args.tier else [1, 2, 3, 4]

    tier_results = []
    for t in selected_tiers:
        res = run_tier(t, verbose=args.verbose, keyword=args.keyword)
        tier_results.append(res)

    all_passed = print_summary(tier_results)

    if args.json_report:
        report_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "overall_passed": all_passed,
            "tiers": tier_results,
        }
        Path(args.json_report).write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to: {args.json_report}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
