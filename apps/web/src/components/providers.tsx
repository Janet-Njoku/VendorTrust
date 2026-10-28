"use client";

import { createContext, useContext, useMemo, useSyncExternalStore } from "react";

import { type ApiClient, createHttpClient } from "@/lib/api/client";
import type { Enrollment, Factor } from "@/lib/auth/mfa";
import { AuthStore, orgStore } from "@/lib/auth/store";
import type { Session } from "@/lib/auth/supabase";
import { authConfigured, config } from "@/lib/config";
import { createDemoClient } from "@/lib/demo/client";

export interface AuthState {
  /** False until the browser has hydrated (storage is not readable before). */
  ready: boolean;
  session: Session | null;
  demo: boolean;
  configured: boolean;
  problems: string[];
  signIn(email: string, password: string): Promise<void>;
  signUp(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  /** Supabase TOTP multi-factor authentication for the signed-in user. */
  mfa: {
    factors(): Promise<Factor[]>;
    enroll(): Promise<Enrollment>;
    verify(factorId: string, code: string): Promise<void>;
  };
  orgId: string | null;
  setOrgId(value: string | null): boolean;
}

const AuthContext = createContext<AuthState | null>(null);
const ApiContext = createContext<ApiClient | null>(null);

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <Providers>");
  return value;
}

export function useApi(): ApiClient {
  const value = useContext(ApiContext);
  if (!value) throw new Error("useApi must be used inside <Providers>");
  return value;
}

const noopSubscribe = () => () => {};

export function useHydrated(): boolean {
  return useSyncExternalStore(noopSubscribe, () => true, () => false);
}

const authStore = new AuthStore(
  authConfigured(config) && config.supabaseUrl && config.supabaseKey
    ? { url: config.supabaseUrl, key: config.supabaseKey }
    : null,
);

const DEMO_SESSION: Session = {
  accessToken: "demo",
  refreshToken: "demo",
  expiresAt: Number.MAX_SAFE_INTEGER,
  userId: "00000000-0000-4000-8000-0000000000b1",
  email: "demo.analyst@example.invalid",
};

/** The real providers: Supabase auth plus the HTTP client, or the isolated demo client. */
export function Providers({ children }: { children: React.ReactNode }) {
  const hydrated = useHydrated();
  const stored = useSyncExternalStore(
    authStore.subscribe,
    authStore.getSnapshot,
    authStore.getServerSnapshot,
  );
  const orgId = useSyncExternalStore(
    orgStore.subscribe,
    orgStore.getSnapshot,
    orgStore.getServerSnapshot,
  );

  const auth = useMemo<AuthState>(
    () => ({
      ready: hydrated,
      session: config.demo ? DEMO_SESSION : stored,
      demo: config.demo,
      configured: config.demo || authConfigured(config),
      problems: config.problems,
      signIn: (email, password) => authStore.signIn(email, password),
      signUp: (email, password) => authStore.signUp(email, password),
      signOut: () => authStore.signOut(),
      mfa: {
        factors: () => authStore.mfaFactors(),
        enroll: () => authStore.mfaEnroll(),
        verify: (factorId, code) => authStore.mfaVerify(factorId, code),
      },
      orgId,
      setOrgId: (value) => orgStore.set(value),
    }),
    [hydrated, stored, orgId],
  );

  const client = useMemo<ApiClient>(
    () =>
      config.demo
        ? createDemoClient({ delayMs: 250 })
        : createHttpClient({
            baseUrl: config.apiBaseUrl,
            getToken: authStore.getToken,
            getOrgId: () => orgStore.getSnapshot(),
            onUnauthorized: () => void authStore.signOut(),
          }),
    [],
  );

  return (
    <AuthContext.Provider value={auth}>
      <ApiContext.Provider value={client}>{children}</ApiContext.Provider>
    </AuthContext.Provider>
  );
}

/** For tests: supply the client and auth state directly. */
export function TestProviders({
  client,
  auth,
  children,
}: {
  client: ApiClient;
  auth?: Partial<AuthState>;
  children: React.ReactNode;
}) {
  const value: AuthState = {
    ready: true,
    session: { ...DEMO_SESSION, email: "analyst@example.invalid" },
    demo: false,
    configured: true,
    problems: [],
    signIn: async () => {},
    signUp: async () => {},
    signOut: async () => {},
    mfa: {
      factors: async () => [],
      enroll: async () => {
        throw new Error("mfa.enroll is not stubbed in this test");
      },
      verify: async () => {
        throw new Error("mfa.verify is not stubbed in this test");
      },
    },
    orgId: null,
    setOrgId: () => true,
    ...auth,
  };
  return (
    <AuthContext.Provider value={value}>
      <ApiContext.Provider value={client}>{children}</ApiContext.Provider>
    </AuthContext.Provider>
  );
}
