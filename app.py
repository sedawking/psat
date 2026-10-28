"""PSAT Study Tool — Streamlit app.

Serves 10 questions per page (Browse) or one at a time (Flashcards / Quiz).
Supports filters on test / tier / section / module / topic / subtopic / format,
full-text search, and answer-reveal with the official College Board
answer explanations.

Data file: all_questions_classified.jsonl (produced by extract.py + classify.py).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import streamlit as st

# ---------------------------- config ----------------------------
PAGE_SIZE = int(os.environ.get("PSAT_PAGE_SIZE", 10))
MIN_SECONDS_BETWEEN_PAGES = float(os.environ.get("PSAT_MIN_PAGE_INTERVAL", 1.5))
MAX_QS_PER_HOUR = int(os.environ.get("PSAT_MAX_QS_PER_HOUR", 500))
ROOT = Path(__file__).parent
DATA_FILES = [
    ROOT / "all_questions_classified.jsonl",   # extracted from paper practice tests
    ROOT / "qbank_questions.jsonl",            # SAT Suite Question Bank (if fetched)
]

SECTION_ORDER = ["Reading and Writing", "Math"]
TOPIC_ORDER = {
    "Reading and Writing": [
        "Information and Ideas",
        "Craft and Structure",
        "Expression of Ideas",
        "Standard English Conventions",
    ],
    "Math": [
        "Algebra",
        "Advanced Math",
        "Problem-Solving and Data Analysis",
        "Geometry and Trigonometry",
    ],
}

st.set_page_config(
    page_title="PSAT Study Tool",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------- data ----------------------------
SOURCE_LABELS = {
    "pdfs": "PSAT Practice Tests",
    "qbank": "SAT Suite Question Bank",
}


@st.cache_data(show_spinner=False)
def load_rows(cache_key: tuple) -> list[dict]:
    """Cache keyed on (file mtime, file size) tuples so new JSONLs invalidate.
    Loads from any available data file and tags each row with a source."""
    rows: list[dict] = []
    for path in DATA_FILES:
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                r = json.loads(line)
                r["choices"] = [tuple(c) for c in r.get("choices", [])]
                # Normalize the source bucket. (extract.py puts the PDF
                # filename in `source`; keep it under `source_file` instead
                # and use a single "pdfs" / "qbank" tag for filtering.)
                existing_source = r.get("source", "")
                if path.name == "all_questions_classified.jsonl":
                    r["source"] = "pdfs"
                    if existing_source and existing_source != "pdfs":
                        r.setdefault("source_file", existing_source)
                else:
                    r["source"] = "qbank"
                r.setdefault("programs", [r.get("tier", "")] if r.get("tier") else [])
                rows.append(r)
    # Stable sort: source → tier → test → section → module → number.
    rows.sort(
        key=lambda r: (
            0 if r.get("source") == "pdfs" else 1,
            r.get("tier", ""), r.get("test_id", ""),
            0 if r["section"] == "Reading and Writing" else 1,
            r.get("module", 0), r.get("number", 0),
        )
    )
    return rows


def _data_cache_key():
    parts = []
    for p in DATA_FILES:
        if p.exists():
            s = p.stat()
            parts.append((p.name, s.st_mtime, s.st_size))
        else:
            parts.append((p.name, 0, 0))
    return tuple(parts)


# ---------------------------- session ----------------------------
if "sid" not in st.session_state:
    st.session_state.sid = uuid.uuid4().hex[:8]
if "page" not in st.session_state:
    st.session_state.page = 1
if "last_page_at" not in st.session_state:
    st.session_state.last_page_at = 0.0
if "seen_log" not in st.session_state:
    st.session_state.seen_log = []  # timestamps when questions were served
if "reveal" not in st.session_state:
    st.session_state.reveal = {}  # qid -> True
if "expl_open" not in st.session_state:
    st.session_state.expl_open = {}  # qid -> True
# Flashcard / Quiz state
if "fc_idx" not in st.session_state:
    st.session_state.fc_idx = 0
if "fc_flipped" not in st.session_state:
    st.session_state.fc_flipped = False
if "fc_order" not in st.session_state:
    st.session_state.fc_order = []
if "fc_filter_key" not in st.session_state:
    st.session_state.fc_filter_key = ""
if "quiz_history" not in st.session_state:
    st.session_state.quiz_history = []
if "quiz_submitted" not in st.session_state:
    st.session_state.quiz_submitted = False
if "quiz_user_answer" not in st.session_state:
    st.session_state.quiz_user_answer = None

rows = load_rows(_data_cache_key())


# ---------------------------- sidebar ----------------------------
with st.sidebar:
    st.title("📘 PSAT Study Tool")
    src_counts = Counter(r.get("source", "pdfs") for r in rows)
    _caption_parts = []
    if src_counts.get("pdfs"):
        _caption_parts.append(f"{src_counts['pdfs']:,} from practice tests")
    if src_counts.get("qbank"):
        _caption_parts.append(f"{src_counts['qbank']:,} from SAT Suite Question Bank")
    st.caption(f"**{len(rows):,}** questions · " + " · ".join(_caption_parts))
    st.markdown("---")

    mode = st.radio(
        "Mode",
        ["📖 Browse", "🎴 Flashcards", "✏️ Quiz"],
        horizontal=False,
        key="mode",
    )

    present_sources = sorted({r.get("source", "pdfs") for r in rows})
    if len(present_sources) > 1:
        source_opts = ["All"] + [SOURCE_LABELS.get(s, s) for s in present_sources]
        source_label = st.selectbox("Source", source_opts)
        source = next(
            (s for s, label in SOURCE_LABELS.items() if label == source_label),
            "All" if source_label == "All" else source_label,
        )
    else:
        source = "All"

    source_rows = [r for r in rows if source == "All" or r.get("source") == source]

    # Tier matches if it's in the record's `programs` list (so a question
    # that appears in both PSAT/NMSQT and PSAT 8/9 shows under either filter).
    present_tiers = ["All"] + sorted({
        p for r in source_rows for p in (r.get("programs") or [r.get("tier", "")]) if p
    })
    tier = st.selectbox("Tier", present_tiers)

    tier_rows = [
        r for r in source_rows
        if tier == "All" or tier in (r.get("programs") or [r.get("tier", "")])
    ]
    present_tests = ["All"] + sorted({r.get("test", "") for r in tier_rows if r.get("test")})
    test = st.selectbox("Practice test / set", present_tests)

    present_sections = ["All"] + [s for s in SECTION_ORDER if any(r["section"] == s for r in tier_rows)]
    section = st.selectbox("Section", present_sections)

    # Module filter only applies to paper-practice-test rows.
    if source != "qbank":
        module = st.selectbox("Module", ["All", 1, 2])
    else:
        module = "All"

    # Difficulty filter is only meaningful for question-bank rows (E/M/H).
    if source != "pdfs":
        present_diff = sorted({r.get("difficulty", "") for r in tier_rows if r.get("difficulty")})
        if present_diff:
            diff_map = {"E": "Easy", "M": "Medium", "H": "Hard"}
            diff_opts = ["All"] + [diff_map.get(d, d) for d in present_diff]
            diff_pick = st.selectbox("Difficulty", diff_opts)
            difficulty = next(
                (k for k, v in diff_map.items() if v == diff_pick),
                "All" if diff_pick == "All" else diff_pick,
            )
        else:
            difficulty = "All"
    else:
        difficulty = "All"

    # Topic / subtopic cascade — scoped to the already-filtered pool
    topic_pool = tier_rows
    if section != "All":
        topic_pool = [r for r in topic_pool if r["section"] == section]
    present_topics = ["All"]
    if section == "All":
        # offer union of RW + Math topics but preserve order
        for s in SECTION_ORDER:
            for t in TOPIC_ORDER[s]:
                if any(r["topic"] == t for r in topic_pool):
                    present_topics.append(t)
    else:
        for t in TOPIC_ORDER.get(section, []):
            if any(r["topic"] == t for r in topic_pool):
                present_topics.append(t)
    topic = st.selectbox("Topic", present_topics)

    subtopic = "All"
    if topic != "All":
        sub_counts = Counter(
            r.get("subtopic", "Other / General") for r in topic_pool if r["topic"] == topic
        )
        sub_options = ["All"] + [f"{name} ({n})" for name, n in sub_counts.most_common()]
        chosen = st.selectbox("Sub-topic", sub_options)
        subtopic = chosen.rsplit(" (", 1)[0] if chosen != "All" else "All"

    fmt = st.selectbox("Format", ["All", "Multiple Choice", "Student-Produced Response"])
    search = st.text_input("Search text (question + answer + explanation)", "")

    st.markdown("---")
    st.caption(f"Session **#{st.session_state.sid}**")
    served = len([t for t in st.session_state.seen_log if time.time() - t < 3600])
    st.caption(f"Served this hour: **{served}** / {MAX_QS_PER_HOUR}")


# ---------------------------- filter ----------------------------
q = search.strip().lower()


def match(r: dict) -> bool:
    if source != "All" and r.get("source") != source:
        return False
    if tier != "All" and tier not in (r.get("programs") or [r.get("tier", "")]):
        return False
    if test != "All" and r.get("test") != test:
        return False
    if section != "All" and r["section"] != section:
        return False
    if module != "All" and r.get("module") != module:
        return False
    if difficulty != "All" and r.get("difficulty") != difficulty:
        return False
    if topic != "All" and r["topic"] != topic:
        return False
    if subtopic != "All" and r.get("subtopic") != subtopic:
        return False
    if fmt != "All" and r.get("format") != fmt:
        return False
    if q:
        hay = (
            r["question"] + " " + str(r.get("answer", "")) + " "
            + r.get("explanation", "") + " " + r["section"] + " " + r.get("test", "")
        ).lower()
        if q not in hay:
            return False
    return True


filtered = [r for r in rows if match(r)]
total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
st.session_state.page = min(max(1, st.session_state.page), total_pages)


# ---------------------------- header ----------------------------
st.markdown("## 📘 PSAT – Practice Test Study Tool")

# Filter signature so Flashcard/Quiz state resets when filters change
filter_sig = f"{source}|{tier}|{test}|{section}|{module}|{difficulty}|{topic}|{subtopic}|{fmt}|{q}|{mode}"
if filter_sig != st.session_state.fc_filter_key:
    st.session_state.fc_filter_key = filter_sig
    st.session_state.fc_idx = 0
    st.session_state.fc_flipped = False
    st.session_state.quiz_submitted = False
    st.session_state.quiz_user_answer = None
    st.session_state.fc_order = list(range(len(filtered)))

if len(st.session_state.fc_order) != len(filtered):
    st.session_state.fc_order = list(range(len(filtered)))


# ---------------------------- helpers ----------------------------
_CHOICE_LETTER_RE = re.compile(r"^\s*([A-D])\)")
_NUM_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?|-?\d+/\d+|-?\.\d+)\s*$")


def _qid(r: dict) -> str:
    return f"{r.get('test_id','')}|{r['section']}|M{r.get('module','?')}|Q{r.get('number','?')}"


def normalize_mc_answer(ans: str) -> str | None:
    """Extract the A/B/C/D letter from an answer string. Handles 'B', 'B)',
    'Choice B', 'Choice B is the best answer', etc."""
    if not ans:
        return None
    s = ans.strip().upper()
    # 'Choice X ...' takes precedence over the first character ('C' in 'Choice').
    m = re.search(r"\bCHOICE\s*([A-D])\b", s)
    if m:
        return m.group(1)
    # Short answers like 'B' or 'B)' or 'B ...' where the FIRST token is a
    # single A-D letter.
    first = re.match(r"\s*([A-D])\b", s)
    if first:
        return first.group(1)
    return None


def _parse_number_token(token: str) -> Fraction | None:
    token = token.strip().replace(" ", "")
    if not token:
        return None
    try:
        if "/" in token:
            return Fraction(token)
        if "." in token:
            return Fraction(token).limit_denominator(10**9)
        return Fraction(int(token))
    except (ValueError, ZeroDivisionError):
        return None


def spr_acceptable_values(answer: str) -> set[Fraction]:
    """Student-produced response answers can be given as decimals, fractions,
    or both separated by ';' or ','. Return the set of numeric values that
    the official answer accepts."""
    out: set[Fraction] = set()
    if not answer:
        return out
    for part in re.split(r"[;,]", answer):
        f = _parse_number_token(part)
        if f is not None:
            out.add(f)
    return out


def is_correct_spr(user: str, official_answer: str) -> bool:
    uf = _parse_number_token(user)
    if uf is None:
        return False
    accepted = spr_acceptable_values(official_answer)
    if not accepted:
        return False
    # Accept exact match on fractions OR very close decimal
    for a in accepted:
        if uf == a:
            return True
        # tolerate rounding at 4 decimal places (PSAT truncates/rounds at 4 digits)
        if abs(float(uf) - float(a)) < 5e-4:
            return True
    return False


def render_question(r: dict, idx: int) -> None:
    qid = _qid(r)
    with st.container(border=True):
        is_qbank = r.get("source") == "qbank"
        meta_bits = [f"**{r.get('test','')}**", r["section"]]
        if not is_qbank:
            meta_bits.append(f"Module {r.get('module','?')}")
        meta_bits.append(f"Q{r.get('number','?')}")
        if is_qbank and r.get("difficulty"):
            diff_label = {"E": "Easy", "M": "Medium", "H": "Hard"}.get(r["difficulty"], r["difficulty"])
            meta_bits.append(diff_label)
        meta_bits.append(r.get("format", ""))
        meta_bits.append(f"_{r.get('topic','')}_")
        sub = r.get("subtopic")
        if sub:
            meta_bits.append(f"[{sub}]")
        st.caption(" · ".join([b for b in meta_bits if b]))

        st.markdown(r["question"])

        if r["format"] == "Multiple Choice" and r.get("choices"):
            for letter, text in r["choices"]:
                st.markdown(f"- **{letter})** {text}")

        if st.session_state.reveal.get(qid):
            st.success(f"**Answer:** {r['answer']}")
            if r.get("explanation"):
                if st.session_state.expl_open.get(qid):
                    st.markdown("**Explanation:**")
                    st.markdown(r["explanation"])
                    if st.button("Hide explanation", key=f"hide-expl-{st.session_state.page}-{idx}"):
                        st.session_state.expl_open[qid] = False
                        st.rerun()
                else:
                    if st.button("📘 Show explanation", key=f"show-expl-{st.session_state.page}-{idx}"):
                        st.session_state.expl_open[qid] = True
                        st.rerun()
        else:
            if st.button("👁 Reveal answer", key=f"rev-{st.session_state.page}-{idx}"):
                st.session_state.reveal[qid] = True
                st.rerun()


# =================================================================
# MODE: BROWSE
# =================================================================
if mode.startswith("📖") or mode == "Browse":
    st.markdown(
        f"**{len(filtered):,}** matching questions · page **{st.session_state.page}** of "
        f"**{total_pages}** · {PAGE_SIZE} per page"
    )

    cols = st.columns([1, 1, 2, 1])
    if cols[0].button("⟵ Prev", disabled=st.session_state.page <= 1):
        st.session_state.page -= 1
    if cols[1].button("Next ⟶", disabled=st.session_state.page >= total_pages):
        st.session_state.page += 1
    cols[2].number_input(
        "Jump to page", min_value=1, max_value=total_pages,
        value=st.session_state.page, key="jump", label_visibility="collapsed",
        on_change=lambda: setattr(st.session_state, "page", st.session_state.jump),
    )
    if cols[3].button("🎲 Random page"):
        import random
        st.session_state.page = random.randint(1, total_pages)

    now = time.time()
    dt = now - st.session_state.last_page_at
    if dt < MIN_SECONDS_BETWEEN_PAGES and st.session_state.last_page_at > 0:
        time.sleep(MIN_SECONDS_BETWEEN_PAGES - dt)

    served_this_hour = len([t for t in st.session_state.seen_log if now - t < 3600])
    page_slice = filtered[(st.session_state.page - 1) * PAGE_SIZE : st.session_state.page * PAGE_SIZE]
    if served_this_hour + len(page_slice) > MAX_QS_PER_HOUR:
        remaining = max(0, MAX_QS_PER_HOUR - served_this_hour)
        st.warning(
            f"You've viewed {served_this_hour} questions in the last hour "
            f"(limit {MAX_QS_PER_HOUR}). Only showing {remaining} this page."
        )
        page_slice = page_slice[:remaining]

    st.session_state.last_page_at = time.time()
    st.session_state.seen_log.extend([time.time()] * len(page_slice))
    st.session_state.seen_log = [t for t in st.session_state.seen_log if time.time() - t < 3600]

    prev_header = None
    for i, r in enumerate(page_slice):
        header_key = (r.get("source"), r.get("test_id"), r["section"], r.get("module"))
        if header_key != prev_header:
            if r.get("source") == "qbank":
                st.markdown(f"### {r.get('test','SAT Suite Question Bank')} — {r['section']}")
            else:
                st.markdown(f"### {r.get('test','')} — {r['section']} · Module {r.get('module','?')}")
            prev_header = header_key
        render_question(r, i)


# =================================================================
# MODE: FLASHCARDS
# =================================================================
elif mode.startswith("🎴") or mode == "Flashcards":
    import random as _rand

    if not filtered:
        st.info("No questions match your filters. Loosen them in the sidebar.")
    else:
        n = len(filtered)
        idx = min(st.session_state.fc_idx, n - 1)
        r = filtered[st.session_state.fc_order[idx]]

        c_top = st.columns([2, 1, 1, 1])
        c_top[0].markdown(f"**Card {idx + 1} / {n}** · {r['topic']} · {r.get('subtopic','')}")
        if c_top[1].button("🔀 Shuffle"):
            _rand.shuffle(st.session_state.fc_order)
            st.session_state.fc_idx = 0
            st.session_state.fc_flipped = False
            st.rerun()
        if c_top[2].button("↩️ Restart"):
            st.session_state.fc_order = list(range(n))
            st.session_state.fc_idx = 0
            st.session_state.fc_flipped = False
            st.rerun()
        if r.get("source") == "qbank":
            diff_label = {"E": "Easy", "M": "Medium", "H": "Hard"}.get(r.get("difficulty",""), "")
            c_top[3].caption(
                f"{r.get('test','SAT Suite Qbank')}"
                + (f" · {diff_label}" if diff_label else "")
                + f" · Q{r.get('number','?')}"
            )
        else:
            c_top[3].caption(
                f"{r.get('test','')} · Module {r.get('module','?')} · Q{r.get('number','?')}"
            )

        with st.container(border=True):
            st.caption("QUESTION" if not st.session_state.fc_flipped else "ANSWER")
            st.markdown(f"### {r['question']}")
            if r["format"] == "Multiple Choice" and r.get("choices"):
                for letter, text in r["choices"]:
                    st.markdown(f"- **{letter})** {text}")
            if st.session_state.fc_flipped:
                st.success(f"**Answer:** {r['answer']}")
                if r.get("explanation"):
                    with st.expander("📘 Explanation"):
                        st.markdown(r["explanation"])

        c_bot = st.columns([1, 1, 1])
        if c_bot[0].button("⟵ Previous card", disabled=idx == 0, use_container_width=True):
            st.session_state.fc_idx = max(0, idx - 1)
            st.session_state.fc_flipped = False
            st.rerun()
        flip_label = "👁 Reveal answer" if not st.session_state.fc_flipped else "🙈 Hide answer"
        if c_bot[1].button(flip_label, use_container_width=True):
            st.session_state.fc_flipped = not st.session_state.fc_flipped
            st.rerun()
        if c_bot[2].button("Next card ⟶", disabled=idx >= n - 1, use_container_width=True):
            st.session_state.fc_idx = min(n - 1, idx + 1)
            st.session_state.fc_flipped = False
            st.rerun()


# =================================================================
# MODE: QUIZ
# =================================================================
elif mode.startswith("✏️") or mode == "Quiz":
    import random as _rand

    if not filtered:
        st.info("No questions match your filters. Loosen them in the sidebar.")
    else:
        n = len(filtered)
        idx = min(st.session_state.fc_idx, n - 1)
        r = filtered[st.session_state.fc_order[idx]]
        qid = _qid(r)

        # ---------- scoreboard ----------
        hist = st.session_state.quiz_history
        attempted = len(hist)
        correct = sum(1 for h in hist if h["correct"])
        pct = (correct / attempted * 100) if attempted else 0.0

        # Compute current / best streak
        cur = best = 0
        for h in hist:
            if h["correct"]:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0

        st.markdown(
            f"**Question {idx + 1} / {n}** · {r['topic']} · {r.get('subtopic','')}"
        )

        m = st.columns(4)
        m[0].metric("Correct", f"{correct}/{attempted}", f"{pct:.0f}%" if attempted else "—")
        m[1].metric("Current streak", f"🔥 {cur}")
        m[2].metric("Best streak", f"🏆 {best}")
        m[3].metric("This question", r.get("format", ""))

        st.progress(idx / max(1, n - 1), text=f"Deck progress: {idx + 1} / {n}")

        if attempted:
            with st.expander("📊 Score breakdown by topic / sub-topic"):
                def _agg(key: str):
                    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"c": 0, "a": 0})
                    for h in hist:
                        k = h.get(key) or "—"
                        buckets[k]["a"] += 1
                        if h["correct"]:
                            buckets[k]["c"] += 1
                    return buckets

                for label, key in [
                    ("Section", "section"),
                    ("Topic", "topic"),
                    ("Sub-topic", "subtopic"),
                    ("Format", "format"),
                ]:
                    buckets = _agg(key)
                    st.markdown(f"**By {label.lower()}**")
                    md = ["| " + label + " | Correct | % |", "|---|---:|---:|"]
                    for k, v in sorted(buckets.items(), key=lambda x: -x[1]["c"]):
                        p = (v["c"] / v["a"] * 100) if v["a"] else 0
                        md.append(f"| {k} | {v['c']}/{v['a']} | {p:.0f}% |")
                    st.markdown("\n".join(md))

        ctl = st.columns([1, 1, 1])
        if ctl[0].button("🔀 Shuffle deck"):
            _rand.shuffle(st.session_state.fc_order)
            st.session_state.fc_idx = 0
            st.session_state.quiz_submitted = False
            st.session_state.quiz_user_answer = None
            st.rerun()
        if ctl[1].button("↺ Reset score"):
            st.session_state.quiz_history = []
            st.rerun()
        if ctl[2].button("↩️ Restart deck"):
            st.session_state.fc_order = list(range(n))
            st.session_state.fc_idx = 0
            st.session_state.quiz_submitted = False
            st.session_state.quiz_user_answer = None
            st.rerun()

        # ---------- question card ----------
        with st.container(border=True):
            if r.get("source") == "qbank":
                diff_label = {"E": "Easy", "M": "Medium", "H": "Hard"}.get(r.get("difficulty",""), "")
                st.caption(
                    f"{r.get('test','SAT Suite Qbank')} · {r['section']} · "
                    f"Q{r.get('number','?')} · {r.get('format','')}"
                    + (f" · {diff_label}" if diff_label else "")
                )
            else:
                st.caption(
                    f"{r.get('test','')} · {r['section']} · Module {r.get('module','?')} · "
                    f"Q{r.get('number','?')} · {r.get('format','')}"
                )
            if r["format"] == "Multiple Choice":
                st.markdown(f"### {r['question']}")
                if r.get("choices"):
                    letters = [l for l, _ in r["choices"]]
                    labels = {l: f"{l}) {t}" for l, t in r["choices"]}
                else:
                    # Choices couldn't be extracted — show plain A/B/C/D.
                    letters = ["A", "B", "C", "D"]
                    labels = {l: l for l in letters}
                    st.caption("_(Choice text not extracted — pick the letter you believe is correct.)_")
                picked = st.radio(
                    "Your answer:",
                    letters,
                    format_func=lambda l: labels.get(l, l),
                    key=f"quiz_mc_{qid}_{idx}",
                    index=(
                        letters.index(st.session_state.quiz_user_answer)
                        if (
                            st.session_state.quiz_submitted
                            and st.session_state.quiz_user_answer in letters
                        )
                        else None
                    ),
                )
                if st.button(
                    "✅ Check answer",
                    key=f"check_mc_{idx}",
                    disabled=picked is None or st.session_state.quiz_submitted,
                ):
                    correct_letter = normalize_mc_answer(r["answer"])
                    is_ok = picked == correct_letter
                    st.session_state.quiz_user_answer = picked
                    st.session_state.quiz_submitted = True
                    st.session_state.quiz_history.append({
                        "qid": qid, "test": r.get("test"), "section": r["section"],
                        "module": r.get("module"), "number": r.get("number"),
                        "topic": r["topic"], "subtopic": r.get("subtopic"),
                        "format": r["format"], "correct": is_ok,
                        "user_answer": picked, "official_answer": r["answer"],
                        "question": r["question"][:200],
                    })
                    st.rerun()

                if st.session_state.quiz_submitted:
                    correct_letter = normalize_mc_answer(r["answer"])
                    if st.session_state.quiz_user_answer == correct_letter:
                        st.success(f"✅ Correct! Answer: **{r['answer']}**")
                    else:
                        st.error(
                            f"❌ You picked **{st.session_state.quiz_user_answer})** · "
                            f"Correct: **{r['answer']}**"
                        )
                    if r.get("explanation"):
                        with st.expander("📘 Explanation"):
                            st.markdown(r["explanation"])

            else:  # Student-Produced Response
                st.markdown(f"### {r['question']}")
                user_in = st.text_input(
                    "Your answer (number, decimal, or fraction a/b):",
                    key=f"quiz_sa_{qid}_{idx}",
                    disabled=st.session_state.quiz_submitted,
                )
                if st.button(
                    "✅ Check answer",
                    key=f"check_sa_{idx}",
                    disabled=not user_in or st.session_state.quiz_submitted,
                ):
                    ok = is_correct_spr(user_in, r["answer"])
                    st.session_state.quiz_user_answer = user_in
                    st.session_state.quiz_submitted = True
                    st.session_state.quiz_history.append({
                        "qid": qid, "test": r.get("test"), "section": r["section"],
                        "module": r.get("module"), "number": r.get("number"),
                        "topic": r["topic"], "subtopic": r.get("subtopic"),
                        "format": r["format"], "correct": ok,
                        "user_answer": user_in, "official_answer": r["answer"],
                        "question": r["question"][:200],
                    })
                    st.rerun()

                if st.session_state.quiz_submitted:
                    if is_correct_spr(st.session_state.quiz_user_answer or "", r["answer"]):
                        st.success(f"✅ Correct! Official answer: **{r['answer']}**")
                    else:
                        st.error(
                            f"❌ You wrote **{st.session_state.quiz_user_answer}** · "
                            f"Official answer: **{r['answer']}**"
                        )
                        if st.button("I was right — give me the point", key=f"override_{idx}"):
                            st.session_state.quiz_history[-1]["correct"] = True
                            st.rerun()
                    if r.get("explanation"):
                        with st.expander("📘 Explanation"):
                            st.markdown(r["explanation"])

        c_bot = st.columns([1, 1, 1])
        if c_bot[0].button("⟵ Previous", disabled=idx == 0, use_container_width=True):
            st.session_state.fc_idx = max(0, idx - 1)
            st.session_state.quiz_submitted = False
            st.session_state.quiz_user_answer = None
            st.rerun()
        if c_bot[1].button(
            "⏭ Skip (don't count)", use_container_width=True, disabled=idx >= n - 1,
        ):
            st.session_state.fc_idx = min(n - 1, idx + 1)
            st.session_state.quiz_submitted = False
            st.session_state.quiz_user_answer = None
            st.rerun()
        if c_bot[2].button(
            "Next question ⟶",
            use_container_width=True,
            disabled=idx >= n - 1 or not st.session_state.quiz_submitted,
        ):
            st.session_state.fc_idx = min(n - 1, idx + 1)
            st.session_state.quiz_submitted = False
            st.session_state.quiz_user_answer = None
            st.rerun()

        # End-of-deck summary
        if idx == n - 1 and st.session_state.quiz_submitted:
            st.markdown("---")
            st.markdown("## 🏁 Deck complete!")
            s = st.columns(3)
            s[0].metric("Correct", f"{correct}/{attempted}")
            s[1].metric("Accuracy", f"{pct:.0f}%")
            s[2].metric("Best streak", f"🏆 {best}")

        # Review wrong answers
        wrong = [h for h in hist if not h["correct"]]
        if wrong:
            with st.expander(f"📝 Review wrong answers ({len(wrong)})"):
                for i, h in enumerate(wrong, 1):
                    st.markdown(
                        f"**{i}. [{h['section']} · {h['topic']}]** "
                        f"{h['question']}\n\n"
                        f"Your answer: `{h['user_answer']}`  →  "
                        f"Correct: **{h['official_answer']}**"
                    )
                    st.caption(f"from {h.get('test','')} · Module {h.get('module','?')} · Q{h.get('number','?')}")
                    st.divider()
                import csv, io
                buf = io.StringIO()
                w = csv.DictWriter(
                    buf,
                    fieldnames=[
                        "test", "section", "module", "number", "topic", "subtopic",
                        "format", "question", "user_answer", "official_answer",
                    ],
                )
                w.writeheader()
                for h in wrong:
                    w.writerow({k: h.get(k, "") for k in w.fieldnames})
                st.download_button(
                    "Download wrong answers (CSV)",
                    buf.getvalue(),
                    file_name="psat_wrong_answers.csv",
                    mime="text/csv",
                )


# ---------------------------- footer / watermark ----------------------------
st.markdown("---")
st.caption(
    f"Session {st.session_state.sid} · "
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} · "
    f"page {st.session_state.page}/{total_pages} · "
    f"Sources: College Board digital-PSAT practice tests + SAT Suite Question Bank (free study use)"
)


# ---------------------------- deterrents ----------------------------
st.markdown(
    """
    <style>
      @media print { body { display: none !important; } }
    </style>
    <script>
      document.addEventListener('contextmenu', e => e.preventDefault());
      document.addEventListener('keydown', e => {
        const k = (e.key || '').toLowerCase();
        if ((e.metaKey || e.ctrlKey) && (k === 'p' || k === 's' || k === 'u')) {
          e.preventDefault();
        }
      });
    </script>
    """,
    unsafe_allow_html=True,
)
