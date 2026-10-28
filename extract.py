"""Extract PSAT questions, correct answers, and explanations from the official
College Board practice-test PDFs in this directory.

Produces:
  all_questions.jsonl   - one JSON record per question
  summary.md            - counts per test / section / module

Each record has:
  test            e.g. "PSAT 10 Practice Test #1"
  test_id         e.g. "psat-10-practice-test-1"
  tier            "PSAT 10" | "PSAT 8/9" | "PSAT/NMSQT"
  section         "Reading and Writing" | "Math"
  module          1 | 2
  number          1..N (per module)
  format          "Multiple Choice" | "Student-Produced Response"
  question        full question text, choices stripped out
  choices         [(letter, text), ...]  (empty for SPR)
  answer          correct answer letter (MC) or canonical string (SPR)
  explanation     human-readable explanation (if available) else ""
  source          the practice-test PDF basename
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

ROOT = Path(__file__).parent

# ----- which PDFs make up each test -----
# key = canonical test id
# value = (title, tier, test_pdf, scoring_pdf, explanations_pdf_or_None)
TESTS: dict[str, tuple[str, str, str, str, str | None]] = {
    "psat-10-practice-test-1": (
        "PSAT 10 Practice Test #1", "PSAT 10",
        "psat-10-practice-test-1.pdf",
        "psat-10-practice-test-1-scoring-guide.pdf",
        "psat-10-practice-test-1-answer-explanations.pdf",
    ),
    "psat-10-practice-test-2": (
        "PSAT 10 Practice Test #2", "PSAT 10",
        "psat-10-practice-test-2.pdf",
        "psat-10-practice-test-2-scoring-guide.pdf",
        "psat-10-practice-test-2-answer-explanations.pdf",
    ),
    "psat-8-9-practice-test-1": (
        "PSAT 8/9 Practice Test #1", "PSAT 8/9",
        "psat-8-9-practice-test-1.pdf",
        "psat-8-9-practice-test-1-scoring-guide.pdf",
        "psat-8-9-practice-test-1-answers.pdf",
    ),
    "psat-8-9-practice-test-2": (
        "PSAT 8/9 Practice Test #2", "PSAT 8/9",
        "psat-8-9-practice-test-2.pdf",
        "psat-8-9-practice-test-2-scoring-guide.pdf",
        "psat-8-9-practice-test-2-answers.pdf",
    ),
    "psat-8-9-practice-test-3": (
        "PSAT 8/9 Practice Test #3", "PSAT 8/9",
        "psat-8-9-practice-test-3.pdf",
        "psat-8-9-practice-test-3-scoring-guide.pdf",
        "psat-8-9-practice-test-3-answers.pdf",
    ),
    "psat-nmsqt-practice-test-1": (
        "PSAT/NMSQT Practice Test #1", "PSAT/NMSQT",
        "psat-nmsqt-practice-test-1.pdf",
        "scoring-psat-nmsqt-practice-test-1.pdf",
        "psat-nmsqt-practice-test-1-answers.pdf",
    ),
    "psat-nmsqt-practice-test-2": (
        "PSAT/NMSQT Practice Test #2", "PSAT/NMSQT",
        "psat-nmsqt-practice-test-2.pdf",
        "scoring-psat-nmsqt-practice-test-2.pdf",
        "psat-nmsqt-practice-test-2-answer-explanations.pdf",
    ),
    "psat-nmsqt-psat-10-practice-test-3": (
        "PSAT/NMSQT & PSAT 10 Practice Test #3", "PSAT/NMSQT",
        "psat-nmsqt-psat-10-practice-test-3.pdf",
        "psat-nmsqt-psat-10-practice-test-3-scoring-guide.pdf",
        "psat-nmsqt-psat-10-practice-test-3-answers.pdf",
    ),
}


# ---------------- helpers ----------------

def read_pdf_text(path: Path) -> str:
    """Fallback single-column read (used only for the small scoring-guide and
    explanation booklets, which don't suffer from two-column reading order)."""
    r = PdfReader(str(path))
    parts: list[str] = []
    for p in r.pages:
        try:
            parts.append(p.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts)


_MODULE_START_RE = re.compile(
    r"Module\s*\n?\s*(1|2)\s*\n+\s*(Reading and Writing|Math)\s*\n+\s*\d+\s*QUESTIONS?",
    re.IGNORECASE,
)
# Sidebar artifact that pdfplumber picks up on every question page:
#   "Module\n1\n" (or 2) followed by content. We strip these BEFORE
# question-number parsing so the bare "1"/"2" doesn't look like a question.
_SIDEBAR_RE = re.compile(r"(?m)^Module\n[12]\n")


BARE_NUM_RE = re.compile(r"^\s*(\d{1,2})\s*$")


def _strip_before_first_bare_num(col_text: str) -> str:
    """On a module's first page we want to discard the module header +
    directions block that sits above the first question. Keep from the first
    line that is just a 1-2 digit number onward.

    Note: dot-leader page dividers can share a line with the question number
    (e.g. ".......... 3"). We mirror strip_noise's dot-leader removal here so
    those lines are recognized as bare numbers.
    """
    lines = col_text.splitlines()
    for i, ln in enumerate(lines):
        cleaned = DOT_LEADER_RE.sub(" ", DASH_LEADER_RE.sub(" ", ln)).strip()
        if BARE_NUM_RE.match(cleaned):
            return "\n".join(lines[i:])
    return col_text


def read_pdf_pages(path: Path) -> list[dict]:
    """Walk the PDF page-by-page and return per-page records:
        full : full-page text (used for module-header detection only)
        col  : left-column + right-column text, concatenated
        start: (section, module) tuple if this page begins a new module, else None
    """
    out: list[dict] = []
    with pdfplumber.open(str(path)) as pdf:
        for p in pdf.pages:
            w = p.width
            full = p.extract_text() or ""
            m = _MODULE_START_RE.search(full)
            start = None
            if m:
                section = (
                    "Reading and Writing"
                    if m.group(2).lower().startswith("reading")
                    else "Math"
                )
                start = (section, int(m.group(1)))

            left = (
                p.within_bbox((0, 0, w * 0.52, p.height)).extract_text() or ""
            )
            right = (
                p.within_bbox((w * 0.48, 0, w, p.height)).extract_text() or ""
            )
            # Strip the "Module 1" / "Module 2" sidebar marker so bare question
            # numbers aren't shadowed by the sidebar.
            left = _SIDEBAR_RE.sub("", left)
            right = _SIDEBAR_RE.sub("", right)
            # On a module's first page, each column also has directions
            # boilerplate ABOVE its first question. Discard that.
            if start is not None:
                left = _strip_before_first_bare_num(left)
                right = _strip_before_first_bare_num(right)
            col = left + "\n" + right
            out.append({"full": full, "col": col, "start": start})
    return out


# Lines that are page noise and that confuse the question-number regex.
# These patterns are stripped entirely (whole line discarded).
NOISE_LINES = [
    re.compile(r"^\s*Unauthorized copying.*illegal\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*CO\s*N\s*T\s*I\s*N\s*U\s*E\s*$"),
    re.compile(r"^\s*CONTINUE\s*$"),
    re.compile(r"^\s*\~{3,}.*$"),
]
# PSAT PDFs insert a long dot-leader to separate the two columns. The dots
# sometimes share a line with a question number ("...... 6"). Substitute just
# the dot run so the line's remaining content survives.
DOT_LEADER_RE = re.compile(r"\.{10,}")
# Standalone long run of dashes
DASH_LEADER_RE = re.compile(r"-{10,}")


def strip_noise(text: str) -> str:
    out_lines = []
    for ln in text.splitlines():
        # strip dot-leader runs within a line
        ln = DOT_LEADER_RE.sub(" ", ln)
        ln = DASH_LEADER_RE.sub(" ", ln)
        if any(p.search(ln) for p in NOISE_LINES):
            continue
        if not ln.strip():
            continue
        out_lines.append(ln.rstrip())
    return "\n".join(out_lines)


# ---------------- scoring-guide parsing ----------------

# An answer token is a letter A..D, a signed int, a signed decimal, or a
# signed fraction. Student-produced response answers may provide multiple
# equivalent forms separated by ";" or "," (e.g. ".25; 1/4").
_ANS_TOKEN = r"(?:[A-D]|[-]?(?:\d+(?:\.\d+)?|\.\d+)(?:/\d+)?)"
ANSWER_LINE_RE = re.compile(
    r"^\s*(\d{1,2})\s+(" + _ANS_TOKEN + r"(?:\s*[;,]\s*" + _ANS_TOKEN + r")*)\s*$"
)


def _looks_like_conversion_row(answers: list[str]) -> bool:
    """A conversion-table run has only integer answers all >= 100 (160..760
    scaled section-score values)."""
    for a in answers:
        if not re.fullmatch(r"-?\d+", a):
            return False
        try:
            if int(a) < 100:
                return False
        except ValueError:
            return False
    return True


def parse_scoring_guide(path: Path) -> dict[tuple[str, int], dict[int, str]]:
    """Return {(section, module): {q_num: correct_answer}} parsed from the
    scoring guide's four answer-key tables (R&W M1, R&W M2, Math M1, Math M2).

    The scoring guides print the four tables in a fixed order on the "Answer
    Key" page. We find all (num, answer) pairs, group them into runs where
    numbers are strictly increasing (so gaps caused by regex misses are OK),
    discard runs that look like raw-score→section-score conversion tables,
    and keep the first four surviving runs.
    """
    text = read_pdf_text(path)
    pairs: list[tuple[int, str]] = []
    for ln in text.splitlines():
        m = ANSWER_LINE_RE.match(ln)
        if m:
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            pairs.append((n, m.group(2).strip()))

    # Build runs: a new run starts at n == 1; within a run, each subsequent n
    # must be strictly greater than the last (gaps allowed). Non-increasing
    # numbers are ignored without ending the current run.
    runs: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for n, a in pairs:
        if n == 1:
            if current:
                runs.append(current)
            current = [(n, a)]
        elif current and n > current[-1][0]:
            current.append((n, a))
        else:
            # Non-increasing, non-start noise — just skip.
            continue
    if current:
        runs.append(current)

    # Filter out conversion-table runs.
    real_tables = [r for r in runs if not _looks_like_conversion_row([a for _, a in r])]
    tables = real_tables[:4]
    if len(tables) < 4:
        raise ValueError(
            f"scoring guide {path.name}: only parsed {len(tables)} tables, "
            f"needed 4. runs={[(len(r), r[0], r[-1]) for r in runs]}"
        )

    sections = [
        ("Reading and Writing", 1),
        ("Reading and Writing", 2),
        ("Math", 1),
        ("Math", 2),
    ]
    out: dict[tuple[str, int], dict[int, str]] = {}
    for key, tbl in zip(sections, tables):
        out[key] = {n: a for n, a in tbl}
    return out


# ---------------- explanation parsing ----------------

QUESTION_HDR_RE = re.compile(r"^\s*QUESTION\s+(\d{1,2})\s*$", re.MULTILINE)
EXPL_MODULE_RE = re.compile(
    r"(READING AND WRITING|MATH)\s*:\s*MODULE\s*(\d)", re.IGNORECASE
)


def parse_explanations(path: Path | None) -> dict[tuple[str, int, int], str]:
    """Return {(section, module, number): explanation_text}."""
    if path is None or not path.exists():
        return {}
    text = read_pdf_text(path)
    text = strip_noise(text)

    # Walk the text, keep track of current (section, module) via the running
    # header "READING AND WRITING: MODULE 1" style tags that appear on each
    # page of the explanations booklet.
    out: dict[tuple[str, int, int], str] = {}

    # Find all QUESTION N markers, and for each, snapshot the most recent
    # (section, module) header that preceded it.
    headers = [(m.start(), m.group(1).upper(), int(m.group(2))) for m in EXPL_MODULE_RE.finditer(text)]
    questions = [(m.start(), int(m.group(1))) for m in QUESTION_HDR_RE.finditer(text)]
    if not questions:
        return {}

    # Build sorted event stream
    def _section_name(raw: str) -> str:
        return "Reading and Writing" if raw.startswith("R") else "Math"

    for i, (start, num) in enumerate(questions):
        # most recent header before this question
        current = ("Reading and Writing", 1)
        for h_start, h_raw, h_mod in headers:
            if h_start <= start:
                current = (_section_name(h_raw), h_mod)
            else:
                break
        # explanation text until next QUESTION marker (or next module header)
        end = questions[i + 1][0] if i + 1 < len(questions) else len(text)
        # Also cut at next module header if it falls before next question
        for h_start, _, _ in headers:
            if start < h_start < end:
                end = h_start
                break
        body = text[start:end]
        # drop the "QUESTION N" header line itself
        body = re.sub(r"^\s*QUESTION\s+\d{1,2}\s*\n?", "", body, count=1)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        key = (current[0], current[1], num)
        # keep first occurrence
        out.setdefault(key, body)

    # Many "answers only" PDFs don't have "QUESTION N" markers - we'll just
    # return the empty dict in that case and leave explanations blank.
    return out


# ---------------- practice-test parsing ----------------

# Markers like "Module\n1\n...Reading and Writing" or "Module\n2\n...Math"
MODULE_HDR_RE = re.compile(
    r"Module\s*\n?\s*(1|2)\s*\n+\s*(Reading and Writing|Math)",
    re.IGNORECASE,
)


def assemble_module_blocks(pages: list[dict]) -> dict[tuple[str, int], str]:
    """Walk per-page records and concatenate the column-text for each module.
    Returns {(section, module): block_text}.
    """
    blocks: dict[tuple[str, int], list[str]] = defaultdict(list)
    current: tuple[str, int] | None = None
    for pg in pages:
        if pg["start"] is not None:
            current = pg["start"]
        if current is None:
            continue
        blocks[current].append(pg["col"])
    return {k: strip_noise("\n".join(v)) for k, v in blocks.items()}


CHOICE_LINE_RE = re.compile(r"^\s*([A-D])\)\s*(.*)$")


def split_questions(block: str, expected_nums: list[int]) -> dict[int, str]:
    """Given a module's text block and a sorted list of expected question
    numbers (e.g. [1..33]), return {num: question_body_text}.

    Walk line-by-line and treat a line that is exactly the next expected
    question number as the start of a new question. Everything until the
    following expected number becomes that question's body.
    """
    lines = block.splitlines()
    # Find starting index of question #1 (strip the module header noise first)
    # Walk lines once, advancing a pointer over expected_nums.
    starts: list[int] = []
    pos = 0  # pointer into expected_nums
    for i, ln in enumerate(lines):
        m = BARE_NUM_RE.match(ln)
        if not m:
            continue
        n = int(m.group(1))
        if pos < len(expected_nums) and n == expected_nums[pos]:
            starts.append(i)
            pos += 1
            if pos == len(expected_nums):
                break

    if len(starts) != len(expected_nums):
        # best-effort fallback: look for ANY bare number in sequence, even if
        # the first expected number is missed (rare but happens if "1" is
        # glued to the module header).
        pass

    out: dict[int, str] = {}
    for idx, start_line in enumerate(starts):
        end_line = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        body_lines = lines[start_line + 1 : end_line]
        # drop trailing/leading blank lines
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        out[expected_nums[idx]] = "\n".join(body_lines)
    return out


def split_choices(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Pull A)/B)/C)/D) choices out of a question body. Returns (stem, choices)."""
    lines = body.splitlines()
    # locate first A) marker
    first_choice_idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^\s*A\)\s*", ln):
            # make sure B,C,D also appear after this to avoid false positives
            tail = "\n".join(lines[i : i + 40])
            if re.search(r"^\s*B\)", tail, re.MULTILINE) and re.search(r"^\s*C\)", tail, re.MULTILINE) and re.search(r"^\s*D\)", tail, re.MULTILINE):
                first_choice_idx = i
                break
    if first_choice_idx is None:
        # Student-produced response or we failed to split - return the whole body
        return body.strip(), []

    stem = "\n".join(lines[:first_choice_idx]).strip()
    choice_lines = lines[first_choice_idx:]

    # Trim trailing garbage: once we've captured the four choices, choice
    # D's text sometimes picks up directions-prose that leaked from a
    # neighbouring column. Stop at the first clearly off-topic line (blank,
    # directions boilerplate, module header leakage).
    _STOP_PATTERNS = (
        re.compile(r"^\s*Module\s*$"),
        re.compile(r"QUESTIONS?\s*$"),
        re.compile(r"best answer", re.IGNORECASE),
        re.compile(r"important .* skills", re.IGNORECASE),
        re.compile(r"Use of a calculator", re.IGNORECASE),
        re.compile(r"section address", re.IGNORECASE),
    )
    trimmed: list[str] = []
    saw_D = False
    blank_run = 0
    for ln in choice_lines:
        if re.match(r"^\s*D\)\s*", ln):
            saw_D = True
        if saw_D:
            if not ln.strip():
                blank_run += 1
                if blank_run >= 1:
                    # one blank line after D) is a reliable "end of choices" signal
                    break
                trimmed.append(ln)
                continue
            else:
                blank_run = 0
            if any(p.search(ln) for p in _STOP_PATTERNS):
                break
        trimmed.append(ln)
    choice_block = "\n".join(trimmed)

    # Split on the four choice markers.
    choices: list[tuple[str, str]] = []
    parts = re.split(r"^\s*([A-D])\)\s*", choice_block, flags=re.MULTILINE)
    # parts = [preamble, 'A', 'a_text', 'B', 'b_text', ...]
    for i in range(1, len(parts) - 1, 2):
        letter = parts[i]
        txt = parts[i + 1].strip()
        # Join multi-line choice text with a single space.
        txt = re.sub(r"\s+", " ", txt).strip()
        # Trim trailing page-footer noise that leaked through column cuts:
        # a trailing page number, the word CONTINUE, and partial "Module"
        # fragments like "Mo" / "dule" / "Mod" / "Modul".
        _TRAIL_TOKEN = (
            r"(?:\d{1,2}|CONTINUE|Module|Modul|Modu|Mod|dule|ule|Mo)"
        )
        txt = re.sub(rf"(?:\s+{_TRAIL_TOKEN})+\s*$", "", txt).strip()
        choices.append((letter, txt))

    # keep only A..D in order
    wanted = {"A", "B", "C", "D"}
    choices = [(l, t) for (l, t) in choices if l in wanted]
    # If multiple occurrences of a letter (noise), prefer the shortest text that
    # looks reasonable - keep first occurrence of each letter.
    seen = set()
    uniq = []
    for l, t in choices:
        if l in seen:
            continue
        seen.add(l)
        uniq.append((l, t))
    uniq.sort(key=lambda x: "ABCD".index(x[0]))
    return stem, uniq


def clean_question_text(stem: str) -> str:
    """Collapse weird whitespace from pypdf extraction."""
    # Collapse runs of 3+ newlines
    stem = re.sub(r"\n{3,}", "\n\n", stem)
    # Trim trailing noise
    stem = stem.strip()
    return stem


# ---------------- main ----------------

def extract_one_test(test_id: str, meta: tuple[str, str, str, str, str | None]) -> list[dict]:
    title, tier, test_pdf, scoring_pdf, expl_pdf = meta
    test_path = ROOT / test_pdf
    scoring_path = ROOT / scoring_pdf
    expl_path = ROOT / expl_pdf if expl_pdf else None
    if not test_path.exists():
        print(f"  !! missing {test_pdf}, skipping", file=sys.stderr)
        return []
    if not scoring_path.exists():
        print(f"  !! missing {scoring_pdf}, skipping", file=sys.stderr)
        return []

    print(f"  reading {test_pdf} ...")
    pages = read_pdf_pages(test_path)
    print(f"  reading {scoring_pdf} ...")
    answers = parse_scoring_guide(scoring_path)
    explanations = parse_explanations(expl_path) if expl_path else {}

    blocks = assemble_module_blocks(pages)
    if len(blocks) < 4:
        print(
            f"  !! {test_id}: only located {len(blocks)}/4 module headers "
            f"({list(blocks.keys())})",
            file=sys.stderr,
        )
        if not blocks:
            return []

    out: list[dict] = []
    for key in [
        ("Reading and Writing", 1),
        ("Reading and Writing", 2),
        ("Math", 1),
        ("Math", 2),
    ]:
        if key not in blocks:
            continue
        section, module = key
        block = blocks[key]
        ans_map = answers.get(key, {})
        expected = sorted(ans_map.keys())
        if not expected:
            print(f"  !! {test_id} {section} M{module}: no answers in scoring guide", file=sys.stderr)
            continue
        bodies = split_questions(block, expected)
        parsed = 0
        for num in expected:
            body = bodies.get(num, "")
            if not body:
                continue
            stem, choices = split_choices(body)
            stem = clean_question_text(stem)
            correct = ans_map[num]
            # Determine format: if we got 4 clean choices it's MC. Otherwise,
            # fall back to the official answer: a lone A/B/C/D letter means
            # Multiple Choice (choices just weren't extractable), anything
            # else is a Student-Produced Response.
            if len(choices) == 4:
                fmt = "Multiple Choice"
            elif isinstance(correct, str) and correct.strip().upper() in {"A", "B", "C", "D"}:
                fmt = "Multiple Choice"
            else:
                fmt = "Student-Produced Response"
            expl = explanations.get((section, module, num), "")
            out.append({
                "test_id": test_id,
                "test": title,
                "tier": tier,
                "section": section,
                "module": module,
                "number": num,
                "format": fmt,
                "question": stem,
                "choices": choices,
                "answer": correct,
                "explanation": expl,
                "source": test_pdf,
            })
            parsed += 1
        print(f"    {section} M{module}: parsed {parsed}/{len(expected)} questions")
    return out


def main():
    all_rows: list[dict] = []
    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "mc": 0, "spr": 0, "with_expl": 0, "with_choices": 0})

    for test_id, meta in TESTS.items():
        print(f"\n== {test_id} ==")
        rows = extract_one_test(test_id, meta)
        for r in rows:
            s = stats[test_id]
            s["total"] += 1
            if r["format"] == "Multiple Choice":
                s["mc"] += 1
                if len(r["choices"]) == 4:
                    s["with_choices"] += 1
            else:
                s["spr"] += 1
            if r["explanation"]:
                s["with_expl"] += 1
        all_rows.extend(rows)

    # Write JSONL
    out_path = ROOT / "all_questions.jsonl"
    with out_path.open("w") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Summary
    with (ROOT / "summary.md").open("w") as f:
        f.write("# PSAT Extraction Summary\n\n")
        f.write(f"Total questions extracted: **{len(all_rows)}**\n\n")
        f.write("| Test | Total | MC | SPR | w/choices | w/explanation |\n|---|---:|---:|---:|---:|---:|\n")
        for test_id, s in stats.items():
            f.write(
                f"| {test_id} | {s['total']} | {s['mc']} | {s['spr']} | {s['with_choices']} | {s['with_expl']} |\n"
            )

    print(f"\nwrote {len(all_rows)} questions → {out_path}")


if __name__ == "__main__":
    main()
