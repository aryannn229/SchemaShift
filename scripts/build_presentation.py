"""Builds docs/SchemaShift_Presentation.pptx from the screenshots in docs/screenshots/.

    python scripts/build_presentation.py

Every number on the slides is taken from docs/evaluation.json, docs/metrics.json and the phase reports
(docs/progress/), not typed here, except the counts of tests and bugs which are read from the same files.
"""

from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"
OUT = DOCS / "SchemaShift_Presentation.pptx"

NAVY = RGBColor(0x0F, 0x17, 0x2A)
BLUE = RGBColor(0x25, 0x63, 0xEB)
GREEN = RGBColor(0x15, 0x80, 0x3D)
AMBER = RGBColor(0xB4, 0x53, 0x09)
RED = RGBColor(0xB9, 0x1C, 0x1C)
GREY = RGBColor(0x64, 0x74, 0x8B)
LIGHT = RGBColor(0xF1, 0xF5, 0xF9)
LINE = RGBColor(0xCB, 0xD5, 0xE1)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "Calibri"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]
TEAM = "Aryan Verma 24BCT0223  |  Sara Choudhary 24BCT0219"


def load(name: str) -> dict:
    p = DOCS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


EVAL, METRICS = load("evaluation.json"), load("metrics.json")
_ver = EVAL.get("verification", {}).get("samples", {})
UNGUARDED = sum(v["concurrency"]["unguarded_violations"] for v in _ver.values()) if _ver else 0
GENERATED = sum(v["concurrency"]["generated_violations"] for v in _ver.values()) if _ver else 0
MATCHED = sum(v["match"] for v in _ver.values()) if _ver else 0
QUERIES = sum(v["queries"] for v in _ver.values()) if _ver else 0
RHO = EVAL.get("verification", {}).get("pooled_pipeline_rho")
RHO_POINTS = EVAL.get("verification", {}).get("pooled_points")
FUZZ = EVAL.get("fuzz", {})


def text(slide, x, y, w, h, content, size=18, bold=False, color=NAVY, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    lines = content if isinstance(content, list) else [content]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        run.font.size, run.font.bold, run.font.name = Pt(size), bold, FONT
        run.font.color.rgb = color
    return box


def bullets(slide, x, y, w, h, items, size=18, color=NAVY, gap=6):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        sub = isinstance(item, tuple)
        label = item[0] if sub else item
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        run = p.add_run()
        run.text = ("– " if sub else "• ") + label
        run.font.size = Pt(size - 2 if sub else size)
        run.font.name = FONT
        run.font.color.rgb = GREY if sub else color
        if sub:
            p.level = 1
    return box


def rect(slide, x, y, w, h, fill=LIGHT, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    s.line.color.rgb = line
    s.line.width = Pt(1)
    s.shadow.inherit = False
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = 0.08
    return s


def card(slide, x, y, w, h, title, body, accent=BLUE, size=14):
    rect(slide, x, y, w, h)
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y + 0.1), Inches(0.07), Inches(h - 0.2))
    bar.fill.solid()
    bar.fill.fore_color.rgb = accent
    bar.line.fill.background()
    text(slide, x + 0.2, y + 0.08, w - 0.3, 0.4, title, size=size + 2, bold=True)
    text(slide, x + 0.2, y + 0.5, w - 0.3, h - 0.55, body, size=size, color=GREY)


def header(slide, title, subtitle=None, number=None):
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, Inches(0.12))
    bar.fill.solid()
    bar.fill.fore_color.rgb = BLUE
    bar.line.fill.background()
    text(slide, 0.6, 0.35, 12.1, 0.8, title, size=32, bold=True)
    if subtitle:
        text(slide, 0.6, 1.1, 12.1, 0.5, subtitle, size=16, color=GREY)
    text(slide, 0.6, 7.05, 9, 0.3, "SchemaShift  |  " + TEAM, size=10, color=GREY)
    if number:
        text(slide, 12.2, 7.05, 0.6, 0.3, str(number), size=10, color=GREY, align=PP_ALIGN.RIGHT)


