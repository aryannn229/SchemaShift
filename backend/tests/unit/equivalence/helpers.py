from schemashift.equivalence import EquivalenceReport, RuleOptions, check_program
from schemashift.ir import IRProgram, build_ir
from schemashift.models import Placement, PlacementPlan, Verdict
from schemashift.parser import parse
from schemashift.semantic import SchemaGraph, analyze


def compile_ir(sql: str) -> tuple[SchemaGraph, IRProgram]:
    result = parse(sql)
    analysis = analyze(result.schema_, result.queries)
    return analysis.graph, build_ir(analysis.graph, result.queries)


def run(
    sql: str,
    plan: dict[str, Placement] | None = None,
    options: RuleOptions | None = None,
) -> EquivalenceReport:
    graph, program = compile_ir(sql)
    return check_program(program, graph, PlacementPlan.of(plan or {}), options)


def verdict(
    sql: str,
    node_id: str,
    plan: dict[str, Placement] | None = None,
    options: RuleOptions | None = None,
) -> Verdict:
    return run(sql, plan, options).verdict_for(node_id)
