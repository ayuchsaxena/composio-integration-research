"""Select a reproducible top-20 inbox of weak, high-impact claims."""
import argparse,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WEIGHTS={"api_access":12,"credential_requirements":12,"auth_methods":10,"pricing_restrictions":10,"buildability":10,"blocker":8,"api_type":7,"endpoints":7,"capabilities":7,"scopes":6,"read_write":6,"rate_limits":5,"webhooks":4,"mcp_or_agent_support":4,"description":2}
LABELS={"api_access":"Can developers get access themselves?","credential_requirements":"What do developers need first?","auth_methods":"How does it connect securely?","pricing_restrictions":"Which plans include API access?","buildability":"Could Composio build this now?","blocker":"What could delay a build?","api_type":"What kind of API is documented?","endpoints":"Which app features can we connect?","capabilities":"What can an integration do?","scopes":"What permissions are needed?","read_write":"Can it view or change data?","rate_limits":"Are there usage limits?","webhooks":"Can it send automatic updates?","mcp_or_agent_support":"Can AI agents use it?","description":"About this app"}
def load(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def main():
 p=argparse.ArgumentParser();p.add_argument("--input",default="data/research.json");p.add_argument("--initial",default="data/research_initial.json");p.add_argument("--output",default="data/human_review_selection.json");p.add_argument("--limit",type=int,default=20);args=p.parse_args()
 data=load(args.input); initial={a["id"]:a for a in load(args.initial).get("applications",[])} if Path(args.initial).exists() else {}
 candidates=[]
 for app in data.get("applications",[]):
  r=app.get("research",{}); claims=r.get("claims",{}); ev=r.get("evaluation",{}); checks=ev.get("checks",{}); sources={s.get("url"):s for s in r.get("sources",[])}
  old=initial.get(app["id"],{}).get("research",{}).get("claims",{})
  for field,value in claims.items():
   if field not in WEIGHTS: continue
   item=value or {}; check=checks.get(field,{})
   evid=item.get("evidence",[]); usable=[e for e in evid if e.get("quote") and e.get("source_url") in sources and sources[e.get("source_url")].get("fetch_status")=="success"]
   score=WEIGHTS[field]; reasons=[f"High-impact field ({LABELS[field]})"]
   outcome=check.get("outcome")
   if outcome=="incorrect": score+=18;reasons.append("Evaluator marked the Researcher finding incorrect")
   elif outcome=="uncertain": score+=12;reasons.append("Evaluator could not confirm the finding")
   if not usable: score+=14;reasons.append("No matching excerpt from successfully retrieved official page text")
   elif len(usable)==1: score+=3;reasons.append("Only one usable evidence excerpt")
   if item.get("evidence_class")=="inference": score+=5;reasons.append("Finding is an inference")
   if str(item.get("value","unknown")).strip().lower().startswith("unknown"): score+=4;reasons.append("Researcher left the finding unknown")
   if field in ("api_access","credential_requirements","auth_methods") and not usable: score+=7;reasons.append("Access or sign-in requirements lack direct evidence")
   if field in ("buildability","capabilities","endpoints","read_write") and (outcome=="uncertain" or not usable): score+=5;reasons.append("Could change the integration build decision")
   baseline=old.get(field,{})
   sources_out=[]
   urls=[]
   for e in evid+check.get("evidence",[]):
    u=e.get("source_url")
    if u and u not in urls: urls.append(u)
   for u in urls:
    src=sources.get(u) or next((x for x in ev.get("sources",[]) if x.get("url")==u),{})
    excerpt=next((e.get("quote") for e in evid+check.get("evidence",[]) if e.get("source_url")==u and e.get("quote")),"")
    sources_out.append({"url":u,"title":src.get("title") or u,"excerpt":excerpt})
   if not sources_out:
    tokens={"api_access":["api","access","pricing","plan"],"credential_requirements":["auth","credential","oauth","key","token"],"auth_methods":["auth","oauth","credential","token"],"pricing_restrictions":["pricing","plan","limits","billing"],"buildability":["api","reference","quickstart","getting started"],"blocker":["api","permission","rate","plan"],"api_type":["api","rest","graphql"],"endpoints":["api","reference","endpoint"],"capabilities":["api","reference","endpoint"],"scopes":["scope","permission","oauth"],"read_write":["create","update","delete","api"],"rate_limits":["rate","limit","quota"],"webhooks":["webhook","event"],"mcp_or_agent_support":["mcp","agent"],"description":["overview","about"]}.get(field,[])
    pool=[x for x in r.get("sources",[]) if x.get("url")]
    pool.sort(key=lambda x:(-sum(1 for t in tokens if t in (str(x.get("title","")).lower()+" "+str(x.get("url","")).lower())), x.get("fetch_status")!="success"))
    for src in pool[:3]: sources_out.append({"url":src["url"],"title":src.get("title") or src["url"],"excerpt":""})
   candidates.append({"app_id":app["id"],"app":app["name"],"category":app.get("category"),"field":field,"field_label":LABELS[field],"score":score,"selection_reasons":reasons,"first_pass_claim":baseline.get("value","unknown"),"researcher_claim":item.get("value","unknown"),"researcher_evidence_class":item.get("evidence_class","unknown"),"researcher_evidence":evid,"evaluator_outcome":outcome or "unavailable","evaluator_finding":check.get("suggested_correction") or check.get("reason") or "No separate correction supplied.","evaluator_reason":check.get("reason",""),"evaluator_evidence":check.get("evidence",[]),"official_sources":sources_out})
 candidates.sort(key=lambda x:(-x["score"],x["app_id"],x["field"]))
 selected=[];field_counts={}
 for row in candidates:
  if len(selected)>=args.limit: break
  if field_counts.get(row["field"],0)>=3: continue
  row["rank"]=len(selected)+1;selected.append(row);field_counts[row["field"]]=field_counts.get(row["field"],0)+1
 result={"schema_version":1,"selection_method":"Deterministic weighted score: field impact + evaluator disagreement + evidence gaps + inference/unknown penalties. A maximum of three claims per field preserves coverage; ties break by app ID then field. Selection is claim-level; multiple claims from one app are allowed.","source":args.input,"initial_baseline":args.initial,"selected_count":len(selected),"claims":selected}
 out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
 print(f"Selected {len(selected)} weak claims from {len(data.get('applications',[]))} apps. No human outcomes were written.")
if __name__=="__main__": main()


