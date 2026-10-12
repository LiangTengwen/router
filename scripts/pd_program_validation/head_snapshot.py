import datetime,json,subprocess,time,os
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'slurm-allocation' if os.environ.get('SLURM_JOB_ID') else 'head-only','commands':{}}
commands={'host':['hostname'],'scheduler':['scontrol','ping'],'jobs':['squeue','-o','%.18i %.12u %.30j %.2t %.10M %.6D %R'],'nodes':['sinfo','-N','-l'],'memory':['free','-h'],'load':['uptime'],'storage':['df','-h','/home','/tmp','/dev/shm'],'processes':['ps','-eo','pid,ppid,lstart,stat,rss,args']}
for name,cmd in commands.items():
 t=time.monotonic()
 try:
  p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=12)
  r['commands'][name]={'returncode':p.returncode,'seconds':time.monotonic()-t,'stdout':p.stdout,'stderr':p.stderr}
 except subprocess.TimeoutExpired:r['commands'][name]={'timeout':True,'seconds':time.monotonic()-t}
r['blocked_or_zombie']=[l for l in r['commands'].get('processes',{}).get('stdout','').splitlines()[1:] if len(l.split())>7 and l.split()[7][:1] in ('D','Z')]
r['codex']=[l for l in r['commands'].get('processes',{}).get('stdout','').splitlines() if 'codex' in l.lower()]
print(json.dumps(r,indent=2))
