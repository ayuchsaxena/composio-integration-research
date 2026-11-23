"""Match app brands to public SVG libraries and bundle sanitized assets locally.

Exact name/alias matches only. Unmatched apps deliberately use a monogram.
Run on demand; the deployed report never requests icon assets from a CDN.
"""
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets" / "logos"
MAP_PATH = ROOT / "data" / "logos.json"
UA = "ComposioIntegrationResearch/1.0 (local brand asset cache)"
ALIASES = {
    # Product names that use their parent product mark in these public libraries.
    "zoho-crm": ["Zoho"], "zoho-cliq": ["Zoho"], "whatsapp-business": ["WhatsApp"],
    "meta-ads": ["Meta"], "linkedin-ads": ["LinkedIn"], "threads": ["Threads"],
    "salesforce-commerce-cloud": ["Salesforce"], "adobe-commerce": ["Adobe"],
    "amazon-selling-partner": ["Amazon"], "mongodb-atlas": ["MongoDB"],
    "gohighlevel": ["HighLevel"], "otter-ai": ["Otter"], "mermaid-cli": ["Mermaid"],
}
LIBRARY_URLS = {
    "SVGL": "https://svgl.app/docs/api",
    "Lobe Icons": "https://github.com/lobehub/lobe-icons",
    "Simple Icons": "https://github.com/simple-icons/simple-icons",
    "theSVG": "https://thesvg.org/docs/api",
}

