"""Runs the approved CLI as a subprocess: no shell, fixed argv, scrubbed env, hard timeout."""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
APPROVED = {"site-crawler": os.path.join(HERE, "crawler_cli.py")}  # allow-list of tools

def run_cli(tool, params, wall_timeout=45):
    if tool not in APPROVED: raise ValueError(f"tool '{tool}' is not approved")
    argv = [sys.executable, "-I", APPROVED[tool], "--url", str(params["url"]),
            "--max-pages", str(int(params["max_pages"])), "--max-depth", str(int(params["max_depth"]))]
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
    # Keep proxy/CA vars only so network works in sandboxes; never API keys.
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "https_proxy", "http_proxy"):
        if k in os.environ: env[k] = os.environ[k]
    t0 = time.time()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, shell=False)
    try:
        out, err = p.communicate(timeout=wall_timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        p.kill(); out, err = p.communicate(); timed_out = True
    res = {"argv": [os.path.basename(argv[2])] + argv[3:], "exit_code": p.returncode, "timed_out": timed_out,
           "duration_s": round(time.time() - t0, 2), "stderr": (err or "")[-1500:], "data": None, "error": None}
    if timed_out: res["error"] = f"CLI exceeded {wall_timeout}s and was killed"; return res
    try:
        res["data"] = json.loads(out)
        if res["data"].get("status") == "error": res["error"] = res["data"]["error"]
    except Exception:
        res["error"] = "CLI produced invalid JSON output" if p.returncode == 0 else f"CLI failed (exit {p.returncode})"
    return res
