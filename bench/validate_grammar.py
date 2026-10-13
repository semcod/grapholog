"""Validate actual GBNF with llama.cpp's native test-gbnf-validator (no model)."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from grapholog.contracts import gbnf,reply_schema,schema,validate


def canonical_order(definition, value):
    if 'oneOf' in definition:
        for choice in definition['oneOf']:
            try:validate(choice,value)
            except ValueError:continue
            return canonical_order(choice,value)
    if isinstance(value,dict) and 'properties' in definition:
        result={key:canonical_order(child,value[key]) for key,child in definition['properties'].items() if key in value}
        result.update({key:item for key,item in value.items() if key not in result})
        return result
    if isinstance(value,list):return [canonical_order(definition['items'],item) for item in value]
    return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--validator',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    group='g_'+'a'*32;ids=['s_'+'b'*32,'s_'+'c'*32];snapshot='d'*64
    definition=reply_schema(snapshot,[group],ids)
    good={'schema':'grapholog.refactor-proposals.v1','snapshot_id':snapshot,'recommendations':[
        {'group_id':group,'decision':'extract_library','library_name':'normalizacja',
         'rationale':'Zażółć gęślą 😀; zakres wymaga oceny.','evidence_ids':ids,
         'risks':['Efekty uboczne są nieznane.'],'tests':['Sprawdzić wartości ujemne.'],'confidence':0.6}]}
    cases=[('valid-bound-reply',definition,good,True)]
    for name,field,value in [('invented-evidence','evidence_ids',['s_'+'0'*32]),('invalid-decision','decision','execute'),
                              ('confidence-outside-range','confidence',1.2)]:
        bad=copy.deepcopy(good);bad['recommendations'][0][field]=value;cases.append((name,definition,bad,False))
    extra=copy.deepcopy(good);extra['extra']=True;cases.append(('unknown-property',definition,extra,False))
    missing=copy.deepcopy(good);del missing['recommendations'][0]['risks'];cases.append(('missing-property',definition,missing,False))
    for operation,params in [('summary',{}),('clones',{'offset':0,'limit':20,'cross_project':True,'match':'','language':'','project_id':''}),
                             ('members',{'offset':0,'limit':20,'group_id':group,'project_id':''}),
                             ('context',{'group_ids':[group],'max_chars':40000,'max_members':12,'include_source':True})]:
        cases.append(('valid-'+operation,schema('request'),{'schema':'grapholog.request.v1','operation':operation,'params':params},True))
    from grapholog.extraction import config_defaults
    config=config_defaults({'schema':'grapholog.extraction-config.v1','coordinator':'shared'})
    cases.append(('valid-extraction-plan-query',schema('request'),canonical_order(schema('request'),
        {'schema':'grapholog.request.v1','operation':'extraction_plan','params':{'config':config}}),True))
    artifact={'path':'dist/shared.whl','sha256':'c'*64}
    result={'schema':'grapholog.task-result.v1','task_key':'gt_'+'a'*32,'input_digest':'b'*64,'outcome':'succeeded',
        'artifacts':[artifact],'tests':[{'name':'import','passed':True,'evidence':artifact}],
        'package':{'name':'shared','version':'0.1.0','kind':'package','exports':[{'family_id':'family-demo','import_path':'shared','symbol':'normalize'}]},
        'license_review':{'status':'compatible','evidence':artifact},'notes':['Przykład składni; plik nie jest tutaj odczytywany.']}
    cases.append(('valid-task-result',schema('task-result'),result,True))
    wrong=copy.deepcopy(result);wrong['outcome']='execute-anything'
    cases.append(('invalid-task-result-outcome',schema('task-result'),wrong,False))
    proposal={'schema':'planfile.ticket-proposal.v1','proposal_id':'grapholog/example','dedupe_key':'grapholog:example',
        'name':'Utwórz wspólną paczkę','description':'Przykład bez wykonania.','priority':'normal',
        'source':{'tool':'grapholog','tool_version':'0.5.0','finding_id':'example','artifact_digest':'d'*64},
        'labels':['grapholog'],'files':['src/main.py'],'acceptance_criteria':['Testy przechodzą.'],'evidence_refs':['grapholog://example']}
    cases.append(('valid-ticket-proposal',schema('ticket-proposal'),proposal,True))
    wrong=copy.deepcopy(proposal);wrong['executor']={'kind':'shell'}
    cases.append(('proposal-cannot-grant-execution',schema('ticket-proposal'),wrong,False))
    results=[]
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        for name,contract,value,expected in cases:
            grammar=root/'grammar.gbnf';grammar.write_text(gbnf(contract));document=root/'input.json';document.write_text(json.dumps(value,ensure_ascii=True))
            check=subprocess.run([str(args.validator),str(grammar),str(document)],capture_output=True,text=True,timeout=30)
            # This upstream utility returns 0 for a parsed grammar even when the
            # input string is rejected; inspect its explicit validation verdict.
            accepted=check.returncode==0 and 'Input string is valid according to the grammar.' in check.stdout
            assert accepted==expected,(name,check.returncode,check.stdout[:2000],check.stderr[:2000])
            try:validate(contract,value);schema_ok=True
            except ValueError:schema_ok=False
            assert schema_ok==expected,(name,'JSON Schema mismatch')
            results.append({'case':name,'expected':expected,'gbnf_accepted':accepted,'json_schema_accepted':schema_ok,'exit_code':check.returncode})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'validator':'native llama.cpp test-gbnf-validator, bundled with llama-cpp-python 0.3.36 source distribution',
        'validator_sha256':hashlib.sha256(args.validator.read_bytes()).hexdigest(),'model_loaded':False,'cases':results},indent=2)+'\n')
    print(json.dumps({'native_grammar_cases':len(results),'all_passed':True}))


if __name__=='__main__':main()
