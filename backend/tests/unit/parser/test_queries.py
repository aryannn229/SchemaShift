from sqlglot import exp

from schemashift.models import Query, TransactionBlock
from schemashift.parser import parse, parse_queries


def test_supported_select_subset() -> None:
    sql = """
    SELECT DISTINCT c.name, COUNT(*) AS n, SUM(o.total), AVG(o.total), MIN(o.total), MAX(o.total)
    FROM customers c
    INNER JOIN orders o ON o.customer_id = c.id
    LEFT JOIN items i ON i.order_id = o.id AND i.kind = o.kind
    WHERE c.id IN (1, 2) AND o.total BETWEEN 1 AND 5 AND c.name LIKE 'a%' AND c.x IS NULL
    GROUP BY c.name HAVING COUNT(*) > 1 ORDER BY n DESC LIMIT 10 OFFSET 5;
    """
    result = parse_queries(sql)
    assert not result.has_errors, result.diagnostics
    q = result.queries[0]
    assert isinstance(q, Query) and q.kind == "SELECT"
    assert isinstance(q.ast, exp.Select)


def test_select_star_and_simple() -> None:
    assert not parse_queries("SELECT * FROM t;").has_errors


def test_dml_kinds() -> None:
    result = parse_queries(
        "INSERT INTO t (a, b) VALUES (1, 'x'), (2, 'y'); UPDATE t SET a = 1 WHERE b = 'x'; "
        "DELETE FROM t WHERE a = 2;"
    )
    assert not result.has_errors
    assert [q.kind for q in result.all_queries()] == ["INSERT", "UPDATE", "DELETE"]


def test_query_ids_are_sequential_and_raw_sql_kept() -> None:
    result = parse_queries("SELECT 1 FROM a; SELECT 2 FROM b;")
    assert [q.id for q in result.all_queries()] == ["q1", "q2"]
    assert result.all_queries()[0].raw_sql == "SELECT 1 FROM a"


def test_query_span_line() -> None:
    result = parse_queries("SELECT 1 FROM a;\nSELECT 2 FROM b;")
    q = result.all_queries()[1]
    assert q.span is not None and q.span.line_start == 2


def test_transaction_block() -> None:
    sql = """
    BEGIN;
    UPDATE a SET x = 1 WHERE id = 1;
    UPDATE b SET x = 2 WHERE id = 1;
    COMMIT;
    SELECT * FROM a;
    """
    result = parse(sql)
    assert not result.has_errors, result.diagnostics
    block, loose = result.queries
    assert isinstance(block, TransactionBlock) and block.id == "t1"
    assert [q.id for q in block.statements] == ["q1", "q2"]
    assert isinstance(loose, Query) and loose.id == "q3"
    assert block.span is not None and block.span.line_start == 2 and block.span.line_end == 5


def test_begin_transaction_and_end_keywords() -> None:
    result = parse("BEGIN TRANSACTION; DELETE FROM a WHERE id = 1; END;")
    assert isinstance(result.queries[0], TransactionBlock)


def test_start_transaction() -> None:
    result = parse("START TRANSACTION; DELETE FROM a WHERE id = 1; COMMIT;")
    assert isinstance(result.queries[0], TransactionBlock)


def test_empty_transaction_produces_no_block() -> None:
    assert parse("BEGIN; COMMIT;").queries == ()


def test_unterminated_transaction_is_error() -> None:
    result = parse("BEGIN; DELETE FROM a WHERE id = 1;")
    assert result.queries == ()
    assert [d.code for d in result.diagnostics] == ["PARSE004"]


def test_commit_without_begin_warns() -> None:
    result = parse("COMMIT;")
    assert result.diagnostics[0].severity == "warning"


def test_nested_begin_warns() -> None:
    result = parse("BEGIN; BEGIN; DELETE FROM a WHERE id = 1; COMMIT;")
    assert [d.code for d in result.diagnostics] == ["PARSE004"]
    assert isinstance(result.queries[0], TransactionBlock)


def test_rollback_discards_block() -> None:
    result = parse("BEGIN; DELETE FROM a WHERE id = 1; ROLLBACK;")
    assert result.queries == ()
    assert "UNSUPPORTED_ROLLBACK" in [d.code for d in result.diagnostics]


def test_bad_query_does_not_stop_the_rest() -> None:
    result = parse("SELECT FROM FROM; SELECT * FROM t;")
    assert any(d.code == "PARSE001" for d in result.diagnostics)
    assert len(result.queries) == 1


def test_all_queries_flattens_blocks() -> None:
    result = parse("BEGIN; DELETE FROM a WHERE id = 1; COMMIT; SELECT * FROM a;")
    assert [q.id for q in result.all_queries()] == ["q1", "q2"]
