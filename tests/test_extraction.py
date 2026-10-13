"""Real Planfile integration with simulated GitHub and bounded worker processes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from grapholog.extraction import digest, validate_plan
from grapholog.planfile_bridge import (HOLD, atomic_json, catalog, dispatch, load_state, materialize, run,
                                      status, sync_github, verify_result, _prepare_handoff, _stores)
from grapholog.service import query
from grapholog.workspace import index_workspace


class FakeGitHub:
    """Provider boundary simulator. The production Planfile sync code still runs."""
    def __init__(self, repository):
        self.config = {'repo': repository}
        self.items = {}
        self.created = 0
        self.read_ids = []
        self.fail_create = False

    def create_ticket(self, ticket):
        from planfile.sync.base import TicketRef, TicketState
        if self.fail_create:raise RuntimeError('simulated provider outage')
        self.created += 1
        id_ = str(self.created)
        url = 'https://github.com/' + self.config['repo'] + '/issues/' + id_
        self.items[id_] = TicketState(id=id_, name=ticket['name'], description=ticket['description'],
            status='open', labels=[x for x in ticket['labels'] if not x.startswith('dedupe:')],
            url=url, metadata=ticket.get('metadata', {}))
        return TicketRef(id=id_, url=url, key=self.config['repo'] + '#' + id_)

    def update_ticket(self, id_, **kwargs):
        old = self.items[id_]
        mapping = {'name': kwargs.get('name', old.name), 'description': kwargs.get('body', old.description),
                   'labels': [x for x in kwargs.get('labels', old.labels) if not x.startswith('dedupe:')],
                   'status': 'open' if kwargs.get('status') in {'open', 'in_progress', 'review'} else 'closed'}
        self.items[id_] = old.model_copy(update=mapping)

    def get_ticket(self, id_):
        self.read_ids.append(id_)
        return self.items[id_]

    def list_tickets(self, **kwargs):
        raise AssertionError('Grapholog must not fetch a whole GitHub backlog')

    def search_tickets(self, query):
        return [x for x in self.items.values() if x.metadata.get('planfile_id') == query]


@unittest.skipUnless(importlib.util.find_spec('planfile'), 'install grapholog[tickets]')
class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.specs = []
        for project in ('alpha', 'beta', 'gamma'):
            root = self.root / project
            (root / 'src').mkdir(parents=True)
            argument = 'text' if project == 'gamma' else 'value'
            (root / 'src/util.py').write_text(f'def normalize({argument}):\n    if {argument} is None:\n        return ""\n    return str({argument}).strip().lower()\n')
            (root / 'pyproject.toml').write_text(f'[project]\nname="{project}"\nversion="0.1.0"\n')
            self.specs.append({'id': project, 'path': str(root)})
        self.db, self.state_path = self.root / 'index.sqlite', self.root / 'bindings.json'
        index_workspace(self.db, self.specs)
        self.config = {'schema': 'grapholog.extraction-config.v1', 'coordinator': 'alpha'}

    def plan(self, **changes):
        return query(self.db, 'extraction_plan', config={**self.config, **changes})['data']

    def publish(self, plan=None):
        plan = plan or self.plan()
        result = materialize(self.db, plan, self.state_path)
        return plan, result

    def complete(self, task, *, outcome='succeeded'):
        from planfile import Planfile
        state = load_state(self.state_path)
        root = Path(state['projects'][task['project']])
        artifact = '.grapholog-artifacts/' + task['key'] + '/evidence.txt'
        path = root / artifact; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Test fixture result; no model was called.\n')
        ref = {'path': artifact, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        result = {'schema': 'grapholog.task-result.v1', 'task_key': task['key'], 'input_digest': task['input_digest'],
                  'outcome': outcome, 'artifacts': [ref], 'tests': [{'name': 'fixture', 'passed': True, 'evidence': ref}],
                  'notes': ['Synthetic test fixture.']}
        if task['stage'] == 'verify':
            target = next(t for t in state['plan']['targets'] if t['id'] in task['target_ids'])
            result['package'] = {'name': target['name'], 'version': '0.1.0', 'kind': target['kind'],
                'exports': [{'family_id': f, 'import_path': 'shared_keys', 'symbol': 'normalize'} for f in task['family_ids']]}
            result['license_review'] = {'status': 'compatible', 'evidence': ref}
        atomic_json(root / task['result_path'], result)
        pf = Planfile(str(root)); binding = state['bindings'][task['key']]
        pf.complete_ticket(binding['ticket_id'], artifacts=[task['result_path']], note='Fixture completion', actor='test')
        return root / task['result_path']

    def test_new_package_overlap_and_one_migration_per_project(self):
        from planfile.contracts import TicketProposalV1
        plan = self.plan()
        self.assertEqual(len(plan['families']), 1)
        self.assertEqual(plan['families'][0]['selection'], 'new-package')
        self.assertEqual(len(plan['families'][0]['group_ids']), 2)
        self.assertEqual(len(plan['tasks']), 7)
        self.assertEqual([t['project'] for t in plan['tasks'] if t['stage'] == 'migrate'], ['alpha', 'beta', 'gamma'])
        for task in plan['tasks']:
            proposal = TicketProposalV1.model_validate(task['proposal'])
            self.assertNotIn('executor', proposal.to_ticket_kwargs())
        self.assertEqual(plan, self.plan())
        self.assertFalse(any((self.root / p / '.planfile').exists() for p in ('alpha', 'beta', 'gamma')))

    def test_existing_owner_and_explicit_route(self):
        package = {'id': 'keys', 'name': 'shared-keys', 'project': 'gamma', 'path': 'src', 'language': 'python', 'kind': 'package'}
        plan = self.plan(packages=[package])
        self.assertEqual(plan['families'][0]['selection'], 'existing-code')
        self.assertEqual(plan['targets'][0]['action'], 'extend')
        self.assertEqual([t['project'] for t in plan['tasks'] if t['stage'] == 'migrate'], ['alpha', 'beta'])
        (self.root / 'gamma' / 'empty-package').mkdir()
        package['path'] = 'empty-package'
        route = {'package_id': 'keys', 'language': 'python', 'path_glob': 'src/*', 'symbol_glob': '*'}
        routed = self.plan(packages=[package], routes=[route])
        self.assertEqual(routed['families'][0]['selection'], 'explicit-route')

    def test_ambiguous_catalog_and_route_resolution(self):
        packages = [{'id': p, 'name': p + '-keys', 'project': p, 'path': 'src', 'language': 'python', 'kind': 'package'} for p in ('beta', 'gamma')]
        plan = self.plan(packages=packages)
        self.assertEqual([t['stage'] for t in plan['tasks']], ['route-review'])
        resolved = self.plan(packages=packages, routes=[{'package_id': 'gamma', 'language': 'python', 'path_glob': 'src/*', 'symbol_glob': '*'}])
        self.assertEqual(resolved['targets'][0]['id'], 'gamma')

    def test_catalog_validation_and_unknown_fields(self):
        with self.assertRaises(ValueError):self.plan(new_package_directory='../outside')
        with self.assertRaises(ValueError):self.plan(coordinator='missing')
        with self.assertRaises(ValueError):self.plan(executor='shell')
        with self.assertRaises(ValueError):self.plan(routes=[{'package_id': 'missing', 'language': 'python', 'path_glob': '*', 'symbol_glob': '*'}])

    def test_local_idempotence_including_terminal_ticket(self):
        plan, first = self.publish()
        self.assertEqual(len(first['created']), 7)
        self.complete(plan['tasks'][0])
        second = materialize(self.db, plan, self.state_path)
        self.assertEqual(second['created'], [])
        self.assertEqual(first['tickets'], second['tickets'])
        # Recover a lost bindings file through the durable Planfile identities.
        recovered = materialize(self.db, plan, self.root / 'recovered.json')
        self.assertEqual(recovered['created'], [])
        self.assertEqual(first['tickets'], recovered['tickets'])

    def test_stale_source_and_stale_snapshot_fail_before_writes(self):
        plan = self.plan()
        path = self.root / 'beta/src/util.py'
        path.write_text(path.read_text() + '\n# changed\n')
        with self.assertRaisesRegex(ValueError, 'Source changed'):materialize(self.db, plan, self.state_path)
        self.assertFalse((self.root / 'alpha/.planfile').exists())
        index_workspace(self.db, self.specs)
        with self.assertRaisesRegex(ValueError, 'stale'):materialize(self.db, plan, self.state_path)

    def test_tampered_plan_or_source_symlink_rejected(self):
        plan = self.plan()
        changed = copy.deepcopy(plan);changed['tasks'][0]['proposal']['name'] = 'Changed'
        with self.assertRaises(ValueError):validate_plan(changed)
        raw = {k:v for k,v in changed.items() if k not in {'plan_id', 'plan_hash'}}
        changed['plan_hash'] = digest(raw);changed['plan_id'] = 'extraction-' + changed['plan_hash'][:24]
        with self.assertRaisesRegex(ValueError, 'deterministic'):materialize(self.db, changed, self.state_path)
        source = self.root / 'beta/src/util.py';data = source.read_text();source.unlink()
        outside = self.root / 'outside.py';outside.write_text(data);source.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink'):materialize(self.db, plan, self.state_path)

    def test_cross_project_dependencies_require_artifacts_not_done_or_cancel(self):
        from planfile import Planfile
        plan, _ = self.publish()
        for task in plan['tasks'][:2]:self.complete(task)
        provider = plan['tasks'][2]
        binding = load_state(self.state_path)['bindings'][provider['key']]
        pf = Planfile(str(self.root / provider['project']))
        pf.update_ticket(binding['ticket_id'], status='done')
        rows = status(self.state_path, reconcile=True)['tickets']
        self.assertFalse(any(r['runnable'] for r in rows if r['stage'] == 'migrate'))
        self.complete(provider)
        rows = status(self.state_path, reconcile=True)['tickets']
        self.assertTrue(all(r['runnable'] for r in rows if r['stage'] == 'migrate'))
        evidence = self.root / provider['project'] / '.grapholog-artifacts' / provider['key'] / 'evidence.txt'
        evidence.write_text('tampered')
        rows = status(self.state_path, reconcile=True)['tickets']
        self.assertFalse(any(r['runnable'] for r in rows if r['stage'] == 'migrate'))

    def test_canceled_dependency_never_unlocks_work(self):
        from planfile import Planfile
        plan, _ = self.publish()
        task = plan['tasks'][0]
        pf = Planfile(str(self.root / task['project']))
        pf.update_ticket(load_state(self.state_path)['bindings'][task['key']]['ticket_id'], status='canceled')
        rows = status(self.state_path, reconcile=True)['tickets']
        self.assertFalse(any(r['runnable'] for r in rows))

    def test_hash_verified_artifact_handoff_to_consumer(self):
        plan, _ = self.publish()
        for task in plan['tasks'][:3]:self.complete(task)
        state = load_state(self.state_path)
        consumer = next(t for t in plan['tasks'] if t['stage'] == 'migrate' and t['project'] == 'beta')
        _prepare_handoff(state, _stores(state), consumer)
        handoff = json.loads((self.root / 'beta/.planfile/grapholog/handoffs' / (consumer['key'] + '.json')).read_text())
        for item in handoff['dependencies'][0]['local_artifacts']:
            path = self.root / 'beta' / item['local_path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item['sha256'])

    def test_dispatch_does_not_treat_exit_zero_as_completion_or_retry_it(self):
        plan, _ = self.publish()
        worker = self.root / 'fake-koru'
        worker.write_text('#!' + sys.executable + '\nimport sys\nprint("Simulated worker", sys.argv[1:])\n')
        worker.chmod(0o755)
        preview = dispatch(self.state_path)
        self.assertFalse(preview['attempts'][0]['executed'])
        run = dispatch(self.state_path, koru_bin=str(worker), execute=True)
        self.assertEqual(run['attempts'][0]['returncode'], 0)
        self.assertFalse(any(r['runnable'] for r in run['tickets']))
        self.assertEqual(dispatch(self.state_path, koru_bin=str(worker), execute=True)['attempts'], [])
        state = load_state(self.state_path)
        self.assertEqual(len(state['dispatches']), 1)

    def test_dispatch_timeout_records_uncertain_attempt(self):
        self.publish()
        worker = self.root / 'fake-koru'
        worker.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(20)\n')
        worker.chmod(0o755)
        run = dispatch(self.state_path, koru_bin=str(worker), timeout=1, execute=True)
        self.assertEqual(run['attempts'][0]['state'], 'timeout')
        self.assertFalse(any(r['runnable'] for r in run['tickets']))

    def test_github_scoped_sync_dedupe_and_closed_without_receipt(self):
        plan = self.plan(repositories=[{'project': p, 'github': 'fixture/' + p} for p in ('alpha', 'beta', 'gamma')])
        self.publish(plan)
        backends = {p: FakeGitHub('fixture/' + p) for p in ('alpha', 'beta', 'gamma')}
        factory = lambda project, repository: backends[project]
        preview = sync_github(self.state_path)
        self.assertFalse(preview['executed'])
        sync_github(self.state_path, direction='push', execute=True, backend_factory=factory)
        self.assertEqual(sum(b.created for b in backends.values()), 7)
        self.assertIn('https://github.com/fixture/alpha/issues/', backends['beta'].items['1'].description)
        sync_github(self.state_path, direction='both', execute=True, backend_factory=factory)
        self.assertEqual(sum(b.created for b in backends.values()), 7)
        first = next(iter(backends['alpha'].items))
        old = backends['alpha'].items[first]
        backends['alpha'].items[first] = old.model_copy(update={'status': 'closed', 'metadata': {**old.metadata, 'state_reason': 'completed'}})
        pulled = sync_github(self.state_path, direction='pull', execute=True, backend_factory=factory)
        self.assertFalse(any(r['runnable'] for r in pulled['tickets'] if r['stage'] != 'design'))
        self.assertTrue(all('999' not in b.read_ids for b in backends.values()))

    def test_no_remote_sync_without_explicit_repository_bindings(self):
        self.publish()
        with self.assertRaisesRegex(ValueError, 'GitHub bindings'):sync_github(self.state_path)

    def test_partial_sync_retry_preserves_created_issue_identity(self):
        from planfile.sync.outbound import OutboundSyncError
        plan = self.plan(repositories=[{'project': p, 'github': 'fixture/' + p} for p in ('alpha', 'beta', 'gamma')])
        self.publish(plan)
        backends = {p: FakeGitHub('fixture/' + p) for p in ('alpha', 'beta', 'gamma')}
        backends['beta'].fail_create = True
        factory = lambda project, repository: backends[project]
        with self.assertRaises(OutboundSyncError):
            sync_github(self.state_path, direction='push', execute=True, backend_factory=factory)
        self.assertEqual(backends['alpha'].created, 5)
        backends['beta'].fail_create = False
        sync_github(self.state_path, direction='push', execute=True, backend_factory=factory)
        self.assertEqual(sum(b.created for b in backends.values()), 7)

    def test_cli_plan_is_same_inert_contract(self):
        config = self.root / 'catalog.json'
        config.write_text(json.dumps(self.config))
        done = subprocess.run([sys.executable, '-m', 'grapholog', 'tickets', 'plan', '--db', str(self.db),
                               '--config', str(config)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(done.stdout), self.plan())

    def test_actual_koru_parser_accepts_dispatch_contract(self):
        if not importlib.util.find_spec('koru'):
            self.skipTest('Koru optional executable not installed')
        from koru.cli_ticket_queue import build_ticket_parser
        args = build_ticket_parser().parse_args(['auto', 'PLF-001', '--project', str(self.root), '--queue', 'grapholog', '--no-sync'])
        self.assertFalse(args.sync)
        self.assertEqual(args.ticket_id, 'PLF-001')
        self.assertEqual(args.queue_name, 'grapholog')

    def test_verified_package_promoted_into_next_catalog(self):
        plan, _ = self.publish()
        self.assertEqual(catalog(self.state_path)['packages'], [])
        for task in plan['tasks'][:3]:self.complete(task)
        target = plan['targets'][0]
        (self.root / target['project'] / target['path']).mkdir(parents=True)
        promoted = catalog(self.state_path)
        self.assertEqual(promoted['packages'][0]['id'], target['id'])
        self.assertNotIn('action', promoted['packages'][0])

    def test_bounded_runner_stops_without_repeating_an_ambiguous_attempt(self):
        self.publish()
        worker = self.root / 'fake-koru'
        worker.write_text('#!' + sys.executable + '\nprint("worker fixture")\n');worker.chmod(0o755)
        result = run(self.state_path, koru_bin=str(worker), max_steps=20, execute=True)
        self.assertEqual(len(result['steps']), 1)
        self.assertFalse(result['limit_reached'])

    def test_tsx_language_can_be_planned(self):
        for spec in self.specs:
            (Path(spec['path']) / 'src/util.tsx').write_text('export function clamp(x: number) { if (x > 5) { return 5; } return x + 1; }\n')
        index_workspace(self.db, self.specs)
        self.assertTrue(any(f['language'] == 'tsx' for f in self.plan()['families']))


if __name__ == '__main__':
    unittest.main()
