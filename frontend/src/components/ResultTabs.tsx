import { useState } from "react";

import type { CompileRequest, CompileResponse, RunDetail, Verdict } from "../api/client";
import { CodeTab } from "./CodeTab";
import { IRTab } from "./IRTab";
import { OverviewTab } from "./OverviewTab";
import { RelationshipsTab } from "./RelationshipsTab";
import { SummaryTab } from "./SummaryTab";
import { EmptyState, ErrorState, SkeletonBlock, Tabs } from "./ui";
import { VerdictsTab } from "./VerdictsTab";
import { VerificationTab } from "./VerificationTab";

const TABS = [
  { id: "Overview", label: "Overview" },
  { id: "Summary", label: "Summary" },
  { id: "Verdicts", label: "Verdicts" },
  { id: "Relationships", label: "Relationships" },
  { id: "IR", label: "IR" },
  { id: "Generated Code", label: "Generated Code" },
  { id: "Verification", label: "Verification" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export function ResultTabs({
  result,
  loading,
  error,
  verifyError,
  run,
  onSelectVerdict,
  onOverride,
  request,
}: {
  result?: CompileResponse | null;
  loading?: boolean;
  error?: string | null;
  verifyError?: string | null;
  run?: RunDetail;
  onSelectVerdict?: (v: Verdict) => void;
  onOverride?: (relationshipId: string, decision: "EMBED" | "REFERENCE") => void;
  request?: { schema_sql: string; queries_sql?: string; options?: CompileRequest["options"] };
}) {
  const [tab, setTab] = useState<Tab>("Overview");

  let body: React.ReactNode;
  if (tab === "Verification")
    body = verifyError && !run ? <ErrorState message={verifyError} /> : <VerificationTab run={run} />;
  else if (loading) body = <SkeletonBlock rows={6} label="Compiling" />;
  else if (error) body = <ErrorState message={error} />;
  else if (!result)
    body = (
      <EmptyState title="No results yet">
        Compile some SQL to see results. Load a sample from the editor toolbar to start.
      </EmptyState>
    );
  else if (tab === "Overview") body = <OverviewTab result={result} />;
  else if (tab === "Summary") body = <SummaryTab result={result} run={run} />;
  else if (tab === "Verdicts") body = <VerdictsTab result={result} run={run} onSelect={onSelectVerdict} />;
  else if (tab === "Relationships")
    body = <RelationshipsTab result={result} onOverride={onOverride} request={request} />;
  else if (tab === "IR") body = <IRTab result={result} />;
  else body = <CodeTab result={result} />;

  return (
    <div className="flex h-full min-h-[24rem] flex-col">
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Result sections" />
      <div role="tabpanel" tabIndex={0} aria-label="Result details" className="min-h-0 flex-1 overflow-auto">
        {body}
      </div>
    </div>
  );
}
