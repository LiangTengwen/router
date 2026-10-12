"""Fixed-workload, same-allocation PD comparison. No external engine cleanup."""
import concurrent.futures, csv, hashlib, http.client, io, json, os, pathlib, re
import signal, socket, statistics, subprocess, sys, time, traceback

ROOT = pathlib.Path(os.environ['PD_VALIDATION_ROOT']).resolve()
OUT = ROOT / ('gpu-' + os.environ['SLURM_JOB_ID'])
OUT.mkdir(exist_ok=False)
MODEL = os.environ['PD_VALIDATION_MODEL']
PORTS = [38510, 38511, 38512]
SIDE_PORTS = [39510, 39511, 39512]
ROUTER_PORT = 18480
ROUTER_METRICS_PORT = 18481
owned = []
logs = []
allocated_uuids = []

def command(cmd, name):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    (OUT / name).write_text(r.stdout + '\nSTDERR:\n' + r.stderr)
    if r.returncode: raise RuntimeError('Health command failed: ' + name)
    return r.stdout

def health(stage, engines_live=False):
    data = command(['python3', str(ROOT/'head_snapshot.py')], stage+'-node-health.json')
    snapshot = json.loads(data.split('\nSTDERR:')[0])
    # Record harmless zero-RSS zombies after identifying their full parent process.
    # Never clean up system/other-task processes; D-state processes still need diagnosis.
    for line in snapshot['blocked_or_zombie']:
        fields=line.split();state=fields[7];rss=int(fields[8]);parent=fields[1]
        parent_rows=[r for r in snapshot['commands']['processes']['stdout'].splitlines() if r.split() and r.split()[0]==parent]
        (OUT/(stage+'-zombie-diagnosis.txt')).open('a').write(line+'\n'+ '\n'.join(parent_rows)+'\n')
        # Node diagnosis 4925 identified this existing root monitor and found
        # transient D -> S waits with normal storage/memory. Recheck its exact
        # identity for at most one second; persistent D still aborts startup.
        identity_file=os.environ.get('PD_VALIDATION_MONITOR_IDENTITY_FILE')
        monitor=json.loads(pathlib.Path(identity_file).read_text()) if identity_file else {}
        known_monitor=(fields[0]==monitor.get('pid') and parent==monitor.get('ppid')
                       and fields[2:7]==monitor.get('started')
                       and fields[9:]==monitor.get('command')
                       and any(r.endswith(monitor.get('parent_command','\x00')) for r in parent_rows))
        if state.startswith('D') and known_monitor:
            resolved=False
            samples=[]
            for _ in range(10):
                time.sleep(.1)
                probe=subprocess.run(['ps','-p',fields[0],'-o','pid=,ppid=,lstart=,stat=,rss=,args='],
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2)
                row=probe.stdout.strip();samples.append(row);values=row.split()
                same=(len(values)>=10 and values[0:7]==fields[0:7] and values[9:]==monitor.get('command'))
                if same and values[7].startswith(('S','R')):
                    resolved=True;break
            (OUT/(stage+'-monitor-recheck.json')).write_text(json.dumps({'identity':line,'samples':samples,'resolved':resolved},indent=2))
            if resolved:continue
        if state.startswith('D') or rss or not parent_rows:
            raise RuntimeError('D/Z process requires further diagnosis before launch: '+line)
    for name in ['storage','memory','scheduler']:
        result = snapshot['commands'][name]
        if result.get('timeout') or result.get('returncode') != 0: raise RuntimeError('Unhealthy '+name)
    command(['scontrol','show','job',os.environ['SLURM_JOB_ID']], stage+'-allocation.txt')
    port_range=command(['cat','/proc/sys/net/ipv4/ip_local_port_range'],stage+'-ephemeral-ports.txt')
    low,high=map(int,port_range.split())
    if any(low<=port<=high for port in [ROUTER_PORT,ROUTER_METRICS_PORT]):
        raise RuntimeError('Router listeners must be outside the ephemeral port range')
    command(['ss','-ltnp'], stage+'-ports.txt')
    command(['ipcs','-m','-p'], stage+'-ipc.txt')
    command(['ls','-l','/dev/shm'], stage+'-shared-memory.txt')
    gpu = command(['nvidia-smi','--query-gpu=index,uuid,memory.total,memory.used,utilization.gpu','--format=csv,noheader,nounits'],stage+'-gpus.csv')
    processes = command(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory','--format=csv,noheader,nounits'],stage+'-gpu-processes.csv')
    ids = set(os.environ['SLURM_JOB_GPUS'].split(','))
    rows = list(csv.reader(io.StringIO(gpu), skipinitialspace=True))
    if len(ids) != 3:raise RuntimeError('SLURM allocation is not three GPUs')
    if allocated_uuids:
        assigned=[row for row in rows if row[1] in allocated_uuids]
        if len(assigned)!=3:raise RuntimeError('Allocated GPU UUIDs disappeared')
    # nvidia-smi indices can be renumbered by the GPU cgroup. Map by the UUIDs
    # visible to CUDA inside this allocation, not SLURM physical index strings.
    return rows

def request_http(port, path, body=None, timeout=30):
    c = http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,{'Content-Type':'application/json'})
        r=c.getresponse(); data=r.read()
        if r.status != 200: raise RuntimeError(f'{port}{path}: {r.status} {data[:1000]!r}')
        return data
    finally: c.close()

