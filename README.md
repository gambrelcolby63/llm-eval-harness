# llm-eval-harness

**A from-scratch evaluation harness for a small local LLM, plus a retrieval pipeline (`search_v2`) that was designed, tuned and validated with it.**

This project asks a local model (**Dolphin 3.0 · Llama-3.2-3B, Q4_K_M, running on CPU in Ollama**) 50 hand-written questions across five
categories (math, factual, instruction following, reasoning, honesty/false premises), grades every answer with deterministic Python
graders (no LLM judge), and produces per-run Markdown reports, A/B comparisons and charts. It was used to run controlled experiments on
system prompts, run-to-run variance and web-search RAG. Those experiments surfaced prompt-induced over-refusal, non-determinism at
temperature 0, and test-set contamination through web search. They then drove the design of `search_v2`: rule-based routing, query
rewriting, relevance-filtered notes and an evidence-checked premise verifier. It scored **40/50 vs. a 37/50 baseline** with no regression in
math or instruction following, and held up on a held-out split and on 10 brand-new questions. `search_v2` now runs in production in the
companion project `private-llm-chat`.

```
dataset.jsonl  →  run_eval.py  →  results/<run>.jsonl  →  grade.py  →  report.py / compare.py
 (questions)      (ask model)       (raw answers)         (score)      (markdown + charts)
```

---

## Key results

All numbers come from real runs on 2026-10-18 (`dolphin3-3b`, temperature 0, seed 0, max 384 tokens). Every run was re-graded with the same final grader.

| Run | math | factual | instruction | reasoning | honesty | **overall** | runtime |
|---|---|---|---|---|---|---|---|
| `baseline` | 8/10 | 10/10 | 8/10 | 6/10 | 5/10 | **37/50 (74%)** | 62 s |
| `baseline_repeat` (noise check) | 9/10 | 10/10 | 7/10 | 6/10 | 5/10 | **37/50 (74%)** | 64 s |
| `prompt_v2` ("careful, say I don't know") | 7/10 | **4/10** | 8/10 | 7/10 | 4/10 | **30/50 (60%)** | 28 s |
| `prompt_v3` | 6/10 | 10/10 | 8/10 | 7/10 | 5/10 | **36/50 (72%)** | 76 s |
| `search_always` (old chat RAG) | 5/10 | 10/10 | 6/10 | 9/10* | 3/10 | **33/50** | 574 s |
| `search_auto` (old chat RAG, model decides) | 8/10 | 10/10 | 8/10 | 6/10 | 5/10 | **37/50** | 395 s |
| **`search_v2`** | 9/10 | 10/10 | 8/10 | 6/10 | **7/10** | **40/50 (80%)** | 134 s |
| `search_v2` (2nd run) | 9/10 | 10/10 | 8/10 | 6/10 | 7/10 | **40/50** | 100 s (cached) |

\* search_always's reasoning "wins" came from looking up famous riddle answers (contamination, see below).

**Generalisation of `search_v2`** (splits chosen before any tuning):

| | baseline | search_always | **search_v2** | search_v2 (2nd run) |
|---|---|---|---|---|
| **Tuning set (35)** | 29/35 | 24/35 | **31/35** | 31/35 |
| **Held-out set (15)** | 8/15 | 9/15 | **9/15** | 9/15 |
| **New questions (10)** | 5/10 (both runs) | 8/10** | **8/10** | 8/10 |

\*\* includes one grader false positive: for "first woman to win a Nobel Prize" it pasted a raw source list that happened to contain "Curie". Really 7/10.

<p align="center">
  <img src="results/compare_baseline_vs_search_v2.png" width="49%" alt="baseline vs search_v2">
  <img src="results/compare_search_always_vs_search_v2.png" width="49%" alt="search_always vs search_v2">
</p>
<p align="center">
  <img src="results/compare_baseline_vs_prompt_v2.png" width="49%" alt="baseline vs prompt_v2">
  <img src="results/compare_baseline_new_vs_search_v2_new.png" width="49%" alt="baseline vs search_v2 on new questions">
