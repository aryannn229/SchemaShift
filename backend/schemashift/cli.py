"""Command line entry point."""

from pathlib import Path

import typer

from schemashift import __version__

app = typer.Typer(help="SchemaShift CLI")


@app.command()
def version() -> None:
    """Print the SchemaShift version."""
    typer.echo(__version__)


@app.command()
def compile(  # noqa: A001 (CLI verb)
    schema: Path = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file"),
    queries: Path = typer.Option(None, "--queries", "-q", help="Optional SQL queries file"),
    seed: Path = typer.Option(None, "--seed", help="Optional INSERT seed file"),
    options: Path = typer.Option(None, "--options", "-o", help="JSON file with CompileOptions"),
    workload: Path = typer.Option(
        None, "--workload", help="workload.yaml with per-query frequencies"
    ),
    out: Path = typer.Option(None, "--out", help="Directory to write the generated code to"),
    fail_on: str = typer.Option("none", help="Exit 1 when the verdict reaches none|changed|broken"),
) -> None:
    """Compile a PostgreSQL schema: verdicts, placement plan and generated MongoDB code."""
    from schemashift.codegen import generate_code
    from schemashift.pipeline import CompileOptions, compile_sql

    opts = (
        CompileOptions.model_validate_json(options.read_text(encoding="utf-8"))
        if options
        else CompileOptions()
    )
    if workload is not None:
        opts = opts.model_copy(update={"workload": _load_workload(workload)})
    result = compile_sql(
        schema.read_text(encoding="utf-8"),
        queries.read_text(encoding="utf-8") if queries else "",
        seed.read_text(encoding="utf-8") if seed else "",
        opts,
    )
    for d in result.diagnostics:
        where = f"{d.location.line_start}:{d.location.col_start}" if d.location else "-"
        typer.echo(f"{d.severity.upper():7} {d.code:28} {where:>8}  {d.message}")
    counts = result.equivalence.final.counts
    typer.echo(
        f"verdict: {result.overall_verdict}  (SAFE {counts['SAFE']}, CHANGED {counts['CHANGED']}, "
        f"BROKEN {counts['BROKEN']})"
    )
    for rid, dec in sorted(result.plan.decisions.items()):
        mark = f"  [{dec.fragility.level}]" if dec.fragility else ""
        typer.echo(f"  {dec.decision:9} {dec.score:.2f}  {rid}{mark}")
    if out is not None:
        generated = generate_code(result, opts)
        for f in generated.files:
            target = out / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content, encoding="utf-8", newline="\n")
        typer.echo(f"wrote {len(generated.files)} file(s) to {out}")
    order = {"none": 99, "changed": 1, "broken": 2}
    rank = {"SAFE": 0, "CHANGED": 1, "BROKEN": 2}[result.overall_verdict]
    raise typer.Exit(1 if result.has_errors or rank >= order.get(fail_on, 99) else 0)


def _load_workload(path: Path):  # type: ignore[no-untyped-def]
    import yaml

    from schemashift.optimizer.workload import WorkloadSpec

    return WorkloadSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


@app.command()
def sensitivity(
    schema: Path = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file"),
    queries: Path = typer.Option(None, "--queries", "-q", help="Optional SQL queries file"),
    seed: Path = typer.Option(None, "--seed", help="Optional INSERT seed file"),
    workload: Path = typer.Option(
        None, "--workload", help="workload.yaml with per-query frequencies"
    ),
) -> None:
    """Which embed/reference decisions flip when a weight moves by 10% or 20%."""
    from schemashift.pipeline import CompileOptions, compile_sql

    opts = CompileOptions()
    if workload is not None:
        opts = opts.model_copy(update={"workload": _load_workload(workload)})
    result = compile_sql(
        schema.read_text(encoding="utf-8"),
        queries.read_text(encoding="utf-8") if queries else "",
        seed.read_text(encoding="utf-8") if seed else "",
        opts,
    )
    for rid, dec in sorted(result.plan.decisions.items()):
        fr = dec.fragility
        typer.echo(f"{(fr.level if fr else 'n/a'):12} {dec.decision:9} {rid}")
        for f in fr.flips if fr else ():
            typer.echo(f"    {f.weight} {f.change:+.0%} -> {f.becomes}")


