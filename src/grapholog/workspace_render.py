"""Full streaming graph exports and a bounded, offline clone map."""
from importlib.resources import files
import json
from .render import safe
from .service import _meta, summary
from .workspace import canonical, connect_read


def visual_data(conn, limit):
    meta=_meta(conn)
    groups=[]
    for row in conn.execute('SELECT record FROM clone_groups ORDER BY project_count DESC,repeated_nodes DESC,id LIMIT ?',(limit,)):
        group=json.loads(row[0])
        group['project_counts']={r[0]:r[1] for r in conn.execute('SELECT project_id,COUNT(*) FROM members WHERE group_id=? GROUP BY project_id ORDER BY project_id',(group['id'],))}
        sql='''SELECT s.record FROM members m JOIN symbols s ON m.node_id=s.id WHERE m.group_id=?
          ORDER BY ROW_NUMBER() OVER (PARTITION BY m.project_id ORDER BY m.node_id),m.project_id LIMIT 40'''
        group['members']=[json.loads(r[0]) for r in conn.execute(sql,(group['id'],))]
        group['omitted_members']=group['occurrences']-len(group['members']);groups.append(group)
    projects=[json.loads(r[0]) for r in conn.execute('SELECT record FROM projects ORDER BY label')]
    return {'snapshot_id':meta['snapshot_id'],'summary':summary(conn,meta),'projects':projects,'groups':groups,
            'omitted_groups':conn.execute('SELECT COUNT(*) FROM clone_groups').fetchone()[0]-len(groups)}


def export(database, handle, format_='ndjson', limit=100):
    conn=connect_read(database)
    try:
        meta=_meta(conn);head={'schema':'grapholog.graph.v1','snapshot_id':meta['snapshot_id'],'summary':summary(conn,meta)}
        if format_ in {'json','ndjson'}:
            if format_=='json':handle.write(canonical(head)[:-1])
            else:handle.write(canonical({'schema':'grapholog.graph-record.v1','type':'meta','value':head})+'\n')
            for plural,kind,sql in [('nodes','node','SELECT record FROM nodes ORDER BY id'),('edges','edge','SELECT record FROM edges ORDER BY id'),
                                   ('exclusions','exclusion','SELECT project_id,path,reason,bytes FROM exclusions ORDER BY project_id,path')]:
                if format_=='json':handle.write(',"'+plural+'":[')
                first=True
                for row in conn.execute(sql):
                    value=dict(row) if kind=='exclusion' else json.loads(row[0])
                    if format_=='json':
                        if not first:handle.write(',')
                        handle.write(canonical(value));first=False
                    else:handle.write(canonical({'schema':'grapholog.graph-record.v1','type':kind,'value':value})+'\n')
                if format_=='json':handle.write(']')
            if format_=='json':handle.write('}\n')
        elif format_=='html':
            payload=canonical(visual_data(conn,limit)).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
            template=files('grapholog').joinpath('data/workspace.html').read_text()
            handle.write(template.replace('__GRAPHOLOG_DATA__',payload))
        elif format_ in {'dot','terminal'}:
            data=visual_data(conn,limit);labels={p['id']:p['label'] for p in data['projects']}
            if format_=='dot':
                handle.write('digraph grapholog {\nrankdir=LR; graph [label="Grapholog: clone groups (bounded view)",labelloc=t]; node [shape=box];\n')
                emitted=set()
                for group in data['groups']:
                    handle.write(json.dumps(group['id'])+' [shape=hexagon,label='+json.dumps(group['match']+' / '+str(group['occurrences'])+' occurrences')+'];\n')
                    for m in group['members']:
                        pid=m['project_id'];sid=m['id']
                        if pid not in emitted:handle.write(json.dumps(pid)+' [label='+json.dumps(labels[pid])+',style=filled,fillcolor="#e5edf8"];\n');emitted.add(pid)
                        if sid not in emitted:
                            label=m['path']+':'+str(m['symbol']['line'])+' '+m['label']
                            handle.write(json.dumps(sid)+' [label='+json.dumps(label)+'];\n');emitted.add(sid)
                            handle.write(json.dumps(pid)+' -> '+json.dumps(sid)+';\n')
                        handle.write(json.dumps(sid)+' -> '+json.dumps(group['id'])+' [label="clone"];\n')
                handle.write('// omitted groups: '+str(data['omitted_groups'])+'; per-group members capped at 40\n}\n')
            else:
                counts=data['summary']['counts']
                handle.write('GRAPHOLOG WORKSPACE 0.5\n'+safe(data['snapshot_id'])+'\n')
                handle.write(f"{counts['projects']} projects | {counts['files']} included files | {counts['symbols']} symbols | {counts['cross_project_groups']} cross-project groups\n")
                for g in data['groups']:
                    handle.write('\n'+safe(g['id'])+' '+g['match']+f" [{g['language']}] projects={g['project_count']} occurrences={g['occurrences']}\n")
                    for m in g['members']:
                        handle.write('  '+safe(labels[m['project_id']])+' / '+safe(m['path'])+':'+str(m['symbol']['line'])+' -> '+safe(m['label'])+'\n')
                    if g['omitted_members']:handle.write(f"  ... {g['omitted_members']} further members (query members)\n")
                handle.write(f"\nView omitted {data['omitted_groups']} groups. JSON/NDJSON contains the complete indexed graph.\n")
        else:raise ValueError('Unsupported graph format')
    finally:conn.close()
