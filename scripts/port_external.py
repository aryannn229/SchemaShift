"""Fetch the public schemas in samples/external/ from their upstream repositories and port them.

Porting is deliberately minimal and mechanical (so the result is reproducible and auditable):
  - drop session/administrative statements that carry no schema meaning:
    SET, set_config(), ALTER ... OWNER TO, COMMENT ON, GRANT, REVOKE, CREATE/ALTER EXTENSION, DROP ...
  - drop data statements (INSERT, COPY ... FROM stdin blocks) from the dumps that include data
  - keep everything else byte for byte (functions, triggers, views and partitioning stay in, so the
    parser's diagnostics for them are part of the benchmark).

Usage: python scripts/port_external.py [name ...]    (network access required)
The upstream commit SHA is resolved at run time and written to each schema's README.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "samples" / "external"


@dataclass(frozen=True)
class Source:
    name: str
    repo: str  # owner/name
    branch: str
    path: str
    license: str
    license_path: str
    description: str
    strip_data: bool = False
    notes: tuple[str, ...] = field(default=())


SOURCES = [
    Source(
        "pagila",
        "devrimgunduz/pagila",
        "master",
        "pagila-schema.sql",
        "PostgreSQL License",
        "LICENSE.txt",
        "DVD rental store (PostgreSQL port of MySQL Sakila): 22 tables, views, functions, triggers, "
        "a partitioned table. Deliberately keeps constructs SchemaShift rejects.",
    ),
    Source(
        "chinook",
        "lerocha/chinook-database",
        "master",
        "ChinookDatabase/DataSources/Chinook_PostgreSql.sql",
        "MIT",
        "LICENSE.md",
        "Digital music store: 11 tables (artist, album, track, invoice, playlist, ...).",
        strip_data=True,
    ),
    Source(
        "northwind",
        "pthom/northwind_psql",
        "master",
        "northwind.sql",
        "Microsoft Public License (Ms-PL)",
        "LICENSE",
        "Classic Microsoft Northwind trading company: 14 tables (orders, products, employees, ...).",
        strip_data=True,
    ),
    Source(
        "supabase_slack_clone",
        "supabase/supabase",
        "master",
        "examples/slack-clone/nextjs-slack-clone/full-schema.sql",
        "Apache License 2.0",
        "LICENSE",
        "Open-source app schema (Supabase Slack clone): users, channels, messages, roles and "
        "permissions with enums, identity keys, a function, a trigger and row level security. "
        "References auth.users, a table outside this file.",
    ),
    Source(
        "graphile_starter",
        "graphile/starter",
        "main",
        "@app/db/migrations/committed/000001.sql",
        "MIT",
        "GRAPHILE_STARTER_LICENSE.md",
        "Open-source app schema (Graphile Starter, first migration): users, emails, organizations, "
        "sessions, with many functions, triggers and policies. A stress test for unsupported constructs.",
    ),
]

RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
STRIP_PREFIXES = (
    "set ",
    "alter table only ",  # handled below: only OWNER TO is dropped
    "comment on ",
    "grant ",
    "revoke ",
    "create extension",
    "alter extension",
    "drop ",
    "alter default privileges",
    "alter schema",
    "alter type",
    "alter function",
    "alter domain",
    "alter sequence",
    "alter view",
    "select pg_catalog.set_config",
)


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310
        return str(r.read().decode("utf-8", errors="replace"))


def head_sha(repo: str, branch: str) -> str:
    data = json.loads(fetch(f"https://api.github.com/repos/{repo}/commits/{branch}"))
    return str(data["sha"])


def split_statements(sql: str) -> list[str]:
    """Split on top-level semicolons, respecting quotes, dollar quoting and comments."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    dollar: str | None = None
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if dollar:
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
        elif quote:
            buf.append(ch)
            if ch == quote:
                if sql[i + 1 : i + 2] == quote:  # doubled quote
                    buf.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
            i += 1
            continue
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            buf.append(sql[i:j])
            i = j
            continue
        elif ch in "'\"":
            quote = ch
        elif ch == "$":
            m = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if m:
                dollar = m.group(0)
                buf.append(dollar)
                i += len(dollar)
                continue
        elif ch == ";":
            buf.append(ch)
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def code_of(stmt: str) -> str:
    """The statement without leading comments/whitespace, lower-cased (for classification)."""
    stmt = re.sub(r"/\*.*?\*/", " ", stmt, flags=re.S)  # block comments
    lines = [ln for ln in stmt.strip().splitlines() if not ln.strip().startswith("--")]
    return " ".join(" ".join(lines).split()).lower()