@app.command()
def verify(
    schema: Path = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file"),
    queries: Path = typer.Option(None, "--queries", "-q", help="Optional SQL queries file"),
    seed_file: Path = typer.Option(None, "--seed-file", help="Optional INSERT seed file"),
    options: Path = typer.Option(None, "--options", "-o", help="JSON file with CompileOptions"),
    seed: int = typer.Option(0, help="Seed of the synthetic data generator"),
    rows: int = typer.Option(50, help="Rows per table (max 500)"),
    probes: bool = typer.Option(True, help="Run guarantee probes for CHANGED/BROKEN verdicts"),
    admin_dsn: str = typer.Option(
        None,
        envvar="SANDBOX_ADMIN_DATABASE_URL",
        help="PostgreSQL role allowed to CREATE ROLE/SCHEMA",
    ),
    mongo_url: str = typer.Option(None, envvar="MONGO_URL", help="MongoDB URI (replica set)"),
) -> None:
    """Run the SQL on PostgreSQL and the generated code on MongoDB with identical seed data."""
    from schemashift.pipeline import CompileOptions, compile_sql
    from schemashift.verification import SandboxConfig
    from schemashift.verification import verify as run_verify

    if not admin_dsn or not mongo_url:
        raise typer.BadParameter(
            "set SANDBOX_ADMIN_DATABASE_URL and MONGO_URL (or pass the options)"
        )
    opts = (
        CompileOptions.model_validate_json(options.read_text(encoding="utf-8"))
        if options
        else CompileOptions()
    )
    opts = opts.model_copy(update={"rows_per_table": rows})
    seed_sql = seed_file.read_text(encoding="utf-8") if seed_file else ""
    result = compile_sql(
        schema.read_text(encoding="utf-8"),
        queries.read_text(encoding="utf-8") if queries else "",
        seed_sql,
        opts,
    )
    report = run_verify(
        result,
        opts,
        SandboxConfig(pg_admin_dsn=admin_dsn, mongo_url=mongo_url),
        seed_sql=seed_sql,
        seed=seed,
        run_probes=probes,
    )
    for o in report.queries:
        detail = o.hypothesis or o.error or o.pg_error or ""
        typer.echo(f"{o.query_id:>4} {o.status:<14} {o.kind:<7} {detail}")
    for p in report.probes:
        flag = "demonstrated" if p.demonstrates else "no difference"
        typer.echo(f"probe {p.verdict_id} ({p.status}): {flag} - {p.description}")
    typer.echo(
        f"result-set correctness: {report.correctness:.1%} "
        f"({len(report.verified)} verified, {len(report.unexplained)} unexplained mismatch(es))"
    )
    raise typer.Exit(1 if report.correctness < 0.95 or report.unexplained else 0)


@app.command()
def evaluate(
    corpus: Path = typer.Option(None, help="Corpus directory (default: tests/corpus)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List every mismatch"),
    write_docs: bool = typer.Option(
        False, "--write-docs", help="Update docs/METRICS.md and docs/metrics.json"
    ),
    verify_samples: bool = typer.Option(
        False, "--verify", help="Also measure result-set correctness (needs PostgreSQL + MongoDB)"
    ),
    with_ai: bool = typer.Option(
        False, "--ai", help="Also measure AI agreement (needs ANTHROPIC_API_KEY)"
    ),
) -> None:
    """Evaluate the equivalence checker against the labeled corpus."""
    from schemashift.metrics.evaluate import (
        DEFAULT_CORPUS,
        evaluate_equivalence,
        format_report,
        load_corpus,
    )

    metrics, _ = evaluate_equivalence(load_corpus(corpus or DEFAULT_CORPUS))
    typer.echo(format_report(metrics))
    system = None
    sandbox = None
    if verify_samples or with_ai:
        import os

        from schemashift.ai import AnthropicAdvisor
        from schemashift.metrics.evaluate import evaluate_system
        from schemashift.verification import SandboxConfig

        if verify_samples:
            sandbox = SandboxConfig(
                pg_admin_dsn=os.environ["SANDBOX_ADMIN_DATABASE_URL"],
                mongo_url=os.environ["MONGO_URL"],
            )
        advisor = None
        if with_ai:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                typer.echo("--ai needs ANTHROPIC_API_KEY", err=True)
                raise typer.Exit(2)
            advisor = AnthropicAdvisor(api_key=key)
        system = evaluate_system(sandbox=sandbox, advisor=advisor)
        rs, ai = system["result_set"], system["ai"]
        if rs["computed"]:
            typer.echo(f"result-set correctness: {rs['rate']:.1%} ({rs['match']}/{rs['verified']})")
        if ai["computed"]:
            typer.echo(f"AI agreement          : {ai['agreement']} over {ai['judged']} placements")
    if write_docs:
        from schemashift.metrics import report

        data = report.compute(sandbox=sandbox if (verify_samples or with_ai) else None)
        for key in ("result_set", "ai"):
            fresh = (system or {}).get(key)
            if fresh and fresh.get("computed"):
                data[key] = fresh
        if system and system.get("samples"):
            data["samples"] = system["samples"]
        for path in report.write_docs(data):
            typer.echo(f"wrote {path}")
    if verbose:
        for m in metrics.mismatches:
            typer.echo(f"MISMATCH {m.case} {m.node_id}: label={m.label} predicted={m.predicted}")
    raise typer.Exit(
        0 if metrics.detection_rate >= 0.95 and metrics.false_positive_rate <= 0.05 else 1
    )


