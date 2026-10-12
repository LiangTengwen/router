"""Reject unequal/failed work before comparing paired Program JCT measurements."""
import json, pathlib, statistics, sys

root=pathlib.Path(sys.argv[1])
require_linear='--require-linear' in sys.argv[2:]
results=json.loads((root/'results.json').read_text())
raw_cache={}
def raw_p95(row):
    key=(row['profile'],row['arm'],row['repeat'])
    if key in raw_cache:return raw_cache[key]
    path=root/f"{key[0]}-{key[1]}-r{key[2]}.json"
    if not path.exists():return None,'Missing raw request data'
    try:
        records=json.loads(path.read_text())['records']
        good=[r for r in records if r['ok']]
        assert len(records)==row['requests'] and len(good)==row['successes']
        assert sum(r['usage']['prompt_tokens'] for r in good)==row['prompt_tokens']
        assert sum(r['usage']['completion_tokens'] for r in good)==row['completion_tokens']
        values=sorted(r['tpot_s'] for r in good)
        position=.95*(len(values)-1);index=int(position);weight=position-index
        p95=values[index]*(1-weight)+values[min(index+1,len(values)-1)]*weight
        assert p95>0
        result=(p95,None)
    except (AssertionError,ValueError,KeyError,TypeError,IndexError) as error:
        result=(None,'Invalid/incomplete raw request data: '+type(error).__name__)
    raw_cache[key]=result
    return result
report=[]
for profile in dict.fromkeys(r['profile'] for r in results):
    baseline={r['repeat']:r for r in results if r['profile']==profile and r['arm']=='main'}
    for arm in dict.fromkeys(r['arm'] for r in results if r['profile']==profile):
        rows=[r for r in results if r['profile']==profile and r['arm']==arm]
        paired=[];tpot=[];linear=[];errors=[]
        if len({r['repeat'] for r in rows}) != len(rows):
            errors.append('Duplicate arm repeat')
        main_rows=[r for r in results if r['profile']==profile and r['arm']=='main']
        if len({r['repeat'] for r in main_rows}) != len(main_rows):
            errors.append('Duplicate main repeat')
        for r in rows:
            b=baseline.get(r['repeat'])
            if not b:errors.append('Missing paired main run');continue
            valid=(r['requests']==r['successes']==b['requests']==b['successes']
                   and r['prompt_tokens']==b['prompt_tokens'] and r['completion_tokens']==b['completion_tokens'])
            if not valid:errors.append('Incomplete/unequal work in repeat '+str(r['repeat']));continue
            paired.append(1-r['jct_mean_s']/b['jct_mean_s'])
            tpot.append(r['tpot_p95_s']/b['tpot_p95_s'])
            if require_linear:
                rp,re=raw_p95(r);bp,be=raw_p95(b)
                if re or be:errors.append(f"Repeat {r['repeat']}: {re or be}")
                else:linear.append(rp/bp)
        value={'profile':profile,'arm':arm,'repeats':len(rows),'successes':sum(r['successes'] for r in rows),
               'jct_mean_median_s':statistics.median(r['jct_mean_s'] for r in rows),
               'jct_p95_median_s':statistics.median(r['jct_p95_s'] for r in rows),
               'ttft_p95_median_s':statistics.median(r['ttft_p95_s'] for r in rows),
               'tpot_p95_median_s':statistics.median(r['tpot_p95_s'] for r in rows),
               'paired_jct_gain':paired,'paired_tpot_p95_ratio':tpot,'invalid_comparisons':errors,
               'linear_tails_required':require_linear,'paired_linear_tpot_p95_ratio':linear,
               'acceptance_met':len(paired)==3 and {r['repeat'] for r in rows}=={0,1,2}
                   and not errors and min(paired)>=.10 and max(tpot)<=1.05
                   and (not require_linear or len(linear)==3 and max(linear)<=1.05)}
        report.append(value)
(root/'analysis.json').write_text(json.dumps(report,indent=2))
for r in report:
    print(r['profile'],r['arm'],f"JCT {r['jct_mean_median_s']:.4f}s",'paired gains',*[f'{g:.1%}' for g in r['paired_jct_gain']],
          'TPOT ratios',*[round(v,3) for v in r['paired_tpot_p95_ratio']],
          'linear ratios',*[round(v,3) for v in r['paired_linear_tpot_p95_ratio']],
          'PASS' if r['acceptance_met'] else 'not passed',r['invalid_comparisons'])
