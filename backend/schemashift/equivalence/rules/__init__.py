"""Importing this package registers every rule."""

from schemashift.equivalence.rules import (  # noqa: F401
    atomicity,
    columns,
    queries,
    referential,
    uniqueness,
)
