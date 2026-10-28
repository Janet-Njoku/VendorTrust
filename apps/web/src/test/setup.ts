import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

import { nav } from "./router";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => nav.pathname,
  useParams: () => nav.params,
  useSearchParams: () => new URLSearchParams(),
}));

/**
 * React warns "An update to X inside a test was not wrapped in act(...)" when a state update lands
 * outside Testing Library's act: it means a test is asserting before the screen has settled. That is a
 * test bug (a race), so the warning is turned into a failure instead of stderr noise. Nothing is
 * silenced: every other console.error still prints.
 */
const ACT_WARNING = /not wrapped in act\(|An update to .* inside a test/;
let actWarnings: string[] = [];
let originalError: typeof console.error;

beforeEach(() => {
  actWarnings = [];
  originalError = console.error;
  console.error = (...args: unknown[]) => {
    const text = args.map(String).join(" ");
    if (ACT_WARNING.test(text)) actWarnings.push(text.split("\n")[0] ?? text);
    originalError(...args);
  };
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  console.error = originalError;
  expect(actWarnings, "a state update happened outside act(...): wait for the screen to settle").toEqual([]);
});