@app.command()
def diff(
    old: Path = typer.Argument(..., exists=True, readable=True, help="Old DDL (version 1)"),
    new: Path = typer.Argument(None, exists=True, readable=True, help="New DDL (version 2)"),
    alter: Path = typer.Option(None, "--alter", exists=True, help="ALTER script applied to OLD"),
    queries: Path = typer.Option(None, "--queries", "-q", help="Optional SQL queries file"),
    confirm: list[str] = typer.Option(
        [], "--confirm-rename", help="old=new, e.g. users.fullname=name"
    ),
    out: Path = typer.Option(None, "--out", help="Directory for evolve.py and plan.json"),
    fail_on_regression: bool = typer.Option(False, help="Exit 1 when a guarantee gets worse"),
) -> None:
    """Compare two schema versions and plan the incremental MongoDB migration."""
    from schemashift.evolve import plan_evolution, render_script

    if new is None and alter is None:
        typer.echo("give a NEW ddl file or --alter", err=True)
        raise typer.Exit(2)
    renames = dict(item.split("=", 1) for item in confirm if "=" in item)
    plan = plan_evolution(
        old.read_text(encoding="utf-8"),
        new.read_text(encoding="utf-8") if new else "",
        alter_sql=alter.read_text(encoding="utf-8") if alter else "",
        queries_sql=queries.read_text(encoding="utf-8") if queries else "",
        confirmed_renames=renames,
    )
    for w in plan.warnings:
        typer.echo(f"WARNING {w}")
    typer.echo(f"{len(plan.diff.ops)} schema change(s):")
    for op in plan.diff.ops:
        typer.echo(f"  {op.kind:18} {op.description}")
    for cand in plan.diff.rename_candidates:
        if not cand.confirmed:
            typer.echo(
                f"  ? rename {cand.kind} {cand.key} -> {cand.new} (score {cand.score}); "
                f"confirm with --confirm-rename {cand.key}={cand.new}"
            )
    for pc in plan.plan_changes:
        typer.echo(
            f"placement {pc.relationship_id}: {pc.old} -> {pc.new}"
            + (" (restructures)" if pc.restructures else "")
        )
    typer.echo(f"{len(plan.steps)} migration step(s):")
    for st in plan.steps:
        typer.echo(f"  {st.id:4} {st.reversibility:27} {st.kind:19} {st.description}")
    g = plan.guarantees
    typer.echo(
        f"guarantees: {g.old_overall} -> {g.new_overall}; "
        f"{g.regressions} worse, {g.improvements} better"
    )
    for c in g.changes:
        typer.echo(f"  {c.direction:8} {c.rule_id:14} {c.node_id}: {c.old} -> {c.new}")
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "evolve.py").write_text(render_script(plan), encoding="utf-8", newline="\n")
        (out / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"wrote {out / 'evolve.py'} and {out / 'plan.json'}")
    raise typer.Exit(1 if fail_on_regression and g.regressions else 0)


