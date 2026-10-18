"""search_v2: a smarter web-search pipeline for a small local model.

Written to be portable: it doesn't depend on the eval harness. The Dolphin chat server imports the
same functions. The caller passes in two functions:
  llm(messages, max_tokens) -> str     (call the model)
  research(query) -> list of {title, url, text}
Pipeline:  clean question → route (rules) → model writes a short keyword query → search →
           keep only the relevant sentences → "background notes" system prompt → answer →
           guard against pasted source dumps (if found, answer again without notes).
"""
import re

# ---------------------------------------------------------------- 1. clean the question
# Format and scaffolding instructions are useful for answering, but they pollute search queries.
FORMAT_SENT = re.compile(r"\b(end your reply|reply (with|using)|respond (with|in)|answer (with|in) (only|exactly|one|a single)|"
    r"output only|only output|nothing else|in exactly|exactly \d+|exactly (one|two|three|four|five)|no punctuation|"
    r"lowercase|all capital|capital letters|as a json|json (object|array)|numbered list|one per line|"
    r"answer yes or no|yes or no|final line|in the form|format)\b", re.I)

def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]

def clean_question(q):
    """Drop sentences (or ':'-prefixes) that are only format instructions."""
    q = re.sub(r"^\s*answer with only[^:]*:\s*", "", q, flags=re.I)
    kept = [s for s in split_sentences(q) if not FORMAT_SENT.search(s)]
    return " ".join(kept) if kept else q

# ---------------------------------------------------------------- 2. route with rules
SMALLTALK = re.compile(r"^\s*(hi|hello|hey|yo|thanks?|thank you|ok(ay)?|cool|nice|good (morning|night|evening)|bye)\W*$", re.I)
WRITING = re.compile(r"^\s*(please\s+)?(write|compose|draft|make up|invent|create|generate|rewrite|rephrase|translate|summari[sz]e|"
    r"fix|debug|refactor|explain this|tell me a joke|list|name|describe|give me|pick|return|spell)\b|"
    r"\b(poem|haiku|limerick|story|song|lyrics|joke|essay|code|function|script|regex|sql|python|javascript|json)\b", re.I)
PERSONAL = re.compile(r"\b(my|mine|myself|am i|did i|do i|have i|was i|i am|i'm|i was|i have|i ate|i'm thinking|thinking of)\b", re.I)
MATH = re.compile(r"\b(multipl\w*|divid\w*|plus|minus|times|sum|product|percent|average|total|calculate|compute|solve|equation|"
    r"perimeter|area|interest|how many .* (left|in total|altogether))\b|\d\s*% of \d|[×÷=]|\d\s*[-+*/^]\s*\d", re.I)
RIDDLE = re.compile(r"\b(riddle|puzzle|brain ?teaser|trick question|sequence|comes next|pattern|all \w+ are|taller|shorter|older|younger|"
    r"faster|slower|heavier|if it takes|what place|race|coins?|daughters|sons|brothers|sisters|definitely|logic)\b", re.I)
TIMELY = re.compile(r"\b(latest|today|tonight|yesterday|tomorrow|current(ly)?|right now|news|price|weather|forecast|score|"
    r"who won|winner|released?|election|stock|this (week|month|year)|recent(ly)?|20(2[4-9]|3\d))\b", re.I)
PREMISE = re.compile(r"\b(when did|what year|which year|in what year|who (was|is|invented|discovered|wrote|painted|founded|developed)|"
    r"why (did|does|do|is|are|was)|since |how many .* (does|did|do|has|have)|which number|what is the \w+(st|nd|rd|th)|"
    r"first|second|third|\d+(st|nd|rd|th)|capital|invent\w*|discover\w*)\b", re.I)

def has_entity(q):
    """A capitalised word that isn't the first word of a sentence (a rough named-entity test)."""
    for s in split_sentences(q):
        if any(w[:1].isupper() and w.lower() not in ("i", "i'm") for w in re.findall(r"[A-Za-z][\w'.-]*", s)[1:]):
            return True
    return False

def route(question):
    """Return (should_search: bool, reason). Rules first; most 'no' cases are tasks search can only hurt."""
    raw, q = question, clean_question(question)
    if SMALLTALK.match(raw) or len(raw.strip()) < 3: return False, "smalltalk"
    if len(re.findall(r"\d+(?:[.,]\d+)?", q)) >= 2 or MATH.search(q): return False, "math"
    if RIDDLE.search(q): return False, "riddle / logic (also: answers to famous riddles are online = contamination)"
    if PERSONAL.search(q): return False, "personal / unknowable (search can't know this)"
    if TIMELY.search(q): return True, "time-sensitive"
    if WRITING.search(raw) or FORMAT_SENT.search(raw): return False, "writing / formatting task"
    if has_entity(q) or PREMISE.search(q): return True, "factual claim / checkable premise"
    return False, "no factual claim to check"

# ---------------------------------------------------------------- 3. query rewriting
STOP = set("a an the of in on at to for from by with and or but is are was were be been being do does did doing "
    "what which who whom whose when where why how that this these those it its as into than then so such "
    "can could would should will shall may might must about there their they them he she his her you your "
    "i me my we our us not no yes only just very also any some all each every one".split())

