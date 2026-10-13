"""Explicit planning, local materialization, GitHub synchronization and Koru delegation."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from .extraction import config_defaults
from .planfile_bridge import catalog, dispatch, materialize, read_json, run, status, sync_github
from .render import safe
from .service import query


def parser():
    p = argparse.ArgumentParser(prog='grapholog tickets')
    commands = p.add_subparsers(dest='action', required=True)
    init = commands.add_parser('config', help='emit an extraction catalog template')
    init.add_argument('--coordinator', required=True)
    init.add_argument('-o', '--output', type=Path)
    plan = commands.add_parser('plan', help='compile an inert extraction DAG and ticket proposals')
    plan.add_argument('--db', type=Path, required=True)
    plan.add_argument('--config', type=Path, required=True)
    plan.add_argument('-o', '--output', type=Path)
    create = commands.add_parser('materialize', help='create or reuse local Planfile tickets; no source edits')
    create.add_argument('plan', type=Path)
    create.add_argument('--db', type=Path, required=True)
    create.add_argument('--state', type=Path, required=True)
    show = commands.add_parser('status', help='read authoritative Planfile state and verify result artifacts')
    show.add_argument('--state', type=Path, required=True)
    show.add_argument('--reconcile', action='store_true', help='update dependency wait labels from live evidence')
    register = commands.add_parser('catalog', help='derive the next catalog from verified package results')
    register.add_argument('--state', type=Path, required=True)
    register.add_argument('-o', '--output', type=Path)
    sync = commands.add_parser('sync', help='sync only this plan through the Planfile GitHub backend')
    sync.add_argument('--state', type=Path, required=True)
    sync.add_argument('--direction', choices=('push', 'pull', 'both'), default='both')
    sync.add_argument('--execute', action='store_true', help='perform remote synchronization; default is a local preview')
    for name in ('dispatch', 'run'):
        execute = commands.add_parser(name, help='delegate runnable tickets to Koru; default prints argv')
        execute.add_argument('--state', type=Path, required=True)
        execute.add_argument('--koru-bin', default='koru')
        execute.add_argument('--timeout', type=int, default=900)
        execute.add_argument('--execute', action='store_true')
        if name == 'dispatch':execute.add_argument('--limit', type=int, default=1)
        else:
            execute.add_argument('--max-steps', type=int, default=20)
            execute.add_argument('--sync-github', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == 'config':
            result = config_defaults({'schema': 'grapholog.extraction-config.v1', 'coordinator': args.coordinator})
        elif args.action == 'plan':
            result = query(args.db, 'extraction_plan', config=read_json(args.config))['data']
        elif args.action == 'materialize':
            result = materialize(args.db, read_json(args.plan), args.state)
        elif args.action == 'status':
            result = status(args.state, reconcile=args.reconcile)
        elif args.action == 'catalog':
            result = catalog(args.state)
        elif args.action == 'sync':
            result = sync_github(args.state, direction=args.direction, execute=args.execute)
        else:
            binary = str(Path(args.koru_bin).resolve()) if '/' in args.koru_bin else args.koru_bin
            if args.action == 'run':
                result = run(args.state, koru_bin=binary, max_steps=args.max_steps, timeout=args.timeout,
                             execute=args.execute, github=args.sync_github)
            else:
                result = dispatch(args.state, koru_bin=binary, limit=args.limit, timeout=args.timeout, execute=args.execute)
        text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        output = getattr(args, 'output', None)
        if output:
            with output.open('x', encoding='utf-8') as stream:
                stream.write(text)
        else:
            sys.stdout.write(text)
        return 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        # Planfile and provider errors have several exception types. Never echo a
        # provider response containing credentials. Details stay in Planfile logs.
        if isinstance(exc, (ValueError, KeyError, OSError, sqlite3.Error, subprocess.SubprocessError)):
            message = safe(exc)
        else:
            message = type(exc).__name__ + ': operation failed; inspect the Planfile/project logs'
        print('grapholog tickets: ' + message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
