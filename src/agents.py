"""Two-role integration research workflow: Researcher then Evaluator."""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

from .composio_mcp import ComposioResearchMCP

FIELDS = ["description", "auth_methods", "credential_requirements", "api_access", "pricing_restrictions", "api_type", "endpoints", "scopes", "read_write", "rate_limits", "webhooks", "capabilities", "mcp_or_agent_support", "buildability", "blocker"]
UNKNOWN = {"value": "unknown", "sources": [], "evidence": [], "evidence_class": "unknown", "deterministic_evidence_check": "not_applicable", "confidence": "none", "review_status": "unreviewed"}

RESEARCHER = """You are the Researcher on a careful product-operations team. Read the supplied retrieved page text, not just titles. Use only official-domain page content. For each field return a concise value, evidence_class (confirmed_fact, inference, or unknown), confidence, and evidence entries containing the exact source_url and an exact short quote copied from page_content. A URL working is never evidence. If page content does not establish a fact, say unknown and return no quote. Use confirmed_fact only when the quote directly states the answer; use inference for a clearly explained conclusion supported by a quote. Never guess credential access, plans, approval, scopes, limits, or write support. Check pricing and credential setup separately. For API access use self_serve_free, self_serve_trial, paid_plan, admin_approval, partnership, contact_sales, mixed, or unknown only when evidence supports it. For buildability use likely_buildable, gated, or uncertain and name the blocker. Distinguish first-party MCP from third-party support. Do not fabricate endpoints, citations, or quotes."""

EVALUATOR = """You are the Evaluator, a distinct skeptical reviewer. You did not write the draft. Independently search official docs, read the retrieved page content, and assess each proposed claim using only this fresh evidence. Mark correct, incorrect, or uncertain. A live URL or matching title is not proof. For any correct/incorrect outcome, cite an exact independent source URL and copy an exact short supporting quote from its page_content. If the evidence is missing, ambiguous, contradictory, or a quote cannot be found, mark uncertain. Explain the key gap and suggested correction. This is an automated review, never a human audit."""


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key,value=line.split("=",1)
            os.environ.setdefault(key.strip(),value.strip().strip("\"'"))


def parse_llm_json(raw: str) -> dict:
    """Parse JSON from Composio's helper response without trusting its envelope."""
    def visit(value: Any, depth: int = 0) -> dict:
        if depth > 6:
            return {}
        if isinstance(value, dict):
            if any(field in value for field in FIELDS) or "status" in value:
                return value
            for key in ("data", "response", "result", "output", "content", "text"):
                if key in value:
                    found=visit(value[key],depth+1)
                    if found:
                        return found
            return {}
        if isinstance(value, list):
            for item in value:
                found=visit(item,depth+1)
                if found:
                    return found
            return {}
        if isinstance(value, str):
            candidate=value.strip()
            if candidate.startswith("```"):
                candidate=candidate.split("\n",1)[-1].rsplit("```",1)[0].strip()
            try:
                return visit(json.loads(candidate),depth+1)
            except json.JSONDecodeError:
                start=candidate.find("{")
                end=candidate.rfind("}")
                if start>=0 and end>start:
                    try:
                        return visit(json.loads(candidate[start:end+1]),depth+1)
                    except json.JSONDecodeError:
                        return {}
        return {}
    return visit(raw)


def _root_domain(url: str) -> str:
    host=(urllib.parse.urlparse(url).hostname or "").lower().split(":")[0]
    parts=host.split(".")
    compound_suffixes={"co.uk","org.uk","ac.uk","com.au","net.au","org.au","co.nz","com.br","com.mx","co.jp","co.in","com.sg","com.cn"}
    suffix=".".join(parts[-2:]) if len(parts)>1 and ".".join(parts[-2:]) in compound_suffixes else (parts[-1] if parts else "")
    label_count=2 if suffix in compound_suffixes else 1
    return ".".join(parts[-(label_count+1):]) if len(parts)>label_count else host