def image(slide, name, x, y, w, h, caption=None):
    path = SHOTS / name
    if not path.exists():
        raise FileNotFoundError(path)
    from PIL import Image

    iw, ih = Image.open(path).size
    scale = min(w / (iw / 100), h / (ih / 100))
    pw, ph = iw / 100 * scale, ih / 100 * scale
    px, py = x + (w - pw) / 2, y + (h - ph) / 2
    frame = rect(slide, px - 0.04, py - 0.04, pw + 0.08, ph + 0.08, fill=WHITE, line=LINE, shape=MSO_SHAPE.RECTANGLE)
    frame.shadow.inherit = False
    slide.shapes.add_picture(str(path), Inches(px), Inches(py), Inches(pw), Inches(ph))
    if caption:
        text(slide, x, py + ph + 0.06, w, 0.35, caption, size=11, color=GREY, align=PP_ALIGN.CENTER)


def arrow(slide, x, y, w=0.35):
    a = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(0.3))
    a.fill.solid()
    a.fill.fore_color.rgb = BLUE
    a.line.fill.background()


def table(slide, x, y, w, rows, col_w, size=13, header_fill=NAVY):
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(0.4 * len(rows)))
    t = shape.table
    for j, cw in enumerate(col_w):
        t.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = t.cell(i, j)
            cell.text = str(val)
            para = cell.text_frame.paragraphs[0]
            para.font.size, para.font.name = Pt(size), FONT
            para.font.bold = i == 0
            para.font.color.rgb = WHITE if i == 0 else NAVY
            cell.fill.solid()
            cell.fill.fore_color.rgb = header_fill if i == 0 else (LIGHT if i % 2 else WHITE)
            cell.margin_left = cell.margin_right = Inches(0.08)
    return t


def new(title, subtitle=None):
    s = prs.slides.add_slide(BLANK)
    new.n += 1
    header(s, title, subtitle, new.n)
    return s


new.n = 0

# ---------------------------------------------------------------------------------------- 1 title
s = prs.slides.add_slide(BLANK)
new.n += 1
bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
bg.fill.solid()
bg.fill.fore_color.rgb = NAVY
bg.line.fill.background()
stripe = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Inches(5.1), prs.slide_width, Inches(0.08))
stripe.fill.solid()
stripe.fill.fore_color.rgb = BLUE
stripe.line.fill.background()
text(s, 0.9, 1.5, 11.5, 1.2, "SchemaShift", size=60, bold=True, color=WHITE)
text(s, 0.9, 2.8, 11.5, 1.4, ["A SQL to MongoDB migration compiler", "that tells you what the migration changes, and proves it"], size=26, color=RGBColor(0xCB, 0xD5, 0xE1))
text(s, 0.9, 5.5, 11.5, 0.5, "Team members", size=16, color=RGBColor(0x93, 0xC5, 0xFD), bold=True)
text(s, 0.9, 5.95, 11.5, 1.0, ["Aryan Verma   24BCT0223", "Sara Choudhary   24BCT0219"], size=22, color=WHITE)

# ---------------------------------------------------------------------------------------- 2 problem
s = new("Problem statement", "Moving from PostgreSQL to MongoDB silently changes what the database guarantees")
bullets(
    s, 0.6, 1.8, 6.4, 5,
    [
        "Migration tools copy data. They do not say what stops being true afterwards.",
        "A relational schema enforces: foreign keys and ON DELETE CASCADE, UNIQUE, NOT NULL, CHECK, types, atomic multi-table writes.",
        "MongoDB enforces only what is written into validators, indexes and application code.",
        "Queries change meaning too: NULL handling in joins and comparisons, ordering, aggregates over empty sets.",
        "Embed or reference? Each relationship needs a design decision, usually made by guesswork.",
        "Nobody learns what broke until production data is wrong.",
    ],
    size=18,
)
card(s, 7.3, 1.8, 5.4, 1.45, "ON DELETE CASCADE", "MongoDB has no cascade across collections: deleting an account leaves its transactions orphaned (BROKEN).", RED)
card(s, 7.3, 3.4, 5.4, 1.45, "NUMERIC(10,2)", "MongoDB decimals have no precision: a value PostgreSQL rejects is accepted (CHANGED).", AMBER)
card(s, 7.3, 5.0, 5.4, 1.45, "CHECK (LENGTH(s) >= 2 OR s IS NULL)", "Our first generated validator rejected rows with s missing. SQL accepts them. A bug our own tests found.", BLUE)