def start(cmd, name, env=None, cores=None):
    log = open(OUT/(name+'.log'),'w'); logs.append(log)
    (OUT/(name+'-command.json')).write_text(json.dumps({'argv':cmd,'CUDA_VISIBLE_DEVICES':(env or os.environ).get('CUDA_VISIBLE_DEVICES'),'cores':cores},indent=2))
    p = subprocess.Popen(cmd,stdout=log,stderr=log,env=env,start_new_session=True,
                         preexec_fn=(lambda: os.sched_setaffinity(0,cores)) if cores else None)
    owned.append(p); return p

def stop(p):
    if p.poll() is None:
        os.killpg(p.pid,signal.SIGTERM)
        try: p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            if p.poll() is None: os.killpg(p.pid,signal.SIGKILL)
            p.wait(timeout=10)

def wait_ready(p, port):
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        if p.poll() is not None: raise RuntimeError(f'Server {port} exited {p.returncode}')
        try:
            request_http(port,'/health',timeout=2); return
        except (OSError,RuntimeError): time.sleep(1)
    raise RuntimeError(f'Server {port} readiness timed out')

def percentile(a,q):
    a=sorted(a); return a[min(len(a)-1,int((len(a)-1)*q))] if a else None

def idle_reset(label):
    # Remote producer blocks expire before cache reset; no competing clients use these ports.
    time.sleep(8)
    for port in PORTS:
        metrics=request_http(port,'/metrics').decode()
        (OUT/f'{label}-{port}-before-reset.metrics').write_text(metrics)
        for line in metrics.splitlines():
            if line.startswith(('vllm:num_requests_running{','vllm:num_requests_waiting{')) and float(line.split()[-1]) != 0:
                raise RuntimeError('Engine is not idle before resetting caches')
        result=request_http(port,'/reset_prefix_cache',{})
        (OUT/f'{label}-{port}-reset.txt').write_bytes(result)
        (OUT/f'{label}-{port}-before-run.metrics').write_bytes(request_http(port,'/metrics'))

