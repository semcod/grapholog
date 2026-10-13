"""Reproduce local Planfile ticket generation without invoking a model or GitHub."""
import json
from pathlib import Path
import shutil
import tempfile

from grapholog.planfile_bridge import load_state, materialize, status
from grapholog.service import query
from grapholog.workspace import index_workspace, project_specs


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'results/tickets'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='grapholog-ticket-demo-') as directory:
        work = Path(directory)
        shutil.copytree(root / 'examples/extraction', work / 'example')
        database, state_path = work / 'index.sqlite', work / 'state.json'
        index_workspace(database, project_specs(manifest=work / 'example/projects.json'))
        config = json.loads((work / 'example/catalog.json').read_text())
        plan = query(database, 'extraction_plan', config=config)['data']
        created = materialize(database, plan, state_path)
        repeated = materialize(database, plan, state_path)
        current = status(state_path)
        state = load_state(state_path)
        from planfile import Planfile
        tickets = []
        for task in plan['tasks']:
            binding = state['bindings'][task['key']]
            pf = Planfile(state['projects'][task['project']])
            ticket = pf.get_ticket(binding['ticket_id']).model_dump(mode='json')
            tickets.append({'project': task['project'], 'task_key': task['key'], 'ticket': ticket})
        outputs = {'example-plan.json': plan, 'example-planfile-tickets.json': tickets,
                   'example-status.json': current, 'integration-demo.json': {
                       'projects': len(state['projects']), 'targets': [{k:t[k] for k in ('name','project','path','action')} for t in plan['targets']],
                       'tasks': len(plan['tasks']), 'created_first_run': len(created['created']),
                       'created_second_run': len(repeated['created']), 'reused_second_run': len(repeated['reused']),
                       'runnable_stages': [r['stage'] for r in current['tickets'] if r['runnable']],
                       'source_code_changed': False, 'github_called': False, 'model_called': False}}
        for name, value in outputs.items():
            (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(outputs['integration-demo.json'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
