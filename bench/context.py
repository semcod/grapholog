"""Measure provider-independent evidence preparation in old/new Python packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--baseline',type=Path)
    p.add_argument('--current',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path)
    p.add_argument('--repeats',type=int,default=7)
    p.add_argument('--child',type=Path)
    args=p.parse_args()
    if args.child:
        from grapholog.llm import prepare
        report=json.loads(args.child.read_text())
        packet=prepare(report,40000)
        print(json.dumps({k:packet[k] for k in ('selected_records','omitted_records','message_characters','report_sha256')}))
        return
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    pairs=[{'left':f'f{i}.js','right':f'f{i}.js','status':'changed',
        'symbol_delta':{'added':[],'removed':[],'changed':['calc']}} for i in range(2000)]
    files=[{'path':f'f{i}.js','sha256':hashlib.sha256(str(i).encode()).hexdigest(),'language':'javascript',
        'status':'parsed','symbols':[],'imports':[],'calls':[],'notes':[]} for i in range(2000)]
    report={'schema':'grapholog.compare.v1','pairs':pairs,'left':{'files':files},'right':{'files':files},'duplicates':{}}
    fixture=out/'report.json';fixture.write_text(json.dumps(report))
    measure=out/'measure'
    subprocess.run(['cc','-O2',str(Path(__file__).with_name('measure.c')),'-o',str(measure)],check=True)
    cpu=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{cpu})
    versions={'0.1':args.baseline.resolve(),'0.2':args.current.resolve()}
    samples={k:[] for k in versions};details={}
    command=[sys.executable,str(Path(__file__).resolve()),'--child',str(fixture)]
    for k,root in versions.items():
        env={**os.environ,'PYTHONPATH':str(root/'src')}
        details[k]=json.loads(subprocess.check_output(command,env=env,text=True))
        subprocess.run([str(measure)]+command,env=env,check=True,capture_output=True)
    for n in range(args.repeats):
        for k in (list(versions) if n%2==0 else list(versions)[::-1]):
            sample=json.loads(subprocess.check_output([str(measure)]+command,env={**os.environ,'PYTHONPATH':str(versions[k]/'src')},text=True))
            if sample['exit_code']!=0:raise RuntimeError(sample)
            samples[k].append(sample)
    result={'method':'Sequential CLI read+prepare 6000 evidence candidates, 40000 character budget, warmup, single CPU, alternate order. Output selection differs intentionally. No LLM calls.',
        'cpu':cpu,'samples':samples,'details':details,'summary':{k:{'median_seconds':statistics.median(r['wall_seconds'] for r in rows),
        'median_peak_rss_mib':statistics.median(r['peak_rss_kib'] for r in rows)/1024} for k,rows in samples.items()}}
    (out/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result['summary']))


if __name__=='__main__':main()
