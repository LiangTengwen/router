"""Accept only three equal-work pairs improving mean/p50/p95/p99 task duration.

Other performance measurements, including TPOT, are observations rather than
gates. Missing raw samples, failed work and unmatched repeats never qualify.
Writes task-duration-analysis.json without overwriting the historical analysis.
"""

import argparse
import collections
import json
import math
import pathlib
import statistics

from report_metrics import quantile


METRICS = ("mean_task_duration_seconds", "p50_task_duration_seconds",
           "p95_task_duration_seconds", "p99_task_duration_seconds")


def task_metrics(directory, summary):
    path = directory / f"{summary['profile']}-{summary['arm']}-r{summary['repeat']}.json"
    if not path.exists():
        return None, "Missing raw run"
    try:
        raw = json.loads(path.read_text())
        records = raw["records"]
        samples = raw["program_jct_s"]
        assert len(records) == summary["requests"] == summary["successes"]
        assert all(r["ok"] and r["status"] == 200 and not r.get("error") for r in records)
        assert sum(r["usage"]["prompt_tokens"] for r in records) == summary["prompt_tokens"]
        assert sum(r["usage"]["completion_tokens"] for r in records) == summary["completion_tokens"]
        programs = collections.defaultdict(list)
        for record in records:
            programs[json.loads(record["program"])["program_id"]].append(record["turn"])
        assert len(samples) == len(programs)
        assert all(sorted(turns) == [0, 1, 2, 3] for turns in programs.values())
        assert all(math.isfinite(v) and v > 0 for v in samples)
        mean = statistics.mean(samples)
        assert math.isclose(mean, summary["jct_mean_s"], abs_tol=1e-8)
        return {"mean_task_duration_seconds": mean,
                "p50_task_duration_seconds": quantile(samples, .50),
                "p95_task_duration_seconds": quantile(samples, .95),
                "p99_task_duration_seconds": quantile(samples, .99)}, None
    except (KeyError, ValueError, TypeError, AssertionError, IndexError) as error:
        return None, "Invalid/missing raw task or request samples: " + type(error).__name__


def analyze(directory):
    results = json.loads((directory / "results.json").read_text())
    report = []
    for profile in dict.fromkeys(row["profile"] for row in results):
        baselines = [row for row in results if row["profile"] == profile and row["arm"] == "main"]
        for arm in dict.fromkeys(row["arm"] for row in results if row["profile"] == profile):
            rows = [row for row in results if row["profile"] == profile and row["arm"] == arm]
            errors, pairs = [], []
            if len({row["repeat"] for row in rows}) != len(rows):
                errors.append("Duplicate candidate repeat")
            if len({row["repeat"] for row in baselines}) != len(baselines):
                errors.append("Duplicate baseline repeat")
            for row in rows:
                candidates = [b for b in baselines if b["repeat"] == row["repeat"]]
                if len(candidates) != 1:
                    errors.append(f"Repeat {row['repeat']}: missing/duplicate baseline")
                    continue
                baseline = candidates[0]
                if not all(row[key] == baseline[key] for key in
                           ("requests", "successes", "prompt_tokens", "completion_tokens")):
                    errors.append(f"Repeat {row['repeat']}: unequal successful work")
                    continue
                actual, error = task_metrics(directory, row)
                reference, baseline_error = task_metrics(directory, baseline)
                if error or baseline_error:
                    errors.append(f"Repeat {row['repeat']}: {error or baseline_error}")
                    continue
                gain = {key: 1 - actual[key] / reference[key] for key in METRICS}
                pairs.append({"repeat": row["repeat"], "main": reference,
                              "candidate": actual, "relative_improvement": gain,
                              "all_four_strictly_better": all(value > 0 for value in gain.values())})
            complete = len(pairs) == 3 and {pair["repeat"] for pair in pairs} == {0, 1, 2}
            value = {"profile": profile, "arm": arm, "repeats": len(rows),
                     "paired_runs": pairs, "invalid_comparisons": errors,
                     "acceptance_met": arm != "main" and complete and not errors
                         and all(pair["all_four_strictly_better"] for pair in pairs)}
            report.append(value)
    return {"target": {"metrics": list(METRICS), "comparison": "strictly less than paired main",
                       "paired_repeats": 3, "percentiles": "linear interpolation",
                       "other_performance_gates": [], "equal_successful_work_required": True},
            "results": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=pathlib.Path)
    args = parser.parse_args()
    report = analyze(args.directory)
    (args.directory / "task-duration-analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    for result in report["results"]:
        outcome = "BASELINE" if result["arm"] == "main" else ("PASS" if result["acceptance_met"] else "NOT PASSED")
        print(result["profile"], result["arm"], outcome, result["invalid_comparisons"])
        for pair in result["paired_runs"]:
            print("  repeat", pair["repeat"], "gains", {k: f"{v:.2%}" for k, v in pair["relative_improvement"].items()})


if __name__ == "__main__":
    main()