def benchmark(arm, profile, repeat, capacity, trace):
    idle_reset(f'{profile}-{arm}-{repeat}')
    rp=ROUTER_PORT
    binary='main' if arm=='main' else ('candidate' if arm=='original' else ('token-aware' if arm in ('token-aware','cohort','paced','bounded','serial') else 'permit-fix'))
    cmd=[str(ROOT/'bin'/binary),'--host','127.0.0.1','--port',str(rp),'--vllm-pd-disaggregation',
         '--prefill',f'http://127.0.0.1:{PORTS[0]}','--decode',f'http://127.0.0.1:{PORTS[1]}','--decode',f'http://127.0.0.1:{PORTS[2]}',
         '--kv-connector','nixl','--policy','round_robin','--log-level','info','--prometheus-host','127.0.0.1','--prometheus-port',str(ROUTER_METRICS_PORT)]
    if arm!='main':
        cfg={'token_capacity_per_dp_rank':capacity,'enable_batch_gain_admission':False,'queue_timeout_seconds':180,'force_resume_timeout_seconds':5,
             'metrics_interval_seconds':.1,'pd':{'enabled':True,'max_prefill_requests_per_target':1 if arm=='original' else 8}}
        if arm in ('optimized','token-aware','cohort','paced','bounded','serial'):
            cfg.update(binding_strategy='program_round_robin',admission_waiting_request_threshold=4,metrics_interval_seconds=.02)
        if arm in ('cohort','paced','bounded','serial'):
            cfg['max_active_programs_per_target']=1 if arm=='serial' else (2 if arm=='bounded' else 4)
            cfg['pd']['explicit_ttl']=True
            cfg['force_resume_timeout_seconds']=60
            if arm in ('paced','bounded','serial'):cfg['pd']['max_prefill_requests_per_target']=4
            if arm=='serial':
                cfg.update(metrics_interval_seconds=.1,force_resume_timeout_seconds=300,queue_timeout_seconds=300)
        cmd += ['--enable-program-scheduling','--program-scheduling-config-json',json.dumps(cfg)]
    name=f'{profile}-{arm}-r{repeat}'; p=start(cmd,name); wait_ready(p,rp)
    records=[]
    def task(program):
        start_at=time.perf_counter()
        for turn,request in enumerate(program):
            at=time.perf_counter();first=None;last=None;done=False;usage=None;events=[];error=None;status=None
            c=http.client.HTTPConnection('127.0.0.1',rp,timeout=240)
            try:
                c.request('POST','/v1/completions',json.dumps(request),{'Content-Type':'application/json'})
                r=c.getresponse();status=r.status
                if status!=200: error=r.read().decode(errors='replace')
                else:
                    for line in r:
                        if not line.startswith(b'data:'):continue
                        data=line[5:].strip()
                        if data==b'[DONE]':done=True;continue
                        event=json.loads(data); events.append(event)
                        if event.get('error'): error=event['error']
                        if any(v.get('text') or v.get('token_ids') for v in event.get('choices',[])):
                            last=time.perf_counter()-at
                            if first is None:first=last
                        if event.get('usage'):usage=event['usage']
                ok=status==200 and done and not error and usage is not None and usage['completion_tokens']==request['max_tokens'] and usage['prompt_tokens']==len(request['prompt'])
                row={'program':request['vllm_xargs']['agentic_context'],'turn':turn,'status':status,'ok':ok,'error':error,'usage':usage,
                     'ttft_s':first,'latency_s':time.perf_counter()-at,'tpot_s':(last-first)/(request['max_tokens']-1) if first is not None else None,'events':events}
            except Exception as e: row={'turn':turn,'ok':False,'error':repr(e),'latency_s':time.perf_counter()-at}
            finally:c.close()
            records.append(row)
            if turn<len(program)-1:time.sleep(.01+(.03 if len(request['prompt'])%3==0 else 0))
        return time.perf_counter()-start_at
    begin=time.perf_counter()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4 if profile=='light' else 16) as pool:
            jct=list(pool.map(task,trace))
        wall=time.perf_counter()-begin
        good=[r for r in records if r['ok']]
        result={'arm':arm,'profile':profile,'repeat':repeat,'requests':len(records),'successes':len(good),'wall_s':wall,
                'jct_mean_s':statistics.mean(jct),'jct_p95_s':percentile(jct,.95),
                'ttft_p95_s':percentile([r['ttft_s'] for r in good],.95),'tpot_p95_s':percentile([r['tpot_s'] for r in good],.95),
                'completion_tokens':sum(r['usage']['completion_tokens'] for r in good),'prompt_tokens':sum(r['usage']['prompt_tokens'] for r in good),
                'records':records,'commands':cmd,'program_jct_s':jct}
        (OUT/(name+'.json')).write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k not in ['records','commands','program_jct_s']}),flush=True)
        for port in PORTS:(OUT/f'{name}-{port}.metrics').write_bytes(request_http(port,'/metrics'))
        return {k:v for k,v in result.items() if k not in ['records','commands','program_jct_s']}
    finally: stop(p)

