import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import App from "../src/App";

vi.mock("@monaco-editor/react", () => ({ default: () => <div data-testid="monaco" /> }));

function renderAt(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("renders the title and nav", () => {
  renderAt("/about");
  expect(screen.getByRole("heading", { name: "SchemaShift" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /how schemashift works/i })).toBeInTheDocument();
  expect(screen.getByRole("list", { name: "Pipeline" }).children).toHaveLength(7);
});

test("home shows empty result state and disabled compile", () => {
  renderAt("/");
  expect(screen.getByText(/compile some sql/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Compile" })).toBeDisabled();
  expect(screen.getAllByRole("tab")).toHaveLength(6);
});
