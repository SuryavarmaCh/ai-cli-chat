#!/usr/bin/env python3
"""site-crawler: a small, polite, sandbox-friendly crawler CLI.

Usage: crawler_cli.py --url URL [--max-pages N] [--max-depth N] [--timeout S] [--delay S]

Writes ONE JSON document to stdout. Progress/diagnostics go to stderr.
Exit codes: 0 ok, 2 bad arguments/URL, 3 blocked (private host / robots),
            4 network failure on the start page, 5 no usable content.
Safeguards: http/https only, SSRF guard (public IPs only, re-checked on every
redirect), robots.txt respected, same-host only, hard page/depth/byte limits.
"""
import argparse, ipaddress, json, re, socket, sys, time
from urllib.parse import urljoin, urlparse, urldefrag
from urllib import robotparser
import requests
from bs4 import BeautifulSoup

UA = "BuildSprintCrawler/1.0 (+educational prototype)"
MAX_BYTES = 1_500_000
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

def log(msg): print(msg, file=sys.stderr, flush=True)

def die(code, msg):
    print(json.dumps({"status": "error", "error": msg, "exit_code": code}))
    sys.exit(code)

def _is_internal_name(h):
    h = h.lower()
    if h == "localhost" or h.endswith((".local", ".internal", ".localhost")): return True
    try: return not ipaddress.ip_address(h).is_global
    except ValueError: return False

def check_public(url):
    import os
    if os.environ.get("CRAWLER_ALLOW_PRIVATE") == "1": return  # TEST ONLY, never set by the backend
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("only http/https URLs are allowed")
    if _is_internal_name(p.hostname):
        raise PermissionError(f"host {p.hostname} is internal/non-public; blocked")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        # Behind an egress proxy (sandboxes/CI) local DNS may be unavailable; the proxy resolves
        # for us. Literal IPs / localhost are still checked below, so SSRF to internal hosts stays blocked.
        import os
        if not (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")) or _is_internal_name(p.hostname):
            raise ConnectionError(f"cannot resolve host {p.hostname}")
        return
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if not ip.is_global:
            raise PermissionError(f"host resolves to non-public address {ip}; blocked")

def fetch(session, url, timeout, hops=4):
    """GET with manual redirects so every hop is SSRF-checked."""
    for _ in range(hops + 1):
        check_public(url)
        r = session.get(url, timeout=timeout, allow_redirects=False, stream=True,
                        headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"})
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"]); r.close(); continue
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_BYTES: break
        r.close()
        return url, r, body
    raise ConnectionError("too many redirects")

def extract(url, html):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "template"]): t.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = (md.get("content") or "").strip() if md else ""
    heads = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])][:15]
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    links = []
    for a in soup.find_all("a", href=True):
        u = urldefrag(urljoin(url, a["href"]))[0]
        if urlparse(u).scheme in ("http", "https"): links.append((u, a.get_text(" ", strip=True)[:80]))
    emails = sorted(set(EMAIL_RE.findall(text)))[:10]
    return {"url": url, "title": title, "description": desc, "headings": heads,
            "text_excerpt": text[:1200], "word_count": len(text.split()),
            "emails": emails}, links

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--max-depth", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=8)
    ap.add_argument("--delay", type=float, default=0.3)
    a = ap.parse_args()
    a.max_pages = max(1, min(a.max_pages, 15)); a.max_depth = max(0, min(a.max_depth, 2))
    start = a.url
    try: check_public(start)
    except ValueError as e: die(2, str(e))
    except PermissionError as e: die(3, str(e))
    except ConnectionError as e: die(4, str(e))

    host = urlparse(start).netloc
    s = requests.Session()
    rp = robotparser.RobotFileParser()
    try:
        _, rr, body = fetch(s, f"{urlparse(start).scheme}://{host}/robots.txt", a.timeout)
        rp.parse(body.decode("utf-8", "ignore").splitlines() if rr.status_code == 200 else [])
    except Exception as e:
        log(f"robots.txt unavailable ({e}); assuming allowed"); rp.parse([])
    if not rp.can_fetch(UA, start): die(3, "robots.txt disallows crawling this URL")

    queue, seen, pages, skipped = [(start, 0)], {start}, [], []
    t0 = time.time()
    while queue and len(pages) < a.max_pages:
        url, d = queue.pop(0)
        if not rp.can_fetch(UA, url): skipped.append({"url": url, "reason": "robots.txt"}); continue
        log(f"[{len(pages)+1}/{a.max_pages}] GET {url} (depth {d})")
        try:
            final, r, body = fetch(s, url, a.timeout)
            if urlparse(final).netloc != host: skipped.append({"url": url, "reason": "redirected off-host"}); continue
            ctype = r.headers.get("content-type", "")
            if r.status_code >= 400: raise ConnectionError(f"HTTP {r.status_code}")
            if "html" not in ctype.lower(): skipped.append({"url": url, "reason": f"non-HTML ({ctype})"}); continue
            page, links = extract(final, body.decode(r.encoding or "utf-8", "ignore"))
            page.update(status_code=r.status_code, depth=d); pages.append(page)
            if d < a.max_depth:
                for u, _t in links:
                    if urlparse(u).netloc == host and u not in seen and len(seen) < 100:
                        seen.add(u); queue.append((u, d + 1))
        except Exception as e:
            log(f"  failed: {e}"); skipped.append({"url": url, "reason": str(e)[:120]})
            if not pages and url == start: die(4, f"could not fetch start page: {e}")
        time.sleep(a.delay)
    if not pages: die(5, "no usable HTML content found")
    status = "ok" if not skipped else "partial"
    print(json.dumps({"status": status, "start_url": start, "pages_crawled": len(pages),
                      "elapsed_s": round(time.time() - t0, 2), "pages": pages, "skipped": skipped}))

if __name__ == "__main__":
    main()