def official_candidates(app: dict, results: list[dict]) -> list[dict]:
    """Keep likely first-party domains; do not claim that this heuristic proves ownership."""
    root=_root_domain(app.get("hint_url", ""))
    rows=[]
    for item in results:
        host=(urllib.parse.urlparse(item.get("url","")).hostname or "").lower()
        if root and (host==root or host.endswith("."+root)):
            rows.append({**item,"domain_match":"assignment hint registrable domain"})
    return rows


def _normal_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip().casefold()


def _quote_in_source(quote: str, source: dict) -> bool:
    quote=_normal_text(quote)
    body=_normal_text(str(source.get("page_content", "")))
    return bool(quote and len(quote)>=24 and quote in body)


def normalize_access(value: str) -> tuple[str,str]:
    """Map prose to a conservative access bucket without inferring free eligibility."""
    text=(value or "").strip().casefold()
    if text in {"self_serve_free","self_serve_trial","self_serve_plan_unknown","paid_plan","admin_approval","admin_role_required","partnership","contact_sales","mixed","unknown"}:
        return text,"model returned a supported access enum"
    if text.startswith("unknown") or "plan eligibility not established" in text or "plan or tier not stated" in text or "plan/tier eligibility is not stated" in text:
        if any(x in text for x in ("self-serve","self serve","self-generated","self generated","generated in")):
            return "self_serve_plan_unknown","self-serve credential route is described, but free/trial/paid eligibility is not"
        return "unknown","the fetched evidence does not classify access or plan eligibility"
    if "partnership" in text or "partner approval" in text or "partner app" in text:
        return "partnership","the fetched text names a partner or partner-approval gate"
    if "contact sales" in text or "contact your sales" in text:
        return "contact_sales","the fetched text directs developers to sales"
    if any(x in text for x in ("admin approval","administrator approval","approval required","requires approval","app review","review required")):
        if any(x in text for x in ("self-serve","self serve","private app")):
            return "mixed","self-serve/private access and an approval-gated route are both described"
        return "admin_approval","the fetched text names an approval or review gate"
    if any(x in text for x in ("only admins","only administrators","workspace admin","account admin","administrator must","admin must")):
        return "admin_role_required","credential creation requires an administrator role; plan eligibility is separate"
    if any(x in text for x in ("self-serve","self serve","self-generated","self generated","generated in account settings","create an api key in")):
        if "free" in text or "no charge" in text or "free of charge" in text:
            return "self_serve_free","the fetched text states self-serve access and no-cost/free eligibility"
        if "trial" in text or "free trial" in text:
            return "self_serve_trial","the fetched text states a self-serve trial"
        return "self_serve_plan_unknown","self-serve credential route is described, but plan eligibility is not"
    if any(x in text for x in ("available only on the","requires a paid","paid plan required","subscription required","must be on the .*plan")):
        return "paid_plan","the fetched text explicitly ties API availability to a paid plan"
    if "mixed" in text:
        return "mixed","the fetched text describes multiple access routes"
    return "unknown","no conservative access bucket matched the cited claim"


def normalize_buildability(value: str) -> str:
    text=(value or "").strip().casefold()
    if text.startswith("unknown"): return "unknown"
    if "uncertain" in text or "not established" in text: return "uncertain"
    if "not gated" in text or "ungated" in text: return "likely_buildable"
    if "gated" in text or "approval required" in text or "contact sales" in text or "partnership" in text: return "gated"
    if "likely buildable" in text or "likely_buildable" in text: return "likely_buildable"
    return "uncertain"


