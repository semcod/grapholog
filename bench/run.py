"""Reproduce correctness comparison and paired, sequential end-to-end timings."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from corpus import small, scale


def collect(command):
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def correctness(python, rust, directory):
    a, b = small(directory)
    left_py = collect(python+["scan",str(a),"--format","json"])
    left_rs = collect(rust+["scan",str(a),"--format","json"])
    diff_py = collect(python+["diff",str(a),str(b),"--format","json"])
    diff_rs = collect(rust+["diff",str(a),str(b),"--format","json"])
    expected_py = json.loads((directory/"expected-python.json").read_text())
    expected_rs = {**expected_py,"quote.py":"changed","paren.py":"changed"}
    actual = lambda data: {p["right"] or p["left"]:p["status"] for p in data["pairs"]}
    py_status, rs_status = actual(diff_py), actual(diff_rs)
    py_files, rs_files = {f["path"]:f for f in left_py["files"]},{f["path"]:f for f in left_rs["files"]}
    shared = [p for p,f in py_files.items() if f["language"] in {"javascript","typescript","tsx","php","bash"} and f["status"]=="parsed"]
    differences = []
    fields = ["sha256","syntax_hash","shape_hash","fingerprints","nodes","symbols","imports","calls","logic"]
    for p in shared:
        for key in fields:
            if py_files[p][key]!=rs_files[p][key]:
                differences.append({"path":p,"field":key,"python":py_files[p][key],"rust":rs_files[p][key]})
    case_failures = {
        "python": [{"path":p,"expected":s,"actual":py_status.get(p)} for p,s in expected_py.items() if py_status.get(p)!=s],
        "rust": [{"path":p,"expected":s,"actual":rs_status.get(p)} for p,s in expected_rs.items() if rs_status.get(p)!=s],
    }
    inv = lambda data: [(f["path"],f["kind"],f["sha256"]) for f in data["files"]]
    result = {"fixture_files":len(py_files),"expected_cases":len(expected_py),
        "case_failures":case_failures,"inventory_equal":inv(left_py)==inv(left_rs),
        "shared_parser_files":len(shared),"shared_fields_checked":fields,"shared_differences":differences,
        "status_differences":[{"path":p,"python":py_status[p],"rust":rs_status.get(p)} for p in py_status if py_status[p]!=rs_status.get(p)],
        "cycles_equal":left_py["cycles"]==left_rs["cycles"],"cycles":left_py["cycles"],
        "dependencies_equal":diff_py["dependencies_added"]==diff_rs["dependencies_added"] and diff_py["dependencies_removed"]==diff_rs["dependencies_removed"]}
    for name,data in [("python-scan.json",left_py),("rust-scan.json",left_rs),("python-diff.json",diff_py),("rust-diff.json",diff_rs)]:
        (directory/name).write_text(json.dumps(data,indent=2),encoding="utf-8")
    return result


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--rust",type=Path,required=True)
    args.add_argument("--output",type=Path,required=True)
    args.add_argument("--sizes",default="10,100,500")
    args.add_argument("--repeats",type=int,default=7)
    args.add_argument("--functions-per-file",type=int,default=1)
    args.add_argument("--correctness-only",action="store_true")
    cfg=args.parse_args()
    cfg.output.mkdir(parents=True,exist_ok=True)
    python=[sys.executable,"-m","grapholog"];rust=[str(cfg.rust.resolve())]
    result={"correctness":correctness(python,rust,cfg.output/"correctness"),"benchmarks":[]}
    result["environment"]={"platform":platform.platform(),"python":sys.version,"cpu_count":os.cpu_count(),
        "cpu_model":next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),"unknown"),
        "packages":{p:importlib.metadata.version(p) for p in ["tree-sitter","tree-sitter-javascript","tree-sitter-typescript","tree-sitter-php","tree-sitter-bash"]},
        "method":"Sequential alternating order; 1 warmup per engine; end-to-end CLI diff + JSON to /dev/null; warm filesystem cache; release Rust; single CPU affinity; no compile time.",
        "rust_binary_sha256":hashlib.sha256(cfg.rust.read_bytes()).hexdigest()}
    if not cfg.correctness_only:
        cpu=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{cpu});result["environment"]["pinned_cpu"]=cpu
        measure=cfg.output/"measure"
        subprocess.run(["cc","-O2",str(Path(__file__).with_name("measure.c")),"-o",str(measure)],check=True)
        for count in map(int,cfg.sizes.split(',')):
            a,b=scale(cfg.output/f"size-{count}",count,cfg.functions_per_file)
            commands={"python":python+["diff",str(a),str(b),"--format","json"],"rust":rust+["diff",str(a),str(b),"--format","json"]}
            samples={name:[] for name in commands}
            for command in commands.values():collect([str(measure)]+command)
            for repeat in range(cfg.repeats):
                for name in (["python","rust"] if repeat%2==0 else ["rust","python"]):
                    sample=collect([str(measure)]+commands[name])
                    if sample["exit_code"]!=0:raise RuntimeError(sample)
                    samples[name].append(sample)
            summaries={}
            for name,rows in samples.items():
                values=[r["wall_seconds"] for r in rows]
                summaries[name]={"median_seconds":statistics.median(values),"min_seconds":min(values),"max_seconds":max(values),
                    "iqr_seconds":statistics.quantiles(values,n=4)[2]-statistics.quantiles(values,n=4)[0] if len(values)>1 else 0,
                    "median_peak_rss_mib":statistics.median(r["peak_rss_kib"] for r in rows)/1024}
            # Structural-result agreement is independently checked on this same corpus.
            dp,dr=collect(commands["python"]),collect(commands["rust"])
            compare_keys=["pairs","counts","dependencies_added","dependencies_removed","similarities","duplicates"]
            mismatches=[k for k in compare_keys if dp[k]!=dr[k]]
            row={"modules_per_project":count,"files_per_project":count*3,"functions_per_ts_file":cfg.functions_per_file,"bytes_per_project":sum(p.stat().st_size for p in a.rglob('*') if p.is_file()),
                "repeats":cfg.repeats,"samples":samples,"summary":summaries,"output_mismatch_fields":mismatches,
                "speedup":summaries["python"]["median_seconds"]/summaries["rust"]["median_seconds"]}
            result["benchmarks"].append(row)
            print(json.dumps({"files":count*3,"speedup":row["speedup"],"summary":summaries,"mismatches":mismatches}),flush=True)
            (cfg.output/"results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    (cfg.output/"results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result["correctness"],indent=2))


if __name__=="__main__":main()
