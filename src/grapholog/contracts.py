"""One versioned semantic contract for CLI, MCP, REST, gRPC and LLM replies."""
from __future__ import annotations
import copy
from functools import lru_cache
import json

DRAFT = 'https://json-schema.org/draft/2020-12/schema'
ID = {'type':'string','pattern':'^[pfsxge]_[0-9a-f]{32}$'}
HASH = {'type':'string','pattern':'^[0-9a-f]{64}$'}
TEXT = {'type':'string'}
BOOL = {'type':'boolean'}
UINT = {'type':'integer','minimum':0}


def array(item, **kw): return {'type':'array','items':item,**kw}
def obj(properties, required=None):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}
def enum(*values): return {'type':'string','enum':list(values)}
def bounded(lo,hi): return {'type':'integer','minimum':lo,'maximum':hi}
def ref(name): return {'$ref':'#/$defs/'+name}


SYMBOL = obj({k:TEXT for k in ('name','qualname','kind','syntax_hash','body_hash','shape_hash')} |
             {k:UINT for k in ('line','end_line','nodes','decisions')})
IMPORT = obj({'module':TEXT,'kind':TEXT,'line':UINT,'names':array(TEXT),
              'bindings':{'type':'object','additionalProperties':TEXT},'targets':array(TEXT),'status':TEXT})
CALL = obj({'caller':TEXT,'name':TEXT,'line':UINT,'target':TEXT,'resolution':TEXT})
LOGIC = obj({'scope':TEXT,'kind':TEXT,'line':UINT,'depth':UINT,'label':TEXT})
COVERAGE = obj({'statuses':{'type':'object','additionalProperties':UINT},
                'excluded_reasons':{'type':'object','additionalProperties':UINT},
                'excluded_entries':UINT,'known_skipped_bytes':UINT,'complete_within_policy':BOOL})
PROJECT = obj({'id':ID,'kind':{'const':'project'},'project_id':ID,'label':TEXT,
               'coverage':COVERAGE,'warnings':array(TEXT),'options':{'type':'object'},'cycles':array(array(TEXT))})
FILE = obj({'id':ID,'kind':{'const':'file'},'project_id':ID,'label':TEXT,'path':TEXT,
            'language':TEXT,'parser':TEXT,'status':TEXT,'sha256':TEXT,'role':enum('source','test','context'),
            'lines':UINT,'nodes':UINT,'symbol_count':UINT})
SYMBOL_NODE = obj({'id':ID,'kind':{'const':'symbol'},'project_id':ID,'file_id':ID,'label':TEXT,'path':TEXT,
                   'language':TEXT,'parser':TEXT,'sha256':TEXT,'role':enum('source','test','context'),'symbol':SYMBOL})
MATCHES=('function-body','function-shape')
CLONE = obj({'id':ID,'kind':{'const':'clone_group'},'match':enum(*MATCHES),'language':TEXT,'parser':TEXT,
             'fingerprint':HASH,'occurrences':UINT,'project_count':UINT,'repeated_nodes':UINT,
             'equivalence':enum('syntax-except-declaration-name','identifier-shape-heuristic'),
             'semantic_equivalence_proven':{'const':False},'effects':{'const':'unknown'}})
EXTERNAL = obj({'id':ID,'kind':{'const':'external'},'project_id':ID,'label':TEXT,'resolution':TEXT})
NODE = {'oneOf':[PROJECT,FILE,SYMBOL_NODE,CLONE,EXTERNAL]}
EDGE = obj({'id':ID,'source':ID,'target':ID,'kind':enum('contains','imports','calls','clone_member'),
            'attributes':obj({'line':UINT,'module':TEXT,'resolution':TEXT,'name':TEXT,'certainty':TEXT,'match':enum(*MATCHES)},[])})
