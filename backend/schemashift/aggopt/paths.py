"""Field-path analysis of aggregation stages: which paths a stage reads and which it produces."""

from __future__ import annotations

from typing import Any

Stage = dict[str, Any]


def expr_refs(node: Any, out: set[str] | None = None) -> set[str]:
    """Field paths read by an aggregation *expression*: dict keys are operators or literal names."""
    out = set() if out is None else out
    if isinstance(node, str):
        if node.startswith("$$"):
            out.add(node)
        elif node.startswith("$"):
            out.add(node[1:])
    elif isinstance(node, list):
        for x in node:
            expr_refs(x, out)
    elif isinstance(node, dict):
        for v in node.values():
            expr_refs(v, out)
    return out


def refs_in(node: Any, out: set[str] | None = None) -> set[str]:
    """Every field path referenced by an expression or match document ("$a.b" strings and match
    keys).

    ``$$ROOT`` / ``$$CURRENT`` / ``$$var`` are reported as ``$$...`` so callers can refuse to reason
    about stages that read the whole document.
    """
    out = set() if out is None else out
    if isinstance(node, str):
        if node.startswith("$$"):
            out.add(node)
        elif node.startswith("$"):
            out.add(node[1:])
    elif isinstance(node, list):
        for x in node:
            refs_in(x, out)
    elif isinstance(node, dict):
        for k, v in node.items():
            if not k.startswith("$"):
                out.add(k)
            refs_in(v, out)
    return out


def stage_name(stage: Stage) -> str:
    return next(iter(stage))


def stage_reads(stage: Stage) -> set[str]:
    """Paths a stage reads (conservative: includes ``$$ROOT`` style whole-document reads)."""
    name = stage_name(stage)
    body = stage[name]
    if name == "$lookup":
        reads = {body["localField"]} if "localField" in body else set()
        for key in ("let", "pipeline"):
            if key in body:
                reads.add("$$ROOT")  # correlated sub-pipeline: do not reason about it
        return reads
    if name == "$unwind":
        path = body["path"] if isinstance(body, dict) else body
        return {path[1:]}
    if name == "$sort":
        return set(body)
    if name == "$group":
        return expr_refs(body)
    if name in ("$limit", "$skip", "$count"):
        return set()
    if name in ("$addFields", "$set", "$replaceRoot"):
        return expr_refs(body)
    if name == "$project":
        proj: set[str] = set()
        for k, v in body.items():
            if v in (1, True):
                proj.add(k)
            elif v not in (0, False):
                expr_refs(v, proj)
        return proj
    if name == "$match":
        return refs_in(body)
    return {"$$ROOT"}  # unknown stage: assume it reads everything


def under(path: str, prefix: str) -> bool:
    """``path`` is ``prefix`` or inside it."""
    return path == prefix or path.startswith(prefix + ".")


def touches(paths: set[str], prefix: str) -> bool:
    """Some path in ``paths`` overlaps ``prefix`` (inside it, or an ancestor of it)."""
    return any(under(p, prefix) or under(prefix, p) for p in paths if not p.startswith("$$"))


def rewrite_refs(node: Any, old: str, new: str) -> Any:
    """Copy of ``node`` with the leading path component ``old.`` replaced by ``new`` (``""``
    strips it)."""
    if isinstance(node, str):
        if node.startswith("$$"):
            return node
        if node.startswith("$" + old + "."):
            rest = node[len(old) + 2 :]
            return "$" + (new + "." + rest if new else rest)
        return node
    if isinstance(node, list):
        return [rewrite_refs(x, old, new) for x in node]
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if not k.startswith("$") and k.startswith(old + "."):
                rest = k[len(old) + 1 :]
                k = new + "." + rest if new else rest
            out[k] = rewrite_refs(v, old, new)
        return out
    return node
