"""Web-search mode for run_eval.py: mirrors the Dolphin web chat (server.py in the companion
private-llm-chat project).

We import the chat's own research.py (vendored here as ./research.py) (DuckDuckGo via `ddgs` + page-text extraction) and copy the
chat's two prompt pieces word for word:
  1. decide_search(): the "should I search?" prompt + keyword heuristics (used by --search auto)
  2. the "Web search results for ..." block that gets appended to the system prompt.
We don't import server.py itself because loading it starts the web app's config and login code.

One addition, for the sake of the eval: the chat's research.search() quietly returns [] on any error.
Here we call the same DDGS().text(...) with the same arguments and the same URL filter, but we
record errors, back off and retry on rate limits, and wait between queries to be polite to DuckDuckGo.
Needs the search extras from requirements.txt (ddgs, requests, trafilatura):  python run_eval.py --search always ...
"""
import datetime, json, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # vendored research.py / search_v2.py live next to this file
import research  # the chat's own module, vendored (no side effects on import)
from ddgs import DDGS

LOCAL_CFG = {"n_src": 3, "max_src_chars": 900}   # server.py BACKENDS["local"]
POLITE_DELAY_S = 3.0                              # minimum gap between DuckDuckGo queries
RETRY_WAITS_S = [10, 30, 60]                      # backoff when DDG errors / rate-limits
_last_query_t = 0.0

# ---- copied verbatim from server.py ----
SMALLTALK = re.compile(r"^\s*(hi|hello|hey|yo|thanks?|thank you|ok(ay)?|cool|nice|good (morning|night|evening)|bye)\W*$", re.I)
NO_SEARCH = re.compile(r"^\s*(please\s+)?(write|compose|draft|make up|invent|create|generate|rewrite|rephrase|translate|summari[sz]e|fix|debug|refactor|explain this|tell me a joke)\b|"
                       r"\b(poem|haiku|limerick|story|song|lyrics|joke|essay|code|function|script|regex|sql|python|javascript)\b", re.I)
TIMELY = re.compile(r"\b(latest|today|tonight|yesterday|tomorrow|current(ly)?|right now|news|price|cost|weather|forecast|score|"
                    r"who won|winner|released?|election|stock|rate|this (week|month|year)|recent(ly)?|20(2[4-9]|3\d))\b", re.I)

def _decide_prompt(last):
    today = datetime.date.today().isoformat()
    return [{"role": "system", "content":
        f"Today is {today}. You decide if a web search is needed to answer the user's latest message accurately. "
        "Search for: current events, news, prices, sports, recent facts, specific people/companies/products/places, "
        "statistics, laws, anything that may have changed since 2023, or facts you are not sure about. "
        "Do NOT search for: greetings, chit-chat, creative writing, coding help, math, opinions, or timeless general explanations. "
        "Reply with exactly one line: 'SEARCH: <short web search query>' or 'NO'.\n\nExamples:\n"
        "Latest message: Write a poem about autumn -> NO\n"
        "Latest message: How do I reverse a list in Python? -> NO\n"
        "Latest message: Why is the sky blue? -> NO\n"
        "Latest message: What's the price of bitcoin right now? -> SEARCH: bitcoin price today\n"
        "Latest message: Who is the CEO of OpenAI? -> SEARCH: OpenAI CEO\n"
        "Latest message: Best restaurants in Austin -> SEARCH: best restaurants Austin Texas"},
        {"role": "user", "content": f"Latest message: {last[:1000]}"}]

def context_block(q, results):
    ctx = "\n\n".join(f"[{i+1}] {r['title']}\nURL: {r['url']}\n{r['text']}" for i, r in enumerate(results))
    return (f"\n\nWeb search results for \"{q}\":\n\n{ctx}\n\n"
            "Use these search results to answer the user's latest message. Cite the sources you use "
            "inline with their numbers like [1] or [2]. Prefer the search results over your own memory "
            "for facts and dates. If they don't contain the answer, say so. Never invent URLs.")

def date_suffix():
    return f" Today's date is {datetime.date.today().isoformat()}."
# ---- end of copied code ----

