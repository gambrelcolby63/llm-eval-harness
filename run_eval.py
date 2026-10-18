"""Run a model on every item in the dataset and save raw outputs.

Usage:
  python3 run_eval.py --run-name baseline --system-prompt prompts/baseline.txt
  python3 run_eval.py --run-name prompt_v2 --system-prompt prompts/prompt_v2.txt --max-tokens 384
  # web search like the Dolphin chat (needs ddgs/requests/trafilatura from requirements.txt):
  python3 run_eval.py --run-name search_always --search always
  python3 run_eval.py --run-name search_v2 --search v2

Output: results/<run_name>.jsonl  (one line per question: output, latency, token counts)
        results/<run_name>.meta.json (the settings used, so runs are reproducible)
"""
import argparse, json, os, sys, time, urllib.request, datetime

def ask_ollama(url, model, system, prompt, temperature, max_tokens, seed, timeout):
    """Send one chat request to Ollama (non-streaming) and return the parsed JSON response."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens, "seed": seed}}
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-name", required=True)
    p.add_argument("--model", default="dolphin3-3b")
    p.add_argument("--system-prompt", default="prompts/baseline.txt", help="path to a text file ('' for none)")
    p.add_argument("--dataset", default="dataset.jsonl")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=384, help="cap on answer length (CPU is slow)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    p.add_argument("--limit", type=int, default=0, help="only run the first N items (for quick tests)")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--split", choices=["tuning", "holdout"], help="only run items in this split (see splits.json)")
    p.add_argument("--resume", action="store_true", help="keep finished items in an existing results file and do only the rest")
    p.add_argument("--search", choices=["off", "always", "auto", "v2"], default="off",
                   help="web search like the Dolphin chat: always = search every question; auto = the chat's own decide-to-search logic")
    a = p.parse_args()

    system = open(a.system_prompt).read().strip() if a.system_prompt else ""
    items = [json.loads(l) for l in open(a.dataset) if l.strip()]
    if a.split: keep = set(json.load(open("splits.json"))[a.split]); items = [i for i in items if i["id"] in keep]
    if a.limit: items = items[:a.limit]
    os.makedirs("results", exist_ok=True)
    out_path = f"results/{a.run_name}.jsonl"
    meta = dict(run_name=a.run_name, model=a.model, system_prompt_file=a.system_prompt, system_prompt=system,
                temperature=a.temperature, max_tokens=a.max_tokens, seed=a.seed, dataset=a.dataset,
                search=a.search, split=a.split, n_items=len(items), started_at=datetime.datetime.now(datetime.timezone.utc).isoformat())

    done = set()
    if a.resume and os.path.exists(out_path):
        done = {json.loads(l)["id"] for l in open(out_path) if l.strip()}
        print(f"resuming: {len(done)} items already done")
    t_start = time.time()
    with open(out_path, "a" if a.resume else "w") as f:
        for i, it in enumerate(items, 1):
            if it["id"] in done: continue
            t0 = time.time()
            sysmsg, sinfo = system, {}
            if a.search != "off":
                import web_search  # imported only when needed (requires ddgs)
                if a.search == "v2":
                    sysmsg, sinfo = web_search.build_system_v2(system, it["prompt"], a.url, a.model, a.seed)
                else:
                    sysmsg, sinfo = web_search.build_system(system, it["prompt"], a.search, a.url, a.model, a.seed)
                print(f"      search: {sinfo['search_query']!r} -> {sinfo['n_results']} results"
                      f"{' (' + str(len(sinfo['notes'])) + ' notes, ' + sinfo['route_reason'][:25] + ')' if a.search == 'v2' else ''}"
                      f"{' ERRORS ' + str(sinfo.get('search_errors')) if sinfo.get('search_errors') else ''}", flush=True)
            try:
                if sinfo.get("override_answer"):  # search_v2 found a false premise: the reply is the correction
                    raise StopIteration
                r = ask_ollama(a.url, a.model, sysmsg, it["prompt"], a.temperature, a.max_tokens, a.seed, a.timeout)
                rec = dict(output=r["message"]["content"], error=None,
                           prompt_tokens=r.get("prompt_eval_count"), output_tokens=r.get("eval_count"),
                           done_reason=r.get("done_reason"),
                           tokens_per_sec=round(r["eval_count"] / (r["eval_duration"] / 1e9), 2) if r.get("eval_duration") else None)
            except StopIteration:
                rec = dict(output=sinfo["override_answer"], error=None, prompt_tokens=None, output_tokens=None,
                           done_reason="premise_correction", tokens_per_sec=None)
            except Exception as e:  # keep going; a failed request is recorded, not fatal
                rec = dict(output="", error=repr(e), prompt_tokens=None, output_tokens=None, done_reason=None, tokens_per_sec=None)
            if a.search == "v2" and not rec["error"] and sysmsg != system:
                import search_v2
                if search_v2.is_source_dump(rec["output"]):  # output guard: answer again without the notes
                    print("      guard: source dump detected, regenerating without notes", flush=True)
                    r2 = ask_ollama(a.url, a.model, system, it["prompt"], a.temperature, a.max_tokens, a.seed, a.timeout)
                    sinfo["guard_triggered"], sinfo["first_output"] = True, rec["output"]
                    rec.update(output=r2["message"]["content"], output_tokens=r2.get("eval_count"), done_reason=r2.get("done_reason"))
            rec = dict(id=it["id"], category=it["category"], prompt=it["prompt"], **rec,
                       latency_s=round(time.time() - t0, 2), truncated=(rec["done_reason"] == "length"), **sinfo)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            print(f"[{i:2d}/{len(items)}] {it['id']:8s} {rec['latency_s']:6.1f}s {rec['output_tokens'] or 0:4d} tok"
                  f"{'  TRUNCATED' if rec['truncated'] else ''}{'  ERROR ' + rec['error'] if rec['error'] else ''}", flush=True)

    meta["total_runtime_s"] = round(time.time() - t_start, 1)
    if a.resume and os.path.exists(f"results/{a.run_name}.meta.json"):  # add earlier partial run's time
        old = json.load(open(f"results/{a.run_name}.meta.json"))
        meta["total_runtime_s"] = round(meta["total_runtime_s"] + old.get("total_runtime_s", 0), 1)
        meta["resumed"] = old.get("resumed", 0) + 1
    json.dump(meta, open(f"results/{a.run_name}.meta.json", "w"), indent=2)
    print(f"done: {out_path} in {meta['total_runtime_s']}s")

if __name__ == "__main__":
    main()
