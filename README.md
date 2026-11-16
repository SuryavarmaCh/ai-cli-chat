# CLI-Driven AI Chat (BuildSprint Round 2)

A chat app where a natural-language request ("Crawl https://example.com, summarise it, return JSON") is turned into a
controlled run of a **real CLI crawler** by the backend. Stdout / stderr / exit code are captured, validated, and shown
in chat as a readable summary + valid JSON, with honest SUCCESS / PARTIAL / ERROR status.

## Architecture
```
Browser chat UI ──POST /api/chat──▶ Flask (app.py)
                                      │ 1. intent.py   NL → {tool, url, max_pages, max_depth}  (LLM optional, rules fallback; params are clamped)
                                      │ 2. runner.py   allow-listed tool → subprocess argv list (shell=False, scrubbed env, 45s wall timeout, kill)
                                      │ 3. crawler_cli.py (the CLI)  → one JSON doc on stdout, logs on stderr, meaningful exit codes
                                      │ 4. validate JSON + status → summarise (LLM or extractive) → build_json
                                      ▼
                          chat reply: status badge, summary, JSON, execution steps, CLI stderr
```
**CLI used:** `crawler_cli.py` (`site-crawler`), a purpose-built crawler on `requests` + `BeautifulSoup`. Chosen over a
general shell/agent because it exposes a tiny, auditable argument surface (`--url --max-pages --max-depth`), which is what
makes safe invocation possible. Exit codes: 0 ok, 2 bad URL, 3 blocked (private host / robots), 4 network failure, 5 no content.

## Setup
Prereqs: Python 3.10+.
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional: add ANTHROPIC_API_KEY for LLM intent/summaries
export $(grep -v '^#' .env | xargs) 2>/dev/null   # or just: export ANTHROPIC_API_KEY=...
python app.py               # http://127.0.0.1:5000
```
Without an API key the app still works end to end (rule-based intent, extractive summary). With a key, Claude
(`ANTHROPIC_MODEL`, default `claude-sonnet-5-5`) parses crawl parameters and writes the summary.

## Test the demo
1. Open http://127.0.0.1:5000 and click **Demo: example.com** (or type: *Crawl https://example.com, extract the important
   information, give me a summary, and return the extracted data in JSON format.*).
2. Open "Execution steps" and "CLI" panels to see the subprocess argv, exit code, duration and stderr log.
3. Edge cases (buttons in UI): private host `http://127.0.0.1` → blocked; unresolvable domain → honest error; `ftp://` → rejected; no URL → asks for one.
4. Run the CLI directly: `python crawler_cli.py --url https://example.com --max-pages 3`

## Security & reliability
- No shell: argv list, `shell=False`; only tools in the `APPROVED` allow-list can run; LLM output is only ever *parameters*, clamped (≤15 pages, depth ≤2).
- URL validation: http/https only; SSRF guard rejects private/loopback/link-local/metadata hosts, re-checked on **every redirect**.
- Respects `robots.txt`; same-host only; 1.5 MB per page cap; per-request timeout; polite delay; 45 s wall-clock kill in the backend.
- Child process gets a scrubbed environment (no API keys); output is regex-redacted for key-like strings.
- Website text is untrusted: it is passed to the summariser inside `<crawl>` tags with an instruction to never follow it, and never reaches any command.
- Failures are surfaced as ERROR/PARTIAL with the exit code and stderr; a success is only reported when the CLI returned valid JSON.
- Not implemented (time-boxed): per-job cancel button, container sandboxing, rate limiting per user.

## AI tools used
Built with Claude (Claude Code) as coding assistant; optional runtime use of the Anthropic Messages API. Libraries: Flask, requests, BeautifulSoup/lxml.
