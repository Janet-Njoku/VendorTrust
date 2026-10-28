import { vi } from "vitest";

/** Controllable stand-in for next/navigation, shared by every UI test. */
export const nav = {
  push: vi.fn(),
  replace: vi.fn(),
  pathname: "/",
  params: {} as Record<string, string>,
};

export function resetNav(params: Record<string, string> = {}, pathname = "/") {
  nav.push.mockReset();
  nav.replace.mockReset();
  nav.params = params;
  nav.pathname = pathname;
}
