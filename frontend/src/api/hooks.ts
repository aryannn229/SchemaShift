import { useMutation, useQuery } from "@tanstack/react-query";

import { client, unwrap, type CompileRequest, type DiffRequest, type RunDetail, type SimulateRequest } from "./client";

export function useSamples() {
  return useQuery({
    queryKey: ["samples"],
    queryFn: async () => unwrap(await client.GET("/api/v1/samples")),
  });
}

export async function fetchSample(name: string) {
  return unwrap(await client.GET("/api/v1/samples/{name}", { params: { path: { name } } }));
}

export function useCompile() {
  return useMutation({
    mutationFn: async (body: CompileRequest) =>
      unwrap(await client.POST("/api/v1/compile", { body })),
  });
}

export function useSimulate() {
  return useMutation({
    mutationFn: async (body: SimulateRequest) =>
      unwrap(await client.POST("/api/v1/simulate", { body })),
  });
}

export function useDiff() {
  return useMutation({
    mutationFn: async (body: DiffRequest) => unwrap(await client.POST("/api/v1/diff", { body })),
  });
}

export function useStartRun() {
  return useMutation({
    mutationFn: async (body: CompileRequest & { verify: boolean; seed?: number }) =>
      unwrap(await client.POST("/api/v1/runs", { body })),
  });
}

/** Polls until the run leaves pending/running. */
export function useRun(id: string | undefined) {
  return useQuery({
    queryKey: ["run", id],
    enabled: !!id,
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: id! } } })),
    refetchInterval: (q) => {
      const s = (q.state.data as RunDetail | undefined)?.status;
      return s === "pending" || s === "running" ? 1000 : false;
    },
  });
}

export function useRuns(page: number) {
  return useQuery({
    queryKey: ["runs", page],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/runs", { params: { query: { page, page_size: 20 } } }),
      ),
  });
}

export function useMetrics() {
  return useQuery({
    queryKey: ["metrics"],
    queryFn: async () => unwrap(await client.GET("/api/v1/metrics")),
  });
}

/** Re-run the minimal counterexample of one verdict on fresh sandboxes. */
export function useReplayCounterexample(runId: string | undefined) {
  return useMutation({
    mutationFn: async (verdictId: string) =>
      unwrap(
        await client.POST("/api/v1/runs/{run_id}/counterexamples/replay", {
          params: { path: { run_id: runId! }, query: { verdict_id: verdictId } },
        }),
      ),
  });
}