EXCLUSION = obj({'project_id':ID,'path':TEXT,'reason':TEXT,'bytes':UINT})
PAGE = {'offset':bounded(0,1000000000),'limit':bounded(1,200)}
PROJECT_FILTER={'type':'string','maxLength':240}
PARAMETERS = {
    'summary':obj({}),
    'projects':obj(PAGE,[]),
    'clones':obj({**PAGE,'cross_project':BOOL,'match':enum('',*MATCHES),'language':{'type':'string','maxLength':32},'project_id':PROJECT_FILTER},[]),
    'members':obj({**PAGE,'group_id':ID,'project_id':PROJECT_FILTER},['group_id']),
    'nodes':obj({**PAGE,'project_id':PROJECT_FILTER,'kind':enum('','project','file','symbol','external','clone_group'),
                 'search':{'type':'string','maxLength':200}},[]),
    'neighbors':obj({**PAGE,'node_id':ID,'direction':enum('both','in','out'),'kind':enum('','contains','calls','imports','clone_member')},['node_id']),
    'exclusions':obj({**PAGE,'project_id':PROJECT_FILTER},[]),
    'evidence':obj({'node_id':ID,'include_source':BOOL,'max_source_chars':bounded(0,16000)},['node_id']),
    'context':obj({'group_ids':array(ID,minItems=1,maxItems=10,uniqueItems=True),'max_chars':bounded(8000,200000),
                   'max_members':bounded(2,40),'include_source':BOOL},['group_ids']),
}
OP_DESCRIPTIONS = {
    'summary':'Workspace size, parser coverage, filtering and limitations. Start here.',
    'projects':'Page of project identities and coverage; absolute filesystem roots are omitted.',
    'clones':'Ranked global clone groups. Shape groups are hypotheses, not semantic equivalence.',
    'members':'Page of occurrences with project, file, line range and source fingerprint.',
    'nodes':'Search graph node cards, not source content.',
    'neighbors':'Page of incoming/outgoing graph edges; calls are static candidates.',
    'exclusions':'Audit skipped entries and reasons. One pruned directory is one entry.',
    'evidence':'Bounded symbol/file evidence with imports, calls and logic; optional hash-verified current source excerpt.',
    'context':'Budgeted LLM packet with selected evidence, bound reply schema and GBNF grammar. Does not call a model.',
}


def reply_schema(snapshot=None, groups=None, evidence=None):
    id_schema=lambda values: {'type':'string','enum':values} if values else copy.deepcopy(ID)
    recommendation=obj({'group_id':id_schema(groups),'decision':enum('extract_library','parameterize','keep_local','needs_review'),
        'library_name':{'type':'string','maxLength':120},'rationale':{'type':'string','minLength':1,'maxLength':3000},
        'evidence_ids':array(id_schema(evidence),minItems=1,maxItems=40,uniqueItems=True),
        'risks':array({'type':'string','minLength':1,'maxLength':1000},minItems=1,maxItems=10),
        'tests':array({'type':'string','minLength':1,'maxLength':1000},minItems=1,maxItems=10),
        'confidence':{'type':'number','minimum':0,'maximum':1}})
    return obj({'schema':{'const':'grapholog.refactor-proposals.v1'},
                'snapshot_id':{'const':snapshot} if snapshot else HASH,
                'recommendations':array(recommendation,maxItems=10)})


def page_schema(item):
    return obj({'items':array(item,maxItems=200),'offset':UINT,'limit':UINT,'total':UINT,'truncated':BOOL})


COUNTS = obj({k:UINT for k in ('projects','files','symbols','nodes','edges','clone_groups','cross_project_groups','excluded_entries')})
SUMMARY = obj({'counts':COUNTS,'coverage_complete_within_policy':BOOL,'engine':{'type':'object'},
               'profile':TEXT,'version':TEXT,'limitations':array(TEXT),'excluded_reasons':{'type':'object','additionalProperties':UINT}})
SOURCE = obj({'status':enum('not-requested','verified','changed','unavailable','too-large'),
              'text':TEXT,'omitted_characters':UINT,'sha256':TEXT,'note':TEXT})
