"""Compare two runs (an A/B test) by category and by individual question.

Usage: python3 compare.py baseline prompt_v2 [--split tuning|holdout]
Writes results/compare_<A>_vs_<B>[_<split>].md and .png
"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from grade import grade_run, summarize, CATEGORY_ORDER

def main(a, b, split=None):
    ga, gb = grade_run(a, split=split), grade_run(b, split=split)
    tag = f"compare_{a}_vs_{b}" + (f"_{split}" if split else "")
    (bya, (pa, n)), (byb, (pb, _)) = summarize(ga), summarize(gb)
    cats = [c for c in CATEGORY_ORDER if c in bya and c in byb]
    lines = [f"# A/B comparison: `{a}` vs `{b}`" + (f" ({split} split only)" if split else ""), "",
             "| Category | " + a + " | " + b + " | change |", "|---|---|---|---|"]
    for c in cats:
        d = byb[c][0] - bya[c][0]
        lines.append(f"| {c} | {bya[c][0]}/{bya[c][1]} | {byb[c][0]}/{byb[c][1]} | {d:+d} |")
    lines.append(f"| **overall** | **{pa}/{n}** | **{pb}/{n}** | **{pb - pa:+d}** |")

    mb = {g["id"]: g for g in gb}
    fixed = [g for g in ga if not g["passed"] and mb[g["id"]]["passed"]]
    broke = [g for g in ga if g["passed"] and not mb[g["id"]]["passed"]]
    lat_a = sum(g["latency_s"] for g in ga) / n; lat_b = sum(g["latency_s"] for g in gb) / n
    tok_a = sum(g["output_tokens"] or 0 for g in ga) / n; tok_b = sum(g["output_tokens"] or 0 for g in gb) / n
    lines += ["", f"Mean latency: {lat_a:.2f}s → {lat_b:.2f}s  |  mean output tokens: {tok_a:.1f} → {tok_b:.1f}", "",
              f"![comparison]({tag}.png)", "",
              f"## Fixed by `{b}` ({len(fixed)})", ""]
    lines += [f"- **{g['id']}**: `{g['output'][:80]!r}` → `{mb[g['id']]['output'][:80]!r}`" for g in fixed] or ["(none)"]
    lines += ["", f"## Broken by `{b}` ({len(broke)})", ""]
    lines += [f"- **{g['id']}**: `{g['output'][:80]!r}` → `{mb[g['id']]['output'][:80]!r}`" for g in broke] or ["(none)"]

    fig, ax = plt.subplots(figsize=(8, 4)); w = 0.38; x = range(len(cats))
    ax.bar([i - w / 2 for i in x], [100 * bya[c][0] / bya[c][1] for c in cats], w, label=a, color="#4C78A8")
    ax.bar([i + w / 2 for i in x], [100 * byb[c][0] / byb[c][1] for c in cats], w, label=b, color="#F58518")
    ax.set_xticks(list(x)); ax.set_xticklabels(cats); ax.set_ylim(0, 110); ax.set_ylabel("accuracy (%)")
    ax.set_title(f"{a} ({pa}/{n}) vs {b} ({pb}/{n})" + (f" [{split}]" if split else "")); ax.legend(); fig.tight_layout()
    fig.savefig(f"results/{tag}.png", dpi=120); plt.close(fig)
    path = f"results/{tag}.md"; open(path, "w").write("\n".join(lines))
    print("\n".join(lines[:len(cats) + 5])); print(f"\nfixed {len(fixed)}, broke {len(broke)} → wrote {path}")

if __name__ == "__main__":
    args = sys.argv[1:]
    split = args[args.index("--split") + 1] if "--split" in args else None
    main(args[0], args[1], split)
