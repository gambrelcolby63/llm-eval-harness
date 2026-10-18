"""Write a Markdown report + bar chart for one run.

Usage: python3 report.py baseline
Writes results/<run>_report.md and results/<run>_chart.png
"""
import json, sys, statistics
import matplotlib
matplotlib.use("Agg")  # no screen needed
import matplotlib.pyplot as plt
from grade import grade_run, summarize, CATEGORY_ORDER

def pct(p, n): return 100 * p / n if n else 0

def latency_stats(graded):
    lat = sorted(g["latency_s"] for g in graded)
    toks = [g["output_tokens"] or 0 for g in graded]
    tps = [g["tokens_per_sec"] for g in graded if g.get("tokens_per_sec")]
    p90 = lat[min(len(lat) - 1, int(0.9 * len(lat)))]
    return dict(mean=statistics.mean(lat), median=statistics.median(lat), p90=p90, max=lat[-1], total=sum(lat),
                mean_tokens=statistics.mean(toks), mean_tps=statistics.mean(tps) if tps else 0,
                truncated=sum(g.get("truncated", False) for g in graded))

def make_chart(by, run, path):
    cats = [c for c in CATEGORY_ORDER if c in by]
    vals = [pct(*by[c]) for c in cats]
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(cats, vals, color="#4C78A8")
    for b, c in zip(bars, cats):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5, f"{by[c][0]}/{by[c][1]}", ha="center")
    ax.set_ylim(0, 110); ax.set_ylabel("accuracy (%)"); ax.set_title(f"{run}: accuracy by category")
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)

def short(s, n=400):
    s = s.strip(); return s if len(s) <= n else s[:n] + " …"

def main(run):
    graded = grade_run(run); by, (p, n) = summarize(graded)
    meta = json.load(open(f"results/{run}.meta.json"))
    chart = f"results/{run}_chart.png"; make_chart(by, run, chart)
    L = latency_stats(graded)
    md = [f"# Eval report: `{run}`", "",
          f"- **Model:** `{meta['model']}`  |  temperature {meta['temperature']}  |  max tokens {meta['max_tokens']}  |  seed {meta['seed']}",
          f"- **System prompt file:** `{meta['system_prompt_file']}`",
          f"- **Started:** {meta['started_at']}  |  **Wall-clock runtime:** {meta['total_runtime_s']} s for {meta['n_items']} questions", "",
          f"## Overall score: **{p}/{n} ({pct(p, n):.0f}%)**", "",
          "| Category | Correct | Accuracy |", "|---|---|---|"]
    md += [f"| {c} | {by[c][0]}/{by[c][1]} | {pct(*by[c]):.0f}% |" for c in CATEGORY_ORDER if c in by]
    md += [f"| **overall** | **{p}/{n}** | **{pct(p, n):.0f}%** |", "", f"![accuracy by category]({run}_chart.png)", "",
           "## Latency & tokens", "",
           "| Stat | Value |", "|---|---|",
           f"| mean latency | {L['mean']:.2f} s |", f"| median latency | {L['median']:.2f} s |",
           f"| p90 latency | {L['p90']:.2f} s |", f"| max latency | {L['max']:.2f} s |",
           f"| mean output tokens | {L['mean_tokens']:.1f} |", f"| mean generation speed | {L['mean_tps']:.1f} tok/s |",
           f"| answers cut off by max-tokens cap | {L['truncated']} |", "",
           "## Failures (model output vs reference)", ""]
    fails = [g for g in graded if not g["passed"]]
    if not fails: md.append("None!")
    for g in fails:
        md += [f"### {g['id']} ({g['category']})", "", f"**Prompt:** {g['prompt']}", "",
               f"**Reference:** {g['reference']}", "", f"**Why it failed:** {g['reason']}", "",
               "**Model output:**", "", "```", short(g["output"]), "```", ""]
    path = f"results/{run}_report.md"; open(path, "w").write("\n".join(md))
    print(f"wrote {path} and {chart}  |  overall {p}/{n} ({pct(p, n):.0f}%)")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "baseline")
