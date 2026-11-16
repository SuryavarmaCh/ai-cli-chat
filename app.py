import json, os, re, time
from flask import Flask, jsonify, request, send_from_directory
from intent import parse_request, summarise, build_json
from runner import run_cli

app = Flask(__name__, static_folder="static")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{10,}|ANTHROPIC_API_KEY\S*)")

@app.get("/")
def index(): return send_from_directory("static", "index.html")

@app.post("/api/chat")
def chat():
    steps = []
    def step(msg): steps.append({"t": round(time.time() - t0, 2), "msg": SECRET_RE.sub("[redacted]", msg)})
    t0 = time.time()
    body = request.get_json(silent=True) or {}
    intent = parse_request(body.get("message", ""))
    if not intent["ok"]:
        return jsonify(status="error", reply=intent["error"], steps=steps)
    p = intent["params"]
    step(f"Intent: site-crawl of {p['url']} (max_pages={p['max_pages']}, max_depth={p['max_depth']}) via {intent['intent_source']}")
    step("Invoking approved CLI 'site-crawler' as subprocess (no shell, scrubbed env)")
    res = run_cli(intent["tool"], p)
    step(f"CLI finished: exit_code={res['exit_code']} timed_out={res['timed_out']} duration={res['duration_s']}s")
    cli_log = SECRET_RE.sub("[redacted]", res["stderr"])
    if res["error"] or not res["data"]:
        return jsonify(status="error", reply=f"The crawl failed: {res['error']}. No results were produced.",
                       steps=steps, cli={"argv": res["argv"], "exit_code": res["exit_code"], "stderr": cli_log})
    data = res["data"]
    step(f"Validated CLI JSON: {data['pages_crawled']} page(s), status={data['status']}")
    summary, how = summarise(data); step(f"Summary generated ({how})")
    out = build_json(data)
    json.loads(json.dumps(out))  # validity check
    note = "" if data["status"] == "ok" else f" Partial result: {len(data['skipped'])} URL(s) skipped (see JSON)."
    return jsonify(status=data["status"], reply=summary + note, json=out, steps=steps,
                   cli={"argv": res["argv"], "exit_code": res["exit_code"], "stderr": cli_log})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