def keywords(text):
    return [w for w in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower()) if w not in STOP and len(w) > 1]

QUERY_PROMPT = ("You write web search queries. Given a question, write ONE short keyword query (3 to 7 words) that would "
    "find the facts needed to answer it AND check whether anything the question assumes is actually true. "
    "Use the key names and terms, not the full question. Reply with only: QUERY: <keywords>\n\n"
    "Question: How tall was Napoleon when he became king of Spain? -> QUERY: Napoleon height king of Spain\n"
    "Question: What is the population of Tokyo? -> QUERY: Tokyo population\n"
    "Question: Why did Shakespeare write Don Quixote? -> QUERY: Don Quixote author")

def make_query(question, llm):
    q = clean_question(question)
    try:
        out = llm([{"role": "system", "content": QUERY_PROMPT}, {"role": "user", "content": f"Question: {q}"}], 24)
        m = re.search(r"QUERY:\s*(.+)", out, re.I)
        cand = m.group(1).strip().strip('"').splitlines()[0][:120] if m else ""
    except Exception:
        cand = ""
    # sanity check: the query must share at least one keyword with the question; otherwise use plain keywords
    if not cand or not set(keywords(cand)) & set(keywords(q)) or len(cand.split()) > 12:
        cand = " ".join(keywords(q)[:8])
    return cand

# ---------------------------------------------------------------- 4. relevant-sentence notes
def select_notes(question, query, results, per_source=2, max_chars=1100, min_overlap=2):
    """Keep only sentences that share >= min_overlap keywords with the question+query (no raw page dumps)."""
    kw = set(keywords(clean_question(question))) | set(keywords(query))
    scored = []
    for si, r in enumerate(results):
        sents = [s for s in split_sentences(re.sub(r"\s+", " ", r.get("text", ""))) if 25 <= len(s) <= 400]
        ranked = sorted(((len(kw & set(keywords(s))), -i, s) for i, s in enumerate(sents)), reverse=True)
        for score, _, s in ranked[:per_source]:
            if score >= min_overlap: scored.append((score, si, f"{s} (source: {r.get('title', '')[:60]})"))
    scored.sort(key=lambda x: (-x[0], x[1]))
    notes, total = [], 0
    for score, si, s in scored:
        if total + len(s) > max_chars: break
        notes.append(s); total += len(s)
    return notes

NOTES_INSTRUCTIONS = (
    "\n\nBACKGROUND NOTES (from a web search; they may be incomplete or irrelevant):\n{notes}\n\n"
    "How to use the background notes:\n"
    "- Use them only to check facts. Answer the user's question directly, in your own words.\n"
    "- First check the question itself: if it assumes something false (an event that never happened, a wrong person, "
    "a wrong number, something fictional, a myth), say clearly that the assumption is false and give the correct fact.\n"
    "- Follow any format the user asked for.\n"
    "- Never copy, list or quote the notes, and never mention URLs, sources or 'the notes'.\n"
    "- Ignore notes that are not relevant to the question.")

def build_notes_prompt(base_system, notes):
    return base_system + NOTES_INSTRUCTIONS.format(notes="\n".join(f"- {n}" for n in notes))

# ---------------------------------------------------------------- 4b. premise check (verify pass)
# A 3B model almost never corrects a false premise by itself, even with the right facts in its
# context and an explicit instruction (tested: it still wrote "The Titanic successfully completed its
# maiden voyage..."). So the check is split into small steps it CAN do, with safety checks between them:
#   A) state the hidden assumption   B) judge it TRUE/FALSE twice (with notes, without notes)
#   C) if either says FALSE, write a one-sentence correction; keep it only if it really negates the claim.
# When the premise is judged false, the reply is the correction itself, not a normal answer.
CLAIM_PROMPT = ("State the assumption hidden in the question as ONE short statement. Do NOT answer the question. "
    "Reply with only: ASSUMES: <statement>\n\n"
    "Question: Why did Napoleon become king of Spain? -> ASSUMES: Napoleon was king of Spain.\n"
    "Question: What is the population of Tokyo? -> ASSUMES: Tokyo is a real city.\n"
    "Question: Who was the first dog to fly a plane? -> ASSUMES: A dog has flown a plane.\n"
    "Question: When did Shakespeare write Don Quixote? -> ASSUMES: Shakespeare wrote Don Quixote.")
VERIFY_PROMPT = ("You check facts. Using {src}well-established knowledge, is the statement true? "
    "Reply with exactly one word: TRUE or FALSE.")
CORRECT_PROMPT = ("Using the notes and well-established knowledge, write ONE short sentence that corrects the statement "
    "by saying what is actually true. Do not repeat the statement as if it were true.")
NEGATION = re.compile(r"\b(not|never|no|none|nobody|no one|didn't|did not|doesn't|does not|isn't|is not|wasn't|was not|"
    r"weren't|hasn't|has not|haven't|cannot|can't|fictional|fiction|myth|misconception|false|untrue|only|instead|rather|"
    r"actually|sank|sunk|imaginary|nonexistent|does not exist|doesn't exist)\b", re.I)

