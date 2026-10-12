"""Recompute report metrics from saved experiments; never launch engines.

Usage: python report_metrics.py VALIDATION_BASE --output-dir OUTPUT_DIRECTORY
VALIDATION_BASE contains 20261010/ and 20261011/. Missing measurements are null,
not zero. Client percentiles use linear interpolation. Historical task samples
were not saved, so only their recorded mean and legacy lower p95 are available.
"""

import argparse
import collections
import csv
import hashlib
import json
import math
import pathlib
import re
import statistics


METRICS = [
    ("completed_tasks", "四轮/八轮全部成功的 Program 数"),
    ("failed_tasks", "至少一轮失败的 Program 数"),
    ("tasks_with_patch", "生成代码补丁的任务数；本实验不适用"),
    ("mean_task_duration_seconds", "成功 Program 的平均完成时间"),
    ("p50_task_duration_seconds", "成功 Program 完成时间 p50"),
    ("p95_task_duration_seconds", "成功 Program 完成时间 p95"),
    ("p99_task_duration_seconds", "成功 Program 完成时间 p99"),
    ("run_wall_time_seconds", "整批从提交到全部结束的用时"),
    ("request_throughput_per_second", "成功请求数 / 整批用时"),
    ("input_token_throughput_per_second", "成功请求输入 tokens / 整批用时"),
    ("output_token_throughput_per_second", "成功请求输出 tokens / 整批用时"),
    ("requests", "已记录的客户端请求数"),
    ("successful_requests", "成功客户端请求数"),
    ("failed_requests", "失败客户端请求数"),
    ("input_tokens", "成功请求 usage 输入 tokens 总数"),
    ("output_tokens", "成功请求 usage 输出 tokens 总数"),
    ("prefix_cache_hit_rate", "P native prefix 命中 tokens / 查询 tokens"),
    ("prompt_token_hit_rate", "P cached prompt tokens / 客户端 usage 输入 tokens"),
    ("latency_mean_seconds", "成功单次请求端到端耗时均值"),
    ("latency_p50_seconds", "成功单次请求端到端耗时 p50"),
    ("latency_p95_seconds", "成功单次请求端到端耗时 p95"),
    ("latency_p99_seconds", "成功单次请求端到端耗时 p99"),
    ("ttft_mean_seconds", "成功单次请求首个内容输出等待均值"),
    ("ttft_p50_seconds", "成功单次请求首个内容输出等待 p50"),
    ("ttft_p95_seconds", "成功单次请求首个内容输出等待 p95"),
    ("ttft_p99_seconds", "成功单次请求首个内容输出等待 p99"),
    ("vllm_queue_time_mean_seconds", "P 与所选 D 的引擎 WAITING 统计均值之和"),
    ("vllm_prefill_time_mean_seconds", "P 与所选 D 的引擎 PREFILL 统计均值之和"),
    ("vllm_decode_time_mean_seconds", "P 与所选 D 的引擎 DECODE 统计均值之和"),
    ("vllm_inference_time_mean_seconds", "P 与所选 D 的引擎 RUNNING 统计均值之和"),
]
KEYS = [key for key, _ in METRICS]
PORTS = {"P": 38510, "D0": 38511, "D1": 38512}


def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * q
    index = math.floor(position)
    weight = position - index
    return values[index] * (1 - weight) + values[min(index + 1, len(values) - 1)] * weight


def describe(target, name, values):
    target[name + "_mean_seconds"] = statistics.mean(values) if values else None
    for label, q in [("p50", .50), ("p95", .95), ("p99", .99)]:
        target[f"{name}_{label}_seconds"] = quantile(values, q)


