"""Conservatively normalize free-text access/buildability claims in saved research."""
import argparse,json
from pathlib import Path
from .agents import normalize_access,normalize_buildability

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',default='data/research.json');p.add_argument('--output',default='data/research.json');a=p.parse_args()
 data=json.loads(Path(a.input).read_text(encoding='utf-8'));changed=0
 for app in data.get('applications',[]):
  claims=app.get('research',{}).get('claims',{})
  for field,normalizer in [('api_access',normalize_access),('buildability',normalize_buildability)]:
   claim=claims.get(field,{})
   value=str(claim.get('value','unknown'))
   if value=='unknown': continue
   normalized,reason=normalizer(value) if field=='api_access' else (normalizer(value),'mapped to the three-state buildability scale')
   if normalized!=value:
    claim['detail']=value;claim['value']=normalized;claim['normalization_reason']=reason;claim['evidence_class']='inference';claim['evidence']=[{**e,'evidence_class':'inference'} for e in claim.get('evidence',[])];changed+=1
   elif claim.get('detail') and claim.get('detail')!=normalized:
    claim['evidence_class']='inference';claim['evidence']=[{**e,'evidence_class':'inference'} for e in claim.get('evidence',[])]
   if normalized=='unknown':
    claim['value']='unknown';claim['evidence_class']='unknown';claim['sources']=[];claim['evidence']=[];claim['deterministic_evidence_check']='quote_found_but_value_unclassified';claim['confidence']='none'
  for field,claim in claims.items():
   if str(claim.get('value','unknown')).strip().casefold().startswith('unknown'):
    if claim.get('value')!='unknown': changed+=1
    claim['value']='unknown';claim['sources']=[];claim['evidence']=[];claim['evidence_class']='unknown';claim['deterministic_evidence_check']='not_applicable';claim['confidence']='none'
 Path(a.output).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
 print(f'Normalized {changed} claim labels. No evidence or verification status was promoted.')
if __name__=='__main__':main()
