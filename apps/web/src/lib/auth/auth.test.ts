import { AuthStore } from "./store";
import { isValidOrgId, loadSession, saveSession } from "./storage";
import { AuthError, refreshSession, signInWithPassword, signUp } from "./supabase";

const KEY = "sb_publishable_test";
const URL = "https://abc.supabase.co";

function tokenResponse(overrides: Record<string, unknown> = {}) {
  return {
    access_token: "access-1",
    refresh_token: "refresh-1",
    expires_in: 3600,
    user: { id: "11111111-1111-4111-8111-111111111111", email: "ana@example.invalid" },
    ...overrides,
  };
}

function settings(status: number, body: unknown) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  return { settings: { url: URL, key: KEY, fetchImpl }, calls };
}

describe("Supabase password auth (public key only)", () => {
  it("signs in with the documented request and builds a session", async () => {
    const { settings: s, calls } = settings(200, tokenResponse());
    const session = await signInWithPassword(s, "ana@example.invalid", "correct horse", 1000);
    expect(calls[0]?.url).toBe(`${URL}/auth/v1/token?grant_type=password`);
    expect(calls[0]?.init.method).toBe("POST");
    const headers = calls[0]?.init.headers as Record<string, string>;
    expect(headers.apikey).toBe(KEY);
    expect(headers.Authorization).toBeUndefined();
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ email: "ana@example.invalid", password: "correct horse" });
    expect(session).toMatchObject({ accessToken: "access-1", refreshToken: "refresh-1", expiresAt: 4600, email: "ana@example.invalid" });
  });

  it("gives one generic message for every bad login", async () => {
    for (const body of [{ error_code: "invalid_credentials" }, { error: "invalid_grant" }, {}]) {
      const { settings: s } = settings(400, body);
      await expect(signInWithPassword(s, "a@b.c", "x")).rejects.toThrow("Invalid email or password.");
    }
  });

  it("asks for email confirmation and handles rate limits and outages", async () => {
    await expect(signInWithPassword(settings(400, { error_code: "email_not_confirmed" }).settings, "a@b.c", "x")).rejects.toThrow(/confirm your email/);
    await expect(signInWithPassword(settings(429, {}).settings, "a@b.c", "x")).rejects.toThrow(/Too many attempts/);
    const down = { url: URL, key: KEY, fetchImpl: (async () => { throw new TypeError("offline"); }) as unknown as typeof fetch };
    await expect(signInWithPassword(down, "a@b.c", "x")).rejects.toBeInstanceOf(AuthError);
  });

  it("rejects an unexpected success body", async () => {
    await expect(signInWithPassword(settings(200, { access_token: "x" }).settings, "a@b.c", "x")).rejects.toThrow(/unexpected response/);
  });

  it("refreshes with the refresh token", async () => {
    const { settings: s, calls } = settings(200, tokenResponse({ access_token: "access-2" }));
    const session = await refreshSession(s, "refresh-1", 0);
    expect(calls[0]?.url).toBe(`${URL}/auth/v1/token?grant_type=refresh_token`);
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ refresh_token: "refresh-1" });
    expect(session.accessToken).toBe("access-2");
  });

  it("registers without granting any access", async () => {
    const { settings: s, calls } = settings(200, {});
    await signUp(s, "new@example.invalid", "a long password");
    expect(calls[0]?.url).toBe(`${URL}/auth/v1/signup`);
    await expect(signUp(settings(422, { error_code: "weak_password" }).settings, "a@b.c", "x")).rejects.toThrow(/too weak/);
  });
});

describe("AuthStore", () => {
  const session = {
    accessToken: "old",
    refreshToken: "refresh-1",
    expiresAt: 10_000,
    userId: "11111111-1111-4111-8111-111111111111",
    email: null,
  };

  it("returns the stored token while it is fresh, without a network call", async () => {
    saveSession(session);
    const { settings: s, calls } = settings(200, tokenResponse());
    const store = new AuthStore(s, () => 5_000);
    expect(await store.getToken()).toBe("old");
    expect(calls).toHaveLength(0);
  });

  it("refreshes once when the token is about to expire, even for concurrent callers", async () => {
    saveSession(session);
    const { settings: s, calls } = settings(200, tokenResponse({ access_token: "new" }));
    const store = new AuthStore(s, () => 9_990);
    const [a, b] = await Promise.all([store.getToken(), store.getToken()]);
    expect([a, b]).toEqual(["new", "new"]);
    expect(calls).toHaveLength(1);
    expect(loadSession()?.accessToken).toBe("new");
  });

  it("signs out when the refresh fails", async () => {
    saveSession(session);
    const { settings: s } = settings(400, { error: "invalid_grant" });
    const store = new AuthStore(s, () => 9_990);
    expect(await store.getToken()).toBeNull();
    expect(loadSession()).toBeNull();
  });

  it("has no token when signed out or when auth is not configured", async () => {
    expect(await new AuthStore(null).getToken()).toBeNull();
    saveSession({ ...session, expiresAt: 1 });
    expect(await new AuthStore(null, () => 5_000).getToken()).toBeNull();
  });

  it("clears the session on sign out", async () => {
    saveSession(session);
    const { settings: s } = settings(204, {});
    const store = new AuthStore(s, () => 5_000);
    await store.signOut();
    expect(loadSession()).toBeNull();
    expect(store.getSnapshot()).toBeNull();
  });

  it("ignores a corrupt stored session", () => {
    window.localStorage.setItem("vt.session", "{not json");
    expect(loadSession()).toBeNull();
    window.localStorage.setItem("vt.session", JSON.stringify({ accessToken: 5 }));
    expect(loadSession()).toBeNull();
  });
});

describe("AuthStore MFA", () => {
  const session = {
    accessToken: "aal1-token",
    refreshToken: "refresh-1",
    expiresAt: 10_000,
    userId: "11111111-1111-4111-8111-111111111111",
    email: null,
  };

  it("replaces the session with the upgraded one after a verified code", async () => {
    saveSession(session);
    const responses = [{ id: "challenge-1" }, tokenResponse({ access_token: "aal2-token" })];
    const calls: string[] = [];
    const fetchImpl = (async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify(responses.shift()), { status: 200 });
    }) as unknown as typeof fetch;
    const store = new AuthStore({ url: URL, key: KEY, fetchImpl }, () => 5_000);
    await store.mfaVerify("factor-1", "123456");
    expect(store.getSnapshot()?.accessToken).toBe("aal2-token");
    expect(loadSession()?.accessToken).toBe("aal2-token");
    expect(calls).toHaveLength(2);
  });

  it("keeps the current session when the code is wrong", async () => {
    saveSession(session);
    const { settings: s } = settings(422, { error_code: "mfa_verification_failed" });
    const store = new AuthStore(s, () => 5_000);
    await expect(store.mfaVerify("factor-1", "000000")).rejects.toThrow();
    expect(store.getSnapshot()?.accessToken).toBe("aal1-token");
  });

  it("needs a signed-in user and a configured deployment", async () => {
    await expect(new AuthStore({ url: URL, key: KEY }).mfaFactors()).rejects.toThrow(/expired/);
    await expect(new AuthStore(null).mfaEnroll()).rejects.toThrow(/not configured/);
  });
});

describe("organization id", () => {
  it("accepts only UUIDs", () => {
    expect(isValidOrgId("11111111-1111-4111-8111-111111111111")).toBe(true);
    expect(isValidOrgId("../etc/passwd")).toBe(false);
    expect(isValidOrgId("")).toBe(false);
  });
});