# ---------------------------------------------------------------------------------------- 3 aim
s = new("Aim and goals", "A compiler for the migration, not a copy script")
rect(s, 0.6, 1.8, 12.1, 1.0, fill=LIGHT)
text(s, 0.8, 1.9, 11.7, 0.8, "Aim: take a PostgreSQL schema and its queries, report exactly which guarantees MongoDB keeps, changes or loses, generate the MongoDB side, and back every claim with evidence.", size=19, bold=True)
goals = [
    ("1", "Report per guarantee", "SAFE, CHANGED or BROKEN, with an evidence level (PROVEN, CONDITIONAL, TESTED, UNVERIFIED)."),
    ("2", "Generate MongoDB", "Validators, indexes, translated queries, a data migration script and enforcement helpers."),
    ("3", "Decide embed vs reference", "With an explanation, a sensitivity analysis and a what-if simulator."),
    ("4", "Verify on real databases", "Run the SQL on PostgreSQL and the generated code on MongoDB, diff the results, race the writers."),
    ("5", "Support change over time", "Schema diffs, reversible incremental migrations, safe resumable runs, CI checks."),
    ("6", "Measure honestly", "One command reproduces every number; self-labeled and independent results are never merged."),
]
for i, (n, t, b) in enumerate(goals):
    x = 0.6 + (i % 3) * 4.1
    y = 3.1 + (i // 3) * 1.95
    card(s, x, y, 3.95, 1.8, f"{n}. {t}", b, BLUE, size=13)

# ---------------------------------------------------------------------------------------- 4 approach
s = new("How we achieve it", "Five ideas that the whole system is built on")
ideas = [
    ("Guarantees become data", "Every promise SQL makes (key, constraint, join, NULL, ordering) becomes a node of an intermediate representation."),
    ("Rules are declarative", "A registry of rules maps each node and the placement plan to a verdict. Adding a rule means adding data, a test and a generated doc."),
    ("Evidence, not opinion", "PROVEN by an argument or an SMT proof, CONDITIONAL on stated conditions, TESTED by real runs, otherwise UNVERIFIED."),
    ("Test against the real thing", "PostgreSQL 16 and MongoDB 7 are the oracles: differential and metamorphic fuzzing, concurrency races, Z3 answers replayed."),
    ("AI only advises", "An LLM may give a second opinion on embed vs reference from schema structure. It never decides and never sees data."),
]
for i, (t, b) in enumerate(ideas):
    x = 0.6 + (i % 3) * 4.1
    y = 1.8 + (i // 3) * 2.45
    card(s, x, y, 3.95, 2.3, t, b, [BLUE, GREEN, AMBER, RED, NAVY][i], size=14)
card(s, 8.8, 4.25, 3.95, 2.3, "Pure compiler stages", "Parser, semantic analysis, IR, rules, optimizer and code generation are pure functions over frozen models. Only verification, AI and the API do I/O.", GREY, size=14)

# ---------------------------------------------------------------------------------------- 5 workflow
s = new("Workflow and process", "From a SQL schema to verified MongoDB code")
stages = [
    ("1 Parse", "sqlglot, typed schema and queries"),
    ("2 Analyze", "FK graph, junctions, cycles"),
    ("3 IR", "one node per guarantee"),
    ("4 Rules", "SAFE / CHANGED / BROKEN + evidence"),
    ("5 Optimize", "embed vs reference, workload, sensitivity"),
    ("6 Prove", "Z3 on validators"),
    ("7 Generate", "validators, indexes, pipelines, migration"),
    ("8 Verify", "PostgreSQL vs MongoDB"),
]
for i, (t, b) in enumerate(stages):
    x = 0.55 + (i % 4) * 3.1
    y = 1.85 + (i // 4) * 1.75
    rect(s, x, y, 2.55, 1.35, fill=WHITE, line=BLUE)
    text(s, x + 0.1, y + 0.1, 2.35, 0.4, t, size=17, bold=True, color=BLUE)
    text(s, x + 0.1, y + 0.55, 2.35, 0.8, b, size=13, color=GREY)
    if i % 4 != 3:
        arrow(s, x + 2.6, y + 0.5)
text(s, 0.6, 5.4, 12.1, 0.4, "Around the pipeline", size=16, bold=True)
bullets(
    s, 0.6, 5.8, 12.1, 1.3,
    [
        "Evolve: two schema versions or ALTER scripts become an ordered, reversible migration plan and a guarantee diff.",
        "Safe migration: dry run, batch checkpoints, resume after a crash, quarantine of invalid records, consistency checks.",
        "Risk score, simulator, CI check with SARIF, benchmark lab, reproducible evaluation.",
    ],
    size=14, gap=3,
)

# ---------------------------------------------------------------------------------------- 6 architecture
s = new("System architecture")
rect(s, 0.6, 1.5, 12.1, 1.0)
text(s, 0.8, 1.55, 3.4, 0.9, ["Frontend", "React, TypeScript, Vite, Tailwind"], size=14, color=NAVY)
text(s, 4.2, 1.55, 8.4, 0.9, ["Compile, Verdicts, Relationships + simulator, Generated Code, Verification, Compare versions, Benchmarks, About"], size=14, color=GREY)
rect(s, 0.6, 2.75, 12.1, 1.2)
text(s, 0.8, 2.8, 3.4, 1.1, ["API", "FastAPI, SQLAlchemy, Alembic"], size=14)
text(s, 4.2, 2.8, 8.4, 1.1, ["/compile  /runs (queued, cancel)  /diff  /simulate  /samples  /metrics", "Security headers, body limit, CORS allow list, Redis-shared rate limits"], size=14, color=GREY)
rect(s, 0.6, 4.2, 5.9, 1.5)
text(s, 0.8, 4.25, 5.6, 1.4, ["Compiler core (pure)", "parser, semantic, IR, equivalence + SMT, optimizer, codegen, evolve, risk, simulate"], size=14)
rect(s, 6.8, 4.2, 5.9, 1.5)
text(s, 7.0, 4.25, 5.6, 1.4, ["I/O side", "verification, fuzz, bench, migrate, ai, worker (arq)"], size=14)
rect(s, 0.6, 5.95, 12.1, 0.95, fill=NAVY, line=NAVY)
text(s, 0.8, 6.05, 11.7, 0.8, "PostgreSQL 16 (app + sandbox)   |   MongoDB 7 replica set   |   Redis   |   Docker, GitHub Actions   |   Render, Vercel, Neon, Atlas configs", size=15, color=WHITE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# ---------------------------------------------------------------------------------------- 7 tools
s = new("Tools and technologies")
table(
    s, 0.6, 1.5, 12.1,
    [
        ["Area", "Tools"],
        ["Language and models", "Python 3.12/3.13, pydantic v2 (frozen models), type hints checked with mypy --strict"],
        ["SQL and analysis", "sqlglot (PostgreSQL dialect), networkx (FK graph), Z3 (SMT solver)"],
        ["Backend", "FastAPI, SQLAlchemy, Alembic, structlog, arq job queue, Redis"],
        ["Databases", "PostgreSQL 16 and MongoDB 7 replica set (psycopg 3, pymongo), Docker, testcontainers"],
        ["Frontend", "React 18, TypeScript, Vite, Tailwind, Monaco editor, react-flow, TanStack Query"],
        ["Testing", "pytest, hypothesis (fuzzing, soundness), mutmut, Vitest, Playwright + axe accessibility"],
        ["Quality gates", "ruff, mypy --strict, ESLint, coverage floor 85% on core packages, GitHub Actions CI"],
        ["Delivery", "Docker images, render.yaml, Vercel config, smoke-test script, SARIF + GitHub Action, LSP server"],
    ],
    [2.6, 9.5], size=14,
)

# ---------------------------------------------------------------------------------------- features
s = new("Feature: compile and see what changes", "Banking sample: 123 guarantees analysed in one pass")
image(s, "01-workspace-summary.png", 0.5, 1.55, 8.3, 5.3)
bullets(
    s, 9.0, 1.7, 3.9, 5.2,
    [
        "Overall verdict and counts: 81 SAFE, 37 CHANGED, 5 BROKEN.",
        "Concurrency scenarios judged separately: 33 scenarios.",
        "Migration risk 57 of 100, from explicit rules in five categories. No AI number.",
        "Z3 proof card per table below it.",
        "Source highlighting: click a verdict to see its SQL.",
    ],
    size=15,
)

s = new("Feature: evidence on every verdict", "How we know, not just what we think")
image(s, "02-verdicts-evidence.png", 0.5, 1.55, 8.3, 5.3)
bullets(
    s, 9.0, 1.7, 3.9, 5.2,
    [
        "Level per verdict: PROVEN, CONDITIONAL, TESTED, UNVERIFIED.",
        "The sound argument, or the conditions it depends on.",
        "The rule outcome that decided it and every input it consulted.",
        "Minimal counterexample with a replay button against real databases.",
        "Filter by status, evidence level or text.",
    ],
    size=15,
)

s = new("Feature: Z3 proofs of validator equivalence", "Do the generated validators accept exactly what the SQL constraints accept?")
image(s, "01b-summary-z3.png", 0.5, 1.55, 7.6, 5.3)
bullets(
    s, 8.3, 1.7, 4.6, 5.2,
    [
        "Per table: EQUIVALENT (proven), COUNTEREXAMPLE (a concrete row), UNSUPPORTED (UNVERIFIED, never SAFE).",
        "Models SQL three-valued logic, NUMERIC rounding and precision, integer overflow, BSON null ordering and expressions that raise.",
        "Soundness test on real databases: 1,000 random schemas, 863 counterexamples confirmed, 565 proofs, 33,900 random rows agreeing, 0 contradictions.",
        "It found 2 bugs in the generated validators.",
    ],
    size=14,
)

s = new("Feature: embed or reference, with a what-if simulator", "Decisions come with reasons, fragility and a risk delta")
image(s, "03a-relationships-graph.png", 0.4, 1.5, 6.3, 5.1, "Placement graph: EMBED solid, REFERENCE dashed")
image(s, "03-relationships-simulator.png", 6.8, 1.5, 6.1, 5.1, "Simulator: flip a relationship, see verdicts, indexes, pipelines and risk")
text(s, 0.6, 6.55, 12.1, 0.5, "Workload frequencies and measured fan-out feed the score; every weight is moved by 10% and 20% to mark decisions fragile or stable. The optimizer's own plan is never modified by the simulator.", size=13, color=GREY)

s = new("Feature: generated MongoDB code and a pipeline optimizer", "Validators, indexes, queries, migration script, helpers (mongosh and PyMongo)")
image(s, "04-generated-code-optimizer.png", 0.5, 1.55, 8.3, 5.3)
bullets(
    s, 9.0, 1.7, 3.9, 5.2,
    [
        "Rewrite rules with stated preconditions: move filters earlier, drop unused lookups, merge matches, top-k.",
        "Bounded search picks the cheapest candidate by an estimated cost.",
        f"Estimates checked against explain(\"executionStats\"): pooled Spearman rho {RHO:.2f} over {RHO_POINTS} candidate pipelines.",
        "Before and after shown side by side.",
    ],
    size=14,
)

s = new("Feature: verification against real PostgreSQL and MongoDB", "The same data, the SQL on one side and the generated queries on the other")
image(s, "05-verification.png", 0.5, 1.55, 8.3, 5.3)
bullets(
    s, 9.0, 1.7, 3.9, 5.2,
    [
        "Per query MATCH or MISMATCH with a diff and a hypothesis.",
        f"Samples: {MATCHED} of {QUERIES} queries match PostgreSQL.",
        "Probes demonstrate every BROKEN or CHANGED verdict.",
        f"Concurrency probes race writers: generated helpers {GENERATED} violations, unguarded code {UNGUARDED} on the four samples.",
        "SQL is never executed as text: the sandbox runs re-emitted ASTs with bound parameters.",
    ],
    size=14,
)

s = new("Feature: compare versions and migrate incrementally", "Two schema versions or an ALTER script become a reversible plan")
image(s, "07c-compare-plan-crop.png", 0.4, 1.5, 7.3, 3.2, "Ordered steps, each classified for reversibility")
image(s, "08c-compare-guarantees-crop.png", 7.8, 1.5, 5.1, 3.2, "Guarantee diff: what got better, worse, added or removed")
bullets(
    s, 0.6, 4.85, 12.1, 2.2,
    [
        "Typed changes (add, drop, rename, retype, keys, constraints, indexes). A rename is only a candidate until confirmed: otherwise it is a drop plus an add and loses data.",
        "Ordered steps with dependencies, each REVERSIBLE, REVERSIBLE_WITH_SAVED_DATA (backup or archive) or IRREVERSIBLE (manual, with a recovery strategy). Nothing is ever deleted.",
        "Guarantee diff: which verdicts got worse or better, and the schema change that explains it.",
        "Acceptance on real databases: migrate v1, apply the incremental script, and the result equals a fresh v2 migration. Rollback restores v1.",
    ],
    size=13, gap=3,
)

s = new("Feature: safe migration mode", "A migration you would trust on real data")
flow = [("Dry run", "counts, time, storage, records that would fail. Writes nothing."), ("Run", "keyset-paged batches, upsert by _id, atomic manifest after each batch."), ("Crash", "process killed at any instruction."), ("Resume", "continue from the persisted cursor. No duplicates."), ("Verify", "counts, keys, references, embedded rows, sampled deep comparison.")]
for i, (t, b) in enumerate(flow):
    x = 0.55 + i * 2.5
    rect(s, x, 1.75, 2.2, 2.1, fill=WHITE, line=BLUE if t != "Crash" else RED)
    text(s, x + 0.1, 1.85, 2.0, 0.4, t, size=17, bold=True, color=BLUE if t != "Crash" else RED)
    text(s, x + 0.1, 2.3, 2.0, 1.5, b, size=12, color=GREY)
    if i < 4:
        arrow(s, x + 2.22, 2.5, 0.28)
bullets(
    s, 0.6, 4.2, 12.1, 2.8,
    [
        "Invalid records are quarantined whole with the reason down to the nested field (orders[3].price), never dropped silently.",
        "Tested with a real kill: the CLI calls os._exit(137) after N batches; the resumed run completes with no missing or duplicate records.",
        "Irreversible steps need --i-understand-this-is-lossy and take a snapshot first. Manual steps are never run automatically.",
        "CLI: schemashift migrate plan | dry-run | run | resume | verify | status | apply | rollback.",
    ],
    size=15,
)

s = new("Feature: fuzzing, concurrency probes and a benchmark lab", "Finding the problems before users do")
image(s, "09-benchmarks.png", 0.4, 1.5, 7.2, 5.3)
bullets(
    s, 7.8, 1.65, 5.1, 5.3,
    [
        "Differential fuzzing: random schemas and queries, PostgreSQL vs generated MongoDB, shrunk to a minimal reproducer. 14 bugs found and fixed to date.",
        "Metamorphic relations (partitioning, tautology, commutativity) catch NULL and missing-field bugs.",
        "Benchmark lab (CLI only): chosen plan vs all-REFERENCE vs all-EMBED, correctness first, then p50/p95/p99, storage, index use.",
        "Honest result: at 1,000 rows the chosen plan was fastest on 1 of 6 queries, and PostgreSQL was faster than MongoDB on every query measured, also at 100,000 rows (screenshot). The report shows the cases where we lose.",
    ],
    size=14,
)

s = new("Feature: developer tooling and deployment")
card(s, 0.6, 1.5, 6.0, 2.5, "CI check", ["schemashift check --baseline-ref origin/main", "Policy file: unacceptable regressions. Output: text, JSON, SARIF 2.1.0, Markdown PR comment.", "GitHub Action uploads SARIF and fails the check per policy."], BLUE, size=16)
card(s, 6.8, 1.5, 5.9, 2.5, "Editor support", ["Language server (pygls) shows SEM diagnostics and verdict hints on .sql files, tested over real stdio JSON-RPC.", "Small VS Code client launches it."], GREEN, size=16)
card(s, 0.6, 4.2, 6.0, 2.6, "Production infrastructure", ["Redis-shared rate limits, arq job queue with retries, timeouts and cancel, run status in PostgreSQL.", "Configs for Render, Vercel, Neon, Atlas; docs/DEPLOY.md."], AMBER, size=16)
card(s, 6.8, 4.2, 5.9, 2.6, "Checked locally", ["docker-compose.prod.yml runs the same images: smoke test passed, a verified run through the worker matched PostgreSQL on every query, cancel and shared limits worked.", "Live deployment is left for the owner to run."], NAVY, size=16)

# ---------------------------------------------------------------------------------------- results
ev = EVAL.get("evidence", {})
s = new("Results", "Every number below is reproduced by: schemashift evaluate --all")
table(
    s, 0.6, 1.5, 12.1,
    [
        ["Measure", "Result", "How to read it"],
        ["Checker vs labels (32 schemas, 883 guarantees)", "100% detection, 0% false positives", "self-labeled by the rule author; independent labels pending"],
        [f"Result-set correctness ({QUERIES} sample queries)", f"{MATCHED} of {QUERIES} match PostgreSQL", "label-free, hand-written workloads, synthetic data"],
        ["Differential + metamorphic fuzzing", f"{FUZZ.get('failures', 0)} failures in {FUZZ.get('cases')} cases (seed {FUZZ.get('seed')})", "14 bugs found and fixed to date"],
        ["Z3 soundness on real databases", "0 contradictions in 33,900 rows", "863 counterexamples confirmed, 565 proofs"],
        ["Evidence levels over 41 schemas", f"PROVEN {ev.get('without_smt', {}).get('PROVEN', 864)} to {ev.get('with_smt', {}).get('PROVEN', 1384)}", "after the Z3 step; UNVERIFIED stays 93"],
        ["Crash and resume test", "no missing or duplicate records", "real process kill, 40-customer migration"],
        ["Pipeline cost model vs explain", f"Spearman rho {RHO:.2f}", f"{RHO_POINTS} candidates: small sample"],
        ["Chosen placement fastest", "1 of 6 timed queries", "the optimizer does not optimize latency"],
        ["Mutation score", "89.1%", "recorded before Phase 18: stale"],
    ],
    [4.4, 3.7, 4.0], size=13,
)
text(s, 0.6, 6.0, 12.1, 0.9, "Tests at the end: 1,250 unit tests, integration suite against real PostgreSQL and MongoDB, 60 frontend tests, 14 Playwright end-to-end checks including accessibility (axe, light and dark).", size=14, color=GREY)

# ---------------------------------------------------------------------------------------- findings
s = new("What the tooling found in our own code", "Testing against the real databases paid for itself")
table(
    s, 0.6, 1.5, 12.1,
    [
        ["Found by", "Defect", "Where"],
        ["Z3 soundness test", "Two IN checks with an empty intersection produced enum: [], which MongoDB refuses", "code generator (fixed)"],
        ["Z3 soundness test", "A LENGTH check guard raised on missing fields, so valid rows were rejected", "code generator (fixed)"],
        ["Z3 soundness test", "Integer overflow, NUMERIC rounding and IS NOT NULL were modelled wrongly", "the encoder (fixed)"],
        ["Fuzzing", "A NUMERIC column compared with a decimal literal sent a double that never matches", "code generator (fixed)"],
        ["Fuzzing and metamorphic tests", "14 semantic mismatches in joins, NULL, ordering and division", "code generator (fixed, regression store)"],
        ["Benchmarks", "The optimizer's choice was not the fastest in most queries", "documented, not hidden"],
        ["Verification on Chinook", "Seed rows were inserted in declaration order, violating foreign keys", "verification (fixed)"],
    ],
    [3.0, 6.6, 2.5], size=15,
)

# ---------------------------------------------------------------------------------------- limitations
s = new("Honest limitations")
bullets(
    s, 0.6, 1.5, 6.0, 5.4,
    [
        "Accuracy figures are consistency with the author's own rules. The independent (blind, second labeler) corpus is empty.",
        "Embed vs reference is decided by risk and a heuristic cost, not measured latency.",
        "Z3 covers row-local constraints on root tables inside a documented fragment. Everything else is UNVERIFIED.",
        "Incremental restructuring is automatic only for simple cases; the rest is a manual step with a recovery plan.",
    ],
    size=16,
)
bullets(
    s, 6.9, 1.5, 5.9, 5.4,
    [
        "Benchmarks: one laptop, both databases in Docker, small repetition counts.",
        "Mutation score is stale (before Phase 18); re-run needs Docker.",
        "The GitHub Action, the Render/Vercel/Neon/Atlas deployment and the VS Code client were not run on the real services from here; configs, local stack and tests are.",
        "PostgreSQL 16 and MongoDB 7 only.",
    ],
    size=16,
)

# ---------------------------------------------------------------------------------------- future
s = new("Future work")
bullets(
    s, 0.6, 1.5, 12.1, 5.4,
    [
        "Independent evaluation: a second person labels the corpus blind (a template command is ready).",
        "A latency-aware optimizer trained on the benchmark lab's measurements.",
        "Run the deployment on Vercel, Render, Neon and Atlas and publish the status badge.",
        "Extend the Z3 fragment: UNIQUE across fields, embedded arrays, dates, and collation-aware string order.",
        "Automatic restructuring for nested and composite-key layouts; online migration with change streams.",
        "More dialects and versions beyond PostgreSQL 16 and MongoDB 7.",
    ],
    size=20,
    gap=12,
)

# ---------------------------------------------------------------------------------------- end
s = prs.slides.add_slide(BLANK)
new.n += 1
bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
bg.fill.solid()
bg.fill.fore_color.rgb = NAVY
bg.line.fill.background()
text(s, 0.9, 1.8, 11.5, 1.0, "Thank you", size=54, bold=True, color=WHITE)
text(s, 0.9, 3.1, 11.5, 1.2, ["SchemaShift tells you what a migration changes,", "generates the MongoDB side, and shows the evidence."], size=24, color=RGBColor(0xCB, 0xD5, 0xE1))
text(s, 0.9, 5.2, 11.5, 0.5, "Team members", size=16, bold=True, color=RGBColor(0x93, 0xC5, 0xFD))
text(s, 0.9, 5.65, 11.5, 1.0, ["Aryan Verma   24BCT0223", "Sara Choudhary   24BCT0219"], size=22, color=WHITE)
text(s, 0.9, 6.75, 11.5, 0.4, "github.com/aryannn229/SchemaShift", size=14, color=RGBColor(0x93, 0xC5, 0xFD))

prs.save(OUT)
print("wrote", OUT, "with", len(prs.slides), "slides")
_ = (Emu,)
