"""Query models."""

from typing import Literal

from pydantic import Field
from sqlglot import exp

from schemashift.models.base import FrozenModel
from schemashift.models.source import SourceSpan

QueryKind = Literal["SELECT", "INSERT", "UPDATE", "DELETE"]


class Query(FrozenModel):
    id: str
    kind: QueryKind
    ast: exp.Expr = Field(exclude=True)
    raw_sql: str
    span: SourceSpan | None = None


class TransactionBlock(FrozenModel):
    id: str
    statements: tuple[Query, ...]
    span: SourceSpan | None = None