EVIDENCE = obj({'node':NODE,'imports':array(IMPORT,maxItems=80),'calls':array(CALL,maxItems=80),'logic':array(LOGIC,maxItems=80),
                'notes':array(TEXT,maxItems=80),'source':SOURCE,
                'omitted':obj({k:UINT for k in ('imports','calls','logic','notes')}),
                'neighbors':obj({'incoming_calls':array(EDGE,maxItems=80),'outgoing_calls':array(EDGE,maxItems=80),
                                 'omitted_incoming_calls':UINT,'omitted_outgoing_calls':UINT}),
                'effects':{'const':'unknown'}})
CONTEXT = obj({'schema':{'const':'grapholog.workspace-context.v1'},'snapshot_id':HASH,
               'messages':array(obj({'role':enum('system','user'),'content':TEXT}),minItems=2,maxItems=2),
               'message_characters':UINT,'max_chars':UINT,'selected_evidence_ids':array(ID),
               'groups':array(obj({'id':ID,'member_ids':array(ID),'total_members':UINT,'omitted_members':UINT})),
               'reply_schema':{'type':'object'},'grammar':TEXT,
               'limitations':array(TEXT)})
OUTPUTS={'summary':SUMMARY,'projects':page_schema(PROJECT),'clones':page_schema(CLONE),'members':page_schema(SYMBOL_NODE),
         'nodes':page_schema(NODE),'neighbors':page_schema(EDGE),'exclusions':page_schema(EXCLUSION),'evidence':EVIDENCE,'context':CONTEXT}


@lru_cache(maxsize=None)
def schema(name):
    if name=='request':
        value={'oneOf':[obj({'schema':{'const':'grapholog.request.v1'},'operation':{'const':op},'params':params}) for op,params in PARAMETERS.items()]}
    elif name=='response':
        value={'oneOf':[response_schema(op) for op in PARAMETERS]}
    elif name=='refactor':value=reply_schema()
    elif name=='context':value=CONTEXT
    elif name=='workspace-config':
        value=obj({'schema':{'const':'grapholog.workspace-config.v1'},'projects':array(obj({
            'id':{'type':'string','minLength':1,'maxLength':240},'path':{'type':'string','minLength':1}}),minItems=1,maxItems=10000)})
    elif name=='graph':
        value=obj({'schema':{'const':'grapholog.graph.v1'},'snapshot_id':HASH,'summary':SUMMARY,
                   'nodes':array(NODE),'edges':array(EDGE),'exclusions':array(EXCLUSION)})
    else:
        from .extraction_contracts import SCHEMAS
        if name not in SCHEMAS:raise ValueError('Unknown schema: '+name)
        value=SCHEMAS[name]
    return {'$schema':DRAFT,'$id':'urn:grapholog:'+name+':v1',**copy.deepcopy(value)}


def response_schema(op):
    return obj({'schema':{'const':'grapholog.response.v1'},'snapshot_id':HASH,'operation':{'const':op},'data':OUTPUTS[op]})


def validate(name_or_schema, value):
    try:from jsonschema import Draft202012Validator
    except ImportError as exc:raise ValueError('Install grapholog[workspace] for JSON Schema validation') from exc
    definition=schema(name_or_schema) if isinstance(name_or_schema,str) else name_or_schema
    error=next(Draft202012Validator(definition).iter_errors(value),None)
    if error:
        path='.'.join(map(str,error.absolute_path)) or '$'
        # Do not echo the entire invalid input or a large oneOf branch.
        detail=error.message if error.validator not in {'oneOf','anyOf'} else 'does not match the versioned operation contract'
        raise ValueError(f'{path}: {detail[:500]}')


