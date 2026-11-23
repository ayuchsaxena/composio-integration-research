"""Composio SDK session or Connect credentials over Streamable HTTP MCP."""
from __future__ import annotations

import json
import os
import asyncio
import random
from typing import Any


def _to_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


class ComposioResearchMCP:
    """A run-scoped Composio session or shared Connect MCP client.

    Composio owns tool access; the MCP client discovers available schemas at
    runtime. Connect mode uses the consumer key and the shared MCP endpoint.
    """

    def __init__(self, user_id: str | None = None):
        self.user_id = user_id or os.getenv("COMPOSIO_USER_ID", "integration-research-agent")
        self.connect_mode = False
        self.session = None
        self._http_context = None
        self._http_client = None
        self._mcp_context = None
        self._client = None
        self._tools = None

    async def __aenter__(self):
        api_key = os.getenv("COMPOSIO_API_KEY")
        consumer_key = os.getenv("COMPOSIO_CONSUMER_API_KEY")
        try:
            from mcp import ClientSession
            from httpx2 import AsyncClient
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise RuntimeError("Install the agent dependencies with: python -m pip install -r requirements.txt") from exc

        try:
            if api_key:
                from composio import Composio, SESSION_PRESET_DIRECT_TOOLS
                composio = Composio(api_key=api_key)
                self.session = composio.sessions.create(
                    user_id=self.user_id,
                    toolkits=["exa"],
                    tools={"exa": {"enable": ["EXA_SEARCH", "EXA_GET_CONTENTS_ACTION"]}},
                    session_preset=SESSION_PRESET_DIRECT_TOOLS,
                    mcp=True,
                    sandbox={"enable": False},
                )
                endpoint=self.session.mcp.url
                headers=self.session.mcp.headers
            elif consumer_key:
                self.connect_mode=True
                endpoint=os.getenv("COMPOSIO_MCP_URL","https://connect.composio.dev/mcp")
                headers={"x-consumer-api-key":consumer_key}
            else:
                raise RuntimeError("Set COMPOSIO_CONSUMER_API_KEY for Connect MCP or COMPOSIO_API_KEY for an SDK-managed session. Keep credentials in .env.")
            self._http_client = AsyncClient(
                headers=headers,
                timeout=float(os.getenv("COMPOSIO_MCP_TIMEOUT", "45")),
            )
            self._http_context = streamable_http_client(endpoint, http_client=self._http_client)
            timeout=float(os.getenv("COMPOSIO_MCP_TIMEOUT", "45"))
            read_stream, write_stream = await asyncio.wait_for(self._http_context.__aenter__(), timeout=timeout)
            self._mcp_context = ClientSession(read_stream, write_stream)
            self._client = await asyncio.wait_for(self._mcp_context.__aenter__(), timeout=timeout)
            await asyncio.wait_for(self._client.initialize(), timeout=timeout)
            return self
        except Exception as exc:
            if self._http_context:
                try:
                    await self._http_context.__aexit__(None, None, None)
                except Exception:
                    pass
            if self._http_client:
                try:
                    await self._http_client.aclose()
                except Exception:
                    pass
            if self.session:
                try:
                    self.session.delete()
                except Exception:
                    pass
            if isinstance(exc, asyncio.TimeoutError):
                raise TimeoutError("Timed out connecting to Composio MCP. Check network access and the Connect credential, then retry.") from exc
            raise

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._mcp_context:
                await self._mcp_context.__aexit__(exc_type, exc, tb)
        finally:
            if self._http_context:
                await self._http_context.__aexit__(exc_type, exc, tb)
            if self._http_client:
                await self._http_client.aclose()
            if self.session:
                try:
                    self.session.delete()
                except Exception:
                    # Preserve the original run result; an expired session is
                    # harmless and will be cleaned up by Composio.
                    pass

    async def _run_workbench(self, code: str, step: str, metric: str) -> str:
        if self._tools is None:
            response = await self._client.list_tools()
            self._tools = [_to_json(tool) for tool in response.tools]
        tool = next((t for t in self._tools if t.get("name") == "COMPOSIO_REMOTE_WORKBENCH"), None)
        if not tool:
            raise RuntimeError("Composio sandbox is unavailable: COMPOSIO_REMOTE_WORKBENCH was not exposed by this MCP connection")
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        code_key = next((k for k in props if k.lower() == "code_to_execute"), None)
        if not code_key:
            raise RuntimeError("Composio workbench schema does not expose code_to_execute")
        args = {code_key: code}
        if "thought" in props:
            args["thought"] = "Research official API documentation with Composio and extract evidence-backed structured findings."
        if "current_step" in props:
            args["current_step"] = step
        if "current_step_metric" in props:
            args["current_step_metric"] = metric
        result = await self._call_with_retry(tool["name"], args)
        if getattr(result, "isError", False):
            raise RuntimeError(f"Composio workbench failed: {self._content_text(result)}")
        raw=self._content_text(result)
        try:
            envelope=json.loads(raw)
            data=envelope.get("data") if isinstance(envelope,dict) else None
            if isinstance(data,dict) and isinstance(data.get("stdout"),str):
                return data["stdout"]
        except json.JSONDecodeError:
            pass
        return raw

    async def invoke_llm(self, prompt: str, step: str = "STRUCTURED_REVIEW") -> str:
        """Use Composio's sandbox LLM helper; no external provider key is read here."""
        code = (
            "from composio_helper import invoke_llm\n"
            "import json\n"
            "prompt = " + json.dumps(prompt, ensure_ascii=False) + "\n"
            "response = invoke_llm(prompt)\n"
            "print(json.dumps(response, ensure_ascii=False, default=str))"
        )
        return await self._run_workbench(code, step, "1 model call")

    async def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not self._client:
            raise RuntimeError("MCP session is not connected")
        delay = max(0, float(os.getenv("RESEARCH_DELAY_SECONDS", "0.2")))
        if delay:
            await asyncio.sleep(delay)
        if self._tools is None:
            response = await self._client.list_tools()
            self._tools = [_to_json(tool) for tool in response.tools]
        tools = self._tools
        tool = next((t for t in tools if t.get("name")=="COMPOSIO_REMOTE_WORKBENCH"),None) if self.connect_mode else self._choose_search_tool(tools)
        if not tool:
            available = ", ".join(t.get("name", "") for t in tools)
            raise RuntimeError(f"No compatible web-search tool was exposed over MCP. Available tools: {available}")
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        args: dict[str, Any] = {}
        if self.connect_mode:
            code=(
                "from composio_helper import run_composio_tool\n"
                "import json\n"
                "result, error = run_composio_tool('EXA_SEARCH', {\"query\": "+json.dumps(query)+", \"numResults\": "+str(min(max(limit,1),10))+"})\n"
                "print(json.dumps({\"result\": result, \"error\": error}, ensure_ascii=False, default=str))"
            )
            raw = await self._run_workbench(code, "SEARCHING_OFFICIAL_DOCS", "1 search")
            try:
                envelope=json.loads(raw)
                if isinstance(envelope,dict) and envelope.get("error"):
                    raise RuntimeError(f"Composio EXA_SEARCH failed: {envelope['error']}")
                if isinstance(envelope,dict) and "result" in envelope:
                    raw=json.dumps(envelope["result"],ensure_ascii=False,default=str)
            except json.JSONDecodeError:
                pass
            return self._normalize_results(raw,query)
        query_key = next((k for k in props if k.lower().replace("_", "") in {"query", "searchquery", "q"}), None)
        if not query_key:
            raise RuntimeError(f"Search tool {tool['name']} has no recognizable query field")
        args[query_key] = query
        limit_key = next((k for k in props if k.lower().replace("_", "") in {"numresults", "limit", "maxresults", "results"}), None)
        if limit_key:
            args[limit_key] = min(max(limit, 1), 10)
        result = await self._call_with_retry(tool["name"], args)
        if getattr(result, "isError", False):
            raise RuntimeError(f"Composio search failed: {self._content_text(result)}")
        rows=self._normalize_results(self._content_text(result), query)
        content_tool=next((t for t in tools if "get_contents" in t.get("name", "").lower() or "fetch_webpage" in t.get("name", "").lower()),None)
        if content_tool:
            for row in rows[:4]:
                try:
                    fetched=await self._fetch_page(content_tool,row["url"])
                    if fetched:
                        row["snippet"]=(row.get("snippet","")+"\n\nPAGE CONTENT:\n"+fetched)[:12000]
                        row["page_fetched"]=True
                except Exception as exc:
                    row["page_fetch_error"]=f"{type(exc).__name__}: {exc}"
        return rows

    async def fetch_contents(self, urls: list[str], max_characters: int = 3500) -> list[dict[str, Any]]:
        """Fetch page text with Exa and retain per-URL status; a URL alone is never evidence."""
        urls=list(dict.fromkeys(u for u in urls if isinstance(u,str) and u.startswith("https://")))[:10]
        if not urls:
            return []
        if self.connect_mode:
            code=(
                "from composio_helper import run_composio_tool\n"
                "import json\n"
                "result, error = run_composio_tool('EXA_GET_CONTENTS_ACTION', {\"urls\": "+json.dumps(urls)+
                ", \"text\": {\"verbosity\": \"standard\", \"maxCharacters\": "+str(max(500,min(max_characters,8000)))+"}})\n"
                "data = result.get('data', result) if isinstance(result, dict) else {}\n"
                "print(json.dumps({\"error\": error, \"results\": data.get('results', []), \"statuses\": data.get('statuses', [])}, ensure_ascii=False, default=str))"
            )
            raw=await self._run_workbench(code,"READING_OFFICIAL_DOCS",f"0/{len(urls)} pages")
            try:
                parsed=json.loads(raw)
            except json.JSONDecodeError:
                parsed=None
                for line in reversed(raw.splitlines()):
                    candidate=line.strip()
                    if candidate.startswith("{") and candidate.endswith("}"):
                        try:
                            parsed=json.loads(candidate); break
                        except json.JSONDecodeError:
                            continue
                if parsed is None:
                    return [{"url":u,"fetch_status":"response_unparsed","page_content":"","fetch_error":raw[-500:]} for u in urls]
            status_by_url={str(x.get("id") or x.get("url") or ""):x for x in parsed.get("statuses",[]) if isinstance(x,dict)}
            rows=[]
            for item in parsed.get("results",[]) or []:
                if not isinstance(item,dict):
                    continue
                url=str(item.get("url") or item.get("id") or "")
                status=status_by_url.get(url,{})
                text=str(item.get("text") or "")[:max_characters]
                rows.append({"url":url,"title":str(item.get("title") or ""),"page_content":text,
                             "fetch_status":str(status.get("status") or ("success" if text else "empty")),
                             "fetch_source":str(status.get("source") or "unknown"),
                             "content_chars":len(text)})
            received={r["url"] for r in rows}
            for url in urls:
                if url not in received:
                    status=status_by_url.get(url,{})
                    rows.append({"url":url,"title":"","page_content":"","fetch_status":str(status.get("status") or "not_returned"),
                                 "fetch_source":str(status.get("source") or "unknown"),"content_chars":0})
            if parsed.get("error"):
                for row in rows:
                    if not row.get("page_content"):
                        row["fetch_error"]=str(parsed["error"])[:500]
            return rows

        if self._tools is None:
            response=await self._client.list_tools()
            self._tools=[_to_json(tool) for tool in response.tools]
        tool=next((t for t in self._tools if t.get("name")=="EXA_GET_CONTENTS_ACTION"),None)
        if not tool:
            return [{"url":u,"fetch_status":"tool_unavailable","page_content":"","content_chars":0} for u in urls]
        schema=tool.get("input_schema",tool.get("inputSchema",{})); props=schema.get("properties",{})
        url_key="urls" if "urls" in props else "ids" if "ids" in props else None
        if not url_key:
            return [{"url":u,"fetch_status":"schema_missing_urls","page_content":"","content_chars":0} for u in urls]
        args={url_key:urls}
        if "text" in props:
            args["text"]={"verbosity":"standard","maxCharacters":max(500,min(max_characters,8000))}
        result=await self._call_with_retry(tool["name"],args)
        parsed=json.loads(self._content_text(result))
        data=parsed.get("data",parsed) if isinstance(parsed,dict) else {}
        statuses={str(x.get("id") or x.get("url") or ""):x for x in data.get("statuses",[]) if isinstance(x,dict)}
        out=[]
        for item in data.get("results",[]) or []:
            url=str(item.get("url") or item.get("id") or ""); text=str(item.get("text") or "")[:max_characters]
            st=statuses.get(url,{})
            out.append({"url":url,"title":str(item.get("title") or ""),"page_content":text,
                        "fetch_status":str(st.get("status") or ("success" if text else "empty")),
                        "fetch_source":str(st.get("source") or "unknown"),"content_chars":len(text)})
        return out

    async def _call_with_retry(self, name: str, args: dict):
        retries=max(0,int(os.getenv("RESEARCH_MAX_RETRIES","4")))
        for attempt in range(retries+1):
            try:
                result=await self._client.call_tool(name,args)
                if getattr(result,"isError",False):
                    message=self._content_text(result)
                    transient=any(x in message.lower() for x in ("429","408","500","502","503","504","timeout","temporarily unavailable"))
                    if transient and attempt<retries:
                        await asyncio.sleep(min(30,1.5**attempt+random.random()))
                        continue
                return result
            except Exception as exc:
                transient=any(x in str(exc).lower() for x in ("429","408","500","502","503","504","timeout","temporarily unavailable"))
                if not transient or attempt==retries:
                    raise
                await asyncio.sleep(min(30,1.5**attempt+random.random()))

    async def _fetch_page(self, tool: dict, url: str) -> str:
        schema=tool.get("inputSchema",tool.get("input_schema",{}))
        props=schema.get("properties",{}) if isinstance(schema,dict) else {}
        key=next((k for k in props if k.lower() in {"url","urls","ids","id"}),None)
        if not key:
            return ""
        value=[url] if props[key].get("type")=="array" or key.lower() in {"urls","ids"} else url
        result=await self._call_with_retry(tool["name"],{key:value})
        if getattr(result,"isError",False):
            return ""
        return self._content_text(result)

    @staticmethod
    def _choose_search_tool(tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        preferred = [t for t in tools if "search" in t.get("name", "").lower() and
                     ("exa" in t.get("name", "").lower() or "web" in t.get("description", "").lower())]
        return preferred[0] if preferred else next((t for t in tools if "search" in t.get("name", "").lower()), None)

    @staticmethod
    def _content_text(result: Any) -> str:
        structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
        if structured:
            return json.dumps(_to_json(structured), ensure_ascii=False)
        parts=[]
        for block in getattr(result, "content", []) or []:
            if getattr(block, "text", None):
                parts.append(block.text)
        return "\n".join(parts)

    @staticmethod
    def _normalize_results(raw: str, query: str) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return [{"url": "", "title": "Search response", "snippet": raw[:12000], "query": query}]
        candidates = ComposioResearchMCP._find_result_list(parsed)
        if candidates is None:
            return [{"url": "", "title": "Search response", "snippet": json.dumps(parsed, ensure_ascii=False)[:12000], "query": query}]
        rows=[]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("link") or item.get("source_url") or "")
            if not url.startswith("https://"):
                continue
            rows.append({"url": url, "title": str(item.get("title") or item.get("name") or ""),
                         "snippet": str(item.get("text") or item.get("snippet") or item.get("content") or item.get("highlights") or "")[:4000],
                         "query": query})
        return rows

    @staticmethod
    def _find_result_list(value: Any, depth: int = 0) -> list | None:
        if depth > 5:
            return None
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                return ComposioResearchMCP._find_result_list(json.loads(value), depth + 1)
            except json.JSONDecodeError:
                return None
        if isinstance(value, dict):
            for key in ("results", "items", "sources", "data", "result", "stdout", "output", "content", "response"):
                if key in value:
                    if key == "results" and value[key] in (None, ""):
                        continue
                    found = ComposioResearchMCP._find_result_list(value[key], depth + 1)
                    if found is not None:
                        return found
        return None
