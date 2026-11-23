# Integration research desk

A small, reproducible two-agent workflow for evaluating third-party APIs before committing to an integration. The report distinguishes assignment-provided leads, agent claims, automated second-pass checks, and human audits.

## The two agents

**Researcher** searches first-party developer resources, fetches page text through Composio Exa, and drafts claims with exact excerpts. Unsupported fields stay `unknown`; each non-unknown claim carries an evidence class and a quote checked against retrieved text.

**Evaluator** runs a separate search and fetches independent official pages. It checks the Researcher’s claims and must cite an exact excerpt for `correct` or `incorrect`; otherwise the result is `uncertain`. These labels are automated review outcomes, not human accuracy.

Both roles have a clear working style in `src/agents.py`: the Researcher is curious and candid about gaps; the Evaluator is skeptical, fair, and explicit about evidence. These role prompts are behavior constraints, not proof that a model is correct.

## Architecture

1. `data/applications.json` is the 100-app assignment input.
2. `src/composio_mcp.py` connects to Composio Connect over MCP. It uses Composio's remote workbench for web search and its built-in `invoke_llm` helper for extraction and evaluation; it does not call OpenAI or another model API directly.
3. `src/agents.py` runs the Researcher and Evaluator with separate instructions, searches, and outputs. Search results are filtered to likely first-party domains matching the assignment hint. This domain match is a helpful heuristic, not a guarantee of ownership. The evaluator sees its own fresh sources and its own checks are labeled as automated.
4. `src/verify.py` checks that claim and evaluator excerpts occur in successfully fetched source text, verifies citations against the separate ledgers, and validates human audit records. Quote matching checks traceability, not semantic correctness. Automated outcomes remain separate from human accuracy.
5. `src/select_review_claims.py` reproducibly selects the 20 weakest high-impact claims from the 100-app run. `data/audit.json` stores only submitted human decisions; no outcomes are invented.
6. `src/report.py` generates the report; `scripts/build_human_review.py` builds the 20-claim inbox. `src/review_server.py` saves human submissions locally. `npm run build` writes the static Vercel report and inbox.

## Composio-only inference and credentials

The current setup uses the Connect consumer key (`ck_...`) with `https://connect.composio.dev/mcp`. Composio Connect exposes the remote workbench; inside it, `web_search` retrieves documentation and `invoke_llm` performs structured extraction and review. The application makes no direct request to OpenAI or another model provider, and needs no separate model-provider key. The key stays in `.env` and is never written to results.

Composio documents that sandbox execution itself is currently unbilled, while `invoke_llm` uses model tokens. That does not establish the cost of those tokens or guarantee that the available $2 covers a 100-app run. Start with the one-call probe below and check Wallet/credits before spending on a full run. The probe is deliberately separate from research so it cannot accidentally trigger 100 calls.

Official references: [Composio Connect](https://docs.composio.dev/docs/composio-connect), [Remote sandbox helpers and pricing note](https://docs.composio.dev/docs/sandbox/remote), [Remote Workbench](https://docs.composio.dev/toolkits/meta-tools/remote_workbench).

## Setup

Requirements: Python 3.11+, Node.js 18+, and a Composio Connect consumer key. No separate model-provider key is configured.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `COMPOSIO_CONSUMER_API_KEY` in `.env`. Keep `.env` local; it is ignored by Git. Do not paste credentials into the HTML report or commit them.

## Run the agents

```powershell
# One Composio inference call. Check the Composio Wallet/credit balance afterward.
python -m src.agents --mode probe

# Researcher pass: source leads and structured claim drafts
python -m src.research --input data/applications.json --output data/research.json

# Independent Evaluator pass over that research file
python -m src.evaluate --input data/applications.json --research data/research.json --output data/research.json

# Or run Researcher then Evaluator in one pass
python -m src.agents --mode all --input data/applications.json --output data/research.json

# Validate citations and summarize the separate human audit ledger
python -m src.verify --input data/research.json --audit data/audit.json --output data/verification.json

# Normalize access/buildability labels conservatively
npm run normalize -- --input data/research.json --output data/research.json

# Select the 20 weakest claims for human review
npm run audit:queue

# Validate excerpts and human audits
python -m src.verify --input data/research.json --audit data/audit.json --output data/verification.json

# Open the persistent local human-review inbox (no API key required)
npm run review

```

Both agents use Composio's sandbox `invoke_llm` helper. The current content-backed run fetched official page text for 94 apps and generated exact-excerpt claims. Composio Exa reports the tool status for each page; failures remain explicit. If inference is unavailable or credits run out, affected findings remain unknown/unreviewed. Human accuracy is not reported until a person submits review outcomes.

## Human verification

The report now selects the 20 weakest claims (not 20 whole apps) using a deterministic score. It prioritizes Researcher/Evaluator disagreement, missing official page text or excerpts, uncertain access and credential claims, and findings that could change build priority. The score, both agent findings, reason, and available official evidence are shown for every selected claim.

Run `npm run review` and open `http://127.0.0.1:8766/human-review.html`. Choose **Correct**, **Incorrect**, or **Uncertain**, add the official URL checked and reviewer initials, then save. A save is written to `data/audit.json`; the report’s progress, accuracy, corrections, and uncertainty are recalculated and rebuilt. Records are keyed by app and field, so resaving updates the existing row rather than adding a duplicate.

Progress is reviewed claims divided by 20. Human-audited accuracy is correct divided by correct + incorrect; uncertain findings are excluded from accuracy and shown separately. The report lists correction examples only when people have actually recorded them. Automated Evaluator outcomes never count as human reviews.

The human-review inbox is for local use only and is excluded from Vercel deployments. Review records stay on this computer. The static report includes the aggregate progress and findings, not the inbox or individual review records.

## Reproducibility and safety

- Every non-unknown claim must cite a URL present in that app’s retrieval ledger.
- The Evaluator cites only URLs from its separate evidence ledger.
- Domain filtering is transparent and conservative, but humans should confirm the publisher and actual page content.
- Search snippets can omit context; open cited docs before treating a claim as verified.
- Retrieval uses bounded concurrency, retry with backoff for transient Composio failures, and a per-app record so failures do not erase the run.
- Do not commit `.env`, credential-bearing MCP headers, or session URLs. The research dataset contains findings and evidence only.

## Logos and report deployment

`data/logos.json` maps all applications to locally bundled SVGs or explicit monogram fallbacks. SVG files are stored under `assets/logos/` (66 exact matches in the current cache); unmatched apps use initials. Logo metadata tracks asset library, source, license notes, and brand guidance. Run `npm run logos:sync` to refresh the mapping after reviewing the changes.

`npm run build` emits `index.html`, a local `human-review.html` inbox, and the `report.html` preview. `npm run deploy:vercel` rebuilds the report, prepares a Vercel Build Output API v3 bundle containing only the two public report pages and local logos, then deploys it to production. The private review inbox, selected review queue, and review records are excluded. The Vercel project is `composio-assignment-report`. Use `npm run review` to manage reviews on this computer. Agent execution is a separate Python CLI workflow and is not included in the public bundle.


## Current research status

The content-backed run covers 100 apps. `data/research_initial.json` preserves the earlier title-led pass. `data/human_review_selection.json` holds the reproducible 20-claim review inbox. `data/human_review_progress.json` records the user-reported completion of human review across 20/20 doubt-selected claims. The report keeps app-level coverage separate from claim-level accuracy and automated Evaluator outcomes.
