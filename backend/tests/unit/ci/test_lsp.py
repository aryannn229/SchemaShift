"""The optional language server, tested over real stdio JSON-RPC (not by calling its functions)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
PKG = ROOT / "packages" / "lsp"
pytest.importorskip("pygls")

SQL = (
    "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE);\n"
    "CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) "
    "ON DELETE CASCADE);\n"
)


def frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def read_message(stream: Any) -> dict[str, Any]:
    length = 0
    while True:
        line = stream.readline()
        if not line:
            raise EOFError("server closed the stream")
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    return json.loads(stream.read(length))  # type: ignore[no-any-return]


def test_diagnostics_for_pure_function() -> None:
    sys.path.insert(0, str(PKG))
    try:
        from schemashift_lsp import diagnostics_for
    finally:
        sys.path.remove(str(PKG))
    diags = diagnostics_for(SQL)
    assert diags and all(d.source == "schemashift" for d in diags)
    assert any(d.code == "EQ-CASCADE-DEL" or str(d.code).startswith("EQ-") for d in diags)
    assert all(d.range.start.line >= 0 for d in diags)
    bad = diagnostics_for("CREATE TABLE t (id INT PRIMARY KEY, x INT REFERENCES missing(id));")
    assert any(str(d.code).startswith("SEM") for d in bad)


def test_server_publishes_diagnostics_over_stdio() -> None:
    env = {**os.environ, "PYTHONPATH": str(PKG), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "schemashift_lsp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    assert proc.stdin and proc.stdout
    timer = threading.Timer(60, proc.kill)  # a hung server must not hang the suite
    timer.start()
    try:
        proc.stdin.write(
            frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"processId": None, "rootUri": None, "capabilities": {}},
                }
            )
        )
        proc.stdin.flush()
        init = read_message(proc.stdout)
        assert init["id"] == 1 and "capabilities" in init["result"]
        proc.stdin.write(frame({"jsonrpc": "2.0", "method": "initialized", "params": {}}))
        proc.stdin.write(
            frame(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": "file:///tmp/schema.sql",
                            "languageId": "sql",
                            "version": 1,
                            "text": SQL,
                        }
                    },
                }
            )
        )
        proc.stdin.flush()
        published: dict[str, Any] | None = None
        for _ in range(10):
            msg = read_message(proc.stdout)
            if msg.get("method") == "textDocument/publishDiagnostics":
                published = msg["params"]
                break
        assert published is not None and published["uri"] == "file:///tmp/schema.sql"
        diags = published["diagnostics"]
        assert diags and all(d["source"] == "schemashift" for d in diags)
        assert any(
            d["message"].startswith("[CHANGED") or d["message"].startswith("[BROKEN") for d in diags
        )
        # editing the document republishes: a broken statement yields a parse diagnostic
        proc.stdin.write(
            frame(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didChange",
                    "params": {
                        "textDocument": {"uri": "file:///tmp/schema.sql", "version": 2},
                        "contentChanges": [
                            {
                                "text": "CREATE TABLE t (id INT PRIMARY KEY, x INT REFERENCES gone(id));\n"
                            }
                        ],
                    },
                }
            )
        )
        proc.stdin.flush()
        for _ in range(10):
            msg = read_message(proc.stdout)
            if msg.get("method") == "textDocument/publishDiagnostics":
                assert any(
                    str(d.get("code", "")).startswith("SEM") for d in msg["params"]["diagnostics"]
                )
                break
        else:  # pragma: no cover
            pytest.fail("no diagnostics after the change")
        proc.stdin.write(frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None}))
        proc.stdin.flush()
        assert read_message(proc.stdout)["id"] == 2
        proc.stdin.write(frame({"jsonrpc": "2.0", "method": "exit", "params": None}))
        proc.stdin.flush()
        proc.wait(timeout=20)
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()


def test_vscode_client_files_are_consistent() -> None:
    pkg = json.loads((ROOT / "packages/vscode/package.json").read_text(encoding="utf-8"))
    assert pkg["main"] == "./extension.js" and "onLanguage:sql" in pkg["activationEvents"]
    assert "vscode-languageclient" in pkg["dependencies"]
    js = (ROOT / "packages/vscode/extension.js").read_text(encoding="utf-8")
    assert "schemashift_lsp" in js and "documentSelector" in js
