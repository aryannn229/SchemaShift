from schemashift.parser.splitter import (
    SourceMap,
    mask_code,
    split_body_segments,
    split_statements,
)


def test_mask_preserves_length_and_newlines() -> None:
    sql = "SELECT 'a;b' -- c;\n/* x;\ny */ FROM t"
    masked = mask_code(sql)
    assert len(masked) == len(sql)
    assert masked.count("\n") == sql.count("\n")
    assert ";" not in masked


def test_mask_escaped_quote() -> None:
    assert ";" not in mask_code("SELECT 'it''s;' ")


def test_mask_dollar_quoted() -> None:
    masked = mask_code("DO $body$ select ';' $body$; SELECT 1")
    assert masked.count(";") == 1


def test_mask_anonymous_dollar_quote() -> None:
    assert ";" not in mask_code("SELECT $$a;b$$")


def test_lone_dollar_is_not_quote() -> None:
    assert mask_code("SELECT $1") == "SELECT $1"


def test_split_statements_offsets() -> None:
    sql = "  SELECT 1;\n-- hi\n SELECT 2 ;;"
    stmts = split_statements(sql)
    assert [s.text for s in stmts] == ["SELECT 1", "SELECT 2"]
    assert sql[stmts[1].start : stmts[1].end] == "SELECT 2"


def test_split_skips_comment_only() -> None:
    assert split_statements("-- nothing\n/* here */") == []


def test_split_unterminated_last_statement() -> None:
    assert [s.text for s in split_statements("SELECT 1")] == ["SELECT 1"]


def test_body_segments() -> None:
    masked = "CREATE TABLE t (a INT, b NUMERIC(3,1), c TEXT)"
    segs = [masked[a:b] for a, b in split_body_segments(masked)]
    assert segs == ["a INT", "b NUMERIC(3,1)", "c TEXT"]


def test_body_segments_without_parens() -> None:
    assert split_body_segments("SELECT 1") == []


def test_body_segments_keep_empty() -> None:
    segs = split_body_segments("(a,, b)", keep_empty=True)
    assert len(segs) == 3 and segs[1][0] == segs[1][1]


def test_source_map() -> None:
    sm = SourceMap("ab\ncde\n")
    assert sm.position(0) == (1, 1)
    assert sm.position(4) == (2, 2)
    span = sm.span(1, 5)
    assert (span.line_start, span.col_start, span.line_end, span.col_end) == (1, 2, 2, 3)
