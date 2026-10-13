"""Closed contracts for inert extraction plans and verifiable worker results."""

def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'additionalProperties': False,
            'required': list(properties) if required is None else required}


def arr(items, maximum=10000, **kwargs):
    return {'type': 'array', 'items': items, 'maxItems': maximum, **kwargs}


TEXT = {'type': 'string', 'maxLength': 4000}
NAME = {'type': 'string', 'minLength': 1, 'maxLength': 240}
HASH = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
NODE_ID = {'type': 'string', 'pattern': '^s_[0-9a-f]{32}$'}
GROUP_ID = {'type': 'string', 'pattern': '^g_[0-9a-f]{32}$'}
KEY = {'type': 'string', 'pattern': '^gt_[0-9a-f]{32}$'}
PATH = {'type': 'string', 'minLength': 1, 'maxLength': 1000}
SLUG = {'type': 'string', 'pattern': '^[a-z][a-z0-9_-]{0,79}$'}
LANG = {'type': 'string', 'enum': ['python', 'javascript', 'typescript', 'tsx', 'php', 'bash']}
KIND = {'type': 'string', 'enum': ['package', 'artifact']}
BOOL = {'type': 'boolean'}
UINT = {'type': 'integer', 'minimum': 0}

PACKAGE = obj({'id': SLUG, 'name': NAME, 'project': NAME, 'path': PATH,
               'language': LANG, 'kind': KIND})
CONFIG = obj({
    'schema': {'const': 'grapholog.extraction-config.v1'},
    'coordinator': NAME, 'queue': SLUG,
    'new_package_project': NAME, 'new_package_directory': PATH,
    'include_tests': BOOL, 'include_shape': BOOL,
    'max_groups': {'type': 'integer', 'minimum': 1, 'maximum': 500},
    'packages': arr(PACKAGE, 1000),
    'routes': arr(obj({'package_id': SLUG, 'language': LANG, 'path_glob': PATH,
                       'symbol_glob': NAME}), 1000),
    'repositories': arr(obj({'project': NAME, 'github': {
        'type': 'string', 'pattern': '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'}}), 1000),
}, ['schema', 'coordinator'])

PROPOSAL = obj({
    'schema': {'const': 'planfile.ticket-proposal.v1'}, 'proposal_id': NAME,
    'dedupe_key': NAME, 'name': TEXT, 'description': {'type': 'string', 'maxLength': 100000},
    'priority': {'enum': ['critical', 'high', 'normal', 'low']},
    'source': obj({'tool': {'const': 'grapholog'}, 'tool_version': NAME,
                   'finding_id': NAME, 'artifact_digest': HASH}),
    'labels': arr(NAME, 30), 'files': arr(PATH),
    'acceptance_criteria': arr(TEXT, 30), 'evidence_refs': arr(TEXT),
})
OCCURRENCE = obj({'id': NODE_ID, 'project': NAME, 'path': PATH, 'sha256': HASH,
                  'name': NAME, 'line': UINT, 'end_line': UINT,
                  'body_hash': HASH, 'shape_hash': HASH, 'role': NAME})
TARGET = obj({'id': NAME, 'name': NAME, 'project': NAME, 'path': PATH,
              'language': LANG, 'kind': KIND, 'action': {'enum': ['create', 'extend']}})
FAMILY = obj({'id': NAME, 'group_ids': arr(GROUP_ID, 1000), 'language': LANG,
              'match': {'enum': ['function-body', 'function-shape']},
              'selection': {'enum': ['new-package', 'existing-code', 'explicit-route', 'ambiguous']},
              'target_id': TEXT, 'candidate_targets': arr(NAME, 1000),
              'occurrence_ids': arr(NODE_ID), 'review_required': {'const': True}})
TASK = obj({'key': KEY, 'stage': {'enum': ['route-review', 'design', 'package', 'verify', 'migrate', 'validate']},
            'project': NAME, 'target_ids': arr(NAME, 1000), 'family_ids': arr(NAME, 1000),
            'group_ids': arr(GROUP_ID, 1000), 'evidence_ids': arr(NODE_ID),
            'depends_on': arr(KEY, 10000), 'input_digest': HASH,
            'result_path': PATH, 'proposal': PROPOSAL})
PLAN = obj({'schema': {'const': 'grapholog.extraction-plan.v1'}, 'plan_id': NAME,
            'plan_hash': HASH, 'snapshot_id': HASH, 'config': CONFIG,
            'targets': arr(TARGET, 1000), 'families': arr(FAMILY, 1000),
            'occurrences': arr(OCCURRENCE, 100000), 'tasks': arr(TASK, 10000),
            'selection': obj({'available_groups': UINT, 'selected_families': UINT,
                              'omitted_groups': UINT, 'skipped_test_occurrences': UINT}),
            'limitations': arr(TEXT, 30)})
ARTIFACT = obj({'path': PATH, 'sha256': HASH})
RESULT = obj({'schema': {'const': 'grapholog.task-result.v1'}, 'task_key': KEY,
              'input_digest': HASH, 'outcome': {'enum': ['succeeded', 'needs_review']},
              'artifacts': arr(ARTIFACT, 100, minItems=1),
              'tests': arr(obj({'name': NAME, 'passed': BOOL, 'evidence': ARTIFACT}), 100),
              'package': obj({'name': NAME, 'version': NAME, 'kind': KIND,
                              'exports': arr(obj({'family_id': NAME, 'import_path': NAME,
                                                  'symbol': NAME}), 1000)}),
              'license_review': obj({'status': {'enum': ['compatible', 'needs_review']}, 'evidence': ARTIFACT}),
              'notes': arr(TEXT, 30),
}, ['schema', 'task_key', 'input_digest', 'outcome', 'artifacts', 'tests', 'notes'])

SCHEMAS = {'extraction-config': CONFIG, 'extraction-plan': PLAN, 'task-result': RESULT,
           'ticket-proposal': PROPOSAL}
