# Composio integration research

A two-agent research workflow and a concise report for deciding which app integrations are practical to build. The agents inspect official developer documentation for 100 apps, record sourced findings, and check each other’s work. When the sources do not answer a question, the result stays unknown or uncertain.

## The agents

### Researcher

The Researcher takes the app list in `data/applications.json` and looks for each app’s developer documentation. It retrieves page content, then records findings about authentication, credential access, API features, scopes, limits, webhooks, pricing, MCP support, and likely buildability.

Each supported finding includes a source URL and a short excerpt from the retrieved page. A working link or search result title alone is not treated as evidence. When the documentation does not answer a question, the Researcher leaves it unknown.

### Evaluator

The Evaluator checks the Researcher’s claims in a separate pass. It searches for official documentation independently and compares the page evidence with each claim. It records a claim as supported, contradicted, or inconclusive, with its own source and excerpt where available.

The Evaluator is an automated second opinion. Its results are not human accuracy scores.

### Deterministic checks

Python validation checks that cited sources belong to the app’s source record and that quoted excerpts appear in fetched page text. It also checks the structure of the output and the human-audit records when present. These checks establish traceability, not that a claim is semantically correct. Unclear or high-impact claims can be selected for human review.

## How the workflow runs

1. Load the 100 app names and official-site hints.
2. Search likely first-party developer resources and retrieve their page text through Composio tools over MCP.
3. Ask the Researcher to create concise, evidence-linked findings.
4. Ask the Evaluator to find and check evidence independently.
5. Run deterministic validation and build the HTML report.

The system uses Composio Connect’s MCP endpoint and Composio’s `invoke_llm` helper. It does not call a separate model-provider API directly. Search and page retrieval are still limited by what the available Composio tools can find and read, so missing or unclear documentation remains visible as uncertainty.

## Setup

You need Python 3.11 or newer, Node.js 24, npm, and a Composio Connect consumer key. No app-specific API credentials are needed to research public documentation.

From the project folder, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and add your key to `COMPOSIO_CONSUMER_API_KEY`. The default MCP URL is `https://connect.composio.dev/mcp`. Keep `.env` on your computer; it is excluded from Git. Do not put keys in source files, research data, or the report.

For macOS or Linux, create and activate the virtual environment with:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Then add the consumer key to `.env` before starting a run.

## Start the agents

Start with a single-call connection and inference probe. It does not research the 100 apps:

```bash
python -m src.agents --mode probe
```

Run both agents across the app list:

```bash
python -m src.agents --mode all --input data/applications.json --output data/research.json
```

Or run the passes separately:

```bash
# Researcher pass
python -m src.agents --mode research --input data/applications.json --output data/research.json

# Evaluator pass (reads the same file and adds independent checks)
python -m src.agents --mode evaluate --research data/research.json --output data/research.json
```

The default concurrency is three apps at a time. You can set `RESEARCH_CONCURRENCY`, `RESEARCH_MAX_RETRIES`, and `RESEARCH_DELAY_SECONDS` in `.env`. Each app is saved as the run progresses, so a failed app does not erase completed results. The probe and full run both use Composio inference; check your Composio wallet before running the full list. The available balance does not guarantee a full run will fit within it.

## Validate and build the report

After a run, check the evidence and output structure:

```bash
python -m src.verify --input data/research.json --output data/verification.json
```

Build the static report:

```bash
npm run build
```

Open `index.html` or `report.html` in a browser. The report is also deployed at [composio-assignment-report.vercel.app](https://composio-assignment-report.vercel.app/). To prepare a production deployment, use `npm run deploy:vercel` after linking the project with the Vercel CLI.

The report contains the reviewed findings and evidence. The private review inbox and its queue are excluded from the public Vercel output.

## Research data

- `data/applications.json` — app list and initial website hints.
- `data/research_initial.json` — earlier search-led pass, retained for comparison.
- `data/research.json` — content-backed Researcher findings and Evaluator checks.
- `data/verification.json` — latest deterministic quote and evidence checks.
- `data/logos.json` and `assets/logos/` — local logo mapping and SVG assets.

The current run covers 100 apps; fetched official page text was available for 94. A human reviewed the 20 claims selected because of evidence gaps or agent doubt. That is review coverage, not a measured accuracy percentage. Automated checks, human review coverage, and human accuracy are kept separate.

## Useful commands

```bash
npm run agents:run       # run Researcher and Evaluator
npm run verify           # validate the current research file
npm run normalize        # conservatively normalize access/buildability labels
npm run audit:queue      # select claims for the local human-review inbox
npm run review           # start the private local review interface
npm run logos:sync       # refresh logo matches from the configured icon source
npm run build            # rebuild the report
```

## Project structure

```text
src/       Agent orchestration, Composio MCP client, validation, and report logic
scripts/   Site, Vercel bundle, human-review page, and logo build scripts
data/      App registry, research results, verification, and logo map
assets/    Locally bundled application SVGs
```

## Evidence and limits

- Official documentation is preferred; assignment website hints are starting points, not proof.
- Facts, inferences, and unknowns are distinct in the research output.
- A quote match confirms that the excerpt is present in retrieved text. It does not prove that the interpretation is correct.
- API availability, credentials, and pricing may depend on account, region, plan, or admin role. The report preserves uncertainty where the public pages do not settle those details.
- Human review must be recorded by a person. Automated Evaluator outcomes are never counted as human verdicts.

## References

- [Composio Connect](https://docs.composio.dev/docs/composio-connect)
- [Composio remote sandbox](https://docs.composio.dev/docs/sandbox/remote)
- [Composio Remote Workbench](https://docs.composio.dev/toolkits/meta-tools/remote_workbench)
