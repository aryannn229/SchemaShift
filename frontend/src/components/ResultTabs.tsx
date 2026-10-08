import { useState } from "react";

import type { CompileResponse, RunDetail, Verdict } from "../api/client";
import { CodeTab } from "./CodeTab";
import { IRTab } from "./IRTab";
import { RelationshipsTab } from "./RelationshipsTab";
import { Empty, ErrorBox, Loading } from "./States";
import { SummaryTab } from "./SummaryTab";
import { VerdictsTab } from "./VerdictsTab";
import { VerificationTab } from "./VerificationTab";

const TABS = ["Summary", "Verdicts", "Relationships", "IR", "Generated Code", "Verification"] as const;
type Tab = (typeof TABS)[number];

export function ResultTabs({
  result,
  loading,
  error,
  run,
  onSelectVerdict,
  onOverride,
}: {
  result?: CompileResponse | null;
  loading?: boolean;
  error?: string | null;
  run?: RunDetail;
  onSelectVerdict?: (v: Verdict) => void;
  onOverride?: (relationshipId: string, decision: "EMBED" | "REFERENCE") => void;
}) {
  const [tab, setTab] = useState<Tab>("Summary");

  let body: React.ReactNode;
  if (tab === "Verification") body = <VerificationTab run={run} />;
  else if (loading) body = <Loading />;
  else if (error) body = <ErrorBox message={error} />;
  else if (!result) body = <Empty>Compile some SQL to see results.</Empty>;
  else if (tab === "Summary") body = <SummaryTab result={result} />;
  else if (tab === "Verdicts") body = <VerdictsTab result={result} onSelect={onSelectVerdict} />;
  else if (tab === "Relationships")
    body = <RelationshipsTab result={result} onOverride={onOverride} />;
  else if (tab === "IR") body = <IRTab result={result} />;
  else body = <CodeTab result={result} />;

  return (
    <div className="flex h-full min-h-[24rem] flex-col">
      <div role="tablist" className="flex flex-wrap border-b dark:border-slate-700">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-indigo-600 font-semibold" : "text-slate-500"}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="flex-1 overflow-auto">
        {body}
      </div>
    </div>
  );
}