def attach_claim_evidence(output: dict, sources: list[dict]) -> dict:
    """Keep only claims with a verbatim excerpt from fetched official page text."""
    by_url={s.get("url"):s for s in sources if s.get("url")}
    claims={}
    for field in FIELDS:
        item=output.get(field,{})
        if not isinstance(item,dict):
            claims[field]=dict(UNKNOWN); continue
        value=str(item.get("value","unknown")).strip()
        evidence_class=str(item.get("evidence_class","unknown")).strip().lower()
        if value.lower().startswith("unknown") or evidence_class not in {"confirmed_fact","inference"}:
            claims[field]={**dict(UNKNOWN),"reason":str(item.get("reason","No directly supported page-level finding."))}
            continue
        matched=[]
        for ev in item.get("evidence",[]) if isinstance(item.get("evidence",[]),list) else []:
            if not isinstance(ev,dict):
                continue
            url=ev.get("source_url"); quote=str(ev.get("quote","")).strip(); source=by_url.get(url)
            if source and source.get("fetch_status")=="success" and _quote_in_source(quote,source):
                matched.append({"source_url":url,"quote":quote,"evidence_class":evidence_class,"quote_match":True})
        if not matched:
            claims[field]={**dict(UNKNOWN),"reason":"No exact supporting excerpt matched retrieved official page text.",
                           "deterministic_evidence_check":"no_matching_excerpt"}
            continue
        raw_value=value
        if field=="api_access":
            value,normalization_reason=normalize_access(value)
        elif field=="buildability":
            value=normalize_buildability(value); normalization_reason="mapped to the three-state buildability scale"
        else:
            normalization_reason=""
        if value=="unknown":
            claims[field]={**dict(UNKNOWN),"reason":normalization_reason or "Page evidence does not establish the requested detail.",
                           "deterministic_evidence_check":"quote_found_but_value_unclassified"}
            continue
        if raw_value!=value:
            evidence_class="inference"
            matched=[{**e,"evidence_class":"inference"} for e in matched]
        claims[field]={"value":value,"detail":raw_value if value!=raw_value else "","normalization_reason":normalization_reason,
                       "sources":list(dict.fromkeys(e["source_url"] for e in matched)),
                       "evidence":matched,"evidence_class":evidence_class,
                       "deterministic_evidence_check":"quote_found_in_retrieved_page",
                       "confidence":str(item.get("confidence","low")),"reason":str(item.get("reason","")),
                       "review_status":"needs_human_review"}
    return claims


def select_content_sources(sources: list[dict], limit: int = 4) -> list[dict]:
    """Prefer likely API reference, auth, access, limit, webhook and MCP pages."""
    terms=("api reference","authentication","oauth","api key","getting started","rate limit","webhook",
           "pricing","permission","scope","mcp","endpoint","rest api","graphql","developer")
    ranked=[]
    for index,source in enumerate(sources):
        title=(str(source.get("title",""))+" "+str(source.get("url",""))).lower()
        score=sum(2 if phrase in title else 0 for phrase in terms)
        ranked.append((score,-index,source))
    chosen=[]; seen=set()
    for _,__,source in sorted(ranked,reverse=True,key=lambda x:(x[0],x[1])):
        url=source.get("url")
        if url and url not in seen:
            seen.add(url); chosen.append(source)
        if len(chosen)>=limit: break
    return chosen


async def fetch_source_pages(gateway: ComposioResearchMCP, sources: list[dict], limit: int = 4) -> list[dict]:
    selected=select_content_sources(sources,limit)
    fetched=await gateway.fetch_contents([s["url"] for s in selected],max_characters=3000) if selected else []
    by_url={s.get("url"):s for s in sources}
    output=[]
    for page in fetched:
        base=by_url.get(page.get("url"),{})
        output.append({**base,**page,"page_fetched":page.get("fetch_status")=="success" and bool(page.get("page_content"))})
    return output