@app.command()
def bench(
    schema: Path = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file"),
    queries: Path = typer.Option(..., "--queries", "-q", exists=True, help="SQL queries file"),
    scale: str = typer.Option("1k", "--scale", help="Rows per table: 1k, 100k, 1M, ..."),
    reps: int = typer.Option(20, help="Timed repetitions per query"),
    warmups: int = typer.Option(3, help="Untimed warm-up runs per query"),
    seed: int = typer.Option(0, help="Seed for the generated data"),
    out: Path = typer.Option(
        Path("bench-out"), "--out", help="Directory for results.json and report.html"
    ),
    admin_dsn: str = typer.Option(None, envvar="SANDBOX_ADMIN_DATABASE_URL"),
    mongo_url: str = typer.Option(None, envvar="MONGO_URL"),
) -> None:
    """Benchmark PostgreSQL vs MongoDB under three placement plans (CLI only)."""
    from schemashift.bench import parse_scale, run_bench, write_report
    from schemashift.verification import SandboxConfig

    if not admin_dsn or not mongo_url:
        typer.echo("bench needs SANDBOX_ADMIN_DATABASE_URL and MONGO_URL", err=True)
        raise typer.Exit(2)
    config = SandboxConfig(
        pg_admin_dsn=admin_dsn,
        mongo_url=mongo_url,
        statement_timeout_ms=600_000,
        idle_in_transaction_timeout_ms=600_000,
    )
    report = run_bench(
        schema.read_text(encoding="utf-8"),
        queries.read_text(encoding="utf-8"),
        config,
        scale=parse_scale(scale),
        seed=seed,
        warmups=warmups,
        reps=reps,
        log=typer.echo,
    )
    js, page = write_report(report, out)
    for line in report.findings:
        typer.echo(line)
    typer.echo(f"wrote {js} and {page}")


@app.command()
def fuzz(
    cases: int = typer.Option(100, "--cases", "-n", help="Number of generated cases"),
    seed: int = typer.Option(0, "--seed", "-s", help="Hypothesis seed (reproducible runs)"),
    metamorphic: bool = typer.Option(
        False, "--metamorphic", help="Also check metamorphic relations"
    ),
    max_tables: int = typer.Option(3, help="Maximum tables per generated schema"),
    save_dir: Path = typer.Option(None, help="Where to write minimal reproducers (YAML)"),
    admin_dsn: str = typer.Option(
        None,
        envvar="SANDBOX_ADMIN_DATABASE_URL",
        help="PostgreSQL role allowed to CREATE ROLE/SCHEMA",
    ),
    mongo_url: str = typer.Option(None, envvar="MONGO_URL", help="MongoDB URI (replica set)"),
) -> None:
    """Differential (and metamorphic) fuzzing: PostgreSQL vs the generated MongoDB code."""
    from schemashift.fuzz.driver import run_fuzz
    from schemashift.fuzz.regression import DEFAULT_DIR, entry_for, save
    from schemashift.verification import SandboxConfig

    if not admin_dsn or not mongo_url:
        raise typer.BadParameter(
            "set SANDBOX_ADMIN_DATABASE_URL and MONGO_URL (or pass the options)"
        )
    directory = save_dir or DEFAULT_DIR

    def on_failure(case, findings) -> None:  # type: ignore[no-untyped-def]
        path = save(entry_for(case, findings, seed), directory)
        typer.echo(f"saved minimal reproducer: {path}")
        for f in findings:
            typer.echo(f"  [{f.kind}] {f.summary}")
            for q in f.sqls:
                typer.echo(f"    {q}")

    result = run_fuzz(
        SandboxConfig(pg_admin_dsn=admin_dsn, mongo_url=mongo_url),
        cases_n=cases,
        seed=seed,
        metamorphic=metamorphic,
        max_tables=max_tables,
        on_failure=on_failure,
    )
    st = result.stats
    typer.echo(
        f"fuzz seed={seed} cases={result.cases} queries={st.queries} compared={st.compared} "
        f"not_translated={st.not_translated} pg_errors={st.pg_errors} "
        f"relation_checks={st.relation_checks} relation_skipped={st.relation_skipped} "
        f"optimizer_checked={st.optimizer_checked} "
        f"duration={result.duration_s}s "
        f"failures={len(result.failures)}"
    )
    for reason, n in sorted(st.untranslated.items(), key=lambda kv: -kv[1])[:8]:
        typer.echo(f"  untranslatable x{n}: {reason}")
    raise typer.Exit(1 if result.failures else 0)


corpus_app = typer.Typer(
    help="Labeled corpus tools: blind-labeling templates and the metrics report."
)
app.add_typer(corpus_app, name="corpus")

from schemashift.migrate.cli import migrate_app  # noqa: E402

app.add_typer(migrate_app, name="migrate")


