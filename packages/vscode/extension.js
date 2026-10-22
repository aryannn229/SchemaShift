// Starts the SchemaShift language server (python -m schemashift_lsp) for SQL files.
const vscode = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function activate(context) {
  const python = vscode.workspace.getConfiguration("schemashift").get("pythonPath", "python");
  const server = { command: python, args: ["-m", "schemashift_lsp"], transport: TransportKind.stdio };
  client = new LanguageClient(
    "schemashift",
    "SchemaShift",
    { run: server, debug: server },
    { documentSelector: [{ scheme: "file", language: "sql" }] },
  );
  context.subscriptions.push(client.start());
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