def get(url, retries=4):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,image/svg+xml,*/*"})
            with urllib.request.urlopen(req, timeout=35) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError):
            if attempt == retries:
                raise
            time.sleep(min(12, 1.5 ** attempt))

def fetch_json(url):
    return json.loads(get(url).decode("utf-8"))

def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())

def sanitize_svg(raw):
    if len(raw) > 600_000:
        raise ValueError("SVG exceeds 600 KB")
    root = ET.fromstring(raw)
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("SVG root element missing")
    bad = {"script", "foreignObject", "iframe", "object", "embed"}
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] in bad:
                parent.remove(child)
        for key in list(parent.attrib):
            local = key.rsplit("}", 1)[-1].lower()
            value = parent.attrib[key].strip()
            if local.startswith("on") or (local in ("href", "src") and value and not value.startswith("#")):
                del parent.attrib[key]
            elif local == "style" and re.search(r"url\s*\(\s*['\"]?(?:https?:|//|data:)", value, re.I):
                del parent.attrib[key]
    root.set("xmlns", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode")

def pick_exact(mapping, names):
    for name in names:
        key = norm(name)
        if key in mapping:
            found = mapping[key]
            return found if isinstance(found, list) else [found]
    return []

def main():
    apps = json.loads((ROOT / "data" / "applications.json").read_text(encoding="utf-8"))
    svgl = fetch_json("https://api.svgl.app")
    simple_version="16.38.1"
    simple = fetch_json(f"https://unpkg.com/simple-icons@{simple_version}/data/simple-icons.json")
    lobe_meta = fetch_json("https://unpkg.com/@lobehub/icons-static-svg@latest/?meta")
    thesvg = fetch_json("https://thesvg.org/api/registry.json")["icons"]
    svgl_map, simple_map, the_map, lobe_map = {}, {}, {}, {}
    for item in svgl: svgl_map.setdefault(norm(item["title"]), []).append(item)
    for item in simple:
        simple_map.setdefault(norm(item["title"]), []).append(item)
        for alias in item.get("aliases", {}).get("aka", []): simple_map.setdefault(norm(alias), []).append(item)
    for item in thesvg:
        the_map.setdefault(norm(item["title"]), []).append(item)
        for alias in item.get("aliases", []): the_map.setdefault(norm(alias), []).append(item)
    lobe_version=lobe_meta.get("version", "latest")
    for item in lobe_meta.get("files", []):
        path=item.get("path", "")
        if "/icons/" not in path or not path.lower().endswith(".svg"): continue
        stem=path.rsplit("/",1)[-1][:-4]
        base=re.sub(r"-(color|text|dark|light|wordmark)$", "", stem, flags=re.I)
        lobe_map.setdefault(norm(base), []).append({"path":path,"stem":stem})

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    manifest={"schema_version":1,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),"libraries":LIBRARY_URLS,"apps":[]}

    def exact_or_alias(app, library_map):
        names=[app["name"]]
        exact=pick_exact(library_map, names)
        if exact: return exact[0], "exact name"
        for alias in ALIASES.get(app["id"], []):
            matched=pick_exact(library_map,[alias])
            if matched: return matched[0], "curated product-brand alias: " + alias
        # Stable IDs are useful only when the slug clearly matches the same title.
        matched=pick_exact(library_map,[app["id"].replace("-"," ")])
        if matched: return matched[0], "exact stable ID"
        return None, None

    def save_variant(app_id, suffix, url=None, svg_text=None):
        if not url and svg_text is None: return None
        raw=svg_text.encode("utf-8") if svg_text is not None else get(url)
        safe=sanitize_svg(raw)
        filename=f"{app_id}{suffix}.svg"
        (ASSET_DIR / filename).write_text(safe,encoding="utf-8")
        return "assets/logos/"+filename

    for number, app in enumerate(apps, 1):
        item, match = exact_or_alias(app, svgl_map); library="SVGL" if item else None
        if not item:
            item, match=exact_or_alias(app,lobe_map); library="Lobe Icons" if item else None
        if not item:
            item, match=exact_or_alias(app,simple_map); library="Simple Icons" if item else None
        if not item:
            item, match=exact_or_alias(app,the_map); library="theSVG" if item else None
        record={"id":app["id"],"name":app["name"],"status":"monogram","asset":None,"dark_asset":None,"source_library":None,"match_type":None,"source_url":None,"brand_homepage":None,"brand_guidelines":None,"license":"No matching library SVG; monogram fallback. No unrelated mark substituted."}
        if item:
            try:
                if library=="SVGL":
                    route=item.get("route")
                    light_url=route.get("light") or route.get("default") if isinstance(route,dict) else route
                    dark_url=route.get("dark") if isinstance(route,dict) else None
                    record["asset"]=save_variant(app["id"],"",light_url)
                    if dark_url: record["dark_asset"]=save_variant(app["id"],".dark",dark_url)
                    record.update({"source_library":library,"match_type":match,"source_url":light_url,"brand_homepage":item.get("url"),"brand_guidelines":item.get("brandUrl"),"license":"SVGL API does not state an asset license; brand mark remains subject to owner terms."})
                elif library=="Lobe Icons":
                    variants=item if isinstance(item,list) else [item]
                    chosen=sorted(variants,key=lambda x:("-color" not in x["stem"],"-text" in x["stem"]))[0]
                    file_url=f"https://unpkg.com/@lobehub/icons-static-svg@{lobe_version}{chosen['path']}"
                    record["asset"]=save_variant(app["id"],"",file_url)
                    stem=chosen["stem"]
                    dark=next((x for x in variants if x["stem"]==stem+"-dark"),None)
                    if dark: record["dark_asset"]=save_variant(app["id"],".dark",f"https://unpkg.com/@lobehub/icons-static-svg@{lobe_version}{dark['path']}")
                    record.update({"source_library":library,"match_type":match,"source_url":file_url,"brand_homepage":None,"brand_guidelines":None,"license":"Lobe Icons package MIT; brand trademarks and logo-use rules remain with their owners."})
                elif library=="Simple Icons":
                    asset_url=f"https://unpkg.com/simple-icons@{simple_version}/icons/{item['slug']}.svg"
                    record["asset"]=save_variant(app["id"],"",asset_url)
                    record.update({"source_library":library,"match_type":match,"source_url":item.get("source") or asset_url,"asset_source_url":asset_url,"brand_homepage":None,"brand_guidelines":item.get("guidelines"),"license":"Simple Icons library code is CC0; individual brand rights/guidelines may differ and should be checked."})
                else:
                    variants=item.get("variants",[])
                    primary="color" if "color" in variants else ("default" if "default" in variants else ("light" if "light" in variants else None))
                    if not primary: raise ValueError("No displayable SVG variant")
                    primary_url=f"https://thesvg.org/icons/{item['slug']}/{primary}.svg"
                    record["asset"]=save_variant(app["id"],"",primary_url)
                    if "dark" in variants: record["dark_asset"]=save_variant(app["id"],".dark",f"https://thesvg.org/icons/{item['slug']}/dark.svg")
                    record.update({"source_library":library,"match_type":match,"source_url":primary_url,"brand_homepage":item.get("url"),"brand_guidelines":item.get("guidelines"),"license":(item.get("license") or "Not stated in the registry")+"; review brand-specific guidance and trademark terms."})
                record["status"]="bundled"
            except Exception as exc:
                record["download_error"]=f"{type(exc).__name__}: {exc}"
                # A failed match stays a monogram. Never swap in an unrelated logo.
                for path in (record.get("asset"),record.get("dark_asset")):
                    if path:
                        try: (ROOT/path).unlink(missing_ok=True)
                        except OSError: pass
                record.update({"status":"monogram","asset":None,"dark_asset":None,"source_library":None,"source_url":None})
        manifest["apps"].append(record)
        print(f"{number:03}/{len(apps)} {app['name']}: {record['source_library'] or 'monogram'}")

    MAP_PATH.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    counts={}
    for app in manifest["apps"]: counts[app["source_library"] or "monogram"]=counts.get(app["source_library"] or "monogram",0)+1
    print("Logo coverage:",json.dumps(counts,ensure_ascii=False))

if __name__=="__main__": main()