def research_prompt(app: dict, evidence: list[dict]) -> str:
    shape={f:{"value":"short answer or unknown","evidence_class":"confirmed_fact|inference|unknown","confidence":"high|medium|low|none","reason":"brief","evidence":[{"source_url":"exact supplied URL","quote":"exact 24-300 character quote from page_content"}]} for f in FIELDS}
    return ("Research this application using only the supplied official-domain retrieved page content. Ignore title-only leads for factual claims. "
            "Every non-unknown value must have at least one exact quote of 24-300 characters from a fetched successful page. "
            "Do not use search snippets as page content. Keep values concise. "
            "For API access use self_serve_free, self_serve_trial, paid_plan, admin_approval, partnership, contact_sales, mixed, or unknown only when directly supported. "
            "Credential requirements should explain how credentials are obtained. Capture pricing restrictions, API style, representative endpoint families, OAuth scopes/permissions, read/write coverage, rate limits, webhooks, API coverage, MCP availability, buildability and main blocker. "
            "For buildability use likely_buildable, gated, or uncertain and give a concise blocker. MCP support must distinguish first-party from third-party. Unknown means documentation did not establish the fact, not that the feature is absent.\nAPP: "+json.dumps(app,ensure_ascii=False)+
            "\nEVIDENCE: "+json.dumps(evidence,ensure_ascii=False)+"\nSCHEMA: "+json.dumps(shape))


def evaluation_prompt(app: dict, draft: dict, draft_sources: list[dict], fresh: list[dict]) -> str:
    check_shape={f:{"outcome":"correct|incorrect|uncertain","reason":"brief evidence-based explanation","evidence":[{"source_url":"exact independent URL","quote":"exact quote from fresh page_content"}],"suggested_correction":"or unknown"} for f in FIELDS}
    return ("Independently review this app. Fresh search evidence is separate from the Researcher. "
            "Use only fresh independent page_content for your assessment, and provide exact quotes for correct/incorrect outcomes. The original source ledger is context, not evidence. "
            "Do not turn unknown into incorrect; use uncertain where the fetched text is absent or inconclusive.\nAPP: "+json.dumps(app,ensure_ascii=False)+
            "\nRESEARCHER DRAFT: "+json.dumps(draft,ensure_ascii=False)+
            "\nORIGINAL SOURCES: "+json.dumps(draft_sources,ensure_ascii=False)+
            "\nFRESH INDEPENDENT EVIDENCE: "+json.dumps(fresh,ensure_ascii=False)+
            "\nSCHEMA: "+json.dumps(check_shape))


class ResearchAgent:
    persona=RESEARCHER
    async def run_app(self, gateway: ComposioResearchMCP, app: dict) -> dict:
        domain=urllib.parse.urlparse(app.get("hint_url","")).hostname or app["name"]
        query=f'{app["name"]} official developer API documentation authentication OAuth API key credentials pricing permissions scopes endpoints rate limits webhooks REST GraphQL MCP'
        leads=await gateway.search(query,limit=8)
        candidates=official_candidates(app,leads)
        if not candidates:
            fallback=await gateway.search(f'{app["name"]} developer API docs {domain} authentication reference',limit=6)
            leads.extend(fallback); candidates=official_candidates(app,fallback)
        sources=await fetch_source_pages(gateway,candidates)
        usable=[s for s in sources if s.get("page_fetched")]
        claims={f:dict(UNKNOWN) for f in FIELDS}
        if usable:
            schema={f:{"value":"string or unknown","evidence_class":"confirmed_fact|inference|unknown","evidence":[{"source_url":"URL","quote":"exact quote"}]} for f in FIELDS}
            try:
                output=parse_llm_json(await gateway.invoke_llm(
                    self.persona+"\n\n"+research_prompt(app,usable)+"\nReturn only a JSON object matching this schema: "+json.dumps(schema),
                    step="RESEARCH_EXTRACTION"))
            except Exception as exc:
                return {"sources":sources,"search_leads":leads,"claims":claims,"review_status":"needs_review",
                        "error":f"Extraction failed: {type(exc).__name__}: {exc}"}
            claims=attach_claim_evidence(output or {},usable)
        return {"sources":sources,"search_leads":leads,"claims":claims,"review_status":"needs_human_review","error":None,
                "content_fetch_summary":{"selected":len(sources),"pages_with_text":len(usable),"content_chars":sum(s.get("content_chars",0) for s in usable)}}


