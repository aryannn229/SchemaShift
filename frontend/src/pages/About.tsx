const STAGES = [
  ["Parse", "SQL DDL and queries become typed models (sqlglot, PostgreSQL dialect)."],
  ["Analyze", "Foreign keys form a graph; junction tables and cycles are detected."],
  ["IR", "Every guarantee SQL gives (keys, constraints, join, NULL and ordering semantics) becomes an IR node."],
  ["Equivalence", "Declarative rules decide whether each guarantee is SAFE, CHANGED or BROKEN in MongoDB."],
  ["Optimizer", "Weighted features decide EMBED or REFERENCE per relationship; an LLM only offers a second opinion."],
  ["Codegen", "Validators, indexes, query pipelines, a migration script and enforcement helpers."],
  ["Verification", "Run the SQL in a sandbox PostgreSQL and the generated code in MongoDB, then diff the results."],
];

export default function About() {
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-6">
      <h1 className="text-2xl font-bold">How SchemaShift works</h1>
      <p>
        SchemaShift is a compiler from a PostgreSQL schema and workload to MongoDB. It reports what
        the migration silently changes, not just how to move the data.
      </p>
      <ol className="space-y-2" aria-label="Pipeline">
        {STAGES.map(([name, text], i) => (
          <li key={name} className="rounded border p-3 dark:border-slate-700">
            <b>
              {i + 1}. {name}
            </b>
            <div className="text-sm text-slate-600 dark:text-slate-400">{text}</div>
          </li>
        ))}
      </ol>
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Your SQL is parsed, never executed as text. Verification re-emits the parsed AST into an
        isolated sandbox and uses bound parameters for data.
      </p>
    </div>
  );
}