def counters(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name = re.split(r"[{ ]", line, maxsplit=1)[0]
        if name.endswith(("_sum", "_count", "_total")):
            result[name] = result.get(name, 0.0) + float(line.split()[-1])
    return result


def engine_data(directory, row, hashes, base):
    roles = {}
    for role, port in PORTS.items():
        before = directory / f"{row['profile']}-{row['arm']}-{row['repeat']}-{port}-before-run.metrics"
        after = directory / f"{row['profile']}-{row['arm']}-r{row['repeat']}-{port}.metrics"
        if not before.exists() or not after.exists():
            continue
        for path in (before, after):
            hashes[str(path.relative_to(base))] = hashlib.sha256(path.read_bytes()).hexdigest()
        b, a = counters(before), counters(after)
        delta = {key: a[key] - b[key] for key in a if key in b}
        if any(v < -1e-8 for v in delta.values()):
            raise ValueError("Counter reset inside run: " + after.name)
        phases = {}
        for phase in ("queue", "prefill", "decode", "inference"):
            key = "vllm:request_" + phase + "_time_seconds"
            count, total = delta.get(key + "_count"), delta.get(key + "_sum")
            phases[phase] = {"count": count, "sum_seconds": total,
                             "mean_seconds": total / count if count and total is not None else None}
        roles[role] = {
            "phases": phases,
            "prefix_queries": delta.get("vllm:prefix_cache_queries_total"),
            "prefix_hits": delta.get("vllm:prefix_cache_hits_total"),
            "prompt_tokens_cached": delta.get("vllm:prompt_tokens_cached_total"),
            "prompt_tokens": delta.get("vllm:prompt_tokens_total"),
            "preemptions": delta.get("vllm:num_preemptions_total"),
            "nixl": {key.removeprefix("vllm:nixl_"): value
                     for key, value in delta.items() if key.startswith("vllm:nixl_")},
        }
    return roles


def extract(raw, summary, kind, batch, profile, base, path, patch_kind):
    records = raw["records"]
    good = [record for record in records if record["ok"]]
    assert len(records) == summary["requests"] and len(good) == summary["successes"]
    turns = 8 if kind == "http-mock" else 4
    expected_tasks = 16 if kind == "http-mock" or profile != "light" else 8
    groups = collections.defaultdict(list)
    for record in records:
        program = record.get("program")
        if isinstance(program, str):
            program = json.loads(program)["program_id"]
        if program is None:
            raise ValueError("Missing Program identity; cannot derive task outcomes")
        groups[program].append(record)
    assert len(groups) == expected_tasks
    completed = sum(len(items) == turns and {v["turn"] for v in items} == set(range(turns))
                    and all(v["ok"] for v in items) for items in groups.values())
    metrics = dict.fromkeys(KEYS)
    metrics.update(completed_tasks=completed, failed_tasks=expected_tasks-completed,
                   requests=len(records), successful_requests=len(good),
                   failed_requests=len(records)-len(good), run_wall_time_seconds=summary["wall_s"],
                   input_tokens=sum((v.get("usage") or {}).get("prompt_tokens", 0) for v in good),
                   output_tokens=sum((v.get("usage") or {}).get("completion_tokens", 0) for v in good))
    assert metrics["input_tokens"] == summary["prompt_tokens"]
    assert metrics["output_tokens"] == summary["completion_tokens"]
    notes = {"tasks_with_patch": "N/A: synthetic workload does not perform code editing or collect patches"}
    if patch_kind == "modified-router":
        metrics["tasks_with_patch"] = expected_tasks if summary["arm"] != "main" else 0
        notes.pop("tasks_with_patch")
    for metric, numerator in [("request_throughput_per_second", "successful_requests"),
                              ("input_token_throughput_per_second", "input_tokens"),
                              ("output_token_throughput_per_second", "output_tokens")]:
        metrics[metric] = metrics[numerator] / summary["wall_s"]
    describe(metrics, "latency", [v["latency_s"] for v in good])
    describe(metrics, "ttft", [v["ttft_s"] for v in good if v.get("ttft_s") is not None])
    samples = raw.get("program_jct_s")
    if samples is not None and completed == expected_tasks:
        assert len(samples) == expected_tasks
        assert math.isclose(statistics.mean(samples), summary["jct_mean_s"], abs_tol=1e-8)
        metrics["mean_task_duration_seconds"] = statistics.mean(samples)
        for label, q in [("p50", .50), ("p95", .95), ("p99", .99)]:
            metrics[f"{label}_task_duration_seconds"] = quantile(samples, q)
    elif completed == expected_tasks:
        metrics["mean_task_duration_seconds"] = summary["jct_mean_s"]
    for key in ("p50_task_duration_seconds", "p95_task_duration_seconds", "p99_task_duration_seconds"):
        if metrics[key] is None:
            notes[key] = "N/A: individual Program JCT samples were not saved"
    if metrics["mean_task_duration_seconds"] is None:
        notes["mean_task_duration_seconds"] = "N/A: recorded mean includes failed tasks; successful-task JCT samples unavailable"
    engine_hashes = {}
    roles = engine_data(path.parent, summary, engine_hashes, base) if kind == "gpu" else {}
    if "P" in roles:
        p = roles["P"]
        if p["prefix_queries"]:
            metrics["prefix_cache_hit_rate"] = p["prefix_hits"] / p["prefix_queries"]
        if p["prompt_tokens_cached"] is not None and metrics["input_tokens"]:
            metrics["prompt_token_hit_rate"] = p["prompt_tokens_cached"] / metrics["input_tokens"]
    for phase in ("queue", "prefill", "decode", "inference"):
        key = f"vllm_{phase}_time_mean_seconds"
        if set(roles) == set(PORTS):
            parts = [roles[role]["phases"][phase] for role in PORTS]
            if all(v["sum_seconds"] is not None and v["count"] is not None for v in parts):
                assert parts[0]["count"] == len(good)
                assert parts[1]["count"] + parts[2]["count"] == len(good)
                # A client request has a P observation and one D observation.
                # Dividing by 2N would silently halve the PD per-request amount.
                metrics[key] = sum(v["sum_seconds"] for v in parts) / len(good)
        if metrics[key] is None:
            notes[key] = "N/A: no complete before/after engine histogram measurements"
    for key in ("prefix_cache_hit_rate", "prompt_token_hit_rate"):
        if metrics[key] is None:
            notes[key] = ("N/A: HTTP mock has no real KV cache" if kind == "http-mock"
                          else "N/A: no before/after P cache counter measurements")
    return {
        "run_id": f"{batch}/{profile}/{summary['arm']}/r{summary['repeat']}",
        "kind": kind, "batch": batch, "profile": profile,
        "arm": summary["arm"], "repeat": summary["repeat"],
        "client_concurrency": summary.get("concurrency", 4 if profile == "light" else 16),
        "metrics": metrics, "missing_metric_notes": notes, "engine_roles": roles,
        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "raw_artifact": str(path.relative_to(base)),
        "engine_metric_artifact_sha256": engine_hashes,
        "legacy_task_p95_lower_seconds": summary.get("jct_p95_s"),
        "reported_tpot_p95_lower_seconds": summary.get("tpot_p95_s"),
        "tpot_p95_linear_seconds": quantile([v["tpot_s"] for v in good if v.get("tpot_s") is not None], .95),
        "recorded_all_attempted_task_mean_seconds": summary["jct_mean_s"],
    }


def load_runs(base, patch_kind):
    result = []
    mock_directories = [
        ("mock-initial", base/"20261010/initial-mock"),
        ("mock-dynamic-port", base/"20261010/dynamic-port-mock"),
        ("mock-20261010", base/"20261010"),
        ("mock-20261011", base/"20261011"),
        ("mock-tuned", base/"20261011/tuned"),
    ]
    for batch, directory in mock_directories:
        for row in json.loads((directory/"mock-results.json").read_text()):
            path = directory / f"mock-{row['arm']}-c{row['concurrency']}-r{row['repeat']}.json"
            raw = json.loads(path.read_text())
            result.append(extract(raw, row, "http-mock", batch, f"c{row['concurrency']}", base, path, patch_kind))
    for job in ("4921", "4922", "4923", "4926", "4927"):
        directory = base / "20261011" / f"gpu-{job}"
        for row in json.loads((directory/"results.json").read_text()):
            path = directory / f"{row['profile']}-{row['arm']}-r{row['repeat']}.json"
            result.append(extract(json.loads(path.read_text()), row, "gpu", job, row["profile"], base, path, patch_kind))
    path = base/"20261011/evidence/first-gpu-main.json"
    raw = json.loads(path.read_text())
    result.append(extract(raw, raw, "gpu-unpaired-probe", "4917", "light", base, path, patch_kind))
    by_key = {(r["batch"], r["profile"], r["arm"], r["repeat"]): r for r in result}
    assert len(by_key) == len(result)
    for run in result:
        baseline = by_key.get((run["batch"], run["profile"], "main", run["repeat"]))
        run["paired_main_available"] = baseline is not None
        a, b = run["metrics"], baseline["metrics"] if baseline else {}
        run["equal_successful_work_vs_main"] = bool(baseline) and all(a[k] == b[k] for k in
            ("completed_tasks", "successful_requests", "input_tokens", "output_tokens")) and a["failed_tasks"] == b["failed_tasks"] == 0
    return result


def median_metric(runs, key):
    values = [r["metrics"][key] for r in runs]
    return statistics.median(values) if all(v is not None for v in values) else None


def format_metric(key, value):
    if value is None:
        return "N/A"
    if key.endswith("_rate"):
        return f"{value:.4%}"
    if key in ("completed_tasks", "failed_tasks", "tasks_with_patch", "requests", "successful_requests", "failed_requests", "input_tokens", "output_tokens"):
        return f"{value:g}"
    return f"{value:.6f}"


def comparison_table(runs, batch, profile):
    selected = [r for r in runs if r["batch"] == batch and r["profile"] == profile]
    arms = sorted({r["arm"] for r in selected}, key=lambda a: (a != "main", a))
    groups = {arm: sorted((r for r in selected if r["arm"] == arm), key=lambda r:r["repeat"]) for arm in arms}
    lines = ["| 对比项 | " + " | ".join(f"{arm}（n={len(groups[arm])}）" for arm in arms) + " |",
             "|---|" + "---:|" * len(arms)]
    for key in KEYS:
        lines.append("| " + key + " | " + " | ".join(format_metric(key, median_metric(groups[arm], key)) for arm in arms) + " |")
    lines.extend(["", "各列包含的 repeat：" + "；".join(f"{arm}={','.join(str(r['repeat']) for r in groups[arm])}" for arm in arms) + "。"])
    lines.extend(["", "计数合计另外列出，避免中位数掩盖某一轮失败：", "",
                  "| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |",
                  "|---|---:|---:|---:|---:|"])
    for arm in arms:
        totals = [sum(r["metrics"][key] for r in groups[arm]) for key in
                  ("completed_tasks", "failed_tasks", "successful_requests", "failed_requests")]
        lines.append("| " + arm + " | " + " | ".join(str(v) for v in totals) + " |")
    if len({tuple(r["repeat"] for r in groups[a]) for a in arms}) > 1:
        lines.append("**各列重复集合不同，不能直接用这些中位数计算配对收益。配对结论只使用相同 repeat 的 main。**")
    if any(not r["equal_successful_work_vs_main"] for r in selected):
        lines.append("**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("validation_base", type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--tasks-with-patch-kind", choices=["generated-code", "modified-router"], default="generated-code")
    args = parser.parse_args()
    runs = load_runs(args.validation_base.resolve(), args.tasks_with_patch_kind)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "metric_names": KEYS, "metric_descriptions": dict(METRICS),
        "percentile_method": "linear interpolation, position=(n-1)*q; legacy task p95 retained separately",
        "table_aggregation": "median of per-run metrics; counts refer to a representative single run, not sums across repeats",
        "tasks_with_patch_kind": args.tasks_with_patch_kind,
        "generator_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "engine_phase_aggregation": "(P histogram delta sum + D0 delta sum + D1 delta sum) / client successful requests; P count=N, D0+D1 count=N; excludes Router queue",
        "prompt_token_hit_rate_denominator": "client successful usage input tokens; P metrics omit one boundary token per request in this deployment",
        "runs": runs,
    }
    (args.output_dir/"pd_program_scheduling_validation_metrics.json").write_text(json.dumps(metadata, indent=2)+"\n")
    extra = ["run_id", "kind", "batch", "profile", "arm", "repeat", "client_concurrency", "paired_main_available", "equal_successful_work_vs_main", "legacy_task_p95_lower_seconds", "reported_tpot_p95_lower_seconds", "tpot_p95_linear_seconds"]
    with (args.output_dir/"pd_program_scheduling_validation_metrics.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=extra+KEYS)
        writer.writeheader()
        for run in runs:
            writer.writerow({**{k: run[k] for k in extra}, **run["metrics"]})
    text = ["# PD Program 调度收益验证：完整指标对比附录", "",
            "由 `scripts/pd_program_validation/report_metrics.py` 根据已保存的原始数据生成。详见[通俗版详细报告](pd_program_scheduling_validation_detailed.md)。", "",
            "每列为所列重复的单次运行指标中位数；计数不是重复总和。全部客户端分位数使用线性插值。N/A 表示未采集或不适用，不是 0。", "",
            "早期无逐个 Program 样本，任务 p50/p95/p99 无法按统一算法补算；旧 lower p95 保留在 CSV/JSON 的 `legacy_task_p95_lower_seconds`。", "",
            "`tasks_with_patch` 的具体口径、P cache 与 prompt hit 的分母、vLLM 两阶段统计相加的限制，见详细报告的指标说明。", "",
            "[逐轮 CSV](pd_program_scheduling_validation_metrics.csv) · [逐轮 JSON 与来源哈希](pd_program_scheduling_validation_metrics.json)", ""]
    pairs = list(dict.fromkeys((r["batch"], r["profile"]) for r in runs))
    for batch, profile in pairs:
        text.extend([f"## {batch} / {profile}", "", comparison_table(runs,batch,profile), ""])
    (args.output_dir/"pd_program_scheduling_validation_metrics.md").write_text("\n".join(text))
    print(f"Exported {len(runs)} saved runs and {len(KEYS)} requested metrics in {len(pairs)} comparison groups")


if __name__ == "__main__":
    main()
