"""Inspect cache/preemption deltas and token-work/SSE content evidence."""
import json
import math
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
results = json.loads((root / 'results.json').read_text())

def linear_p95(values):
    values = sorted(values)
    if not values:
        return None
    position = .95 * (len(values) - 1)
    index = math.floor(position)
    weight = position - index
    return values[index] * (1 - weight) + values[min(index + 1, len(values) - 1)] * weight

def counters(path):
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith('#') or not line.strip():
            continue
        name = re.split('[{ ]', line, maxsplit=1)[0]
        if name in ('vllm:prefix_cache_queries_total', 'vllm:prefix_cache_hits_total',
                    'vllm:num_preemptions_total', 'vllm:nixl_bytes_transferred_sum',
                    'vllm:nixl_xfer_time_seconds_sum', 'vllm:nixl_bytes_transferred_count',
                    'vllm:nixl_num_failed_transfers_total',
                    'vllm:nixl_num_failed_notifications_total',
                    'vllm:nixl_num_kv_expired_reqs_total'):
            values[name] = values.get(name, 0) + float(line.split()[-1])
    return values

report = []
content = {}
for row in results:
    profile, arm, repeat = row['profile'], row['arm'], row['repeat']
    value = {k: row[k] for k in ('profile', 'arm', 'repeat')}
    for role, port in [('P', 38510), ('D0', 38511), ('D1', 38512)]:
        before = root / f'{profile}-{arm}-{repeat}-{port}-before-run.metrics'
        after = root / f'{profile}-{arm}-r{repeat}-{port}.metrics'
        if not (before.exists() and after.exists()):
            continue
        b, a = counters(before), counters(after)
        delta = {k: a[k] - b[k] for k in a if k in b}
        if any(v < 0 for v in delta.values()):
            raise ValueError('Counter reset inside measurement: ' + str(after))
        q, h = delta.get('vllm:prefix_cache_queries_total', 0), delta.get('vllm:prefix_cache_hits_total', 0)
        value[role] = {'queried_tokens': q, 'hit_tokens': h, 'hit_fraction': h / q if q else None,
                       'preemptions': delta.get('vllm:num_preemptions_total'),
                       'nixl': {k.removeprefix('vllm:nixl_'): v for k, v in delta.items()
                                if k.startswith('vllm:nixl_')}}
    raw = root / f'{profile}-{arm}-r{repeat}.json'
    if raw.exists():
        records = json.loads(raw.read_text())['records']
        good = [r for r in records if r['ok']]
        value['linear_request_p95'] = {'ttft_s': linear_p95([r['ttft_s'] for r in good]),
                                       'tpot_s': linear_p95([r['tpot_s'] for r in good])}
        content[(profile, arm, repeat)] = {
            (json.loads(r['program'])['program_id'], r['turn']):
            ''.join(c.get('text', '') for e in r['events'] for c in e.get('choices', []))
            for r in records if r['ok']
        }
    report.append(value)
by_key = {(r['profile'], r['arm'], r['repeat']): r for r in report}
for value in report:
    profile, arm, repeat = value['profile'], value['arm'], value['repeat']
    actual = content.get((profile, arm, repeat))
    baseline = content.get((profile, 'main', repeat))
    if actual is not None and baseline is not None:
        keys = actual.keys() & baseline.keys()
        value['text_comparison'] = {'compared_requests': len(keys),
                                    'identical_text': sum(actual[k] == baseline[k] for k in keys)}
        other = by_key[(profile, 'main', repeat)]
        value['linear_tpot_p95_ratio_vs_main'] = (
            value['linear_request_p95']['tpot_s'] / other['linear_request_p95']['tpot_s'])
    print(json.dumps(value))
(root / 'cache-content-analysis.json').write_text(json.dumps(report, indent=2))
