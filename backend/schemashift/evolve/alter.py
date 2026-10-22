"""Apply a supported subset of ``ALTER TABLE`` (and friends) to a parsed schema. Pure.

Supported: ADD COLUMN, DROP COLUMN, RENAME COLUMN, RENAME TO, ALTER COLUMN TYPE / SET|DROP NOT
NULL /
SET|DROP DEFAULT, ADD CONSTRAINT (PRIMARY KEY, FOREIGN KEY, UNIQUE, CHECK), DROP CONSTRAINT,
CREATE TABLE,
DROP TABLE, CREATE [UNIQUE] INDEX, DROP INDEX. Anything else is reported as a warning and skipped,
never
guessed at.
"""

from __future__ import annotations

import re

from schemashift.models.schema import (
    CheckConstraint,
    Column,
    Index,
    PrimaryKey,
    Schema,
    Table,
)
from schemashift.parser import parse

NAME = r'(?:"[^"]+"|[\w$]+)(?:\.(?:"[^"]+"|[\w$]+))?'
_ALTER = re.compile(
    rf"^ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?({NAME})\s+(.*)$", re.I | re.S
)
_DROP_TABLE = re.compile(
    r"^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(.+?)(?:\s+(?:CASCADE|RESTRICT))?$", re.I | re.S
)
_DROP_INDEX = re.compile(
    rf"^DROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?({NAME})(?:\s+(?:CASCADE|RESTRICT))?$",
    re.I,
)
_CREATE_INDEX = re.compile(
    rf"^CREATE\s+(?:UNIQUE\s+)?INDEX\b.*?\bON\s+(?:ONLY\s+)?({NAME})", re.I | re.S
)


def _name(raw: str) -> str:
    raw = raw.strip()
    part = raw.split(".")[-1] if '"' not in raw else re.findall(r'"[^"]+"|[^.]+', raw)[-1]
    return part[1:-1] if part.startswith('"') else part.lower()


