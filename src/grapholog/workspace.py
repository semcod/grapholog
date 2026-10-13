"""Disk-backed multi-project graph; project-sized parsing, global indexed clone groups."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from . import __version__
from .scan import DEFAULT_EXCLUDES, scan
from .selection import policy

APPLICATION_ID = 0x47524C47
DATABASE_VERSION = 1
MARKERS = {'pyproject.toml', 'package.json', 'Cargo.toml', 'composer.json', 'go.mod',
           'pom.xml', 'build.gradle', 'CMakeLists.txt', '.git'}
NON_FUNCTIONS = {'class', 'interface', 'type', 'enum', 'trait'}


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identifier(kind, *parts):
    return kind + '_' + hashlib.sha256(canonical(parts).encode()).hexdigest()[:32]


def discover(roots, max_depth=5):
    """Stop at the first project marker; explicit manifests handle markerless projects."""
    found = []
    pruned = DEFAULT_EXCLUDES | set(policy()['prune_directories'])
    for base_ in roots:
        base = Path(base_).expanduser().resolve(strict=True)
        if not base.is_dir():
            raise ValueError(f'Not a directory: {base}')
        todo = [(base, 0)]
        while todo:
            folder, depth = todo.pop()
            entries = sorted(folder.iterdir())
            if any(p.name in MARKERS for p in entries):
                rel = folder.relative_to(base).as_posix()
                found.append({'id': base.name if rel == '.' else base.name + '/' + rel, 'path': str(folder)})
            elif depth < max_depth:
                todo.extend((p, depth + 1) for p in reversed(entries)
                            if p.name not in pruned and not p.is_symlink() and p.is_dir())
    return found


def project_specs(roots=(), *, manifest=None, auto_discover=False, max_depth=5):
    if manifest:
        if roots or auto_discover:
            raise ValueError('Use a manifest or positional roots, not both')
        manifest = Path(manifest).expanduser().resolve(strict=True)
        data = json.loads(manifest.read_text())
        from .contracts import validate
        validate('workspace-config', data)
        specs = [{'id': p['id'], 'path': str((manifest.parent / p['path']).resolve())} for p in data['projects']]
    elif auto_discover:
        specs = discover(roots, max_depth)
    else:
        specs = [{'id': Path(p).expanduser().resolve().name, 'path': str(p)} for p in roots]
    if not specs:
        raise ValueError('No projects found; supply roots or an explicit project manifest')
    if len(specs) > 10000:
        raise ValueError('At most 10000 projects per index')
    used_names, used_paths = set(), []
    for item in specs:
        name = item['id']
        if not isinstance(name, str) or not name.strip() or len(name) > 240 or name in used_names:
            raise ValueError('Project IDs must be unique, nonempty and at most 240 characters; use a manifest')
        path = Path(item['path']).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f'Not a project directory: {path}')
        if any(path.is_relative_to(old) or old.is_relative_to(path) for old in used_paths):
            raise ValueError('Project roots overlap; index a repository once or select disjoint packages')
        item['path'] = str(path)
        used_names.add(name); used_paths.append(path)
    return sorted(specs, key=lambda x: x['id'])


DDL = '''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, label TEXT NOT NULL, root TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT NOT NULL, file_id TEXT NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS node_project ON nodes(project_id,kind,id);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS symbols(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, file_id TEXT NOT NULL, language TEXT NOT NULL,
 parser TEXT NOT NULL, body_hash TEXT NOT NULL, shape_hash TEXT NOT NULL, nodes INTEGER NOT NULL, is_function INTEGER NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS symbol_body ON symbols(language,parser,body_hash,nodes);
CREATE INDEX IF NOT EXISTS symbol_shape ON symbols(language,parser,shape_hash,nodes);
CREATE INDEX IF NOT EXISTS symbol_file ON symbols(file_id,id);
CREATE TABLE IF NOT EXISTS edges(id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS edge_source ON edges(source,kind,id);
CREATE INDEX IF NOT EXISTS edge_target ON edges(target,kind,id);
CREATE TABLE IF NOT EXISTS clone_groups(id TEXT PRIMARY KEY, match TEXT NOT NULL, language TEXT NOT NULL, parser TEXT NOT NULL,
 hash TEXT NOT NULL, occurrences INTEGER NOT NULL, project_count INTEGER NOT NULL, repeated_nodes INTEGER NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS clone_rank ON clone_groups(project_count DESC,repeated_nodes DESC,id);
CREATE TABLE IF NOT EXISTS members(group_id TEXT NOT NULL, node_id TEXT NOT NULL, project_id TEXT NOT NULL, PRIMARY KEY(group_id,node_id));
CREATE INDEX IF NOT EXISTS member_node ON members(node_id,group_id);
CREATE TABLE IF NOT EXISTS exclusions(project_id TEXT NOT NULL, path TEXT NOT NULL, reason TEXT NOT NULL, bytes INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS excluded_project ON exclusions(project_id,reason,path);
'''


def connect_read(path):
    path = Path(path).expanduser().resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    if conn.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or conn.execute('PRAGMA user_version').fetchone()[0] != DATABASE_VERSION:
        conn.close()
        raise ValueError('Not a supported Grapholog workspace index')
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')  # Every service request sees one committed snapshot.
    return conn


def _writer(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('Index path cannot be a symbolic link')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        check = connect_read(path); check.close()
    else:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=FULL')
    conn.executescript(DDL)
    conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
    conn.execute(f'PRAGMA user_version={DATABASE_VERSION}')
    return conn


def _node(conn, item, file_id=''):
    conn.execute('INSERT OR IGNORE INTO nodes VALUES (?,?,?,?,?)',
                 (item['id'], item['kind'], item.get('project_id',''), file_id, canonical(item)))


def _edge(conn, source, target, kind, **attributes):
    item = {'id': identifier('e', source, target, kind, attributes), 'source': source,
            'target': target, 'kind': kind, 'attributes': attributes}
    conn.execute('INSERT OR IGNORE INTO edges VALUES (?,?,?,?,?)',
                 (item['id'], source, target, kind, canonical(item)))


def _add_project(conn, spec, report, digest):
    pid = identifier('p', spec['id'])
    files = report['files']
    counts = dict(sorted(Counter(f['status'] for f in files).items()))
    omitted = report.get('runtime', {}).get('filter', {}).get('excluded_entries')
    if omitted is None:
        omitted = [{'path': p, 'reason':'default-or-user-exclude', 'bytes':0} for p in report['excluded']]
    reasons = dict(sorted(Counter(x['reason'] for x in omitted).items()))
    complete = not report['warnings'] and not any(counts.get(s,0) for s in ('parse-error','read-error','changed-during-scan','parser-missing'))
    complete = complete and not reasons.get('unsupported-language',0) and not any(f['language']!='other' and f['status'] in ('size-limit','binary','non-utf8') for f in files)
    record = {'id':pid, 'kind':'project', 'project_id':pid, 'label':spec['id'],
              'coverage':{'statuses':counts,'excluded_reasons':reasons,'excluded_entries':len(omitted),
                          'known_skipped_bytes':sum(x['bytes'] for x in omitted),'complete_within_policy':bool(complete)},
              'warnings':report['warnings'], 'options':report['options'], 'cycles':report['cycles']}
    conn.execute('INSERT INTO projects VALUES (?,?,?,?)',(pid,spec['id'],spec['path'],canonical(record)))
    _node(conn, record)
    digest.update(canonical(record).encode())
    conn.executemany('INSERT INTO exclusions VALUES (?,?,?,?)', [(pid,x['path'],x['reason'],x['bytes']) for x in omitted])
    digest.update(canonical(omitted).encode())
    file_ids, symbol_ids, scopes = {}, {}, {}
    for f in files:
        fid = identifier('f',spec['id'],f['path']); file_ids[f['path']] = fid
        # Keep fingerprints on disk, not in every API node card.
        conn.execute('INSERT INTO files VALUES (?,?,?,?)',(fid,pid,f['path'],canonical(f)))
        card = {'id':fid,'kind':'file','project_id':pid,'label':f['path'],'path':f['path'],
                'language':f['language'],'parser':f['parser'],'status':f['status'],'sha256':f['sha256'],
                'role': 'test' if any(part in {'test','tests','__tests__'} for part in Path(f['path']).parts) or Path(f['path']).name.startswith('test_') else 'source' if f['language']!='other' else 'context',
                'lines':f['lines'],'nodes':f['nodes'],'symbol_count':len(f['symbols'])}
        _node(conn,card,fid);_edge(conn,pid,fid,'contains')
        digest.update(canonical(f).encode())
        for ordinal,s in enumerate(f['symbols']):
            sid = identifier('s', spec['id'],f['path'],s['qualname'],s['line'],ordinal)
            symbol_ids[f"{f['path']}::{s['qualname']}@{s['line']}"] = sid
            scopes.setdefault((fid,s['qualname']),[]).append((s['line'],s['end_line'],sid))
            item = {'id':sid,'kind':'symbol','project_id':pid,'file_id':fid,'label':s['qualname'],
                    'path':f['path'],'language':f['language'],'parser':f['parser'],'sha256':f['sha256'],
                    'role':card['role'],'symbol':s}
            _node(conn,item,fid);_edge(conn,fid,sid,'contains')
            is_function = int(s['kind'] not in NON_FUNCTIONS and f['status']=='parsed')
            conn.execute('INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (sid,pid,fid,f['language'],f['parser'],s['body_hash'],s['shape_hash'],s['nodes'],is_function,canonical(item)))
    for f in files:
        fid=file_ids[f['path']]
        for imp in f['imports']:
            targets = [file_ids[p] for p in imp['targets'] if p in file_ids]
            if not targets:
                xid = identifier('x',pid,'module',imp['module'])
                _node(conn,{'id':xid,'kind':'external','project_id':pid,'label':imp['module'],'resolution':imp['status']})
                targets=[xid]
            for target in targets:
                _edge(conn,fid,target,'imports',line=imp['line'],module=imp['module'],resolution=imp['status'])
        for call in f['calls']:
            possible=[x for x in scopes.get((fid,call['caller']),[]) if x[0]<=call['line']<=x[1]]
            source=min(possible,key=lambda x:x[1]-x[0])[2] if possible else fid
            target=symbol_ids.get(call['target'])
            if not target:
                target=identifier('x',pid,'call',fid,call['name'])
                _node(conn,{'id':target,'kind':'external','project_id':pid,'label':call['name'],'resolution':call['resolution']})
            _edge(conn,source,target,'calls',line=call['line'],name=call['name'],resolution=call['resolution'],certainty='static-candidate')
    return {'project_id':pid,'coverage':record['coverage'],'runtime':report.get('runtime',{})}


def _groups(conn, minimum):
    for column,match in [('body_hash','function-body'),('shape_hash','function-shape')]:
        distinct = 'AND COUNT(DISTINCT body_hash)>1' if match=='function-shape' else ''
        sql=f'''SELECT language,parser,{column},COUNT(*),COUNT(DISTINCT project_id),SUM(nodes)-MAX(nodes)
                FROM symbols WHERE is_function=1 AND nodes>=? AND {column}!=''
                GROUP BY language,parser,{column} HAVING COUNT(*)>1 {distinct} ORDER BY language,parser,{column}'''
        for language,parser,hash_,count,projects,repeated in conn.execute(sql,(minimum,)):
            gid=identifier('g',match,language,parser,hash_)
            item={'id':gid,'kind':'clone_group','match':match,'language':language,'parser':parser,'fingerprint':hash_,
                  'occurrences':count,'project_count':projects,'repeated_nodes':repeated,
                  'equivalence':'syntax-except-declaration-name' if match=='function-body' else 'identifier-shape-heuristic',
                  'semantic_equivalence_proven':False,'effects':'unknown'}
            conn.execute('INSERT INTO clone_groups VALUES (?,?,?,?,?,?,?,?,?)',(gid,match,language,parser,hash_,count,projects,repeated,canonical(item)))
            _node(conn,item)
            for sid,pid in conn.execute(f'SELECT id,project_id FROM symbols WHERE is_function=1 AND nodes>=? AND language=? AND parser=? AND {column}=? ORDER BY id',(minimum,language,parser,hash_)):
                conn.execute('INSERT INTO members VALUES (?,?,?)',(gid,sid,pid))
                _edge(conn,gid,sid,'clone_member',match=match)


def index_workspace(database, specs, *, engine='python', rust_bin=None, jobs=1, cache_dir=None,
                    profile='logic', exclude=(), source_roots=(), max_bytes=2*1024*1024, min_nodes=12):
    if engine not in {'python','rust'} or not 1<=jobs<=64 or min_nodes<0 or max_bytes<0:
        raise ValueError('Invalid engine, jobs or analysis limit')
    if engine=='python' and jobs!=1:
        raise ValueError('Python scanner uses --jobs 1; select --engine rust for parallel file scanning')
    if engine=='rust':
        rust_bin=Path(rust_bin).expanduser().resolve(strict=True) if rust_bin else Path(__file__).resolve().parents[2]/'bin/grapholog-rs-linux-x86_64'
        if not rust_bin.is_file():
            raise ValueError('Specify --rust-bin with the Grapholog Rust binary')
        engine_identity={'engine':'rust','binary_sha256':hashlib.sha256(rust_bin.read_bytes()).hexdigest()}
    else:
        from .cache import identity
        engine_identity={'engine':'python','parser_identity':identity()}
    config={'schema':'grapholog.workspace.v1','version':__version__,'engine':engine_identity,
            'profile':profile,'filter_policy':policy() if profile=='logic' else None,
            'min_nodes':min_nodes,'max_bytes':max_bytes,'exclude':list(exclude),'source_roots':list(source_roots) or ['.','src']}
    conn=_writer(database);started=time.perf_counter();runtime=[]
    digest=hashlib.sha256(canonical(config).encode())
    try:
        conn.execute('BEGIN IMMEDIATE')
        for table in ('meta','projects','nodes','files','symbols','edges','clone_groups','members','exclusions'):
            conn.execute(f'DELETE FROM {table}')
        for spec in specs:
            extra=list(exclude)
            try:
                rel=Path(database).expanduser().resolve().relative_to(Path(spec['path'])).as_posix()
                extra.extend([rel,rel+'-wal',rel+'-shm',rel+'-journal'])
            except ValueError:pass
            if engine=='rust':
                cmd=[str(rust_bin),'scan',spec['path'],'--format','json','--profile',profile,'--jobs',str(jobs),'--max-bytes',str(max_bytes),'--min-nodes',str(min_nodes)]
                if cache_dir:cmd+=['--cache-dir',str(cache_dir)]
                for pattern in extra:cmd+=['--exclude',pattern]
                for source_root in source_roots:cmd+=['--source-root',source_root]
                try:result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=600)
                except subprocess.TimeoutExpired as exc:raise ValueError(f"Scan timed out for {spec['id']} after 600 seconds") from exc
                if result.returncode:raise ValueError(f"Scan failed for {spec['id']}: {result.stderr[:2000]}")
                report=json.loads(result.stdout)
            else:
                report=scan(spec['path'],exclude=extra,source_roots=list(source_roots) or None,max_bytes=max_bytes,cache_dir=cache_dir,profile=profile).to_dict()
            runtime.append(_add_project(conn,spec,report,digest))
        _groups(conn,min_nodes)
        snapshot=digest.hexdigest()
        meta={**config,'snapshot_id':snapshot,'project_count':len(specs),
              'limitations':['Static syntax/call candidates; not semantic equivalence or purity.',
                'Cross-project groups compare the same language and parser only.',
                'Imports/calls resolve within each project; external package identities are not inferred.',
                'Directory pruning counts entries, not unknown descendants. Tests and license context are retained.',
                'repeated_nodes is a syntactic ranking heuristic; nested symbols can overlap. Not saved LOC.',
                'A committed index is a scan result, not an atomic filesystem snapshot.'],
              'runtime':{'seconds':time.perf_counter()-started,'projects':runtime}}
        for key,value in meta.items():conn.execute('INSERT INTO meta VALUES (?,?)',(key,canonical(value)))
        conn.execute('COMMIT')
        return {'schema':'grapholog.index-result.v1','snapshot_id':snapshot,'projects':len(specs),
                'files':conn.execute('SELECT COUNT(*) FROM files').fetchone()[0],
                'symbols':conn.execute('SELECT COUNT(*) FROM symbols').fetchone()[0],
                'clone_groups':conn.execute('SELECT COUNT(*) FROM clone_groups').fetchone()[0],
                'cross_project_groups':conn.execute('SELECT COUNT(*) FROM clone_groups WHERE project_count>1').fetchone()[0],
                'excluded_entries':conn.execute('SELECT COUNT(*) FROM exclusions').fetchone()[0],
                'seconds':time.perf_counter()-started}
    except BaseException:
        if conn.in_transaction:conn.execute('ROLLBACK')
        raise
    finally:
        conn.close()