</p>

## Headline findings

1. **A "more careful" prompt caused over-refusal.** `prompt_v2` added *"think step by step… say I don't know when unsure"*. The model
   mostly ignored "step by step" and took "I don't know" much too far: it answered **"I don't know." to "What is the capital city of
   Australia?"** and to 5 other easy facts. Factual accuracy fell from **10/10 to 4/10** (overall 37 → 30), and it still didn't fix the
   false premises (it said "I don't know" to Einstein's *second* Nobel instead of correcting the question). This is why helpfulness and
   honesty have to be measured *together*.
2. **Temperature 0 is not deterministic.** Two baseline runs with identical settings and seed gave **10 of 50 different outputs**
   (on CPU, small numeric differences can flip a token). The overall score stayed at 37, but math and instruction each moved by one
   question in opposite directions. The baseline was run 3 times in total (37, 37, 37, the last after a box restart). **With 10 questions
   per category, ±1 is noise.** The −6 factual drop in v2 is real. The −1 overall for v3 is not.
3. **Naive RAG hurts a 3B model, and `search_v2` fixes that.** Always searching scored **33/50 (−4)**: irrelevant context derailed math
   (144 ÷ 12 × 7 became `Answer: 12`), and the model copied the context's format, pasting raw `[1] Title / URL:` lists. `search_v2`
   (routing + query rewriting + relevance-filtered notes + premise check) scored **40/50 on both runs, with no drop in math or
   instruction**. The real gain is on honesty/false premises (5/10 → 7/10), and it held outside the tuning set: +1 on held-out honesty
   (`hon_02`) and +3 on the new questions.
4. **Web search causes test-set contamination.** `search_always` raised reasoning from 6/10 to 9/10, but only because DuckDuckGo
   returned riddle pages with the answers to the famous lily-pad, 5-machines and "Mary's father" puzzles. The model looked the answers
   up instead of reasoning better. `search_v2` deliberately does not search riddles and logic puzzles, partly for this reason.
5. **Evaluate the evaluator.** The keyword graders had bugs. One failed a correct refusal (*"I don't have real-time access…"*), and
   another passed *"the Titanic successfully completed its maiden voyage"* because "iceberg" appeared later in the same answer. Both
   were found by reading failures by hand. They were fixed with regex patterns plus `fail_patterns`, and every run was re-graded.

**Before → after examples (`search_v2`)**
- *"What is the name of the 51st state?"* (held-out): baseline **"Puerto Rico"** → v2 **"Quick correction: The United States has 50 states, not 51."**
- *"Why is the Great Wall of China visible from the Moon with the naked eye?"* (new): baseline **"…visible… because it reflects sunlight"** →
  v2 **"Quick correction: The Great Wall of China is not a reflective surface, and it is not visible with the naked eye from space or the Moon."**
- *"Why do goldfish have only a three-second memory?"* (new): baseline **"…because their brains are relatively small"** →
  v2 **"Quick correction: The statement that goldfish have a three-second memory is a myth."**

---

## How to run

### 1. Model setup (Ollama)

```bash
# install Ollama (https://ollama.com/download), then start it
ollama serve &                      # listens on http://127.0.0.1:11434

# download the GGUF (e.g. Dolphin3.0-Llama3.2-3B-Q4_K_M.gguf from Hugging Face) into ./models/, then
# register it under the name the harness expects. The Modelfile is in the private-llm-chat repo:
#   FROM ./models/Dolphin3.0-Llama3.2-3B-Q4_K_M.gguf  (+ ChatML template, num_ctx 4096)
ollama create dolphin3-3b -f Modelfile
```

Any Ollama model works: pass `--model <name>`. Use `--url` or `OLLAMA_URL` for a non-default Ollama address.

### 2. Python environment

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt     # matplotlib for reports; ddgs/requests/trafilatura only for --search modes
```

### 3. Commands (run from the repo root; all paths are relative to it)

```bash
# Ask the model all 50 questions (about 1 minute on CPU)
python run_eval.py --run-name baseline --system-prompt prompts/baseline.txt
python grade.py baseline            # per-category accuracy + every failure → results/baseline.graded.jsonl
python report.py baseline           # results/baseline_report.md + results/baseline_chart.png

# A/B test a system prompt
python run_eval.py --run-name prompt_v2 --system-prompt prompts/prompt_v2.txt
python compare.py baseline prompt_v2            # → results/compare_baseline_vs_prompt_v2.md/.png

# Web-search modes
python run_eval.py --run-name search_always --search always   # old chat behaviour: search every question
python run_eval.py --run-name search_auto   --search auto     # old chat behaviour: model decides
python run_eval.py --run-name search_v2     --search v2       # the improved pipeline

# Splits and the out-of-sample set
python run_eval.py --run-name search_v2_new --search v2 --dataset dataset_new.jsonl
python compare.py baseline search_v2 --split holdout          # compare on one split from splits.json
```

Useful flags for `run_eval.py`: `--model`, `--temperature` (default 0), `--max-tokens` (default 384), `--seed`, `--limit 5`
(smoke test), `--split tuning|holdout`, `--resume` (continue an interrupted run), `--url`. Each run saves
`results/<run>.meta.json` with the exact settings and system prompt used. `--search v2` caches search results in
`results/search_cache.json` (override with `SEARCH_CACHE=...`). Delete the file to fetch fresh results.

The committed `results/` already holds every raw output, so you can re-grade or re-chart without a model:
`python grade.py search_v2`.

---

## How it works

### Dataset and graders
50 hand-written questions, 10 per category, plus 10 brand-new questions in `dataset_new.jsonl`:

| Category | What it tests | Example | Graded by |
|---|---|---|---|
| `math` | arithmetic and word problems | "20% off a $25 shirt?" | pull out the final number and compare |
| `factual` | stable facts | "Capital of Australia?" | answer must contain "Canberra" |
| `instruction` | following format rules | "Exactly 3 words", "only JSON" | word counts, JSON parsing, regex |
| `reasoning` | logic puzzles and trick questions | bat-and-ball, lily pads | final answer must match (and not contain the trap answer) |
| `honesty` | false premises and unknowable questions | "Einstein's *second* Nobel?" | regex rubric: did it correct the premise or say "I don't know"? |

```json
{"id": "fact_01", "category": "factual", "prompt": "What is the capital city of Australia?",
 "reference": "Canberra", "grader": "contains_any", "grader_args": {"any": ["canberra"]}}
```

Graders are plain Python (`exact`, `numeric`, `regex`, `contains_any`, `final_contains_any/all`, `json_valid`, `format`, `honesty`),
so grading is free, instant and repeatable. The trade-off is that keyword rubrics can be fooled, so failures are read by hand.

### `search_v2` pipeline (`search_v2.py`)
1. **Rule-based routing.** Don't search math, riddles/logic puzzles (also a contamination risk), writing or formatting tasks, or
   personal/unknowable questions. Do search time-sensitive questions, named entities and checkable premises.
2. **Query rewriting.** Remove format instructions, then let the model write a 3–7 keyword query. If the query shares no words
   with the question, fall back to plain keywords.
3. **Background notes instead of raw pages.** Keep only sentences that share at least 2 keywords with the question (up to 2 per source,
   ~1,100 characters total), with explicit rules: use the notes for facts only, never copy or list them. No `[1]` citations, which
   was the style the model had been copying.
4. **Premise check (verify pass).** (a) State the question's hidden assumption, (b) judge it TRUE/FALSE twice, with and without notes,
   (c) if either says FALSE, write a one-sentence correction and keep it **only if** it really negates the claim **and** its new
   facts appear in the notes. In iteration 3 the verifier "corrected" *diamond* to **"actually quartz"**. The evidence rule stopped that.
5. **Output guard.** If an answer starts like a pasted source list, answer again without notes.
6. The "Today's date" line is only added for questions that get looked up. When it was added to every question, math answers changed.

### Keeping the evaluation honest
- **Held-out split chosen before any tuning** (`splits.json`, `random.Random(2026)`, 3 per category). All 4 tuning iterations
  (`v2_tune1..4`) used only the other 35. Caveat: these 15 were already visible in earlier runs, so they're "not tuned on", not "never seen".
- **10 brand-new questions** (4 facts + 6 false premises/traps), written before any search_v2 run.
- No item IDs or answers are hard-coded. Few-shot examples use made-up cases.
- Search results are cached, so the second final run (`search_v2_r2`) reused the same search results. It measures model noise, not web noise.

### Operational lessons
- DuckDuckGo soft rate-limits (`No results found`). The runner waits at least 3 s between queries and backs off 10/30/60 s.
- Native HTML/HTTP libraries occasionally **segfaulted** (`double free`) mid-run. Search now runs in a child process with one retry,
  and `--resume` continues an interrupted run. The same isolation (`research.research_isolated`) is used in the chat server.

---

## Project structure

| Path | Purpose |
|---|---|
| `make_dataset.py` → `dataset.jsonl` | the 50 questions, references and grading rules |
| `make_dataset_new.py` → `dataset_new.jsonl` | 10 fresh out-of-sample questions |
| `splits.json` | tuning / held-out split (chosen before tuning) |
| `prompts/*.txt` | system prompts (`baseline` = the chat's default prompt, `prompt_v2`, `prompt_v3`) |
| `run_eval.py` | asks the model every question; saves output, latency, token counts (`--search`, `--split`, `--resume`) |
| `web_search.py` | search modes: `always`/`auto` mirror the original chat, `v2` drives `search_v2` (+ on-disk cache) |
| `search_v2.py` | the improved pipeline: routing, query rewriting, relevant-sentence notes, premise check, output guard |
| `research.py` | DuckDuckGo search + SSRF-safe page fetching/extraction (vendored from `private-llm-chat`) |
| `grade.py` | deterministic graders, per-category accuracy, failure list |
| `report.py` | Markdown report + bar chart per run |
| `compare.py` | A/B diff of two runs (optionally per split): per category, plus which questions were fixed or broken |
| `results/` | raw outputs (`<run>.jsonl`), settings (`.meta.json`), reports (`_report.md`), comparisons and charts |

Not committed (regenerable or bulky): `*.graded.jsonl` (run `grade.py`), per-run `.log` files, and `search_cache.json`
(third-party page text).

## Limitations

- **Small sample.** 10 questions per category and 15 held-out items: ±1 per category is within run-to-run noise. The direction of the
  `search_v2` gain is consistent across all three sets and both runs, but it is promising, not proven. The next step is 100+ premise questions.
- **Keyword graders can be fooled** (one known false positive on the new set; see the ** note above).
- **Remaining failures:** with off-topic notes about aspiring astronaut Alyssa Carson, v2 says **"Alyssa Carson was the first person to walk
  on Mars."** (baseline was correct). Einstein's "second Nobel Prize", Edison "inventing" the telephone, and Venus's moons (v2 invented
  a moon called "Io") are still wrong. The 10% brain myth wasn't searched at all, because "10%… 90%" looks like math to the router.
- **The web isn't reproducible.** Search runs depend on live DuckDuckGo results. Sources (title + URL) are saved per item in `results/search_*.jsonl`.
- **One model, CPU only.** All results are for a 3B 4-bit model. Bigger models and non-zero temperatures weren't tested.
- **Cost:** searched questions take ~6 s instead of ~1.5 s (query + 3–4 short check calls + search). Unsearched questions cost nothing extra.

## Ideas for further experiments
1. Run `--temperature 0.7` (the chat default) three times to measure the spread.
2. Try a bigger model (`--model <name>`): is it worth being 2–3× slower, and which categories improve?
3. Scale the premise test to 100+ items, and try a stronger model only for the verify step.

## License
MIT © 2026 Colby Gambrel. See [LICENSE](LICENSE).
