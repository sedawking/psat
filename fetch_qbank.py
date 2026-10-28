"""Fetch additional practice questions from the College Board SAT Suite
Question Bank — the official, publicly released bank at
https://satsuitequestionbank.collegeboard.org/ — and write them in the same
JSONL schema as all_questions_classified.jsonl so they merge into the app.

Usage:
    .venv/bin/python3 fetch_qbank.py

Writes:
    qbank_questions.jsonl           raw snapshot keyed by external_id
    qbank_questions_classified.jsonl  (= same file; schema matches)

The script is idempotent: it keeps already-fetched questions on disk and only
pulls new ones on subsequent runs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib3
from html import unescape
from pathlib import Path

import requests

ROOT = Path(__file__).parent
OUT = ROOT / "qbank_questions.jsonl"
# A tiny snapshot of lookup codes we need; also fetched fresh at startup.
BASE = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank"

# ----- API identifiers (also discoverable at BASE + "/lookup") -----
PROGRAMS = {
    "99":  "SAT",
    "100": "PSAT/NMSQT & PSAT 10",
    "102": "PSAT 8/9",
}
TESTS = {"1": "Reading and Writing", "2": "Math"}
# Four R&W primary-class codes and four Math primary-class codes.
RW_DOMAINS   = ["INI", "CAS", "EOI", "SEC"]
MATH_DOMAINS = ["H",   "P",   "Q",   "S"]
# Topic names, mirroring classify.py's taxonomy.
DOMAIN_NAMES = {
    "INI": "Information and Ideas",
    "CAS": "Craft and Structure",
    "EOI": "Expression of Ideas",
    "SEC": "Standard English Conventions",
    "H":   "Algebra",
    "P":   "Advanced Math",
    "Q":   "Problem-Solving and Data Analysis",
    "S":   "Geometry and Trigonometry",
}
TIER_NAMES = {
    "99":  "SAT",
    "100": "PSAT/NMSQT",
    "102": "PSAT 8/9",
}

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://satsuitequestionbank.collegeboard.org",
    "Referer": "https://satsuitequestionbank.collegeboard.org/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
}

# Suppress corp-SSL inspection warnings when running from a corporate network.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------- HTML -> plain text ----------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_MULTINEWLINE_RE = re.compile(r"\n{3,}")


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    # Preserve block boundaries as newlines before dropping tags.
    s = re.sub(r"</(?:p|div|li|br|h\d)>", "\n", html, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub("", s)
    s = unescape(s)
    s = _WS_RE.sub(" ", s)
    s = _MULTINEWLINE_RE.sub("\n\n", s)
    return s.strip()


# ---------- API ----------

def _retry_post(session: requests.Session, url: str, payload: dict,
                attempts: int = 5, base_delay: float = 1.5) -> dict:
    """POST with exponential backoff on timeouts / 5xx / connection errors.
    Forces Connection: close + drops the connection pool on each retry so
    stale keep-alive sockets don't get reused."""
    last_exc: Exception | None = None
    for i in range(attempts):
        # Force a brand new TCP connection every request — the upstream
        # cloudfront pool is finicky about reusing idle sockets.
        hdrs = dict(HEADERS)
        hdrs["Connection"] = "close"
        try:
            r = session.post(url, json=payload, headers=hdrs,
                             verify=False, timeout=20)
            if r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} server error", response=r)
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last_exc = e
            # Only retry 5xx / network errors; propagate 4xx client errors.
            if isinstance(e, requests.HTTPError) and e.response is not None \
                    and 400 <= e.response.status_code < 500:
                raise
            # Dump the connection pool so the next request gets a fresh socket.
            try:
                session.close()
            except Exception:
                pass
            sleep_for = base_delay * (1.6 ** i)
            print(f"  …retry {i+1}/{attempts} after {sleep_for:.1f}s ({e})",
                  file=sys.stderr)
            time.sleep(sleep_for)
    assert last_exc is not None
    raise last_exc


def list_questions(session: requests.Session, program_id: str, test_id: str,
                   domains: list[str]) -> list[dict]:
    return _retry_post(
        session, f"{BASE}/digital/get-questions",
        {"asmtEventId": int(program_id), "test": int(test_id),
         "domain": ",".join(domains)},
    )


def fetch_question(session: requests.Session, external_id: str) -> dict:
    return _retry_post(
        session, f"{BASE}/digital/get-question",
        {"external_id": external_id},
    )


# ---------- normalize to app schema ----------

