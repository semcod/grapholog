"""Reproducible 200-project corpus, known clone memberships and end-to-end indexing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from grapholog.service import query
from grapholog.workspace import connect_read


def corpus(base, projects=200):
    base.mkdir(parents=True,exist_ok=True)
    for i in range(projects):
        p=base/f'project-{i:03}';p.mkdir(exist_ok=True)
        for folder in ('src','tests','assets','data','generated','node_modules/lib'):(p/folder).mkdir(parents=True,exist_ok=True)
        (p/'package.json').write_text(json.dumps({'name':p.name,'type':'module'}))
        (p/'LICENSE').write_text('Synthetic benchmark fixture; CC0 test content.\n')
        for j in range(20):
            arg='input' if j==0 and i%2 else 'value'
            source=f'export function normalize_{j:02}({arg}: number): number {{\n  if ({arg} < 0) return 0;\n  return ({arg} + {j}) * 2;\n}}\n'
            (p/f'src/util-{j:02}.ts').write_text(source)
            (p/f'assets/image-{j:02}.bin').write_bytes(bytes([j])*4096)
            (p/f'data/items-{j:02}.json').write_text(json.dumps({'data':'x'*4096}))
            if j<10:(p/f'generated/generated-{j:02}.js').write_text('export function generated(x) { return x * 200; }\n')
        (p/'tests/test-positive.js').write_text("import {normalize_00} from '../src/util-00.ts';\nexport function test_positive() { const result = normalize_00(2); if (result !== 4) throw new Error('unexpected'); return result; }\n")
        (p/'node_modules/lib/index.js').write_text('export function vendored() { return 1; }\n')
    return {'projects':projects,'physical_files':projects*74,'included_files':projects*23,
            'symbols':projects*21,'excluded_entries':projects*42,'function_body_groups':22,'function_shape_groups':1}


def signature(db):
    with connect_read(db) as c:
        result=[]
        for row in c.execute('SELECT id,match,language,occurrences,project_count FROM clone_groups ORDER BY id'):
            members=[tuple(r) for r in c.execute("SELECT p.label,json_extract(n.record,'$.path'),json_extract(n.record,'$.label') FROM members m JOIN nodes n ON n.id=m.node_id JOIN projects p ON p.id=m.project_id WHERE m.group_id=? ORDER BY p.label,n.id",(row['id'],))]
            result.append([*tuple(row),members])
        return hashlib.sha256(json.dumps(result,separators=(',',':')).encode()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--projects',type=int,default=200);parser.add_argument('--repeats',type=int,default=3);parser.add_argument('--rust',type=Path,default=ROOT/'rust/target/release/grapholog-rs');args=parser.parse_args()
    if args.projects<4 or args.projects%2:raise ValueError('Use an even project count >=4')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    expected=corpus(out/'repos',args.projects)
    measure=out/'measure';subprocess.run(['cc','-O2',str(ROOT/'bench/measure.c'),'-o',str(measure)],check=True)
    cpus=sorted(os.sched_getaffinity(0));os.sched_setaffinity(0,set(cpus[:4]))
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
    variants={'python-off':('python',1,False),'python-warm':('python',1,True),'rust-off':('rust',1,False),'rust-warm':('rust',1,True),'rust-jobs4-warm':('rust',4,True)}
    base=[sys.executable,'-m','grapholog','workspace','index',str(out/'repos'),'--discover','--profile','logic']
    result={'environment':{'python':sys.version,'platform':platform.platform(),'affinity':cpus[:4],
            'method':'Full CLI discovery + index transaction + clone groups. 1 warmup, rotating order. Warm OS cache. Empty new DB every sample. Warm application cache prefilled during warmup. Four-CPU affinity for all variants; jobs as named. wait4 RSS is not a simultaneous sum of Python and Rust child RSS.',
            'rust_sha256':hashlib.sha256(args.rust.read_bytes()).hexdigest()},'expected':expected,'samples':{},'checks':{},'query_latency_ms':{}}
    for key in variants:result['samples'][key]=[]
    for repeat in range(-1,args.repeats):
        keys=list(variants);shift=max(repeat,0)%len(keys);keys=keys[shift:]+keys[:shift]
        if repeat%2:keys.reverse()
        for key in keys:
            engine,jobs,cached=variants[key]
            with tempfile.TemporaryDirectory(dir=out,prefix='index-') as temp:
                db=Path(temp)/'workspace.sqlite'
                command=base+['--db',str(db),'--engine',engine,'--jobs',str(jobs)]
                if engine=='rust':command+=['--rust-bin',str(args.rust)]
                if cached:command+=['--cache-dir',str(out/(key+'-cache'))]
                sample=json.loads(subprocess.check_output([str(measure),*command],env=env,text=True))
                if sample['exit_code']:raise RuntimeError((key,sample))
                if repeat>=0:result['samples'][key].append(sample)
                summary=query(db,'summary')['data'];counts=summary['counts']
                assert counts['projects']==expected['projects'] and counts['files']==expected['included_files'] and counts['symbols']==expected['symbols'],counts
                assert counts['excluded_entries']==expected['excluded_entries'],counts
                assert counts['clone_groups']==23 and counts['cross_project_groups']==23,counts
                with connect_read(db) as c:
                    distribution=[tuple(row) for row in c.execute('SELECT match,occurrences,COUNT(*) FROM clone_groups GROUP BY match,occurrences ORDER BY match,occurrences')]
                assert distribution==[('function-body',args.projects//2,2),('function-body',args.projects,20),('function-shape',args.projects,1)],distribution
                checksum=signature(db)
                result['checks'][key]={'counts':counts,'expected_membership_distribution':distribution,'clone_membership_sha256':checksum,'database_bytes':db.stat().st_size}
                if repeat==args.repeats-1:
                    times=[]
                    for _ in range(15):
                        start=time.perf_counter();query(db,'clones',limit=20);times.append((time.perf_counter()-start)*1000)
                    result['query_latency_ms'][key]={'median':statistics.median(times),'max':max(times),'samples':times}
                    if key=='rust-jobs4-warm':
                        import shutil
                        shutil.copy2(db,out/'example-workspace.sqlite')
            print(f'{key} repeat={repeat} seconds={sample["wall_seconds"]:.3f}',flush=True)
    checksums={row['clone_membership_sha256'] for row in result['checks'].values()}
    assert len(checksums)==1,checksums
    result['all_engines_same_clone_memberships']=True
    result['summary']={key:{'median_seconds':statistics.median(s['wall_seconds'] for s in samples),
        'min_seconds':min(s['wall_seconds'] for s in samples),'max_seconds':max(s['wall_seconds'] for s in samples),
        'median_wait4_peak_rss_mib':statistics.median(s['peak_rss_kib'] for s in samples)/1024} for key,samples in result['samples'].items()}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['summary'],indent=2),flush=True)


if __name__=='__main__':main()
