"""Schema model: the typed result of parsing DDL."""

from typing import Literal

import sqlglot
from sqlglot import exp

from schemashift.models.base import FrozenModel
from schemashift.models.source import SourceSpan

RefAction = Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]

TypeBase = Literal[
    "INTEGER",
    "BIGINT",
    "SMALLINT",
    "DECIMAL",
    "FLOAT",
    "DOUBLE",
    "BOOLEAN",
    "TEXT",
    "VARCHAR",
    "CHAR",
    "DATE",
    "TIMESTAMP",
    "TIMESTAMPTZ",
    "TIME",
    "UUID",
    "JSON",
    "JSONB",
    "BYTEA",
    "ENUM",
    "INTERVAL",
]


class NormalizedType(FrozenModel):
    base: TypeBase
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    is_array: bool = False
    enum_name: str | None = None  # set when base == "ENUM"


class DefaultExpr(FrozenModel):
    sql: str
    kind: Literal["literal", "now", "expression"]


class Column(FrozenModel):
    name: str
    sql_type: NormalizedType
    nullable: bool = True
    default: DefaultExpr | None = None
    is_identity: bool = False
    span: SourceSpan | None = None


class PrimaryKey(FrozenModel):
    columns: tuple[str, ...]
    span: SourceSpan | None = None


class ForeignKey(FrozenModel):
    name: str | None = None
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    on_delete: RefAction = "NO ACTION"
    on_update: RefAction = "NO ACTION"
    deferrable: bool = False
    span: SourceSpan | None = None


class UniqueConstraint(FrozenModel):
    name: str | None = None
    columns: tuple[str, ...]
    span: SourceSpan | None = None


class CheckConstraint(FrozenModel):
    name: str | None = None
    expression_sql: str  # serialized sqlglot AST (postgres SQL text)
    span: SourceSpan | None = None

    def parse(self) -> exp.Expr:
        """Re-hydrate the serialized expression into a sqlglot AST."""
        return sqlglot.parse_one(self.expression_sql, dialect="postgres")


class Index(FrozenModel):
    name: str
    columns: tuple[str, ...]
    unique: bool = False
    where_sql: str | None = None  # partial index predicate
    span: SourceSpan | None = None


class Table(FrozenModel):
    name: str
    columns: tuple[Column, ...]
    primary_key: PrimaryKey | None = None
    foreign_keys: tuple[ForeignKey, ...] = ()
    uniques: tuple[UniqueConstraint, ...] = ()
    checks: tuple[CheckConstraint, ...] = ()
    indexes: tuple[Index, ...] = ()
    span: SourceSpan | None = None

    def column(self, name: str) -> Column | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None


class EnumType(FrozenModel):
    name: str
    values: tuple[str, ...]
    span: SourceSpan | None = None


class Schema(FrozenModel):
    tables: dict[str, Table]
    enums: dict[str, EnumType] = {}
    dialect: str = "postgres"