@corpus_app.command("label-template")
def corpus_label_template(
    schema: Path = typer.Argument(..., exists=True, readable=True, help="PostgreSQL DDL file"),
    queries: Path = typer.Option(None, "--queries", "-q", help="Optional SQL queries file"),
    out: Path = typer.Option(
        None, "--out", help="Directory to write <name>/ into (default: stdout)"
    ),
    name: str = typer.Option(None, help="Case name (default: the schema's folder or file name)"),
    include_mechanical: bool = typer.Option(
        False, "--include-mechanical", help="List type:* and not_null:* guarantees too"
    ),
) -> None:
    """Write an empty label file (no verdicts) so a second person can label blind."""
    from schemashift.ir import build_ir
    from schemashift.metrics.labels import label_template
    from schemashift.parser import parse
    from schemashift.semantic import analyze

    schema_sql = schema.read_text(encoding="utf-8")
    queries_sql = queries.read_text(encoding="utf-8") if queries else ""
    parsed = parse(schema_sql + "\n" + queries_sql)
    analysis = analyze(parsed.schema_, parsed.queries)
    program = build_ir(analysis.graph, parsed.queries)
    case = name or (schema.parent.name if schema.name == "schema.sql" else schema.stem)
    text = label_template(
        case,
        schema_sql + "\n" + queries_sql,
        program,
        sorted(analysis.graph.relationships),
        include_mechanical,
    )
    if out is None:
        typer.echo(text)
        return
    folder = out / case
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "schema.sql").write_text(schema_sql, encoding="utf-8", newline="\n")
    if queries_sql:
        (folder / "queries.sql").write_text(queries_sql, encoding="utf-8", newline="\n")
    (folder / "expected.yaml").write_text(text, encoding="utf-8", newline="\n")
    typer.echo(f"wrote {folder}/expected.yaml ({text.count('status:')} guarantees to label)")


@corpus_app.command("report")
def corpus_report(
    write_docs: bool = typer.Option(
        False, "--write-docs", help="Update docs/METRICS.md and metrics.json"
    ),
    verify_external: bool = typer.Option(
        False, "--verify", help="Run the external workloads against PostgreSQL + MongoDB"
    ),
    with_ai: bool = typer.Option(False, "--ai", help="Also ask a real model (ANTHROPIC_API_KEY)"),
) -> None:
    """Print both metric groups, agreement and kappa; the failure-case list; the mutation score."""
    import os

    from schemashift.metrics import report
    from schemashift.metrics.groups import evaluate_groups, format_group

    sandbox = None
    if verify_external:
        from schemashift.verification import SandboxConfig

        sandbox = SandboxConfig(
            pg_admin_dsn=os.environ["SANDBOX_ADMIN_DATABASE_URL"], mongo_url=os.environ["MONGO_URL"]
        )
    advisor = None
    if with_ai:
        from schemashift.ai import AnthropicAdvisor

        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            typer.echo("--ai needs ANTHROPIC_API_KEY", err=True)
            raise typer.Exit(2)
        advisor = AnthropicAdvisor(api_key=key)
    results = evaluate_groups()
    for group in ("self_labeled", "independent"):
        typer.echo(format_group(results[group]))
        typer.echo("")
    data = report.compute(sandbox=sandbox, real_advisor=advisor)
    mock = data["agreement"]["mock"]
    kappa = "undefined" if mock["kappa"] is None else f"{mock['kappa']:.2f}"
    typer.echo(
        f"optimizer vs mock advisor: agreement {mock['agreed']}/{mock['pairs']}, kappa {kappa} "
        "(mock is not an AI signal)"
    )
    real = data["agreement"].get("real")
    typer.echo(
        "optimizer vs real model  : "
        + (f"agreement {real['rate']:.1%}, kappa {real['kappa']}" if real else "not measured")
    )
    ext = data["external"]["result_set"]
    typer.echo(
        f"external result sets     : {ext['match']}/{ext['verified']} MATCH"
        if ext["computed"]
        else "external result sets     : not run (use --verify)"
    )
    mut = data.get("mutation")
    typer.echo(
        f"mutation score           : {mut['score']:.1%}"
        if mut
        else "mutation score           : not measured"
    )
    fails = results["self_labeled"].failures
    typer.echo(f"self-labeled failures    : {len(fails)}")
    for f in fails:
        typer.echo(f"  {f.case} {f.node_id}: label={f.label} predicted={f.predicted}")
    if write_docs:
        for path in report.write_docs(data):
            typer.echo(f"wrote {path}")


@app.callback()
def main() -> None:
    """SchemaShift: SQL to MongoDB migration compiler."""


if __name__ == "__main__":
    app()
