"""Benchmark the read-only planner against an existing workspace index."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import time

from grapholog.service import query
from grapholog.workspace import connect_read


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:raise ValueError('repeats must be 1..20')
    conn = connect_read(args.db)
    coordinator = conn.execute('SELECT label FROM projects ORDER BY label LIMIT 1').fetchone()[0]
    conn.close()
    summary = query(args.db, 'summary')['data']
    config = {'schema': 'grapholog.extraction-config.v1', 'coordinator': coordinator}
    times = []
    for _ in range(args.repeats):
        start = time.perf_counter()
        plan = query(args.db, 'extraction_plan', config=config)['data']
        times.append(time.perf_counter() - start)
    result = {'projects': summary['counts']['projects'], 'indexed_files': summary['counts']['files'],
        'index_version': summary['version'], 'selected_families': len(plan['families']), 'targets': len(plan['targets']),
        'tasks': len(plan['tasks']), 'stages': dict(Counter(t['stage'] for t in plan['tasks'])),
        'migration_tasks': sum(t['stage'] == 'migrate' for t in plan['tasks']),
        'plan_json_bytes': len(json.dumps(plan).encode()), 'seconds': times, 'median_seconds': statistics.median(times),
        'method': 'Read-only Python planning on a saved synthetic JS/TS index; JSON Schema validation included. No Planfile writes, GitHub calls or model calls.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':main()
