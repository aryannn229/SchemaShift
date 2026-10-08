import { render, screen } from "@testing-library/react";

import App from "../src/App";

test("renders the title", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "SchemaShift" })).toBeInTheDocument();
});