def main():
    global allocated_uuids
    if not (ROOT/'ISOLATED_BUILD_COMPLETE').exists():raise RuntimeError('Isolated build validation missing')
    provenance=json.loads((ROOT/'isolated-binary-provenance.json').read_text())
    if len(set(provenance.values()))!=4:raise RuntimeError('Arm binary hashes are not distinct')
    for name,expected in provenance.items():
        digest=hashlib.sha256()
        with (ROOT/'bin'/name).open('rb') as f:
            while chunk:=f.read(1024*1024):digest.update(chunk)
        if digest.hexdigest()!=expected:raise RuntimeError('Arm binary changed: '+name)
    (OUT/'binary-provenance.json').write_text(json.dumps(provenance,indent=2))
    harness=pathlib.Path(__file__).read_bytes()
    (OUT/'harness.py').write_bytes(harness)
    (OUT/'harness.sha256').write_text(hashlib.sha256(harness).hexdigest())
    rows=health('before-engines')
    for port in PORTS+SIDE_PORTS+[ROUTER_PORT,ROUTER_METRICS_PORT]:
        with socket.socket() as s:s.bind(('127.0.0.1',port))
    import torch
    if torch.cuda.device_count()!=3: raise RuntimeError('CUDA allocation size mismatch')
    uuids=[str(torch.cuda.get_device_properties(i).uuid) for i in range(3)]
    normalize=lambda value:value.lower().removeprefix('gpu-')
    by_uuid={normalize(row[1]):row[1] for row in rows}
    if not {normalize(value) for value in uuids}.issubset(by_uuid):
        raise RuntimeError(f'CUDA UUIDs missing from nvidia-smi: {uuids!r}, {rows!r}')
    uuids=[by_uuid[normalize(value)] for value in uuids]
    allocated_uuids=uuids
    assigned=[row for row in rows if row[1] in uuids]
    if any(int(row[3])>200 for row in assigned):raise RuntimeError('Allocated GPU memory is already occupied')
    print(json.dumps({'allocation':os.environ['SLURM_JOB_ID'],'gpu_uuids':uuids}),flush=True)
    cpus=sorted(os.sched_getaffinity(0));servers=[]
    for i,port in enumerate(PORTS):
        env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES=uuids[i],VLLM_NIXL_SIDE_CHANNEL_HOST='127.0.0.1',VLLM_NIXL_SIDE_CHANNEL_PORT=str(SIDE_PORTS[i]))
        transfer={'kv_connector':'NixlConnector','kv_role':'kv_both','kv_connector_extra_config':{'backends':['UCX'],'http_port':port}}
        cmd=[sys.executable,'-m','vllm.entrypoints.cli.main','serve',MODEL,'--served-model-name','pd-model','--host','127.0.0.1','--port',str(port),
             '--dtype','bfloat16','--max-model-len','16384','--max-num-seqs','16','--max-num-batched-tokens','4096',
             '--kv-cache-memory-bytes',str(8*1024**3),'--gpu-memory-utilization','.8','--enforce-eager','--enable-prefix-caching',
             '--mamba-cache-mode','all','--language-model-only','--kv-transfer-config',json.dumps(transfer)]
        servers.append(start(cmd,f'engine-{i}',env,cpus[i*len(cpus)//3:(i+1)*len(cpus)//3]))
    for p,port in zip(servers,PORTS):wait_ready(p,port)
    health('engines-ready',True)
    # A direct connector probe verifies P->D transfer and full token work before benchmarking.
    probe={'model':'pd-model','prompt':[100,200,300,400]*256,'max_tokens':1,'temperature':0,'ignore_eos':True,
           'kv_transfer_params':{'do_remote_decode':True,'do_remote_prefill':False}}
    prefill=json.loads(request_http(PORTS[0],'/v1/completions',probe,120))
    (OUT/'probe-prefill.json').write_text(json.dumps(prefill,indent=2))
    params=prefill.get('kv_transfer_params')
    if not params:raise RuntimeError('Prefill did not return transfer metadata')
    probe['kv_transfer_params']=params;probe['max_tokens']=128
    decode=json.loads(request_http(PORTS[1],'/v1/completions',probe,120))
    (OUT/'probe-decode.json').write_text(json.dumps(decode,indent=2))
    if decode.get('usage',{}).get('completion_tokens')!=128:raise RuntimeError('Connector probe token work failed')
    capacities=[]
    for i in [1,2]:
        matches=re.findall(r'GPU KV cache size:\s*([\d,]+) tokens',(OUT/f'engine-{i}.log').read_text())
        if not matches:raise RuntimeError('No physical cache token capacity in D log')
        capacities.append(int(matches[-1].replace(',','')))
    capacity=min(capacities)
    (OUT/'capacity.json').write_text(json.dumps({'D_capacities':capacities,'admission_capacity':capacity},indent=2))
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(MODEL)
    corpus=tokenizer.encode('We compare complete multi turn programs with fixed input and output token counts. ',add_special_tokens=False)
    traces={}
    pressure_context=min(capacity//12,12000)
    for profile,context,count,output in [('light',2048,8,128),('pressure',pressure_context,16,128),('prefill-heavy',pressure_context,16,32)]:
        traces[profile]=[]
        for i in range(count):
            seed=tokenizer.encode(f'Program {i} context. ',add_special_tokens=False)
            trace=[]
            for turn in range(4):
                length=context+turn*128
                prompt=(seed+corpus*((length//len(corpus))+2))[:length]
                trace.append({'model':'pd-model','prompt':prompt,'max_tokens':output,'temperature':0,'seed':42,'ignore_eos':True,
                              'stream':True,'stream_options':{'include_usage':True},
                              'vllm_xargs':{'agentic_context':json.dumps({'program_id':f'p{i}','expected_resume':turn<3,'kv_retention_ttl_seconds':1 if turn<3 else 0})}})
            traces[profile].append(trace)
    (OUT/'traces.json').write_text(json.dumps(traces))
    (OUT/'trace.sha256').write_text(hashlib.sha256((OUT/'traces.json').read_bytes()).hexdigest())
    # Three paired repeats; every arm must complete identical prompt/output token counts.
    results=[]
    arms=['main','original']
    if (ROOT/'bin'/'permit-fix').exists():arms.append('optimized')
    if (ROOT/'bin'/'token-aware').exists():arms.extend(['token-aware','cohort'])
    if os.environ.get('PD_VALIDATION_ARMS'):
        arms=os.environ['PD_VALIDATION_ARMS'].split(',')
        if ('main' not in arms or len(arms)<2 or len(set(arms))!=len(arms)
            or any(a not in ['main','original','optimized','token-aware','cohort','paced','bounded','serial'] for a in arms)):
            raise RuntimeError('Invalid PD_VALIDATION_ARMS')
    profiles = os.environ.get('PD_VALIDATION_PROFILES', 'light,pressure,prefill-heavy').split(',')
    if not profiles or any(profile not in traces for profile in profiles):
        raise RuntimeError('Unknown PD_VALIDATION_PROFILES')
    for profile in profiles:
        for repeat in range(3):
            for arm in (arms if repeat%2==0 else list(reversed(arms))):
                health(f'{profile}-{arm}-{repeat}-before-router',True)
                results.append(benchmark(arm,profile,repeat,capacity,traces[profile]))
                (OUT/'results.json').write_text(json.dumps(results,indent=2))
    (OUT/'COMPLETE').touch()

try:
    main()
except BaseException:
    (OUT/'FAILED').write_text(traceback.format_exc());traceback.print_exc();sys.exit(1)
finally:
    for p in reversed(owned):stop(p)
    for log in logs:log.close()
