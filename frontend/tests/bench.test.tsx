import { render, screen } from "@testing-library/react";

import { BenchmarkView, parseBench } from "../src/components/BenchmarkView";

const lat = (p50: number) => ({ p50_ms: p50, p95_ms: p50 * 2, p99_ms: p50 * 3, throughput_qps: 1000 / p50 });
const storage = {
  postgres_data_bytes: 1048576,
  postgres_index_bytes: 0,
  mongo_data_bytes: 2097152,
  mongo_storage_bytes: 1048576,
  mongo_index_bytes: 0,
};
const results = {
  meta: { postgres: "PostgreSQL 16", scale_rows_per_table: 1000 },
  findings: ["q1: all_embed beat the chosen plan"],
  plans: [
    {
      name: "chosen",
      correctness: 1,
      load_seconds: 1.5,
      storage,
      queries: [{ query_id: "q1", sql: "SELECT 1", status: "MATCH", postgres: lat(1), mongo: lat(4) }],
    },
    {
      name: "all_embed",
      correctness: 0.5,
      load_seconds: 2,
      storage,
      queries: [{ query_id: "q1", sql: "SELECT 1", status: "MISMATCH", mongo: null }],
    },
  ],
};

describe("benchmark results view", () => {
  it("accepts a results file and rejects other JSON", () => {
    expect(parseBench(results)).not.toBeNull();
    expect(parseBench({ hello: 1 })).toBeNull();
    expect(parseBench(null)).toBeNull();
  });

  it("separates correctness from performance and shows the findings", () => {
    const data = parseBench(results);
    if (!data) throw new Error("not a results file");
    render(<BenchmarkView data={data} />);
    expect(screen.getByText("1. Correctness")).toBeTruthy();
    expect(screen.getByText("2. Performance")).toBeTruthy();
    expect(screen.getByText("50% correct")).toBeTruthy();
    expect(screen.getByText("q1: MISMATCH")).toBeTruthy();
    expect(screen.getByText("q1: all_embed beat the chosen plan")).toBeTruthy();
    expect(screen.getAllByText(/4\.00 ms/).length).toBeGreaterThan(0);
  });
});
