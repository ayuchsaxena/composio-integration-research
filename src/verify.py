"""Validate source references and summarize a manually audited sample."""
import argparse, csv, datetime as dt, json, re, unicodedata

FIELDS=["description","auth_methods","credential_requirements","api_access","pricing_restrictions","api_type","endpoints","scopes","read_write","rate_limits","webhooks","capabilities","mcp_or_agent_support","buildability","blocker"]

def normalize(value):
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",str(value or ""))).strip().casefold()

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",default="data/research.json"); p.add_argument("--audit",default="data/audit.json"); p.add_argument("--output",default="data/verification.json"); a=p.parse_args()
    data=json.load(open(a.input,encoding="utf-8"))
    if __import__('os').path.exists(a.audit):
        if str(a.audit).lower().endswith(".csv"):
            with open(a.audit,encoding="utf-8-sig",newline="") as f:
                audits=[]
                for row in csv.DictReader(f):
                    if not row.get("first_pass_outcome") and not row.get("final_outcome"): continue
                    audits.append({"app_id":row.get("app_id") or next((x["id"] for x in data.get("applications",[]) if x.get("name")==row.get("app")),None),"field":row.get("field"),"reviewer":row.get("reviewer_initials"),"reviewed_at":row.get("reviewed_date"),"first_pass_outcome":row.get("first_pass_outcome"),"final_outcome":row.get("final_outcome"),"evidence_urls":[x.strip() for x in (row.get("evidence_checked_url") or "").split("|") if x.strip()]})
        else: audits=json.load(open(a.audit,encoding="utf-8"))
    else: audits=[]
    valid=[]; errors=[]
    for app in data.get("applications",[]):
        sources={s.get("url"):s for s in app.get("research",{}).get("sources",[]) if s.get("url")}
        for field,item in app.get("research",{}).get("claims",{}).items():
            if not str(item.get("value","unknown")).strip().lower().startswith("unknown"):
                if not item.get("sources"): errors.append({"app":app["id"],"field":field,"issue":"claim lacks citations"})
                for url in item.get("sources",[]):
                    if url not in sources: errors.append({"app":app["id"],"field":field,"issue":"citation absent from retrieval ledger","url":url})
                evidence=item.get("evidence",[])
                if item.get("evidence_class") not in ("confirmed_fact","inference"):
                    errors.append({"app":app["id"],"field":field,"issue":"non-unknown claim has no confirmed_fact/inference distinction"})
                if not evidence:
                    errors.append({"app":app["id"],"field":field,"issue":"non-unknown claim lacks an evidence excerpt"})
                for ev in evidence:
                    url=ev.get("source_url"); quote=normalize(ev.get("quote","")); source=sources.get(url,{})
                    body=normalize(source.get("page_content",""))
                    if source.get("fetch_status")!="success" or not quote or quote not in body:
                        errors.append({"app":app["id"],"field":field,"issue":"claim excerpt does not match successfully fetched page text","url":url})
                    if url not in item.get("sources",[]):
                        errors.append({"app":app["id"],"field":field,"issue":"evidence URL absent from claim source list","url":url})
                valid.append((app,field,item))
    valid_audits=[]
    for x in audits:
        required=(x.get("app_id") and x.get("field") in FIELDS and x.get("reviewer") and x.get("reviewed_at") and isinstance(x.get("evidence_urls"),list) and x.get("evidence_urls"))
        if not required: errors.append({"audit":x.get("app_id","unknown"),"issue":"missing app_id, supported field, reviewer, reviewed_at, or evidence_urls"})
        elif x.get("final_outcome") not in ("correct","incorrect","uncertain"):
            errors.append({"audit":x.get("app_id","unknown"),"issue":"final_outcome must be correct, incorrect, or uncertain"})
        elif x.get("first_pass_outcome") not in (None,"","correct","incorrect","uncertain"):
            errors.append({"audit":x.get("app_id","unknown"),"issue":"first_pass_outcome must be empty or correct, incorrect, or uncertain"})
        else: valid_audits.append(x)
    automated=[]
    for app in data.get("applications",[]):
        evaluation=app.get("research",{}).get("evaluation",{})
        allowed={s.get("url"):s for s in evaluation.get("sources",[])}
        for field,item in evaluation.get("checks",{}).items():
            if field not in FIELDS:
                errors.append({"app":app["id"],"field":field,"issue":"unknown automated evaluation field"})
                continue
            outcome=item.get("outcome")
            if outcome not in ("correct","incorrect","uncertain"):
                errors.append({"app":app["id"],"field":field,"issue":"invalid automated evaluator outcome"})
            if item.get("human_verified") is not False:
                errors.append({"app":app["id"],"field":field,"issue":"automated check must not be labeled human verified"})
            evidence=item.get("evidence",[])
            if outcome in ("correct","incorrect") and not evidence:
                errors.append({"app":app["id"],"field":field,"issue":"decisive automated check lacks an excerpt"})
            for ev in evidence:
                url=ev.get("source_url"); quote=normalize(ev.get("quote","")); source=allowed.get(url,{})
                if source.get("fetch_status")!="success" or not quote or quote not in normalize(source.get("page_content","")):
                    errors.append({"app":app["id"],"field":field,"issue":"evaluator excerpt does not match fetched independent page text","url":url})
                if url not in allowed:
                    errors.append({"app":app["id"],"field":field,"issue":"evaluation citation absent from independent evidence ledger","url":url})
            automated.append(outcome)
    first=[x["first_pass_outcome"] for x in valid_audits if x.get("first_pass_outcome") in ("correct","incorrect","uncertain")]
    final=[x["final_outcome"] for x in valid_audits]
    first_res=[x for x in first if x!="uncertain"]; final_res=[x for x in final if x!="uncertain"]
    corrections=[{"app":x.get("app",x.get("app_id")),"field":x.get("field_label",x.get("field")),"original":x.get("researcher_claim",""),"corrected":x.get("corrected_finding",""),"note":x.get("reviewer_comment","")} for x in valid_audits if x.get("final_outcome")=="incorrect"]
    summary={"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),"citation_errors":errors,"claim_count":len(valid),"audit_count":len(valid_audits),"human_reviewed":len(valid_audits),"human_correct":final.count("correct"),"human_incorrect":final.count("incorrect"),"human_uncertain":final.count("uncertain"),"human_accuracy_denominator":len(final_res),"human_audited_accuracy":(final_res.count("correct")/len(final_res) if final_res else None),"human_corrections":corrections,"automated_check_count":len(automated),"automated_correct":automated.count("correct"),"automated_incorrect":automated.count("incorrect"),"automated_uncertain":automated.count("uncertain"),"first_pass_resolved":len(first_res),"first_pass_uncertain":first.count("uncertain"),"final_resolved":len(final_res),"final_uncertain":final.count("uncertain"),"first_pass_accuracy":(first_res.count("correct")/len(first_res) if first_res else None),"final_accuracy":(final_res.count("correct")/len(final_res) if final_res else None),"accuracy_note":"Human accuracy uses the final human verdict only: correct divided by correct plus incorrect. Uncertain outcomes are excluded and reported separately. Automated evaluator outcomes are not human accuracy."}
    if first_res or final_res: summary["accuracy_note"]="Computed only over resolved, complete audit records; uncertain outcomes excluded and denominators are reported."
    json.dump(summary,open(a.output,"w",encoding="utf-8"),indent=2)
    print(f"Claims checked: {len(valid)}; citation errors: {len(errors)}; audited records: {len(valid_audits)}; first-pass accuracy: {summary['first_pass_accuracy']}")

if __name__=="__main__": main()