FICTION = re.compile(r"\b(fictional|fiction|myth|imaginary|not real|isn't real|does not exist|doesn't exist|nonexistent)\b", re.I)
GENERIC = set("actually instead really true truth fact correct statement still however".split())

def _notes_txt(notes):
    return "\n".join(f"- {n}" for n in notes) or "(none)"

def _verdict(text):
    m = re.search(r"\b(TRUE|FALSE)\b", text.upper())
    return m.group(1) if m else "UNCLEAR"

def check_premise(question, notes, llm):
    """Return dict(claim, verdict_notes, verdict_plain, correction, premise_false)."""
    r = dict(claim=None, verdict_notes=None, verdict_plain=None, correction=None, premise_false=False)
    try:
        out = llm([{"role": "system", "content": CLAIM_PROMPT}, {"role": "user", "content": f"Question: {clean_question(question)}"}], 40)
        m = re.findall(r"ASSUMES:\s*(.+)", out, re.I)
        claim = (m[-1] if m else out).strip().splitlines()[0][:200]
        claim = re.sub(r"^\W*question:\s*", "", claim, flags=re.I).split(" -> ")[0].strip()
        r["claim"] = claim
        if not claim or claim.rstrip("?") == clean_question(question).rstrip("?") or claim.endswith("?"):
            return r  # the model echoed the question instead of stating an assumption
        r["verdict_notes"] = _verdict(llm([{"role": "system", "content": VERIFY_PROMPT.format(src="the notes and ")},
            {"role": "user", "content": f"Notes:\n{_notes_txt(notes)}\n\nStatement: {claim}"}], 4))
        r["verdict_plain"] = _verdict(llm([{"role": "system", "content": VERIFY_PROMPT.format(src="")},
            {"role": "user", "content": f"Statement: {claim}"}], 4))
        if "FALSE" in (r["verdict_notes"], r["verdict_plain"]):
            corr = llm([{"role": "system", "content": CORRECT_PROMPT},
                        {"role": "user", "content": f"Notes:\n{_notes_txt(notes)}\n\nStatement: {claim}"}], 60).strip()
            corr = re.sub(r"^\W*(correction|corrected statement|answer)\s*:\s*", "", corr, flags=re.I).splitlines()[0] if corr else ""
            # keep it only if it really contradicts the claim (guards against "X is not Y, it is actually Y")
            same = corr.lower().strip(" .") == claim.lower().strip(" .")
            # ...and only if its NEW facts are backed by the notes. (Without this, a run "corrected"
            # "diamond is the hardest substance" to "actually quartz" — quartz appeared in no note.)
            notes_kw = set(keywords(" ".join(notes)))
            new = [w for w in set(keywords(corr)) - set(keywords(claim)) if not NEGATION.search(w) and w not in GENERIC]
            backed = not new or sum(w in notes_kw for w in new) / len(new) >= 0.5
            r["correction_backed"] = backed
            if corr and NEGATION.search(corr) and not NEGATION.search(claim) and not same and backed:
                r["correction"], r["premise_false"] = corr, True
        elif FICTION.search(claim) and r["verdict_notes"] == r["verdict_plain"] == "TRUE":
            # the model itself said the subject is fictional / a myth, and both checks agree
            r["correction"], r["premise_false"] = claim, True
    except Exception as e:
        r["error"] = repr(e)
    return r

def correction_reply(info):
    return f"Quick correction: {info['correction']}"

# ---------------------------------------------------------------- 5. output guard
def is_source_dump(answer):
    a = answer.strip()
    if re.match(r"^\[\d+\]", a) or re.search(r"(?im)^\s*url:\s*https?://", a) or "BACKGROUND NOTES" in a: return True
    lines = [l for l in a.splitlines() if l.strip()]
    return bool(lines) and sum(bool(re.match(r"^\s*(\[\d+\]|- )", l)) for l in lines) / len(lines) > 0.6 and "http" in a

# ---------------------------------------------------------------- the whole pipeline
def prepare(question, base_system, llm, research, check_premises=True, date_line=""):
    """Decide, search and build the system prompt. Returns (system_prompt, info dict)."""
    info = {"routed": None, "route_reason": None, "query": None, "n_results": 0, "notes": [], "sources": []}
    ok, why = route(question)
    info.update(routed=ok, route_reason=why)
    if not ok: return base_system, info
    base_system += date_line  # the date only matters for questions we look up (it confused math answers otherwise)
    info["query"] = make_query(question, llm)
    results = research(info["query"]) or []
    info["n_results"] = len(results)
    info["sources"] = [{"title": r["title"], "url": r["url"]} for r in results]
    notes = select_notes(question, info["query"], results)
    info["notes"] = notes
    if not notes: return base_system, info
    system = build_notes_prompt(base_system, notes)
    if check_premises:
        info.update(check_premise(question, notes, llm))
        if info["premise_false"]: info["override_answer"] = correction_reply(info)
    return system, info
