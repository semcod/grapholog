"""Build the zipapp and exercise release artifacts from a clean installed environment.

Run with an interpreter containing the 0.5 wheel and its workspace/parsers/mcp/grpc/tickets
extras. This does not use src/ on PYTHONPATH and does not call an LLM.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipapp
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results' / 'tickets'
VERSION = '0.5.0'


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    import grapholog
    from grapholog.proto import grapholog_pb2, grapholog_pb2_grpc

    installed = Path(grapholog.__file__).resolve().parent
    assert installed != ROOT / 'src' / 'grapholog', 'Use the installed wheel, not src/'
    assert grapholog.__version__ == VERSION
    assert grapholog_pb2.JsonRequest(json_utf8=b'{}').json_utf8 == b'{}'
    assert grapholog_pb2_grpc.GraphologStub
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    checks = []

    with tempfile.TemporaryDirectory(prefix='grapholog-release-') as temporary:
        scratch = Path(temporary)
        staging = scratch / 'zipapp'
        shutil.copytree(ROOT / 'src' / 'grapholog', staging / 'grapholog',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (staging / '__main__.py').write_text('from grapholog.cli import main\nraise SystemExit(main())\n')
        zipapp.create_archive(staging, ROOT / 'grapholog.pyz', interpreter='/usr/bin/env python3', compressed=True)
        wheel = ROOT / 'python-dist' / f'grapholog-{VERSION}-py3-none-any.whl'
        source_files = [p for p in (ROOT / 'src' / 'grapholog').rglob('*')
                        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
        with zipfile.ZipFile(wheel) as archive, zipfile.ZipFile(ROOT / 'grapholog.pyz') as app:
            for source in source_files:
                relative = source.relative_to(ROOT / 'src').as_posix()
                assert archive.read(relative) == source.read_bytes(), relative
                assert app.read(relative) == source.read_bytes(), relative
                assert (installed / source.relative_to(ROOT / 'src' / 'grapholog')).read_bytes() == source.read_bytes(), relative
        checks.append(f'{len(source_files)} source/resource files identical in wheel, installed package and zipapp')

        def cli(*args, app=False, parse=True):
            command = [sys.executable, str(ROOT / 'grapholog.pyz')] if app else [str(Path(sys.executable).parent / 'grapholog')]
            completed = subprocess.run(command + [str(a) for a in args], cwd=scratch, env=env,
                                       capture_output=True, text=True, check=True, timeout=60)
            return json.loads(completed.stdout) if parse else completed.stdout

        for app in (False, True):
            assert cli('--version', app=app, parse=False).strip() == VERSION
            scanned = cli('scan', ROOT / 'examples/workspace/billing', '--profile', 'logic', '--format', 'json', app=app)
            assert scanned
        database = scratch / 'workspace.sqlite'
        cli('workspace', 'index', '--manifest', ROOT / 'examples/workspace/projects.json', '--db', database)
        summary = cli('workspace', 'query', '--db', database)
        assert summary['data']['counts']['projects'] == 3
        assert summary == cli('workspace', 'query', '--db', database, app=True)
        write_json('example-summary.json', summary)
        clones = cli('workspace', 'query', '--db', database, '--operation', 'clones')
        group = next(g for g in clones['data']['items'] if g['match'] == 'function-shape' and g['occurrences'] == 3)
        for fmt, filename in [('json', 'example-graph.json'), ('terminal', 'example-terminal.txt')]:
            (OUT / filename).write_text(cli('workspace', 'export', '--db', database, '--format', fmt, parse=False))
        html_path = scratch / 'map.html'
        cli('workspace', 'export', '--db', database, '--format', 'html', '-o', html_path, app=True, parse=False)
        assert '<svg' in html_path.read_text() or 'createElementNS' in html_path.read_text()
        packet_path = scratch / 'context.json'
        cli('workspace', 'context', '--db', database, '--group-id', group['id'], '--include-source', '-o', packet_path, app=True, parse=False)
        packet = json.loads(packet_path.read_text())
        assert len(packet['selected_evidence_ids']) == 3
        evidence = json.loads(packet['messages'][1]['content'])['evidence']
        assert all(item['source']['status'] == 'verified' for item in evidence)
        write_json('example-llm-context.json', packet)
        reply = {
            'schema': 'grapholog.refactor-proposals.v1', 'snapshot_id': packet['snapshot_id'],
            'recommendations': [{
                'group_id': group['id'], 'decision': 'needs_review', 'library_name': 'shared-key-normalization',
                'rationale': 'Ręcznie napisany przykład, bez wywołania modelu. Trzy funkcje normalizują klucz przez obsługę None oraz str(...).strip().lower(). Grupa kształtu wskazuje kandydata do wspólnej biblioteki; przed decyzją należy sprawdzić kontrakty wywołujących i zgodność licencji.',
                'evidence_ids': packet['selected_evidence_ids'],
                'risks': ['str(value) może wywoływać kod obiektu; czystość i równoważność semantyczna nie zostały dowiedzione.',
                          'Nie przeprowadzono audytu licencji, zależności ani zgodności kontraktów wszystkich wywołujących.'],
                'tests': ['Proponowane: None, pusty napis, białe znaki, Unicode, liczba oraz obiekt z własnym __str__.',
                          'Proponowane: testy regresji wywołujących w każdym projekcie po ewentualnym wydzieleniu biblioteki.'],
                'confidence': 0.5,
            }],
        }
        write_json('example-proposal-manual.json', reply)
        validation = cli('workspace', 'validate', OUT / 'example-llm-context.json', OUT / 'example-proposal-manual.json', app=True)
        assert validation['valid'] and not validation['code_changed']
        write_json('example-proposal-validation.json', validation)
        contract_dir = scratch / 'contracts'
        cli('contracts', '--dir', contract_dir, app=True)
        for definition in contract_dir.iterdir():
            assert definition.read_bytes() == (ROOT / 'contracts' / definition.name).read_bytes(), definition.name
        fixture = scratch / 'extraction'
        shutil.copytree(ROOT / 'examples/extraction', fixture)
        index = scratch / 'tickets.sqlite'
        cli('workspace', 'index', '--manifest', fixture / 'projects.json', '--db', index)
        plan_path = scratch / 'plan.json'
        cli('tickets', 'plan', '--db', index, '--config', fixture / 'catalog.json', '-o', plan_path, app=True, parse=False)
        state_path = scratch / 'state.json'
        created = cli('tickets', 'materialize', plan_path, '--db', index, '--state', state_path, app=True)
        repeated = cli('tickets', 'materialize', plan_path, '--db', index, '--state', state_path)
        assert len(created['created']) == 10 and repeated['created'] == []
        assert cli('tickets', 'dispatch', '--state', state_path, app=True)['attempts']
        assert cli('tickets', 'catalog', '--state', state_path, app=True)['packages'][0]['id'] == 'keys'
        checks.append('installed wheel and zipapp: real Planfile materialization, dedupe, catalog and Koru argv preview')
        checks += ['installed CLI and zipapp: version, logic scan, shared query result',
                   'installed CLI: three-project index, full graph, terminal output',
                   'zipapp resources: HTML, contracts, source evidence, context and proposal validation']

    binary = ROOT / 'bin' / 'grapholog-rs-linux-x86_64'
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    version = subprocess.check_output([binary, '--version'], text=True).strip()
    assert VERSION in version
    checks.append('bundled native binary reports current release version; SHA-256 recorded')
    report = (ROOT / 'RAPORT.html').read_text()
    HTMLParser().feed(report)
    assert '<html' in report and '</html>' in report and '200' in report and 'GBNF' in report
    checks.append('report HTML parsed and expected content present')
    write_json('package-check.json', {'version': VERSION, 'passed': checks, 'llm_called': False})
    write_json('build-info.json', {'version': VERSION, 'python': sys.version, 'rust_sha256': digest,
        'dependencies': {name: importlib.metadata.version(name) for name in
                         ['planfile', 'jsonschema', 'mcp', 'grpcio', 'protobuf', 'tree-sitter', 'tree-sitter-javascript',
                          'tree-sitter-typescript', 'tree-sitter-php', 'tree-sitter-bash']}})
    print(json.dumps({'passed': len(checks), 'summary': summary['data']['counts']}, indent=2))


if __name__ == '__main__':
    main()