class EvaluatorAgent:
    persona=EVALUATOR
    async def review_app(self, gateway: ComposioResearchMCP, app: dict, research: dict) -> dict:
        domain=urllib.parse.urlparse(app.get("hint_url","")).hostname or app["name"]
        query=f'{app["name"]} official developer API reference credentials access requirements pricing scopes permissions rate limits webhooks REST GraphQL MCP'
        leads=await gateway.search(query,limit=8)
        candidates=official_candidates(app,leads)
        if not candidates:
            fallback=await gateway.search(f'{app["name"]} API developer documentation {domain} access authentication reference',limit=6)
            leads.extend(fallback); candidates=official_candidates(app,fallback)
        fresh=await fetch_source_pages(gateway,candidates)
        usable=[s for s in fresh if s.get("page_fetched")]
        if not candidates:
            return {"status":"review_unavailable","sources":fresh,"checks":{},"error":"Independent search did not retrieve a likely official source."}
        check_shape={f:{"outcome":"correct|incorrect|uncertain","reason":"...","evidence":[{"source_url":"URL","quote":"exact quote"}],"suggested_correction":"..."} for f in FIELDS}
        try:
            output=parse_llm_json(await gateway.invoke_llm(
                self.persona+"\n\n"+evaluation_prompt(app,research.get("claims",{}),research.get("sources",[]),usable)+
                "\nReturn only a JSON object matching this schema: "+json.dumps(check_shape),
                step="EVALUATOR_REVIEW"))
        except Exception as exc:
            return {"status":"review_unavailable","sources":fresh,"search_leads":leads,"checks":{},
                    "error":f"Evaluator model call failed: {type(exc).__name__}: {exc}"}
        allowed={s["url"]:s for s in usable}
        checks={}
        for field in FIELDS:
            item=(output or {}).get(field,{})
            outcome=item.get("outcome","uncertain") if isinstance(item,dict) else "uncertain"
            if outcome not in {"correct","incorrect","uncertain"}:
                outcome="uncertain"
            evidence=[]
            for ev in item.get("evidence",[]) if isinstance(item,dict) and isinstance(item.get("evidence",[]),list) else []:
                if isinstance(ev,dict) and ev.get("source_url") in allowed and _quote_in_source(str(ev.get("quote","")),allowed[ev["source_url"]]):
                    evidence.append({"source_url":ev["source_url"],"quote":str(ev["quote"]).strip(),"quote_match":True})
            urls=list(dict.fromkeys(e["source_url"] for e in evidence))
            reason=str(item.get("reason","No reason returned.")) if isinstance(item,dict) else "No evaluator response."
            if outcome in {"correct","incorrect"} and not evidence:
                outcome="uncertain"
                reason+=" Downgraded to uncertain because no exact excerpt matched fetched independent page text."
            checks[field]={"outcome":outcome,"reason":reason,"evidence_urls":urls,"evidence":evidence,
                           "suggested_correction":str(item.get("suggested_correction","unknown")),"reviewer":"automated_evaluator","human_verified":False}
        return {"status":"automated_review_complete" if usable else "content_unavailable_review","sources":fresh,"search_leads":leads,"checks":checks,
                "content_fetch_summary":{"selected":len(fresh),"pages_with_text":len(usable),"content_chars":sum(s.get("content_chars",0) for s in usable)},
                "reviewed_at":dt.datetime.now(dt.timezone.utc).isoformat(),"accuracy_note":"Automated second-pass checks are not human audit outcomes and do not count toward reported accuracy."}


