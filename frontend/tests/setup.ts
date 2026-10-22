import "@testing-library/jest-dom/vitest";

class RO {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver ??= RO as unknown as typeof ResizeObserver;
