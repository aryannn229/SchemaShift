"""Lexical helpers: comment/string-aware masking, statement splitting, source maps.

All helpers keep character offsets stable so positions found in masked text map
1:1 back to the original source.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from schemashift.models.source import SourceSpan

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")
_QUOTES = ("'", '"')


def mask_code(text: str) -> str:
    """Return ``text`` with comments and quoted contents replaced by spaces.

    Newlines are preserved, quote characters are kept, and the result has the
    same length as the input.
    """
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = text[i]
        if ch == "-" and text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif ch == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
        elif ch in _QUOTES:
            j = i + 1
            while j < n:
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:  # escaped quote
                        j += 2
                        continue
                    break
                j += 1
            blank(i + 1, min(j, n))
            i = j + 1
        elif ch == "$":
            m = _DOLLAR_TAG.match(text, i)
            if m:
                tag = m.group(0)
                j = text.find(tag, m.end())
                end = n if j == -1 else j
                blank(m.end(), end)
                i = end + (len(tag) if j != -1 else 0)
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class RawStatement:
    text: str
    masked: str
    start: int  # offset of first code character in the source
    end: int  # exclusive


def split_statements(sql: str) -> list[RawStatement]:
    """Split on top-level semicolons, skipping comment-only statements."""
    masked = mask_code(sql)
    result: list[RawStatement] = []
    pos = 0
    for piece_end in [m.start() for m in re.finditer(";", masked)] + [len(sql)]:
        piece = masked[pos:piece_end]
        stripped = piece.strip()
        if stripped:
            start = pos + (len(piece) - len(piece.lstrip()))
            end = start + len(stripped)
            result.append(RawStatement(sql[start:end], masked[start:end], start, end))
        pos = piece_end + 1
    return result


def split_body_segments(masked: str, keep_empty: bool = False) -> list[tuple[int, int]]:
    """Offsets of the top-level comma separated items of the first ``( ... )``."""
    open_at = masked.find("(")
    if open_at == -1:
        return []
    segments: list[tuple[int, int]] = []
    depth = 0
    seg_start = open_at + 1
    for i in range(open_at, len(masked)):
        ch = masked[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                segments.append((seg_start, i))
                break
        elif ch == "," and depth == 1:
            segments.append((seg_start, i))
            seg_start = i + 1
    trimmed: list[tuple[int, int]] = []
    for a, b in segments:
        piece = masked[a:b]
        if not piece.strip():
            if keep_empty:
                trimmed.append((a, a))
            continue
        a2 = a + (len(piece) - len(piece.lstrip()))
        trimmed.append((a2, a2 + len(piece.strip())))
    return trimmed


class SourceMap:
    """Converts character offsets to 1-based line/column positions."""

    def __init__(self, source: str) -> None:
        self._starts = [0] + [m.end() for m in re.finditer("\n", source)]

    def position(self, offset: int) -> tuple[int, int]:
        line_idx = bisect_right(self._starts, offset) - 1
        return line_idx + 1, offset - self._starts[line_idx] + 1

    def span(self, start: int, end: int) -> SourceSpan:
        ls, cs = self.position(start)
        le, ce = self.position(max(start, end))
        return SourceSpan(line_start=ls, col_start=cs, line_end=le, col_end=ce)