def keep(stmt: str, strip_data: bool) -> bool:
    c = code_of(stmt)
    if not c:
        return False
    if c.startswith("alter ") and " owner to " in c:
        return False
    if c.startswith(STRIP_PREFIXES) and not c.startswith("alter table only "):
        return False
    if c.startswith(("create database", "\\")):  # psql scripts: CREATE DATABASE and \c
        return False
    if strip_data and c.startswith(("insert into", "copy ", "\\.")):
        return False
    return True


def port(text: str, strip_data: bool) -> str:
    text = re.sub(r"^COPY .*?^\\\.\s*$", "", text, flags=re.S | re.M)  # COPY data blocks
    kept = [s for s in split_statements(text) if keep(s, strip_data)]
    return "\n".join(s.strip("\n") for s in kept).strip() + "\n"


README = """# {name}

{description}

- **Source:** https://github.com/{repo}/blob/{sha}/{path}
- **Upstream commit:** `{sha}`
- **License:** {license} (https://github.com/{repo}/blob/{sha}/{license_path})
- **Ported by:** `scripts/port_external.py` (mechanical, reproducible)

## Porting notes
Removed (no schema meaning): `SET`, `set_config()`, `ALTER ... OWNER TO`, `COMMENT ON`, `GRANT`/`REVOKE`,
`CREATE/ALTER EXTENSION`, `CREATE DATABASE`, psql meta-commands, `DROP ...`{data}. Everything else is byte for byte as upstream: functions, triggers, views and
partitioning stay in on purpose, because SchemaShift's diagnostics for them are part of the benchmark.

## Workload
`queries.sql` is a hand-written workload of {queries} queries over this schema (reads with joins and aggregates, plus a few
writes). `seed.sql` is intentionally absent: verification generates constraint-respecting synthetic data (seeded).
{extra}
"""


def main(names: list[str]) -> None:
    for src in SOURCES:
        if names and src.name not in names:
            continue
        sha = head_sha(src.repo, src.branch)
        text = fetch(RAW.format(repo=src.repo, ref=sha, path=src.path))
        ported = port(text, src.strip_data)
        folder = ROOT / src.name
        folder.mkdir(parents=True, exist_ok=True)
        header = (
            f"-- Source: https://github.com/{src.repo}/blob/{sha}/{src.path}\n"
            f"-- License: {src.license}. Ported with scripts/port_external.py (see README.md).\n\n"
        )
        (folder / "schema.sql").write_text(header + ported, encoding="utf-8", newline="\n")
        queries = folder / "queries.sql"
        n_queries = len(re.findall(r";\s*$", queries.read_text("utf-8"), flags=re.M)) if queries.exists() else 0
        (folder / "README.md").write_text(
            README.format(
                name=src.name,
                description=src.description,
                repo=src.repo,
                sha=sha,
                path=src.path,
                license=src.license,
                license_path=src.license_path,
                data=", and the data (`INSERT`/`COPY`)" if src.strip_data else "",
                queries=n_queries or "N",
                extra="",
            ),
            encoding="utf-8",
            newline="\n",
        )
        print(f"{src.name}: {len(ported)} bytes, upstream {sha[:10]}")


if __name__ == "__main__":
    main(sys.argv[1:])