async def run_pipeline(args):
    load_dotenv(args.env)
    if args.mode == "probe":
        async with ComposioResearchMCP() as gateway:
            response=await gateway.invoke_llm(
                'Reply with exactly this JSON and nothing else: {"status":"ok","provider":"Composio sandbox"}',
                step="LLM_CREDIT_SMOKE_TEST")
        parsed=parse_llm_json(response)
        if parsed.get("status") != "ok":
            raise RuntimeError("Composio returned a response, but it did not match the probe. Inspect the response in the sandbox before running the full dataset.")
        print("Composio sandbox LLM is reachable. This probe used one invoke_llm call; check Wallet credits before running all applications.")
        return
    apps=json.loads(Path(args.input).read_text(encoding="utf-8"))
    research=json.loads(Path(args.research).read_text(encoding="utf-8")) if args.mode in {"evaluate","all"} and Path(args.research).exists() else None
    results={a["id"]:None for a in apps}
    evaluator=EvaluatorAgent(); researcher=ResearchAgent()
    async with ComposioResearchMCP() as gateway:
        semaphore=asyncio.Semaphore(max(1,args.concurrency))
        async def one(app):
            async with semaphore:
                try:
                    if args.mode in {"research","all"}:
                        row=await researcher.run_app(gateway,app)
                    else:
                        prior=next((x for x in research.get("applications",[]) if x["id"]==app["id"]),{})
                        row=prior.get("research",{})
                    if args.mode in {"evaluate","all"}:
                        try:
                            row["evaluation"]=await evaluator.review_app(gateway,app,row)
                        except Exception as exc:
                            row["evaluation"]={"status":"review_unavailable","sources":[],"checks":{},
                                                "error":f"Independent search failed: {type(exc).__name__}: {exc}"}
                    return {**app,"research":row}
                except Exception as exc:
                    return {**app,"research":{"sources":[],"claims":{f:dict(UNKNOWN) for f in FIELDS},
                           "review_status":"needs_review","error":f"{type(exc).__name__}: {exc}"}}
        tasks=[asyncio.create_task(one(app)) for app in apps]
        for task in asyncio.as_completed(tasks):
            record=await task
            results[record["id"]]=record
            done=[results[a["id"]] for a in apps if results[a["id"]] is not None]
            payload={"schema_version":2,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),
                     "run_status":"running","completed_apps":len(done),"total_apps":len(apps),
                     "source_registry":"Assignment hints are search leads only. Researcher claims cite retrieved likely first-party sources; automated evaluator checks are not human audits.",
                     "agent_system":{"researcher":"ResearchAgent","evaluator":"EvaluatorAgent","transport":"Composio Connect MCP + remote sandbox","inference":"Composio invoke_llm helper","mode":args.mode},
                     "applications":done}
            Path(args.output).parent.mkdir(parents=True,exist_ok=True)
            Path(args.output).write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
            state=record["research"].get("evaluation",{}).get("status",record["research"].get("review_status","unknown"))
            print(f'{record["name"]}: {state} ({len(done)}/{len(apps)})',flush=True)
    payload={"schema_version":2,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),
             "run_status":"complete","completed_apps":len(apps),"total_apps":len(apps),
             "source_registry":"Assignment hints are search leads only. Researcher claims cite retrieved likely first-party sources; automated evaluator checks are not human audits.",
             "agent_system":{"researcher":"ResearchAgent","evaluator":"EvaluatorAgent","transport":"Composio Connect MCP + remote sandbox","inference":"Composio invoke_llm helper","mode":args.mode},
             "applications":[results[a["id"]] for a in apps if results[a["id"]] is not None]}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
    sourced=sum(bool(x["research"].get("sources")) for x in payload["applications"])
    checked=sum(x["research"].get("evaluation",{}).get("status")=="automated_review_complete" for x in payload["applications"])
    print(f"Saved {len(apps)} apps; {sourced} with likely first-party search results; {checked} with automated evaluator review. Human audit accuracy remains separate.")


def main():
    p=argparse.ArgumentParser(description="Two-agent Composio research and evaluator pipeline")
    p.add_argument("--mode",choices=["research","evaluate","all","probe"],default="all")
    p.add_argument("--input",default="data/applications.json")
    p.add_argument("--research",default="data/research.json",help="Existing research file used by evaluate mode")
    p.add_argument("--output",default="data/research.json")
    p.add_argument("--env",default=".env")
    p.add_argument("--concurrency",type=int)
    args=p.parse_args()
    load_dotenv(args.env)
    args.concurrency=args.concurrency or int(os.getenv("RESEARCH_CONCURRENCY","3"))
    asyncio.run(run_pipeline(args))


if __name__=="__main__":
    main()
