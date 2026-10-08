"""Code generation: collections, validators, indexes, queries, migration and helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from schemashift import __version__
from schemashift.codegen.collections import CollectionsResult, UntranslatedCheck, build_collections
from schemashift.codegen.emit import (
    Emitter,
    GeneratedFile,
    Header,
    MongoshEmitter,
    PymongoEmitter,
)
from schemashift.codegen.indexes import IndexSpec, build_indexes
from schemashift.codegen.layout import TableLayout, build_layouts
from schemashift.codegen.queries import (
    QueryContext,
    TranslatedQuery,
    TranslationError,
    translate_all,
    translate_query,
)
from schemashift.models.base import FrozenModel
from schemashift.pipeline import CompileOptions, CompileResult


class CodegenResult(FrozenModel):
    files: tuple[GeneratedFile, ...]
    collections: tuple[str, ...]
    queries: tuple[TranslatedQuery, ...]
    untranslated_checks: tuple[UntranslatedCheck, ...] = ()
    warnings: tuple[str, ...] = ()

    def file(self, path: str) -> GeneratedFile:
        for f in self.files:
            if f.path == path:
                return f
        raise KeyError(path)


def make_header(run_id: str, timestamp: str | None = None) -> Header:
    return Header(
        version=__version__,
        run_id=run_id,
        timestamp=timestamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def generate_code(
    result: CompileResult,
    options: CompileOptions | None = None,
    run_id: str = "local",
    timestamp: str | None = None,
    emitters: tuple[Emitter, ...] | None = None,
) -> CodegenResult:
    """Generate every artifact for a compile result. Pure: no database access."""
    from schemashift.codegen.helpers import generate_helpers
    from schemashift.codegen.migration import generate_migration

    options = options or CompileOptions()
    header = make_header(run_id, timestamp)
    layouts = build_layouts(result.graph, result.plan)
    cols = build_collections(result.schema_, layouts, options.uuid_as)
    indexes = build_indexes(result.schema_, result.graph, layouts)
    ctx = QueryContext(
        result.schema_, result.graph, layouts, options.uuid_as, options.preserve_integer_ids
    )
    queries = translate_all(result.queries, ctx)
    files: list[GeneratedFile] = []
    for emitter in emitters or (MongoshEmitter(), PymongoEmitter()):
        files.append(emitter.collections(cols.collections, header))
        files.append(emitter.indexes(indexes, header))
        files.append(emitter.queries(queries, header))
    files.append(generate_migration(result, layouts, options, header))
    files.append(generate_helpers(result, layouts, options, header, cols.untranslated))
    warnings = tuple(f"{q.query_id}: {q.error}" for q in queries if q.error)
    return CodegenResult(
        files=tuple(files),
        collections=tuple(c.name for c in cols.collections),
        queries=tuple(queries),
        untranslated_checks=tuple(cols.untranslated),
        warnings=warnings,
    )


__all__ = [
    "CodegenResult",
    "CollectionsResult",
    "Emitter",
    "GeneratedFile",
    "IndexSpec",
    "MongoshEmitter",
    "PymongoEmitter",
    "QueryContext",
    "TableLayout",
    "TranslatedQuery",
    "TranslationError",
    "build_collections",
    "build_indexes",
    "build_layouts",
    "generate_code",
    "translate_all",
    "translate_query",
]
