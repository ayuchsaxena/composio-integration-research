"""Local, keyless service for saving one-time human claim reviews."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "data" / "human_review_selection.json"
AUDIT = ROOT / "data" / "audit.json"
LOCK = threading.Lock()
OUTCOMES = {"correct", "incorrect", "uncertain"}


def read_json(path: Path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/api/audit":
            return self.send_json(200, read_json(AUDIT, []))
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/audit":
            return self.send_json(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 100_000:
                return self.send_json(400, {"error": "Invalid request size"})
            submitted = json.loads(self.rfile.read(length))
            if not isinstance(submitted, dict):
                return self.send_json(400, {"error": "Expected a review record"})
            key = (submitted.get("app_id"), submitted.get("field"))
            selected = read_json(SELECTION, {"claims": []}).get("claims", [])
            source = next((x for x in selected if (x.get("app_id"), x.get("field")) == key), None)
            if not source:
                return self.send_json(400, {"error": "This claim is not in the selected review inbox"})
            outcome = submitted.get("final_outcome")
            if outcome not in OUTCOMES:
                return self.send_json(400, {"error": "Choose Correct, Incorrect, or Uncertain"})
            reviewer = str(submitted.get("reviewer", "")).strip()
            evidence = submitted.get("evidence_urls")
            if not reviewer or not isinstance(evidence, list) or not evidence or not str(evidence[0]).startswith("https://"):
                return self.send_json(400, {"error": "Add reviewer initials and an official HTTPS evidence URL"})
            record = {
                "app_id": source["app_id"], "app": source["app"], "category": source["category"],
                "field": source["field"], "field_label": source["field_label"],
                "first_pass_claim": source["first_pass_claim"], "researcher_claim": source["researcher_claim"],
                "evaluator_outcome": source["evaluator_outcome"], "evaluator_finding": source["evaluator_finding"],
                "selection_reasons": source["selection_reasons"], "first_pass_outcome": None,
                "final_outcome": outcome, "corrected_finding": str(submitted.get("corrected_finding", "")).strip(),
                "evidence_urls": [str(evidence[0]).strip()], "reviewer": reviewer,
                "reviewer_comment": str(submitted.get("reviewer_comment", "")).strip(),
                "reviewed_at": datetime.now(timezone.utc).isoformat(), "human_verified": True,
            }
            with LOCK:
                audits = read_json(AUDIT, [])
                # Replace in place: a resubmission updates the review instead of duplicating it.
                index = next((i for i, x in enumerate(audits) if (x.get("app_id"), x.get("field")) == key), None)
                if index is None:
                    audits.append(record)
                else:
                    audits[index] = record
                tmp = AUDIT.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(AUDIT)
                subprocess.run([sys.executable, "-m", "src.verify", "--audit", "data/audit.json"], cwd=ROOT, check=True, capture_output=True, text=True)
                subprocess.run([sys.executable, "scripts/build_site.py"], cwd=ROOT, check=True, capture_output=True, text=True)
            return self.send_json(200, {"saved": True, "audits": audits, "reviewed": len(audits)})
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": f"Invalid review: {exc}"})
        except subprocess.CalledProcessError as exc:
            return self.send_json(500, {"error": "Review saved, but report refresh failed. Run npm run verify and npm run build."})
        except Exception as exc:
            return self.send_json(500, {"error": "Could not save the review."})

    def log_message(self, fmt, *args):
        # Keep local console output short; never log submitted notes or secrets.
        print(f"review-server: {args[0] if args else fmt}")


def main():
    port = 8766
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Human review is ready at http://127.0.0.1:{port}/human-review.html")
    print("Reviews are saved locally in data/audit.json. No API key is used.")
    server.serve_forever()


if __name__ == "__main__":
    main()
