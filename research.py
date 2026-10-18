"""Web research helpers: DuckDuckGo search (ddgs, no API key) + safe page fetching/extraction.
Nothing here logs queries or page contents."""
import ipaddress, re, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import requests

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None
from ddgs import DDGS

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "metadata.google.internal"}

def _host_ok(url):
    try:
        u = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https") or not u.hostname: return False
    h = u.hostname.lower().rstrip(".")
    if h in BLOCKED_HOSTS or h.endswith(".local") or h.endswith(".internal") or h.endswith(".localhost"): return False
    if u.port not in (None, 80, 443): return False
    try:
        ip = ipaddress.ip_address(h)
        if not ip.is_global: return False
    except ValueError:
        if "." not in h: return False  # bare intranet names
    return True

def search(query, n=5):
    try:
        res = DDGS().text(query, max_results=n + 3, safesearch="off") or []
    except Exception:
        return []
    out, seen = [], set()
    for r in res:
        url = r.get("href") or r.get("url")
        if not url or url in seen or not _host_ok(url): continue
        seen.add(url)
        out.append({"title": (r.get("title") or url).strip(), "url": url, "snippet": (r.get("body") or "").strip()})
        if len(out) >= n: break
    return out

def _fetch(url, max_chars, timeout=7):
    try:
        for _ in range(4):  # follow redirects manually so every hop is checked
            if not _host_ok(url): return ""
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=timeout,
                             allow_redirects=False, stream=True)
            if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
                url = urllib.parse.urljoin(url, r.headers.get("Location", "")); r.close(); continue
            break
        else:
            return ""
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "html"): return ""
        raw = r.raw.read(1_500_000, decode_content=True); r.close()
        html = raw.decode(r.encoding or "utf-8", "replace")
        text = trafilatura.extract(html, include_comments=False, include_tables=True) if trafilatura else None
        if not text:
            text = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", html)
            text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
        return text[:max_chars]
    except Exception:
        return ""

def research(query, n=4, max_chars=1500):
    """Return list of {title,url,snippet,text}. Falls back to the search snippet when a page can't be read."""
    results = search(query, n)
    if not results: return []
    with ThreadPoolExecutor(max_workers=len(results)) as ex:
        texts = list(ex.map(lambda r: _fetch(r["url"], max_chars), results))
    for r, t in zip(results, texts):
        r["text"] = t if len(t) > 200 else r["snippet"]
    return results

# ---------------------------------------------------------------- crash-isolated research (search_v2)
# In eval runs the native HTML/HTTP libraries occasionally crashed the whole Python process
# ("Segmentation fault", "double free"). Running research() in a short-lived child process means
# such a crash only loses one search instead of killing the chat server. The query is passed on
# stdin (not argv) so it never shows up in the process list.
def research_isolated(query, n=4, max_chars=1500, timeout=60, retries=1):
    import json, subprocess, sys, time
    for attempt in range(retries + 1):
        try:
            p = subprocess.run([sys.executable, __file__, "--child"], input=json.dumps({"q": query, "n": n, "max_chars": max_chars}),
                               capture_output=True, text=True, timeout=timeout)
            res = json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else []
            if res: return res
        except Exception:
            pass
        if attempt < retries: time.sleep(3)  # DuckDuckGo sometimes answers "no results" when rate-limiting
    return []

if __name__ == "__main__":
    import json, sys
    if "--child" in sys.argv:
        a = json.loads(sys.stdin.read())
        print(json.dumps(research(a["q"], n=a["n"], max_chars=a["max_chars"])))
