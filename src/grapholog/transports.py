"""Adapters for a pre-indexed local workspace. No remote scan, shell or write methods."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib.parse import urlsplit

from .contracts import OP_DESCRIPTIONS, PARAMETERS, gbnf, openapi, response_schema, schema
from .service import dispatch, query
from .workspace import canonical

SCHEMA_NAMES=('request','response','graph','refactor','context','workspace-config','extraction-config','extraction-plan','task-result','ticket-proposal')
GRAMMAR_NAMES=('request','refactor','task-result','ticket-proposal')


def decode(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON key: '+key)
            result[key]=value
        return result
    def constant(value):raise ValueError('Non-finite JSON number: '+value)
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def contract(name, format_='schema'):
    if format_=='schema' and name in SCHEMA_NAMES:return 'application/schema+json',canonical(schema(name)).encode()
    if format_=='grammar' and name in GRAMMAR_NAMES:return 'text/plain; charset=utf-8',gbnf(schema(name)).encode()
    if format_=='openapi' and name=='openapi':return 'application/json',canonical(openapi()).encode()
    raise KeyError('Unknown contract')


class RestServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,database,port=8765,token=None):
        self.database=database;self.token=token
        super().__init__(('127.0.0.1',port),RestHandler)


class RestHandler(BaseHTTPRequestHandler):
    server_version='Grapholog/0.5'
    def setup(self):
        super().setup();self.connection.settimeout(15)
    def log_message(self,*args):pass
    def send(self,status,value,content_type='application/json'):
        raw=value if isinstance(value,bytes) else canonical(value).encode()
        self.send_response(status);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        self.wfile.write(raw)
    def authorize(self):
        port=self.server.server_address[1]
        hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        if self.headers.get('Host') not in hosts or self.headers.get('Origin') not in {None,*('http://'+h for h in hosts)}:
            self.send(403,{'error':'Host or Origin rejected'});return False
        token=self.server.token
        if token and not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
            self.send(401,{'error':'Authentication required'});return False
        return True
    def do_GET(self):
        if not self.authorize():return
        try:
            path=urlsplit(self.path).path
            if path=='/v1/summary':return self.send(200,query(self.server.database,'summary'))
            if path=='/v1/openapi.json':kind,data=contract('openapi','openapi')
            elif path.startswith('/v1/schema/'):kind,data=contract(path.rsplit('/',1)[-1],'schema')
            elif path.startswith('/v1/grammar/'):kind,data=contract(path.rsplit('/',1)[-1],'grammar')
            else:raise KeyError('Unknown endpoint')
            self.send(200,data,kind)
        except KeyError as exc:self.send(404,{'error':str(exc)})
        except (OSError,ValueError) as exc:self.send(400,{'error':str(exc)[:500]})
    def do_POST(self):
        if not self.authorize():return
        if urlsplit(self.path).path!='/v1/query':return self.send(404,{'error':'Unknown endpoint'})
        try:
            if self.headers.get('Transfer-Encoding'):raise ValueError('Chunked requests are unsupported')
            if self.headers.get_content_type()!='application/json':return self.send(415,{'error':'Expected application/json'})
            size=int(self.headers.get('Content-Length','-1'))
            if not 0<size<=65536:return self.send(413,{'error':'Request must contain 1..65536 bytes'})
            request=decode(self.rfile.read(size).decode('utf-8'))
            result=dispatch(self.server.database,request);self.send(200,result)
        except KeyError as exc:self.send(404,{'error':str(exc)})
        except (OSError,ValueError,UnicodeError) as exc:self.send(400,{'error':str(exc)[:500]})


async def serve_mcp(database):
    try:
        import mcp.types as types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError as exc:raise ValueError('Install grapholog[mcp]') from exc
    server=Server('grapholog',version='0.5.0')
    @server.list_tools()
    async def list_tools():
        return [types.Tool(name='grapholog_'+op,description=OP_DESCRIPTIONS[op],inputSchema=params,
                    outputSchema=response_schema(op),annotations=types.ToolAnnotations(
                        readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
                for op,params in PARAMETERS.items()]
    @server.call_tool()
    async def call_tool(name,arguments):
        if not name.startswith('grapholog_') or name[10:] not in PARAMETERS:raise ValueError('Unknown tool')
        return await asyncio.to_thread(query,database,name[10:],**arguments)
    @server.list_resources()
    async def resources():
        return [types.Resource(uri=f'grapholog://schema/{name}',name=name,mimeType='application/schema+json') for name in SCHEMA_NAMES] + [
            types.Resource(uri=f'grapholog://grammar/{name}',name=name+'.gbnf',mimeType='text/plain') for name in GRAMMAR_NAMES]
    @server.read_resource()
    async def read_resource(uri):
        parts=urlsplit(str(uri));format_=parts.netloc;name=parts.path.lstrip('/')
        _,data=contract(name,format_);return data.decode()
    async with stdio_server() as (reader,writer):
        await server.run(reader,writer,server.create_initialization_options())


def grpc_server(database,port=8766,token=None):
    try:
        import grpc
        from .proto import grapholog_pb2 as pb, grapholog_pb2_grpc as rpc
    except ImportError as exc:raise ValueError('Install grapholog[grpc]') from exc
    class Service(rpc.GraphologServicer):
        def auth(self,ctx):
            if token and not hmac.compare_digest(dict(ctx.invocation_metadata()).get('authorization',''),'Bearer '+token):
                ctx.abort(grpc.StatusCode.UNAUTHENTICATED,'Authentication required')
        def Query(self,req,ctx):
            self.auth(ctx)
            try:
                if len(req.json_utf8)>65536:raise ValueError('Request exceeds 65536 bytes')
                result=dispatch(database,decode(req.json_utf8.decode()))
                return pb.JsonResponse(json_utf8=canonical(result).encode())
            except KeyError as exc:ctx.abort(grpc.StatusCode.NOT_FOUND,str(exc))
            except (ValueError,OSError,UnicodeError) as exc:ctx.abort(grpc.StatusCode.INVALID_ARGUMENT,str(exc)[:500])
        def Contract(self,req,ctx):
            self.auth(ctx)
            try:
                kind,data=contract(req.name,req.format or 'schema')
                return pb.ContractResponse(media_type=kind,content=data)
            except KeyError as exc:ctx.abort(grpc.StatusCode.NOT_FOUND,str(exc))
    server=grpc.server(ThreadPoolExecutor(max_workers=4),options=[('grpc.max_receive_message_length',131072),('grpc.max_send_message_length',8*1024*1024)])
    rpc.add_GraphologServicer_to_server(Service(),server)
    selected=server.add_insecure_port(f'127.0.0.1:{port}')
    if not selected:raise OSError('Cannot bind gRPC port')
    return server,selected
