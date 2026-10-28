"""Classify each PSAT question into an official SAT-suite skill domain and
sub-skill. Reads all_questions.jsonl, writes all_questions_classified.jsonl.

Reference (College Board skill map, used across digital SAT, PSAT/NMSQT,
PSAT 10, and PSAT 8/9):

Reading and Writing
  - Information and Ideas
      Central Ideas and Details, Command of Evidence (Textual),
      Command of Evidence (Quantitative), Inferences
  - Craft and Structure
      Words in Context, Text Structure and Purpose, Cross-Text Connections
  - Expression of Ideas
      Rhetorical Synthesis, Transitions
  - Standard English Conventions
      Boundaries, Form Structure and Sense

Math
  - Algebra
      Linear equations in one variable, Linear equations in two variables,
      Linear functions, Systems of linear equations, Linear inequalities
  - Advanced Math
      Equivalent expressions, Nonlinear equations in one variable and systems,
      Nonlinear functions
  - Problem-Solving and Data Analysis
      Ratios/rates/proportions/units, Percentages, One-variable data,
      Two-variable data (models and scatterplots), Probability and conditional,
      Inference from sample statistics and margin of error,
      Evaluating statistical claims (observational studies and experiments)
  - Geometry and Trigonometry
      Area and volume, Lines, angles, and triangles,
      Right triangles and trigonometry, Circles
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
IN  = ROOT / "all_questions.jsonl"
OUT = ROOT / "all_questions_classified.jsonl"


# ---------- Reading & Writing classifier ----------

RW_DOMAIN_PATTERNS: list[tuple[str, str, list[re.Pattern]]] = [
    # Craft and Structure — Words in Context (fill-in-the-blank vocab) is
    # the single most common RW question type, so check it first.
    (
        "Craft and Structure", "Words in Context",
        [
            re.compile(r"as used in the text", re.I),
            re.compile(r"as used in the passage", re.I),
            re.compile(r"what does the (?:word|phrase)", re.I),
            re.compile(r"most nearly mean", re.I),
            re.compile(r"most logical and precise word or phrase", re.I),
        ],
    ),

    # Expression of Ideas — Transitions
    (
        "Expression of Ideas", "Transitions",
        [
            re.compile(r"most logical transition", re.I),
            re.compile(r"best transition", re.I),
            re.compile(r"the following sentence(?:s)? most logically", re.I),
        ],
    ),

    # Expression of Ideas — Rhetorical Synthesis
    (
        "Expression of Ideas", "Rhetorical Synthesis",
        [
            re.compile(r"following notes about", re.I),
            re.compile(r"while taking notes", re.I),
            re.compile(r"wants to emphasize", re.I),
            re.compile(r"wants to describe", re.I),
            re.compile(r"wants to introduce", re.I),
            re.compile(r"present the (?:study|experiment|project|finding)", re.I),
            re.compile(r"best accomplishes this goal", re.I),
        ],
    ),

    # Standard English Conventions
    (
        "Standard English Conventions", "Boundaries",
        [
            # Sentence boundary / punctuation questions
            re.compile(r"conventions of Standard English", re.I),
            re.compile(r"conforms to the conventions", re.I),
            re.compile(r"punctuation", re.I),
            re.compile(r"semicolon|colon", re.I),
        ],
    ),
    (
        "Standard English Conventions", "Form, Structure, and Sense",
        [
            re.compile(r"subject[- ]verb agreement", re.I),
            re.compile(r"verb tense", re.I),
            re.compile(r"pronoun", re.I),
            re.compile(r"modifier", re.I),
            re.compile(r"parallel structure", re.I),
        ],
    ),

    # Information and Ideas
    (
        "Information and Ideas", "Command of Evidence (Quantitative)",
        [
            re.compile(r"table (?:above|below|shown)", re.I),
            re.compile(r"graph (?:above|below|shown)", re.I),
            re.compile(r"according to the (?:table|graph|data)", re.I),
            re.compile(r"from the (?:table|graph|chart)", re.I),
            re.compile(r"percent(age)?\b", re.I),  # numeric reference
        ],
    ),
    (
        "Information and Ideas", "Command of Evidence (Textual)",
        [
            re.compile(r"which quotation.*(?:illustrate|support|supports|would best illustrate)", re.I),
            re.compile(r"most strongly supports", re.I),
            re.compile(r"evidence (?:to )?support", re.I),
        ],
    ),
    (
        "Information and Ideas", "Inferences",
        [
            re.compile(r"most logically completes the text", re.I),
            re.compile(r"based on the text.*what", re.I),
            re.compile(r"most strongly implies", re.I),
            re.compile(r"would most likely", re.I),
            re.compile(r"best describes.*inference", re.I),
            re.compile(r"it can .*reasonably be inferred", re.I),
        ],
    ),
    (
        "Information and Ideas", "Central Ideas and Details",
        [
            re.compile(r"main (?:idea|topic|purpose)", re.I),
            re.compile(r"primarily (?:argue|argues|claim|claims|suggest|suggests)", re.I),
            re.compile(r"best state(?:s)? the (?:main|primary) (?:purpose|idea|topic)", re.I),
            re.compile(r"according to the text", re.I),
            re.compile(r"text (?:is )?primarily", re.I),
        ],
    ),

    # Craft and Structure — remaining
    (
        "Craft and Structure", "Text Structure and Purpose",
        [
            re.compile(r"overall structure", re.I),
            re.compile(r"best describes (?:the )?(?:function|structure|purpose) of (?:the (?:underlined )?portion|the text|the passage)", re.I),
            re.compile(r"main purpose of the (?:text|passage)", re.I),
            re.compile(r"function of the underlined", re.I),
            re.compile(r"purpose of the underlined", re.I),
        ],
    ),
    (
        "Craft and Structure", "Cross-Text Connections",
        [
            re.compile(r"\bText 1\b.*\bText 2\b", re.I),
            re.compile(r"based on the texts", re.I),
            re.compile(r"author of Text 2 would most likely", re.I),
            re.compile(r"both texts", re.I),
        ],
    ),
]

RW_FALLBACK = ("Information and Ideas", "Central Ideas and Details")


def classify_rw(question: str) -> tuple[str, str]:
    for topic, subtopic, patterns in RW_DOMAIN_PATTERNS:
        for p in patterns:
            if p.search(question):
                return (topic, subtopic)
    # If choices are MC with phrases like "quantities / patterns / designs" and
    # the stem contains "_______" (fill-in-the-blank), it's usually Words in Context.
    if "_______" in question and "most logical and precise word" in question.lower():
        return ("Craft and Structure", "Words in Context")
    return RW_FALLBACK


# ---------- Math classifier ----------

MATH_DOMAIN_PATTERNS: list[tuple[str, str, list[re.Pattern]]] = [
    # Geometry and Trigonometry
    (
        "Geometry and Trigonometry", "Circles",
        [
            re.compile(r"\bcircle\b", re.I),
            re.compile(r"\bradius\b|\bdiameter\b|\bcircumference\b", re.I),
            re.compile(r"\barc\b.*\b(angle|degree|radian|length)", re.I),
            re.compile(r"\btangent to the circle", re.I),
        ],
    ),
    (
        "Geometry and Trigonometry", "Right triangles and trigonometry",
        [
            re.compile(r"right triangle", re.I),
            re.compile(r"\bsin|\bcos|\btan\b", re.I),
            re.compile(r"\bhypotenuse\b", re.I),
            re.compile(r"\bPythagorean", re.I),
            re.compile(r"30[- ]?60[- ]?90|45[- ]?45[- ]?90|special right triangles?", re.I),
        ],
    ),
    (
        "Geometry and Trigonometry", "Lines, angles, and triangles",
        [
            re.compile(r"\btriangle\b|\bquadrilateral\b|\bparallelogram\b|\brectangle\b|\brhombus\b|\btrapezoid\b", re.I),
            re.compile(r"\bparallel lines\b", re.I),
            re.compile(r"\bperpendicular\b", re.I),
            re.compile(r"\bangle\b.*(sum|measure|degrees?)", re.I),
            re.compile(r"\binterior angle\b|\bexterior angle\b", re.I),
        ],
    ),
    (
        "Geometry and Trigonometry", "Area and volume",
        [
            re.compile(r"\barea\b.*(?:square|triangle|rectangle|cm|in|unit)", re.I),
            re.compile(r"\bvolume\b", re.I),
            re.compile(r"\bsurface area\b", re.I),
            re.compile(r"\b(?:cube|cylinder|sphere|prism|cone|pyramid)\b", re.I),
        ],
    ),

    # Problem-Solving and Data Analysis
    (
        "Problem-Solving and Data Analysis", "Probability and conditional probability",
        [
            re.compile(r"\bprobability\b", re.I),
            re.compile(r"\brandomly (?:selected|chosen|drawn)\b", re.I),
        ],
    ),
    (
        "Problem-Solving and Data Analysis", "Inference from sample statistics",
        [
            re.compile(r"\bmargin of error\b", re.I),
            re.compile(r"\bconfidence interval\b", re.I),
            re.compile(r"\brandom sample\b", re.I),
            re.compile(r"\bpopulation\b.*(?:estimate|sample)", re.I),
        ],
    ),
    (
        "Problem-Solving and Data Analysis", "Evaluating statistical claims",
        [
            re.compile(r"observational study", re.I),
            re.compile(r"\b(experimental|experiment)\b.*\b(cause|caus\w+|generaliz\w+)", re.I),
            re.compile(r"\bwhich (?:statement|conclusion) is (?:best |most )?supported\b", re.I),
        ],
    ),
    (
        "Problem-Solving and Data Analysis", "Two-variable data (scatterplots)",
        [
            re.compile(r"\bscatterplot\b", re.I),
            re.compile(r"line of best fit", re.I),
            re.compile(r"best (?:models|fits) the", re.I),
        ],
    ),
    (
        "Problem-Solving and Data Analysis", "One-variable data (statistics)",
        [
            re.compile(r"\b(?:mean|median|mode|range|standard deviation)\b", re.I),
            re.compile(r"\bbox plot\b|\bhistogram\b|\bdot plot\b|\bfrequency\b", re.I),
            re.compile(r"\bquartile\b", re.I),
        ],
    ),
    (
        "Problem-Solving and Data Analysis", "Percentages",
        [
            re.compile(r"\bpercent\b|%", re.I),
            re.compile(r"\bdiscount\b|\btax\b|\binterest\b", re.I),
        ],
    ),
    (
        "Problem-Solving and Data Analysis", "Ratios, rates, proportions, units",
        [
            re.compile(r"\bratio\b", re.I),
            re.compile(r"\bproportion\b", re.I),
            re.compile(r"\bper (?:hour|minute|second|mile|gram|liter|unit)\b", re.I),
            re.compile(r"\b(miles|km|hours|minutes|seconds|grams|liters|gallons)\s+per\b", re.I),
            re.compile(r"\b(?:scale|rate)\b", re.I),
        ],
    ),

    # Advanced Math
    (
        "Advanced Math", "Nonlinear functions",
        [
            re.compile(r"\bquadratic function\b", re.I),
            re.compile(r"\bexponential\b", re.I),
            re.compile(r"\bparabola\b", re.I),
            re.compile(r"\bvertex\b", re.I),
            re.compile(r"\bf\(x\)\s*=\s*[^=]*\^[23]", re.I),
            re.compile(r"\bf\(x\)\s*=\s*\d*\s*(?:\d+)\^"),
        ],
    ),
    (
        "Advanced Math", "Nonlinear equations and systems",
        [
            re.compile(r"\bquadratic equation\b", re.I),
            re.compile(r"\bsolutions? to the equation.*x\^?2\b", re.I),
            re.compile(r"x\^?2|x squared", re.I),
            re.compile(r"\bfactor(?:ed|s|ing)\b", re.I),
        ],
    ),
    (
        "Advanced Math", "Equivalent expressions",
        [
            re.compile(r"which .* equivalent", re.I),
            re.compile(r"\bexpression\b.* equivalent", re.I),
            re.compile(r"\brewrite\b", re.I),
        ],
    ),

    # Algebra
    (
        "Algebra", "Systems of linear equations",
        [
            re.compile(r"\bsystem of (?:linear )?equations\b", re.I),
            re.compile(r"solution.*both equations", re.I),
        ],
    ),
    (
        "Algebra", "Linear inequalities",
        [
            re.compile(r"\binequalit", re.I),
            re.compile(r"\bat most\b|\bat least\b|\bno more than\b|\bno fewer than\b", re.I),
            re.compile(r"[<>≤≥]|less than or equal|greater than or equal", re.I),
        ],
    ),
    (
        "Algebra", "Linear functions",
        [
            re.compile(r"linear function", re.I),
            re.compile(r"\bslope\b", re.I),
            re.compile(r"y-intercept|x-intercept", re.I),
        ],
    ),
    (
        "Algebra", "Linear equations in two variables",
        [
            re.compile(r"xy[- ]plane", re.I),
            re.compile(r"\bgraph of\b.*\bequation\b", re.I),
            re.compile(r"intersect at the point", re.I),
        ],
    ),
    (
        "Algebra", "Linear equations in one variable",
        [
            re.compile(r"solution to the (?:given )?equation", re.I),
            re.compile(r"value of x", re.I),
            re.compile(r"what is the value of", re.I),
        ],
    ),
]

MATH_FALLBACK = ("Algebra", "Linear equations in one variable")


def classify_math(question: str) -> tuple[str, str]:
    for topic, subtopic, patterns in MATH_DOMAIN_PATTERNS:
        for p in patterns:
            if p.search(question):
                return (topic, subtopic)
    return MATH_FALLBACK


# ---------- main ----------

def main() -> None:
    rows: list[dict] = []
    with IN.open() as f:
        for line in f:
            rows.append(json.loads(line))

    topic_counts: Counter = Counter()
    subtopic_counts: Counter = Counter()
    per_section_topic: dict[str, Counter] = defaultdict(Counter)

    for r in rows:
        # Collapse all whitespace so multi-word regex patterns match across
        # line breaks introduced by the PDF extractor.
        q_flat = re.sub(r"\s+", " ", r["question"])
        if r["section"] == "Reading and Writing":
            topic, subtopic = classify_rw(q_flat)
        else:
            topic, subtopic = classify_math(q_flat)
        r["topic"] = topic
        r["subtopic"] = subtopic
        topic_counts[topic] += 1
        subtopic_counts[(topic, subtopic)] += 1
        per_section_topic[r["section"]][topic] += 1

    with OUT.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Write a short classification summary
    with (ROOT / "summary_classified.md").open("w") as f:
        f.write("# PSAT Classification Summary\n\n")
        f.write(f"Total classified questions: **{len(rows)}**\n\n")
        for section, counts in per_section_topic.items():
            f.write(f"## {section}\n\n| Topic | Count |\n|---|---:|\n")
            for t, n in counts.most_common():
                f.write(f"| {t} | {n} |\n")
            f.write("\n")
        f.write("## Sub-topic breakdown\n\n| Topic | Sub-topic | Count |\n|---|---|---:|\n")
        for (t, s), n in subtopic_counts.most_common():
            f.write(f"| {t} | {s} | {n} |\n")

    print(f"classified {len(rows)} questions → {OUT}")
    for section, counts in per_section_topic.items():
        print(f"  {section}:")
        for t, n in counts.most_common():
            print(f"    {t}: {n}")


if __name__ == "__main__":
    main()
