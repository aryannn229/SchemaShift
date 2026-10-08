import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export const client = createClient<paths>({ baseUrl: import.meta.env.VITE_API_URL ?? "" });

export type Schemas = components["schemas"];
export type CompileRequest = Schemas["CompileRequest"];
export type CompileResponse = Schemas["CompileResponse"];
export type RunDetail = Schemas["RunDetail"];
export type RunSummary = Schemas["RunSummary"];
export type Verdict = Schemas["Verdict"];
export type PlacementDecision = Schemas["PlacementDecision"];
export type GeneratedFile = Schemas["GeneratedFile"];
export type Status = "SAFE" | "CHANGED" | "BROKEN";

export class ApiError extends Error {}

/** Unwrap an openapi-fetch result or throw a readable error. */
export function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (res.data !== undefined) return res.data;
  const detail = (res.error as { detail?: unknown } | undefined)?.detail;
  const text =
    typeof detail === "string"
      ? detail
      : detail
        ? JSON.stringify(detail)
        : `request failed (${res.response.status})`;
  throw new ApiError(text);
}
