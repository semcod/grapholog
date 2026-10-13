"""CLI benchmark of 0.2 versus 0.3, cold/warm parse cache and bounded workers.

Cold means a new, empty application cache; the OS filesystem cache remains warm.
All compared reports must match after removing only runtime metadata.
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
import tempfile
from corpus import scale, small


def core(data):
    data = copy.deepcopy(data)
    for p in [data, data.get('left', {}), data.get('right', {})]:
        p.pop('runtime', None)
    return data


def collect(command, env, cpus):
    os.sched_setaffinity(0, set(cpus))
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def stats(data):
    return {k:data[k].get('runtime', {}) for k in ('left','right')}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--current', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repeats', type=int, default=7)
    args = p.parse_args()
    old, new, out = args.baseline.resolve(), args.current.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    available = sorted(os.sched_getaffinity(0))
    single, parallel = available[:1], available[:4]
    workers = len(parallel)
    measure = out/'measure'
    subprocess.run(['cc','-O2',str(Path(__file__).with_name('measure.c')),'-o',str(measure)],check=True)
    env_old = {**os.environ, 'PYTHONPATH':str(old/'src')}
    env_new = {**os.environ, 'PYTHONPATH':str(new/'src')}
    rust_old, rust_new = old/'bin/grapholog-rs-linux-x86_64', new/'rust/target/release/grapholog-rs'
    result = {'environment':{'platform':platform.platform(),'python':sys.version,
        'single_cpu':single,'parallel_cpus':parallel,'workers':workers,
        'cpu_quota':Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
        'cpu_model':next(s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')),
        'method':'Alternating sequential variants, one warmup, 7 repeats by default. CLI diff + complete pretty JSON to /dev/null. wait4 peak child RSS. Cold = empty parse cache, warm OS cache. Single CPU except explicit parallel variants. Cache cleanup and compilation outside timed region.',
        'packages':{name:importlib.metadata.version(name) for name in ('tree-sitter','tree-sitter-javascript','tree-sitter-typescript','tree-sitter-php','tree-sitter-bash')},
        'rust_binary_sha256':{'0.2':hashlib.sha256(rust_old.read_bytes()).hexdigest(),'0.3':hashlib.sha256(rust_new.read_bytes()).hexdigest()}},
        'compatibility':{},'benchmarks':[]}
    a,b = small(out/'correctness')
    baseline = {}
    for lang,cmd,env in [('python',[sys.executable,'-m','grapholog'],env_old),('rust',[str(rust_old)],env_old)]:
        for operation,paths in [('scan',[a]),('diff',[a,b])]:
            baseline[lang,operation] = collect(cmd+[operation,*map(str,paths),'--format','json'],env,single)
    for lang,cmd in [('python',[sys.executable,'-m','grapholog']),('rust',[str(rust_new)])]:
        with tempfile.TemporaryDirectory(dir=out,prefix='correctness-cache-') as cache:
            for operation,paths in [('scan',[a]),('diff',[a,b])]:
                for mode in ('off','cold','warm'):
                    extra = ['--cache-dir',cache] if mode!='off' else []
                    if lang=='rust':extra += ['--jobs',str(workers)]
                    data = collect(cmd+[operation,*map(str,paths),'--format','json',*extra],env_new,parallel if lang=='rust' else single)
                    equal = core(data)==core(baseline[lang,operation])
                    result['compatibility'][f'{lang}-{operation}-{mode}'] = equal
                    if not equal:raise AssertionError((lang,operation,mode))
    for name,count,functions in [('many-small-1500',500,1),('dense-60',20,100)]:
        a,b = scale(out/name,count,functions)
        common = ['diff',str(a),str(b),'--format','json']
        definitions = {
            'python-0.2':([sys.executable,'-m','grapholog'],env_old,'off',single),
            'python-0.3-off':([sys.executable,'-m','grapholog'],env_new,'off',single),
            'python-0.3-cold':([sys.executable,'-m','grapholog'],env_new,'cold',single),
            'python-0.3-warm':([sys.executable,'-m','grapholog'],env_new,'warm',single),
            'rust-0.2':([str(rust_old)],env_old,'off',single),
            'rust-0.3-off':([str(rust_new)],env_new,'off',single),
            'rust-0.3-cold':([str(rust_new)],env_new,'cold',single),
            'rust-0.3-warm':([str(rust_new)],env_new,'warm',single),
            f'rust-0.3-jobs{workers}-off':([str(rust_new),'--jobs',str(workers)],env_new,'off',parallel),
            f'rust-0.3-jobs{workers}-warm':([str(rust_new),'--jobs',str(workers)],env_new,'warm',parallel),
        }
        # Place options after the command; the CLI accepts `diff` only as first argument.
        def command(definition, cache=None):
            cmd,env,mode,cpus=definition
            base=cmd[:1]+common+cmd[1:] if cmd[0]==str(rust_new) else cmd+common
            if cache is not None:base+=['--cache-dir',str(cache)]
            return base
        warmed={key:tempfile.TemporaryDirectory(dir=out,prefix='warm-cache-') for key,(_,_,mode,_) in definitions.items() if mode=='warm'}
        samples={key:[] for key in definitions}
        reports={}
        def execute(key, measured):
            definition=definitions[key];cmd,env,mode,cpus=definition
            temporary=tempfile.TemporaryDirectory(dir=out,prefix='cold-cache-') if mode=='cold' else None
            cache=temporary.name if temporary else warmed[key].name if mode=='warm' else None
            args_=command(definition,cache)
            try:return collect(([str(measure)]+args_) if measured else args_,env,cpus)
            finally:
                if temporary:temporary.cleanup()
        for key in definitions:
            sample=execute(key,True)
            if sample['exit_code']!=0:raise RuntimeError((key,sample))
        keys=list(definitions)
        for repetition in range(args.repeats):
            # Rotate and reverse order to vary both starting point and neighboring variants.
            order=keys[repetition%len(keys):]+keys[:repetition%len(keys)]
            if repetition%2:order.reverse()
            for key in order:
                sample=execute(key,True)
                if sample['exit_code']!=0:raise RuntimeError((key,sample))
                samples[key].append(sample)
        for key in definitions:reports[key]=execute(key,False)
        equality={key:core(data)==core(reports['python-0.2' if key.startswith('python') else 'rust-0.2']) for key,data in reports.items()}
        if not all(equality.values()):raise AssertionError(equality)
        summaries={key:{'median_seconds':statistics.median(r['wall_seconds'] for r in values),
            'min_seconds':min(r['wall_seconds'] for r in values),'max_seconds':max(r['wall_seconds'] for r in values),
            'median_peak_rss_mib':statistics.median(r['peak_rss_kib'] for r in values)/1024}
            for key,values in samples.items()}
        row={'name':name,'files_per_project':count*3,'functions_per_ts_file':functions,
            'bytes_per_project':sum(p.stat().st_size for p in a.rglob('*') if p.is_file()),
            'summary':summaries,'samples':samples,'full_analysis_equal_to_0_2':equality,
            'runtime':{key:stats(data) for key,data in reports.items()},
            'cache_disk':{key:{'entries':len(list(Path(temp.name).rglob('*.jsonl'))),
                'bytes':sum(p.stat().st_size for p in Path(temp.name).rglob('*') if p.is_file())}
                for key,temp in warmed.items()}}
        result['benchmarks'].append(row)
        (out/'results.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({'name':name,'summary':summaries,'all_equal':all(equality.values()),'cache_disk':row['cache_disk']}),flush=True)
        for temp in warmed.values():temp.cleanup()


if __name__=='__main__':main()
