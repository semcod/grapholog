"""Paired v0.1/v0.2 end-to-end benchmark, full-result regression check.

Run with the same Python+parsers environment and release binaries for both versions.
Inputs are synthetic; source code is parsed, never executed.
"""
import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from corpus import scale, small, put


def moved(root, count=150):
    a, b = root/'A', root/'B'
    for i in range(count):
        body = f'export function calc_{i}(x: number) {{\n'
        for j in range(30):
            body += f'  const value_{i}_{j} = x + {100000*i + j};\n'
        body += f'  return value_{i}_29 + 1;\n}}\n'
        put(a, f'old_{i:04}.ts', body)
        put(b, f'new_{i:04}.ts', body.replace(f'return value_{i}_29 + 1', f'return value_{i}_29 + 2'))
    return a, b


def run(command, env):
    result = subprocess.run(command,env=env,capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def normalize(data):
    data = copy.deepcopy(data)
    for key in ('verified','pruned','method','probabilistic'):
        data.get('similarity_search',{}).pop(key,None)
    return data


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--current',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repeats',type=int,default=7)
    args=p.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    old,new=args.baseline.resolve(),args.current.resolve()
    variants={
        'python-0.1':([sys.executable,'-m','grapholog'],{**os.environ,'PYTHONPATH':str(old/'src')}),
        'python-0.2':([sys.executable,'-m','grapholog'],{**os.environ,'PYTHONPATH':str(new/'src')}),
        'rust-0.1':([str(old/'bin/grapholog-rs-linux-x86_64')],os.environ.copy()),
        'rust-0.2':([str(new/'rust/target/release/grapholog-rs')],os.environ.copy()),
    }
    cpu=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{cpu})
    measure=out/'measure'
    subprocess.run(['cc','-O2',str(Path(__file__).with_name('measure.c')),'-o',str(measure)],check=True)
    result={'environment':{'platform':platform.platform(),'python':sys.version,'cpu':cpu,
        'packages':{p:importlib.metadata.version(p) for p in ('tree-sitter','tree-sitter-javascript','tree-sitter-typescript','tree-sitter-php','tree-sitter-bash')},
        'cpu_model':next(s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')),
        'method':'Sequential alternating order, one warmup per variant, warm filesystem cache, single CPU affinity. CLI diff+pretty JSON to /dev/null. Wall time and wait4 child peak RSS. No profiling or compile time included.',
        'rust_sha256':{k:hashlib.sha256(Path(cmd[0]).read_bytes()).hexdigest() for k,(cmd,env) in variants.items() if k.startswith('rust')}},
        'compatibility':{},'benchmarks':[]}
    a,b=small(out/'correctness')
    for command,paths in [('scan',[a]),('diff',[a,b])]:
        reports={k:run(cmd+[command,*map(str,paths),'--format','json'],env) for k,(cmd,env) in variants.items()}
        for language in ('python','rust'):
            same=normalize(reports[language+'-0.1'])==normalize(reports[language+'-0.2'])
            result['compatibility'][f'{language}-{command}']=same
            if not same: raise AssertionError(f'Regression: {language} {command}')
    workloads=[('many-small-300',lambda r:scale(r,100),20000),
               ('many-small-1500',lambda r:scale(r,500),20000),
               ('dense-60',lambda r:scale(r,20,100),20000),
               ('moved-150',moved,22500)]
    for name,generate,limit in workloads:
        a,b=generate(out/name)
        commands={k:(cmd+['diff',str(a),str(b),'--format','json','--max-pairs',str(limit)],env) for k,(cmd,env) in variants.items()}
        samples={k:[] for k in commands}
        for cmd,env in commands.values(): run([str(measure)]+cmd,env)
        keys=list(commands)
        for n in range(args.repeats):
            for k in (keys if n%2==0 else keys[::-1]):
                cmd,env=commands[k]
                sample=run([str(measure)]+cmd,env)
                if sample['exit_code']!=0: raise RuntimeError(sample)
                samples[k].append(sample)
        summary={k:{'median_seconds':statistics.median(r['wall_seconds'] for r in rows),
                    'min_seconds':min(r['wall_seconds'] for r in rows),
                    'max_seconds':max(r['wall_seconds'] for r in rows),
                    'median_peak_rss_mib':statistics.median(r['peak_rss_kib'] for r in rows)/1024}
                 for k,rows in samples.items()}
        reports={k:run(cmd,env) for k,(cmd,env) in commands.items()}
        compatibility={lang:normalize(reports[lang+'-0.1'])==normalize(reports[lang+'-0.2']) for lang in ('python','rust')}
        if not all(compatibility.values()): raise AssertionError(compatibility)
        row={'name':name,'files_per_project':sum(p.is_file() for p in a.rglob('*')),
            'similarity_matches':len(reports['rust-0.2']['similarities']),
            'bytes_per_project':sum(p.stat().st_size for p in a.rglob('*') if p.is_file()),
            'samples':samples,'summary':summary,'full_result_equal_to_0_1':compatibility,
            'search':reports['rust-0.2']['similarity_search'],
            'new_python_rust_fields_equal':all(reports['python-0.2'][k]==reports['rust-0.2'][k] for k in
                ('pairs','counts','similarities','similarity_search','dependencies_added','dependencies_removed','duplicates'))}
        result['benchmarks'].append(row)
        (out/'results.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in row.items() if k!='samples'}),flush=True)


if __name__=='__main__':main()