def split_statements(script: str) -> list[str]:
    """Statements split on top-level ``;``; comments and string literals are respected."""
    out: list[str] = []
    buf: list[str] = []
    i, n, depth = 0, len(script), 0
    while i < n:
        ch = script[i]
        if script.startswith("--", i):
            j = script.find("\n", i)
            i = n if j < 0 else j
            continue
        if script.startswith("/*", i):
            j = script.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "'\"":
            j = i + 1
            while j < n and script[j] != ch:
                j += 2 if script[j] == "\\" else 1
            buf.append(script[i : j + 1])
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == ";" and depth == 0:
            text = "".join(buf).strip()
            if text:
                out.append(text)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def split_top(text: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    depth, start, quote = 0, 0, ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


class _Editor:
    def __init__(self, schema: Schema) -> None:
        self.schema = schema
        self.warnings: list[str] = []
        self.renames: dict[str, str] = {}  # explicit renames: "table" or "table.column" -> new name

    # -- helpers -------------------------------------------------------------------------
    def table(self, name: str) -> Table | None:
        t = self.schema.tables.get(name)
        if t is None:
            self.warnings.append(f"ALTER TABLE on unknown table {name!r} was skipped")
        return t

    def put(self, table: Table) -> None:
        self.schema = self.schema.model_copy(
            update={"tables": {**self.schema.tables, table.name: table}}
        )

    def synthetic(self, body: str) -> Table | None:
        res = parse(f"CREATE TABLE _t ({body});")
        t = res.schema_.tables.get("_t")
        if t is None or any(d.severity == "error" for d in res.diagnostics):
            self.warnings.append(f"could not parse {body!r}")
            return None
        return t

    # -- statements ----------------------------------------------------------------------
    def apply(self, stmt: str) -> None:
        head = stmt.lstrip().upper()
        if head.startswith("CREATE TABLE"):
            res = parse(stmt + ";")
            for t in res.schema_.tables.values():
                self.put(t)
            return
        if head.startswith("DROP TABLE"):
            m = _DROP_TABLE.match(stmt)
            for raw in split_top(m.group(1)) if m else []:
                self.drop_table(_name(raw))
            return
        if head.startswith("DROP INDEX"):
            m = _DROP_INDEX.match(stmt)
            if m:
                self.drop_index(_name(m.group(1)))
            return
        if head.startswith("CREATE") and "INDEX" in head.split("(")[0]:
            self.create_index(stmt)
            return
        m = _ALTER.match(stmt)
        if not m:
            self.warnings.append(f"unsupported statement skipped: {stmt[:60]!r}")
            return
        name = _name(m.group(1))
        for action in split_top(m.group(2)):
            self.alter(name, action)

    def drop_table(self, name: str) -> None:
        if name not in self.schema.tables:
            return
        tables = {}
        for tn, t in self.schema.tables.items():
            if tn == name:
                continue
            fks = tuple(fk for fk in t.foreign_keys if fk.ref_table != name)
            if len(fks) != len(t.foreign_keys):
                self.warnings.append(
                    f"dropping {name} also drops the foreign keys on {tn} that reference it"
                )
                t = t.model_copy(update={"foreign_keys": fks})
            tables[tn] = t
        self.schema = self.schema.model_copy(update={"tables": tables})

    def drop_index(self, name: str) -> None:
        for t in self.schema.tables.values():
            if any(ix.name == name for ix in t.indexes):
                self.put(
                    t.model_copy(update={"indexes": tuple(i for i in t.indexes if i.name != name)})
                )
                return

    def create_index(self, stmt: str) -> None:
        m = _CREATE_INDEX.match(stmt)
        t = self.schema.tables.get(_name(m.group(1))) if m else None
        if t is None:
            self.warnings.append(f"CREATE INDEX skipped: {stmt[:60]!r}")
            return
        cols = ", ".join(f"{c.name} INT" for c in t.columns)
        res = parse(f"CREATE TABLE {t.name} ({cols}); {stmt};")
        made = res.schema_.tables.get(t.name)
        if made is None or not made.indexes:
            self.warnings.append(f"CREATE INDEX not understood: {stmt[:60]!r}")
            return
        self.put(t.model_copy(update={"indexes": (*t.indexes, *made.indexes)}))

    def alter(self, name: str, action: str) -> None:
        t = self.table(name)
        if t is None:
            return
        a = re.sub(r"\s+", " ", action.strip())
        up = a.upper()
        if re.match(r"ADD (CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK)\b", up):
            self.add_constraint(t, a[4:])
        elif up.startswith("ADD "):
            body = re.sub(r"^ADD (COLUMN )?(IF NOT EXISTS )?", "", a, flags=re.I)
            s = self.synthetic(body)
            if s is not None:
                cols = {c.name for c in t.columns}
                new_cols = tuple(c for c in s.columns if c.name not in cols)
                self.put(
                    t.model_copy(
                        update={
                            "columns": (*t.columns, *new_cols),
                            "uniques": (*t.uniques, *s.uniques),
                            "checks": (*t.checks, *s.checks),
                            "foreign_keys": (*t.foreign_keys, *s.foreign_keys),
                            "primary_key": s.primary_key or t.primary_key,
                        }
                    )
                )
        elif m := re.match(
            r"DROP CONSTRAINT (?:IF EXISTS )?(\S+?)(?: CASCADE| RESTRICT)?$", a, re.I
        ):
            self.drop_constraint(t, _name(m.group(1)))
        elif m := re.match(
            r"DROP (?:COLUMN )?(?:IF EXISTS )?(\S+?)(?: CASCADE| RESTRICT)?$", a, re.I
        ):
            self.drop_column(t, _name(m.group(1)))
        elif m := re.match(r"RENAME (?:COLUMN )?(\S+) TO (\S+)$", a, re.I):
            self.rename_column(t, _name(m.group(1)), _name(m.group(2)))
        elif m := re.match(r"RENAME TO (\S+)$", a, re.I):
            self.rename_table(t, _name(m.group(1)))
        elif m := re.match(
            r"ALTER (?:COLUMN )?(\S+) (?:SET DATA )?TYPE (.+?)(?: USING .*)?$", a, re.I
        ):
            self.retype(t, _name(m.group(1)), m.group(2))
        elif m := re.match(r"ALTER (?:COLUMN )?(\S+) (SET NOT NULL|DROP NOT NULL)$", a, re.I):
            self.change_column(t, _name(m.group(1)), nullable=m.group(2).upper().startswith("DROP"))
        elif m := re.match(r"ALTER (?:COLUMN )?(\S+) SET DEFAULT (.+)$", a, re.I):
            s = self.synthetic(f"{_name(m.group(1))} INT DEFAULT {m.group(2)}")
            if s is not None:
                self.change_column(
                    t, _name(m.group(1)), default=s.columns[0].default, set_default=True
                )
        elif m := re.match(r"ALTER (?:COLUMN )?(\S+) DROP DEFAULT$", a, re.I):
            self.change_column(t, _name(m.group(1)), default=None, set_default=True)
        else:
            self.warnings.append(f"unsupported ALTER action skipped on {name}: {a[:60]!r}")

    def add_constraint(self, t: Table, body: str) -> None:
        s = self.synthetic(body)
        if s is None:
            return
        self.put(
            t.model_copy(
                update={
                    "foreign_keys": (*t.foreign_keys, *s.foreign_keys),
                    "uniques": (*t.uniques, *s.uniques),
                    "checks": (*t.checks, *s.checks),
                    "primary_key": s.primary_key or t.primary_key,
                }
            )
        )

    def drop_constraint(self, t: Table, cname: str) -> None:
        # PostgreSQL names unnamed constraints <table>_<columns>_key / _fkey
        def default(cols: tuple[str, ...], suffix: str) -> str:
            return f"{t.name}_{'_'.join(cols)}_{suffix}"

        upd = {
            "foreign_keys": tuple(
                f for f in t.foreign_keys if (f.name or default(f.columns, "fkey")) != cname
            ),
            "uniques": tuple(
                u for u in t.uniques if (u.name or default(u.columns, "key")) != cname
            ),
            "checks": tuple(c for c in t.checks if c.name != cname),
        }
        if cname in (f"{t.name}_pkey", f"{t.name}_pk") and t.primary_key is not None:
            upd["primary_key"] = None  # type: ignore[assignment]
        if all(upd[k] == getattr(t, k) for k in upd) and (
            t.primary_key is None or "primary_key" not in upd
        ):
            self.warnings.append(f"constraint {cname!r} not found on {t.name}")
        self.put(t.model_copy(update=upd))

    def drop_column(self, t: Table, col: str) -> None:
        if t.column(col) is None:
            self.warnings.append(f"column {t.name}.{col} not found")
            return
        word = re.compile(rf"\b{re.escape(col)}\b")
        pk = t.primary_key
        self.put(
            t.model_copy(
                update={
                    "columns": tuple(c for c in t.columns if c.name != col),
                    "primary_key": None if pk and col in pk.columns else pk,
                    "foreign_keys": tuple(f for f in t.foreign_keys if col not in f.columns),
                    "uniques": tuple(u for u in t.uniques if col not in u.columns),
                    "checks": tuple(c for c in t.checks if not word.search(c.expression_sql)),
                    "indexes": tuple(i for i in t.indexes if col not in i.columns),
                }
            )
        )
        tables = dict(self.schema.tables)
        for tn, other in list(tables.items()):  # foreign keys elsewhere that referenced the column
            keep = tuple(
                f
                for f in other.foreign_keys
                if not (f.ref_table == t.name and col in f.ref_columns)
            )
            if len(keep) != len(other.foreign_keys):
                tables[tn] = other.model_copy(update={"foreign_keys": keep})
        self.schema = self.schema.model_copy(update={"tables": tables})

    def rename_column(self, t: Table, old: str, new: str) -> None:
        if t.column(old) is None:
            self.warnings.append(f"column {t.name}.{old} not found")
            return
        prior = next(
            (k for k, v in self.renames.items() if k.startswith(t.name + ".") and v == old), None
        )
        self.renames[prior or f"{t.name}.{old}"] = new

        def r(cols: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(new if c == old else c for c in cols)

        word = re.compile(rf"\b{re.escape(old)}\b")
        pk = t.primary_key
        self.put(
            t.model_copy(
                update={
                    "columns": tuple(
                        c.model_copy(update={"name": new}) if c.name == old else c
                        for c in t.columns
                    ),
                    "primary_key": PrimaryKey(columns=r(pk.columns)) if pk else None,
                    "foreign_keys": tuple(
                        f.model_copy(update={"columns": r(f.columns)}) for f in t.foreign_keys
                    ),
                    "uniques": tuple(
                        u.model_copy(update={"columns": r(u.columns)}) for u in t.uniques
                    ),
                    "checks": tuple(
                        CheckConstraint(name=c.name, expression_sql=word.sub(new, c.expression_sql))
                        for c in t.checks
                    ),
                    "indexes": tuple(
                        Index(
                            name=i.name,
                            columns=r(i.columns),
                            unique=i.unique,
                            where_sql=word.sub(new, i.where_sql) if i.where_sql else None,
                        )
                        for i in t.indexes
                    ),
                }
            )
        )
        tables = dict(self.schema.tables)
        for tn, other in list(tables.items()):
            fks = tuple(
                f.model_copy(update={"ref_columns": r(f.ref_columns)})
                if f.ref_table == t.name
                else f
                for f in other.foreign_keys
            )
            if fks != other.foreign_keys:
                tables[tn] = other.model_copy(update={"foreign_keys": fks})
        self.schema = self.schema.model_copy(update={"tables": tables})

    def rename_table(self, t: Table, new: str) -> None:
        prior = next((k for k, v in self.renames.items() if "." not in k and v == t.name), None)
        self.renames[prior or t.name] = new
        tables: dict[str, Table] = {}
        for tn, other in self.schema.tables.items():
            if tn == t.name:
                tables[new] = other.model_copy(update={"name": new})
                continue
            fks = tuple(
                f.model_copy(update={"ref_table": new}) if f.ref_table == t.name else f
                for f in other.foreign_keys
            )
            tables[tn] = other.model_copy(update={"foreign_keys": fks})
        self.schema = self.schema.model_copy(update={"tables": tables})

    def retype(self, t: Table, col: str, type_sql: str) -> None:
        s = self.synthetic(f"{col} {type_sql}")
        if s is None or t.column(col) is None:
            self.warnings.append(f"cannot change the type of {t.name}.{col}")
            return
        new_type = s.columns[0].sql_type
        self.put(
            t.model_copy(
                update={
                    "columns": tuple(
                        c.model_copy(update={"sql_type": new_type}) if c.name == col else c
                        for c in t.columns
                    )
                }
            )
        )

    def change_column(
        self,
        t: Table,
        col: str,
        nullable: bool | None = None,
        default: object = None,
        set_default: bool = False,
    ) -> None:
        if t.column(col) is None:
            self.warnings.append(f"column {t.name}.{col} not found")
            return
        upd: dict[str, object] = {}
        if nullable is not None:
            upd["nullable"] = nullable
        if set_default:
            upd["default"] = default
        self.put(
            t.model_copy(
                update={
                    "columns": tuple(
                        c.model_copy(update=upd) if c.name == col else c for c in t.columns
                    )
                }
            )
        )


def apply_alters(old: Schema, script: str) -> tuple[Schema, list[str], dict[str, str]]:
    """The schema after ``script``, what was skipped (and why) and the renames the script states.

    A rename written as ``RENAME COLUMN`` is explicit, so it needs no confirmation.
    """
    ed = _Editor(old)
    for stmt in split_statements(script):
        ed.apply(stmt)
    return ed.schema, ed.warnings, ed.renames


__all__ = ["Column", "apply_alters", "split_statements"]
