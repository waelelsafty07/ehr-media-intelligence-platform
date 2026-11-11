import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => vi.restoreAllMocks());

it("shows connected state when the backend health call succeeds", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ version: "0.1.0" }) }));
  render(<App />);
  expect(await screen.findByText(/Backend connected \(v0\.1\.0\)/)).toBeInTheDocument();
});

it("shows an error state when the backend is unreachable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
  render(<App />);
  expect(await screen.findByText(/Backend unreachable/)).toBeInTheDocument();
});
