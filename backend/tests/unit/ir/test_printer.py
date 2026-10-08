from schemashift.ir import build_ir, print_ir
from schemashift.ir.printer import describe
from schemashift.parser import parse
from schemashift.semantic import analyze

SQL = """
CREATE TYPE mood AS ENUM ('a', 'b');
CREATE TABLE a (id SERIAL PRIMARY KEY, m mood DEFAULT 'a', email TEXT UNIQUE, n NUMERIC(5,2) CHECK (n > 0));
CREATE TABLE b (id INT PRIMARY KEY);
CREATE TABLE ab (
  a_id INT REFERENCES a(id) ON DELETE CASCADE ON UPDATE CASCADE,
  b_id INT REFERENCES b(id) ON DELETE SET NULL,
  PRIMARY KEY (a_id, b_id)
);
CREATE TABLE c (id INT PRIMARY KEY, a_id INT REFERENCES a(id) ON DELETE SET DEFAULT, b_id INT REFERENCES b(id) ON DELETE RESTRICT);
BEGIN; UPDATE a SET email = 'x' WHERE id = 1; DELETE FROM b WHERE id = 1; COMMIT;
SELECT a.email, COUNT(*) FROM a LEFT JOIN c ON c.a_id = a.id GROUP BY a.email;
"""


def test_every_node_type_is_described_without_fallback() -> None:
    result = parse(SQL)
    program = build_ir(analyze(result.schema_, result.queries).graph, result.queries)
    for node in program.nodes:
        assert describe(node) != type(node).__name__, node


def test_printer_output_sections() -> None:
    result = parse(SQL)
    text = print_ir(build_ir(analyze(result.schema_, result.queries).graph, result.queries))
    assert text.index("== ENTITIES ==") < text.index("== GUARANTEES ==")
    assert "-- CascadingDelete" in text
    assert "deleting a a deletes its ab rows" in text
    assert "[fk:ab.a_id->a.id]" in text


def test_describe_unknown_object() -> None:
    assert describe(object()) == "object"
