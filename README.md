# PSAT Study Tool

A Streamlit app that serves questions from the eight official College Board
digital-PSAT paper practice tests, grouped by the official SAT-Suite skill
domains.

**Covered tests:**

| Tier | Practice tests |
|---|---|
| PSAT 10 | #1, #2 |
| PSAT 8/9 | #1, #2, #3 |
| PSAT/NMSQT | #1, #2 |
| PSAT/NMSQT + PSAT 10 | #3 (shared form) |

**Covered skill domains**

- Reading and Writing
  - Information and Ideas (Central Ideas, Command of Evidence, Inferences)
  - Craft and Structure (Words in Context, Text Structure and Purpose, Cross-Text Connections)
  - Expression of Ideas (Rhetorical Synthesis, Transitions)
  - Standard English Conventions (Boundaries, Form Structure and Sense)
- Math
  - Algebra, Advanced Math, Problem-Solving and Data Analysis,
    Geometry and Trigonometry

~917 questions extracted (~96% of the ~960 total questions across the 8 tests).
Every question includes the correct answer and, where the College Board
publishes one, the full answer explanation.

**Plus ~3,200 questions from the College Board SAT Suite Question Bank**
([satsuitequestionbank.collegeboard.org](https://satsuitequestionbank.collegeboard.org)),
the official, publicly released digital-PSAT practice item bank. Those come
with the official difficulty rating (Easy / Medium / Hard) and the official
skill tags. See `fetch_qbank.py` to pull them locally.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Then open [http://localhost:8501](http://localhost:8501).

## Rebuilding the question bank

The repo already ships `all_questions_classified.jsonl`. To rebuild from the
raw PDFs (which are also in this repo):

```bash
.venv/bin/python3 extract.py       # → all_questions.jsonl   + summary.md
.venv/bin/python3 classify.py      # → all_questions_classified.jsonl
```

- `extract.py` uses `pdfplumber` to column-split each test PDF, pairs it
  with the per-module answer tables in the scoring guide, and attaches the
  official explanation text from the answer-explanations PDF.
- `classify.py` tags each question with its official SAT-Suite topic and
  sub-skill using keyword rules.

To additionally fetch from the SAT Suite Question Bank:

```bash
.venv/bin/python3 fetch_qbank.py --programs 100,102   # → qbank_questions.jsonl
```

That's idempotent — it skips items already on disk, so re-runs resume where
they left off. The app auto-loads `qbank_questions.jsonl` and exposes a
**Source** sidebar filter (practice tests vs. question bank) and a
**Difficulty** filter (Easy / Medium / Hard).

## Features

- **Browse**: 10 questions per page with topic / subtopic / test / module /
  format filters and full-text search.
- **Flashcards**: one at a time, flip to reveal.
- **Quiz**: multiple-choice radio or student-produced-response text input,
  auto-scored, with per-topic breakdown and wrong-answer review export.
- Answer-reveal is hidden by default so the full answer bank isn't shipped
  to the client until the user actually requests it.
- Per-session hourly cap (`PSAT_MAX_QS_PER_HOUR`, default 500).

## Deploy

- **Streamlit Community Cloud**: push to GitHub → sign in to Streamlit
  Community Cloud with GitHub → New app → point at this repo → `app.py`.
- **Hugging Face Spaces**: create a Streamlit Space, upload `app.py`,
  `requirements.txt`, and `all_questions_classified.jsonl`.

## Attribution

All questions, scoring guides, and answer explanations are College Board
materials from the official PSAT paper-based practice tests
([psat.org](https://psat.org)) and the SAT Suite Question Bank
([satsuitequestionbank.collegeboard.org](https://satsuitequestionbank.collegeboard.org)),
publicly released by the College Board for free study use.