def gbnf(definition):
    """Closed JSON Schema subset -> canonical-order GBNF; validation remains authoritative.

    Emits every declared property (including optional ones), a valid restricted
    subset of schema outputs. Unique IDs, numeric bounds (except [0,1]), patterns
    and cross-field referential integrity are validated after generation.
    """
    rules=[];counter=0
    def terminal(value):return json.dumps(value,ensure_ascii=True)
    def literal(value):return terminal(json.dumps(value,ensure_ascii=True,separators=(',',':')))
    def add(s):
        nonlocal counter
        counter+=1;name=f'rule-{counter}';rules.append(name+' ::= '+s);return name
    def compile_(s):
        if 'const' in s:return literal(s['const'])
        if 'enum' in s:return add(' | '.join(literal(x) for x in s['enum']))
        if 'oneOf' in s:return add(' | '.join(compile_(x) for x in s['oneOf']))
        kind=s.get('type')
        if kind=='object':
            if s.get('additionalProperties') is not False:raise ValueError('GBNF requires closed objects')
            parts=[literal(k)+' ws ":" ws '+compile_(v) for k,v in s['properties'].items()]
            return add('"{" ws '+(' ws "," ws '.join(parts))+' ws "}"')
        if kind=='array':
            item=compile_(s['items']);lo=s.get('minItems',0);hi=s.get('maxItems',40)
            if hi==0:return '"[" ws "]"'
            rest=f'(ws "," ws {item})'+'{'+str(max(0,lo-1))+','+str(hi-1)+'}'
            inside=item+' '+rest
            if lo==0:inside='('+inside+')?'
            return add('"[" ws '+inside+' ws "]"')
        if kind=='string':
            return add('"\\\"" char'+'{'+str(s.get('minLength',0))+','+str(s.get('maxLength',256))+'}'+' "\\\""')
        if kind=='boolean':return '("true" | "false")'
        if kind=='integer':return 'uint'
        if kind=='number' and s.get('minimum')==0 and s.get('maximum')==1:return 'confidence'
        raise ValueError('Unsupported GBNF schema type: '+str(kind))
    root=compile_(definition)
    return '# Grapholog canonical JSON; always apply JSON Schema and reference validation.\nroot ::= '+root+' ws\n'+'\n'.join(rules)+r'''
ws ::= [ \t\n\r]*
char ::= [^"\\\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})
uint ::= "0" | [1-9] [0-9]{0,9}
confidence ::= "0" ("." [0-9]{1,8})? | "1" ("." "0"{1,8})?
'''


def openapi():
    return {'openapi':'3.1.1','info':{'title':'Grapholog workspace query API','version':'0.5.0'},
      'servers':[{'url':'http://127.0.0.1:8765'}], 'paths':{
        '/v1/query':{'post':{'operationId':'query','requestBody':{'required':True,'content':{'application/json':{'schema':schema('request')}}},
            'responses':{'200':{'description':'Committed snapshot result','content':{'application/json':{'schema':schema('response')}}},
                         '400':{'description':'Invalid request'},'404':{'description':'Unknown node/group'},'401':{'description':'Missing or invalid configured token'},'403':{'description':'Host or Origin rejected'}}}},
        '/v1/schema/{name}':{'get':{'operationId':'schema','parameters':[{'name':'name','in':'path','required':True,'schema':enum('request','response','graph','refactor','context','workspace-config','extraction-config','extraction-plan','task-result','ticket-proposal')}],
            'responses':{'200':{'description':'JSON Schema'},'404':{'description':'Unknown contract'}}}},
        '/v1/grammar/{name}':{'get':{'operationId':'grammar','parameters':[{'name':'name','in':'path','required':True,'schema':enum('request','refactor','task-result','ticket-proposal')}],
            'responses':{'200':{'description':'Canonical GBNF'},'404':{'description':'Unknown grammar'}}}}}}


from .extraction_contracts import CONFIG as EXTRACTION_CONFIG, PLAN as EXTRACTION_PLAN
PARAMETERS['extraction_plan']=obj({'config':EXTRACTION_CONFIG})
OUTPUTS['extraction_plan']=EXTRACTION_PLAN
OP_DESCRIPTIONS['extraction_plan']='Read-only extraction DAG and Planfile ticket proposals. Does not create tickets, sync GitHub or execute Koru.'