def decide_search(prompt, ollama_url, model, seed=0):
    """Same logic as server.decide_search (single-turn). Returns (query or None, raw decider output)."""
    last = prompt
    if SMALLTALK.match(last) or len(last.strip()) < 3: return None, "(smalltalk rule)"
    if NO_SEARCH.search(last) and not TIMELY.search(last): return None, "(NO_SEARCH keyword rule)"
    body = {"model": model, "messages": _decide_prompt(last), "stream": False,
            "options": {"temperature": 0, "num_predict": 32, "seed": seed}}  # server: complete(..., 32) at temp 0
    try:
        req = urllib.request.Request(f"{ollama_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        out = json.loads(urllib.request.urlopen(req, timeout=120).read())["message"]["content"].strip()
    except Exception as e:
        out = f"(decider error {e!r})"
    m = re.search(r"SEARCH:\s*(.+)", out, re.I)
    if m:
        q = m.group(1).strip().strip('"').splitlines()[0][:200]
        if q: return q, out
    if TIMELY.search(last): return last.strip()[:200], out
    return None, out

def _search_with_retry(query, n):
    """Same call + filtering as research.search(), but errors are recorded and retried."""
    global _last_query_t
    errors = []
    for attempt in range(len(RETRY_WAITS_S) + 1):
        wait = POLITE_DELAY_S - (time.time() - _last_query_t)
        if wait > 0: time.sleep(wait)
        _last_query_t = time.time()
        try:
            res = DDGS().text(query, max_results=n + 3, safesearch="off") or []
        except Exception as e:
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
            if attempt < len(RETRY_WAITS_S):
                print(f"      DDG error ({errors[-1]}); retrying in {RETRY_WAITS_S[attempt]}s", flush=True)
                time.sleep(RETRY_WAITS_S[attempt])
            continue
        out, seen = [], set()
        for r in res:
            url = r.get("href") or r.get("url")
            if not url or url in seen or not research._host_ok(url): continue
            seen.add(url)
            out.append({"title": (r.get("title") or url).strip(), "url": url, "snippet": (r.get("body") or "").strip()})
            if len(out) >= n: break
        return out, errors
    return [], errors

def do_research(query, n=LOCAL_CFG["n_src"], max_chars=LOCAL_CFG["max_src_chars"], timeout=240):
    """Run _do_research in a child process. The first search_always attempt died with a
    'Segmentation fault' in native code (HTML parsing / HTTP libs) part-way through. In a child
    process, a crash only costs that one question its search results; the eval keeps going."""
    import multiprocessing as mp
    global _last_query_t
    ctx = mp.get_context("fork")
    for attempt in range(2):
        q = ctx.Queue(); p = ctx.Process(target=lambda: q.put(_do_research(query, n, max_chars)))
        p.start(); p.join(timeout)
        if p.is_alive(): p.terminate(); p.join()
        _last_query_t = time.time()  # child's timestamp isn't shared; assume it just queried
        try:
            return q.get(timeout=1)
        except Exception:
            err = f"search subprocess died (exit code {p.exitcode}), attempt {attempt + 1}"
            print(f"      {err}", flush=True)
            time.sleep(POLITE_DELAY_S)
    return [], [err]

def _do_research(query, n, max_chars):
    """Same as research.research(): search, fetch pages in parallel, fall back to the snippet."""
    results, errors = _search_with_retry(query, n)
    if results:
        with ThreadPoolExecutor(max_workers=len(results)) as ex:
            texts = list(ex.map(lambda r: research._fetch(r["url"], max_chars), results))
        for r, t in zip(results, texts):
            r["text"] = t if len(t) > 200 else r["snippet"]
    return results, errors

def build_system(base_system, prompt, mode, ollama_url, model, seed=0):
    """Return (system_prompt, search_info dict) for one question."""
    t0 = time.time()
    system = base_system + date_suffix()
    info = dict(search_mode=mode, search_query=None, decider_output=None, n_results=0, sources=[], search_errors=[])
    if mode == "always":
        info["search_query"] = prompt.strip()[:200]   # the chat's fallback uses the raw message as the query
    elif mode == "auto":
        info["search_query"], info["decider_output"] = decide_search(prompt, ollama_url, model, seed)
    if info["search_query"]:
        results, errors = do_research(info["search_query"])
        info.update(n_results=len(results), search_errors=errors,
                    sources=[{"title": r["title"], "url": r["url"], "chars": len(r["text"])} for r in results])
        if results: system += context_block(info["search_query"], results)
    info["search_overhead_s"] = round(time.time() - t0, 2)
    info["system_chars"] = len(system)
    return system, info

# ---------------------------------------------------------------- search_v2 support
_CACHE_PATH = os.environ.get("SEARCH_CACHE", "results/search_cache.json")
_cache = None

def cached_research(query, use_cache=True):
    """do_research() with an on-disk cache (query -> results). This keeps tuning runs from hitting
    DuckDuckGo with the same query again and again, and makes context-format experiments
    repeatable. Delete results/search_cache.json to fetch fresh results."""
    global _cache
    import os
    if _cache is None:
        _cache = json.load(open(_CACHE_PATH)) if (use_cache and os.path.exists(_CACHE_PATH)) else {}
    if use_cache and query in _cache and _cache[query]["results"]:
        return _cache[query]["results"], [], True
    results, errors = do_research(query)
    _cache[query] = {"results": results, "errors": errors, "fetched_at": datetime.datetime.now().isoformat()}
    json.dump(_cache, open(_CACHE_PATH, "w"))
    return results, errors, False

def build_system_v2(base_system, prompt, ollama_url, model, seed=0):
    import search_v2
    def llm(messages, max_tokens):
        body = {"model": model, "messages": messages, "stream": False,
                "options": {"temperature": 0, "num_predict": max_tokens, "seed": seed}}
        req = urllib.request.Request(f"{ollama_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=120).read())["message"]["content"]
    meta = {}
    def research_fn(q):
        res, errs, hit = cached_research(q)
        meta.update(search_errors=errs, cache_hit=hit)
        return res
    t0 = time.time()
    system, info = search_v2.prepare(prompt, base_system, llm, research_fn, date_line=date_suffix())
    info.update(meta, search_mode="v2", search_query=info["query"], search_overhead_s=round(time.time() - t0, 2), system_chars=len(system))
    return system, info
