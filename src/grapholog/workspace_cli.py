"""Workspace, contracts and transport commands, loaded lazily by the core CLI."""
import argparse
import asyncio
from contextlib import nullcontext
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import sys

from .contracts import gbnf, openapi, schema
from .render import safe
from .service import dispatch, query, validate_proposals
from .transports import GRAMMAR_NAMES, SCHEMA_NAMES, decode
from .workspace import canonical, index_workspace, project_specs
from .workspace_render import export


def parser():
    p=argparse.ArgumentParser(prog='grapholog');subs=p.add_subparsers(dest='area',required=True)
    workspace=subs.add_parser('workspace',help='indeks wielu projektow, klony i dowody');commands=workspace.add_subparsers(dest='action',required=True)
    index=commands.add_parser('index');index.add_argument('roots',nargs='*',type=Path)
    index.add_argument('--manifest',type=Path);index.add_argument('--discover',action='store_true');index.add_argument('--max-depth',type=int,default=5)
    index.add_argument('--db',type=Path,required=True);index.add_argument('--engine',choices=('python','rust'),default='python')
    index.add_argument('--rust-bin',type=Path);index.add_argument('--jobs',type=int,default=1);index.add_argument('--cache-dir',type=Path)
    index.add_argument('--profile',choices=('logic','inventory'),default='logic');index.add_argument('--exclude',action='append',default=[])
    index.add_argument('--source-root',action='append',default=[]);index.add_argument('--max-bytes',type=int,default=2*1024*1024);index.add_argument('--min-nodes',type=int,default=12)
    ask=commands.add_parser('query');ask.add_argument('--db',type=Path,required=True)
    ask.add_argument('--request',type=Path,help='JSON request file; - for stdin');ask.add_argument('--operation',default='summary');ask.add_argument('--params',default='{}')
    show=commands.add_parser('export');show.add_argument('--db',type=Path,required=True);show.add_argument('--format',choices=('terminal','json','ndjson','dot','html'),default='terminal')
    show.add_argument('--limit',type=int,default=100);show.add_argument('-o','--output',type=Path)
    context=commands.add_parser('context');context.add_argument('--db',type=Path,required=True);context.add_argument('--group-id',action='append',required=True)
    context.add_argument('--max-chars',type=int,default=40000);context.add_argument('--max-members',type=int,default=12);context.add_argument('--include-source',action='store_true');context.add_argument('-o','--output',type=Path)
    check=commands.add_parser('validate');check.add_argument('packet',type=Path);check.add_argument('reply',type=Path)
    contracts=subs.add_parser('contracts');contracts.add_argument('--dir',type=Path,required=True)
    serve=subs.add_parser('serve');serve.add_argument('transport',choices=('rest','mcp','grpc'));serve.add_argument('--db',type=Path,required=True)
    serve.add_argument('--port',type=int,help='default REST 8765, gRPC 8766; loopback only')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        result=None
        if args.area=='contracts':
            outputs={name+'.schema.json':json.dumps(schema(name),indent=2,ensure_ascii=False)+'\n' for name in SCHEMA_NAMES}
            outputs.update({name+'.gbnf':gbnf(schema(name)) for name in GRAMMAR_NAMES})
            outputs['openapi.json']=json.dumps(openapi(),indent=2)+'\n'
            outputs['grapholog.proto']=files('grapholog').joinpath('data/grapholog.proto').read_text()
            if any((args.dir/name).exists() for name in outputs):raise ValueError('Contract output exists; choose a new directory')
            args.dir.mkdir(parents=True,exist_ok=True)
            for name,content in outputs.items():
                with (args.dir/name).open('x',encoding='utf-8') as handle:handle.write(content)
            result={'contracts':list(outputs),'directory':str(args.dir)}
        elif args.area=='serve':
            # Validate the database before accepting connections. Servers query only this pre-indexed workspace.
            query(args.db,'summary');token=os.environ.get('GRAPHOLOG_API_TOKEN')
            if args.transport=='mcp':asyncio.run(__import__('grapholog.transports',fromlist=['serve_mcp']).serve_mcp(args.db))
            elif args.transport=='rest':
                from .transports import RestServer
                server=RestServer(args.db,args.port if args.port is not None else 8765,token)
                print(f'Grapholog REST: http://127.0.0.1:{server.server_address[1]}',file=sys.stderr)
                try:server.serve_forever()
                finally:server.server_close()
            else:
                from .transports import grpc_server
                server,port=grpc_server(args.db,args.port if args.port is not None else 8766,token);server.start()
                print(f'Grapholog gRPC: 127.0.0.1:{port}',file=sys.stderr)
                try:server.wait_for_termination()
                finally:server.stop(0).wait()
            return 0
        elif args.action=='index':
            if not 0<=args.max_depth<=30:raise ValueError('max-depth must be 0..30')
            for root in args.source_root:
                if Path(root).is_absolute() or '..' in Path(root).parts:raise ValueError('source-root must be relative inside each project')
            specs=project_specs(args.roots,manifest=args.manifest,auto_discover=args.discover,max_depth=args.max_depth)
            result=index_workspace(args.db,specs,engine=args.engine,rust_bin=args.rust_bin,jobs=args.jobs,cache_dir=args.cache_dir,
                profile=args.profile,exclude=args.exclude,source_roots=args.source_root,max_bytes=args.max_bytes,min_nodes=args.min_nodes)
        elif args.action=='query':
            if args.request:
                raw=sys.stdin.read(65537) if str(args.request)=='-' else args.request.read_text()
                if len(raw.encode())>65536:raise ValueError('Query exceeds 65536 bytes')
                result=dispatch(args.db,decode(raw))
            else:
                params=decode(args.params)
                if not isinstance(params,dict):raise ValueError('params must be a JSON object')
                result=query(args.db,args.operation,**params)
        elif args.action=='validate':
            result=validate_proposals(decode(args.packet.read_text()),decode(args.reply.read_text()))
        elif args.action in {'export','context'}:
            output=args.output
            if output and (output.exists() or output.is_symlink()):raise ValueError('Output exists; choose a new path')
            if args.action=='context':
                result=query(args.db,'context',group_ids=args.group_id,max_chars=args.max_chars,max_members=args.max_members,include_source=args.include_source)['data']
            elif not 1<=args.limit<=10000:raise ValueError('limit must be 1..10000')
            with output.open('x',encoding='utf-8') if output else nullcontext(sys.stdout) as handle:
                if args.action=='export':export(args.db,handle,args.format,args.limit)
                else:handle.write(json.dumps(result,ensure_ascii=True,indent=2)+'\n')
            return 0
        if result is not None:print(json.dumps(result,ensure_ascii=True,indent=2))
        return 0
    except KeyboardInterrupt:return 130
    except BrokenPipeError:
        sys.stdout=open(os.devnull,'w');return 0
    except (ValueError,KeyError,OSError,sqlite3.Error) as exc:
        print('grapholog: '+safe(exc),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
