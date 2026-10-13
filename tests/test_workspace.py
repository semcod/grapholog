import asyncio
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from grapholog.contracts import gbnf, schema, validate
from grapholog.scan import scan
from grapholog.service import query, validate_proposals
from grapholog.workspace import connect_read, discover, index_workspace, project_specs
from grapholog.workspace_render import export


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.roots=[]
        for i,(name,arg) in enumerate([('norm','x'),('clean','x'),('adjust','value')]):
            root=self.base/f'p{i}';root.mkdir();self.roots.append(root)
            (root/'pyproject.toml').write_text('[project]\nname="sample"\n')
            (root/'main.py').write_text(f'def {name}({arg}):\n    if {arg} < 0:\n        return 0\n    return {arg} + 1\n\n{name}(3)\n')
            (root/'image.bin').write_bytes(b'\x00asset')
            (root/'generated').mkdir();(root/'generated/copied.py').write_text((root/'main.py').read_text())
            (root/'README.md').write_text('not parsed')
            (root/'LICENSE').write_text('Example test license')
            (root/'tests').mkdir();(root/'tests/test_core.py').write_text(f'from main import {name}\ndef test_value():\n    assert {name}(1) == 2\n')
        self.specs=project_specs(self.roots);self.db=self.base/'index.sqlite'
        self.result=index_workspace(self.db,self.specs)

    def core_group(self,kind='function-body'):
        groups=query(self.db,'clones',match=kind)['data']['items']
        for g in groups:
            members=query(self.db,'members',group_id=g['id'])['data']['items']
            if all(m['path']=='main.py' for m in members):return g,members
        self.fail('Expected main.py clone group')

    def test_global_groups_and_shape_are_distinct(self):
        exact,members=self.core_group();shape,renamed=self.core_group('function-shape')
        self.assertEqual(exact['project_count'],2);self.assertEqual(len(members),2)
        self.assertEqual(shape['project_count'],3);self.assertEqual(len(renamed),3)
        self.assertFalse(shape['semantic_equivalence_proven'])
        self.assertEqual(exact['effects'],'unknown')
        self.assertEqual(query(self.db,'summary')['data']['counts']['files'],12)

    def test_profile_skips_before_open_and_keeps_tests_and_context(self):
        from unittest.mock import patch
        opened=[];original=os.open
        def track(path,*a,**kw):opened.append(Path(path).name);return original(path,*a,**kw)
        with patch('grapholog.scan.os.open',track):report=scan(self.roots[0],profile='logic')
        self.assertNotIn('image.bin',opened);self.assertNotIn('copied.py',opened);self.assertNotIn('README.md',opened)
        self.assertIn('test_core.py',opened);self.assertIn('LICENSE',opened);self.assertIn('pyproject.toml',opened)
        excluded={x['path']:x['reason'] for x in report.runtime['filter']['excluded_entries']}
        self.assertEqual(excluded['generated'],'generated-or-tool-directory')
        (self.roots[0]/'engine.rs').write_text('fn run() {}')
        index_workspace(self.db,self.specs)
        self.assertFalse(query(self.db,'summary')['data']['coverage_complete_within_policy'])
        self.assertEqual(query(self.db,'summary')['data']['excluded_reasons']['unsupported-language'],1)

    def test_discovery_stops_at_roots_and_overlap_rejected(self):
        (self.roots[0]/'nested').mkdir();(self.roots[0]/'nested/package.json').write_text('{}')
        found=discover([self.base]);self.assertEqual(len(found),3)
        with self.assertRaisesRegex(ValueError,'overlap'):project_specs([self.roots[0],self.roots[0]/'nested'])
        manifest=self.base/'projects.json';manifest.write_text(json.dumps({'schema':'grapholog.workspace-config.v1','projects':[{'id':'a','path':'p0'},{'id':'b','path':'p1'}]}))
        self.assertEqual(len(project_specs(manifest=manifest)),2)

    def test_pagination_and_input_validation(self):
        a=query(self.db,'nodes',limit=2)['data'];b=query(self.db,'nodes',limit=2,offset=2)['data']
        self.assertTrue(a['truncated']);self.assertFalse({n['id'] for n in a['items']} & {n['id'] for n in b['items']})
        for params in ({'limit':0},{'limit':201},{'extra':'oops'},{'offset':-1}):
            with self.assertRaises(ValueError):query(self.db,'nodes',**params)
        with self.assertRaises(ValueError):query(self.db,'execute',command='touch /tmp/no')

    def test_evidence_callers_and_changed_source(self):
        _,members=self.core_group();node=members[0]
        ev=query(self.db,'evidence',node_id=node['id'],include_source=True)['data']
        self.assertEqual(ev['source']['status'],'verified');self.assertIn('return',ev['source']['text'])
        self.assertGreater(len(ev['neighbors']['incoming_calls']),0)
        with connect_read(self.db) as c:root=Path(c.execute('SELECT root FROM projects WHERE id=?',(node['project_id'],)).fetchone()[0])
        (root/node['path']).write_text('CHANGED SOURCE')
        changed=query(self.db,'evidence',node_id=node['id'],include_source=True)['data']
        self.assertEqual(changed['source']['status'],'changed');self.assertEqual(changed['source']['text'],'')

    def test_symlinked_current_source_is_not_followed(self):
        _,members=self.core_group();node=members[0]
        with connect_read(self.db) as c:root=Path(c.execute('SELECT root FROM projects WHERE id=?',(node['project_id'],)).fetchone()[0])
        source=root/node['path'];outside=self.base/'sentinel';outside.write_text(source.read_text())
        source.unlink();source.symlink_to(outside)
        ev=query(self.db,'evidence',node_id=node['id'],include_source=True)['data']
        self.assertEqual(ev['source']['status'],'unavailable')

    def test_context_budget_binding_and_reference_validation(self):
        group,members=self.core_group()
        packet=query(self.db,'context',group_ids=[group['id']],max_chars=20000,include_source=True)['data']
        self.assertLessEqual(sum(len(m['content']) for m in packet['messages']),20000)
        body=json.loads(packet['messages'][1]['content']);self.assertEqual(len(body['projects']),2)
        self.assertTrue(all('context_files' in p for p in body['projects']))
        ids=packet['selected_evidence_ids'];self.assertEqual(len(ids),2)
        reply={'schema':'grapholog.refactor-proposals.v1','snapshot_id':packet['snapshot_id'],'recommendations':[
            {'group_id':group['id'],'decision':'extract_library','library_name':'normalization','rationale':'Wspólna składnia do oceny.',
             'evidence_ids':ids,'risks':['Stan i licencje wymagają sprawdzenia.'],'tests':['Sprawdzić granice i wartości ujemne.'],'confidence':0.6}]}
        self.assertTrue(validate_proposals(packet,reply)['valid'])
        for field,value in [('snapshot_id','0'*64),('ref','s_'+'0'*32),('count',[ids[0]])]:
            bad=copy.deepcopy(reply)
            if field=='snapshot_id':bad[field]=value
            elif field=='ref':bad['recommendations'][0]['evidence_ids']=[value]
            else:bad['recommendations'][0]['evidence_ids']=value
            with self.assertRaises(ValueError):validate_proposals(packet,bad)
        self.assertIn(group['id'],packet['grammar'])
        self.assertIn(ids[0],packet['grammar'])

    def test_full_exports_schema_and_graph_integrity(self):
        output=io.StringIO();export(self.db,output,'json');graph=json.loads(output.getvalue());validate('graph',graph)
        node_ids={n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['source'] in node_ids and e['target'] in node_ids for e in graph['edges']))
        output=io.StringIO();export(self.db,output,'ndjson');records=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len([r for r in records if r['type']=='node']),len(graph['nodes']))
        output=io.StringIO();export(self.db,output,'html');self.assertNotIn('__GRAPHOLOG_DATA__',output.getvalue())
        self.assertIn('snapshot_id',output.getvalue());self.assertIn('connect-src',output.getvalue())

    def test_snapshot_stability_and_rollback(self):
        first=self.result['snapshot_id']
        self.assertEqual(index_workspace(self.db,self.specs,cache_dir=self.base/'cache')['snapshot_id'],first)
        self.assertEqual(index_workspace(self.db,self.specs,cache_dir=self.base/'cache')['snapshot_id'],first)
        bad=[self.specs[0],{'id':'missing','path':str(self.base/'does-not-exist')}]
        with self.assertRaises(ValueError):index_workspace(self.db,bad)
        self.assertEqual(query(self.db,'summary')['snapshot_id'],first)
        (self.roots[0]/'main.py').write_text('def changed(x):\n    return x * 5\n')
        self.assertNotEqual(index_workspace(self.db,self.specs)['snapshot_id'],first)

    def test_cli_rest_and_grpc_same_contract(self):
        from grapholog.transports import RestServer, grpc_server
        request={'schema':'grapholog.request.v1','operation':'summary','params':{}}
        expected=query(self.db,'summary')
        cli=subprocess.run([sys.executable,'-m','grapholog','workspace','query','--db',str(self.db),'--request','-'],
                           input=json.dumps(request),capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(cli.stdout),expected)
        server=RestServer(self.db,0,'test-token');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_address[1]}/v1/query'
        def http(req,headers=None):
            return urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(req).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer test-token',**(headers or {})}),timeout=5)
        with http(request) as response:self.assertEqual(json.load(response),expected)
        extraction_request={'schema':'grapholog.request.v1','operation':'extraction_plan',
                            'params':{'config':{'schema':'grapholog.extraction-config.v1','coordinator':'p0'}}}
        extraction_expected=query(self.db,'extraction_plan',**extraction_request['params'])
        with http(extraction_request) as response:self.assertEqual(json.load(response),extraction_expected)
        for headers,code in [({'Origin':'https://evil.example'},403),({'Authorization':'bad'},401)]:
            with self.assertRaises(urllib.error.HTTPError) as caught:http(request,headers)
            self.assertEqual(caught.exception.code,code)
        if importlib.util.find_spec('grpc'):
            import grpc
            from grapholog.proto import grapholog_pb2 as pb,grapholog_pb2_grpc as rpc
            grpc_,port=grpc_server(self.db,0,'test-token');grpc_.start();self.addCleanup(lambda:grpc_.stop(0).wait())
            with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
                stub=rpc.GraphologStub(channel);metadata=(('authorization','Bearer test-token'),)
                result=stub.Query(pb.JsonRequest(json_utf8=json.dumps(request).encode()),metadata=metadata,timeout=5)
                self.assertEqual(json.loads(result.json_utf8),expected)
                result=stub.Query(pb.JsonRequest(json_utf8=json.dumps(extraction_request).encode()),metadata=metadata,timeout=5)
                self.assertEqual(json.loads(result.json_utf8),extraction_expected)
                definition=stub.Contract(pb.ContractRequest(name='request',format='schema'),metadata=metadata,timeout=5)
                self.assertEqual(json.loads(definition.content),schema('request'))
                with self.assertRaises(grpc.RpcError) as caught:stub.Query(pb.JsonRequest(json_utf8=b'{"schema":"bad"}'),metadata=metadata,timeout=5)
                self.assertEqual(caught.exception.code(),grpc.StatusCode.INVALID_ARGUMENT)

    @unittest.skipUnless(importlib.util.find_spec('mcp'),'mcp optional dependency')
    def test_mcp_real_stdio_client(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        expected=query(self.db,'summary')
        async def exchange():
            args=StdioServerParameters(command=sys.executable,args=['-m','grapholog','serve','mcp','--db',str(self.db)],env=dict(os.environ))
            async with stdio_client(args) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize();listing=await session.list_tools()
                    self.assertEqual(len(listing.tools),10)
                    result=await session.call_tool('grapholog_summary',{})
                    self.assertFalse(result.isError);self.assertEqual(result.structuredContent,expected)
                    self.assertEqual(json.loads(result.content[0].text),expected)
                    params={'config':{'schema':'grapholog.extraction-config.v1','coordinator':'p0'}}
                    extraction=await session.call_tool('grapholog_extraction_plan',params)
                    self.assertFalse(extraction.isError)
                    self.assertEqual(extraction.structuredContent,query(self.db,'extraction_plan',**params))
                    invalid=await session.call_tool('grapholog_nodes',{'limit':0});self.assertTrue(invalid.isError)
                    resource=await session.read_resource('grapholog://schema/request')
                    self.assertEqual(json.loads(resource.contents[0].text),schema('request'))
        asyncio.run(exchange())

    @unittest.skipUnless(os.environ.get('GRAPHOLOG_RUST_BIN'),'native binary required')
    def test_native_profile_and_multi_project_groups(self):
        binary=os.environ['GRAPHOLOG_RUST_BIN']
        for root in self.roots:
            (root/'calc.js').write_text('export function twice(x) { if (x < 0) return 0; return x * 2; }\n')
        index_workspace(self.db,self.specs)
        native=self.base/'rust.sqlite';index_workspace(native,self.specs,engine='rust',rust_bin=binary,jobs=4)
        py=query(self.db,'clones',language='javascript')['data']['items'];rs=query(native,'clones',language='javascript')['data']['items']
        self.assertEqual(py,rs)
        def ignored(db):return {(r['project_id'],r['path'],r['reason']) for r in query(db,'exclusions',limit=200)['data']['items']}
        self.assertEqual(ignored(self.db),ignored(native))
        result=subprocess.run([binary,'scan',str(self.roots[0]),'--profile','logic','--format','json'],capture_output=True,text=True,check=True)
        native_report=json.loads(result.stdout);python_report=scan(self.roots[0],profile='logic')
        self.assertEqual(native_report['excluded'],python_report.excluded)


if __name__=='__main__':unittest.main()