def to_app_record(meta: dict, detail: dict, programs: list[str]) -> dict:
    """Translate a (list-metadata + detail-payload) pair into the schema the
    Streamlit app loads from all_questions_classified.jsonl. `programs` is
    the list of program IDs the question appeared under in list responses."""
    # Prefer the most-specific program the question is used in: PSAT 8/9 if
    # present, otherwise PSAT/NMSQT, otherwise SAT.
    order = ["102", "100", "99"]
    primary_pid = next((p for p in order if p in programs), programs[0] if programs else "99")
    tier = TIER_NAMES.get(primary_pid, "")
    program_names = sorted({TIER_NAMES[p] for p in programs if p in TIER_NAMES})
    primary_class = meta.get("primary_class_cd", "")
    topic = meta.get("primary_class_cd_desc") or DOMAIN_NAMES.get(primary_class, "")
    subtopic = meta.get("skill_desc") or ""
    section = "Reading and Writing" if primary_class in RW_DOMAINS else "Math"

    stimulus = html_to_text(detail.get("stimulus"))
    stem = html_to_text(detail.get("stem"))
    question_text = (stimulus + "\n\n" + stem).strip() if stimulus else stem

    # Build choices
    choices: list[tuple[str, str]] = []
    key_ids = set(detail.get("keys") or [])
    correct_letter: str | None = None
    opts = detail.get("answerOptions") or []
    letters = ["A", "B", "C", "D"]
    for i, opt in enumerate(opts[:4]):
        letter = letters[i]
        txt = html_to_text(opt.get("content"))
        choices.append((letter, txt))
        if opt.get("id") in key_ids:
            correct_letter = letter

    if choices:
        fmt = "Multiple Choice"
        answer = correct_letter or ""
    else:
        fmt = "Student-Produced Response"
        # SPR answers come as array of acceptable strings
        ca = detail.get("correct_answer") or []
        if isinstance(ca, list):
            answer = ", ".join(ca)
        else:
            answer = str(ca)

    rationale = html_to_text(detail.get("rationale"))

    external_id = detail.get("externalid") or meta.get("external_id") or ""
    qid = meta.get("questionId") or external_id[:8]

    return {
        "test_id": "sat-suite-question-bank",
        "test": "SAT Suite Question Bank",
        "tier": tier,
        "programs": program_names,
        "section": section,
        "module": 1,  # question-bank items aren't tied to a module
        "number": 0,  # not applicable; qid_cb is the stable identifier
        "format": fmt,
        "question": question_text,
        "choices": choices,
        "answer": answer,
        "explanation": rationale,
        "source": "qbank",
        "topic": topic,
        "subtopic": subtopic,
        "difficulty": meta.get("difficulty", ""),   # E / M / H
        "qid_cb": qid,
        "external_id": external_id,
    }


# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--programs", default="100,102",
        help="Comma-separated program IDs. 99=SAT, 100=PSAT/NMSQT & PSAT 10, "
             "102=PSAT 8/9. Default: 100,102",
    )
    ap.add_argument("--delay", type=float, default=0.1,
                    help="Delay in seconds between detail fetches (default 0.1).")
    ap.add_argument("--max", type=int, default=0,
                    help="If >0, stop after fetching this many new questions.")
    args = ap.parse_args()

    program_ids = [p.strip() for p in args.programs.split(",") if p.strip()]

    # Load previously-fetched questions, keyed by external_id, so re-runs are
    # idempotent and quick to resume on failure.
    existing: dict[str, dict] = {}
    if OUT.exists():
        with OUT.open() as f:
            for line in f:
                r = json.loads(line)
                existing[r.get("external_id", "")] = r
    print(f"existing on disk: {len(existing)}")

    session = requests.Session()

    # 1. List all question metadata across programs/tests/domains. Track
    # which programs each external_id appears under so we can tag records
    # that are shared across PSAT/NMSQT and PSAT 8/9 banks.
    print("listing questions ...")
    meta_by_id: dict[str, dict] = {}
    programs_by_id: dict[str, set[str]] = {}
    for program_id in program_ids:
        for test_id, domains in [("1", RW_DOMAINS), ("2", MATH_DOMAINS)]:
            try:
                items = list_questions(session, program_id, test_id, domains)
            except requests.HTTPError as e:
                print(f"  !! list {PROGRAMS[program_id]} {TESTS[test_id]}: {e}", file=sys.stderr)
                continue
            for it in items:
                xid = it["external_id"]
                programs_by_id.setdefault(xid, set()).add(program_id)
                it["_test_id"] = test_id
                meta_by_id.setdefault(xid, it)
            print(f"  {PROGRAMS[program_id]} / {TESTS[test_id]}: {len(items)} questions")

    print(f"total unique questions listed: {len(meta_by_id)}")
    todo = [m for xid, m in meta_by_id.items() if xid not in existing]
    print(f"to fetch: {len(todo)} (skipping {len(meta_by_id) - len(todo)} already on disk)")

    # 2. Fetch each new question's detail and write to disk.
    out_f = OUT.open("a")
    try:
        count = 0
        skipped = 0
        consecutive_failures = 0
        for i, meta in enumerate(todo):
            if args.max and count >= args.max:
                break
            xid = meta["external_id"]
            try:
                detail = fetch_question(session, xid)
                consecutive_failures = 0
            except (requests.HTTPError, requests.ConnectionError,
                    requests.Timeout) as e:
                skipped += 1
                consecutive_failures += 1
                print(f"  !! skip {xid} after retries: {e}", file=sys.stderr)
                # Reset the session's connection pool and cool down if we're
                # getting hammered.
                if consecutive_failures >= 3:
                    print(f"  …{consecutive_failures} consecutive failures, "
                          f"cooling down 60s + rebuilding session",
                          file=sys.stderr)
                    time.sleep(60)
                    session.close()
                    session = requests.Session()
                    consecutive_failures = 0
                continue
            record = to_app_record(meta, detail, sorted(programs_by_id.get(xid, set())))
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            count += 1
            if count % 25 == 0:
                print(f"  fetched {count}/{len(todo)} (skipped {skipped})",
                      flush=True)
            # Every 200 successful fetches, pause briefly to be nicer to the
            # upstream API.
            if count and count % 200 == 0:
                time.sleep(5)
            time.sleep(args.delay)
    finally:
        out_f.close()

    # Count final total
    total = 0
    with OUT.open() as f:
        for _ in f:
            total += 1
    print(f"\nwrote {total} total question records to {OUT}")


if __name__ == "__main__":
    main()
