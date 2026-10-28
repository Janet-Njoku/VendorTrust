import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { TestProviders, type AuthState } from "@/components/providers";
import type { ApiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { createDemoClient } from "@/lib/demo/client";

export function renderApp(
  ui: ReactElement,
  options: { client?: ApiClient; auth?: Partial<AuthState> } = {},
) {
  const client = options.client ?? createDemoClient({ seed: true });
  const view = render(
    <TestProviders client={client} auth={options.auth}>
      {ui}
    </TestProviders>,
  );
  return { client, ...view };
}

/** A client whose every call rejects with the given error: for loading, error and retry states. */
export function failingClient(error: ApiError, base: ApiClient = createDemoClient({ seed: false })): ApiClient {
  return new Proxy(base, {
    get: (_target, name) => (name in base ? () => Promise.reject(error) : undefined),
  });
}

/** Wrap a client so individual methods can be replaced for one test. */
export function withOverrides(base: ApiClient, overrides: Partial<ApiClient>): ApiClient {
  return { ...base, ...overrides };
}
