import { authConfigured, buildConfig, looksLikeSecretKey } from "./config";

function jwt(payload: object): string {
  const encode = (value: object) => btoa(JSON.stringify(value)).replace(/=+$/, "");
  return `${encode({ alg: "HS256" })}.${encode(payload)}.signature`;
}

describe("buildConfig", () => {
  it("defaults the API to localhost and needs no Supabase settings", () => {
    const cfg = buildConfig({});
    expect(cfg.apiBaseUrl).toBe("http://localhost:8000");
    expect(cfg.supabaseUrl).toBeNull();
    expect(cfg.demo).toBe(false);
    expect(cfg.problems).toEqual([]);
    expect(authConfigured(cfg)).toBe(false);
  });

  it("accepts a configured project and strips trailing slashes", () => {
    const cfg = buildConfig({
      NEXT_PUBLIC_API_BASE_URL: "https://api.example.com/",
      NEXT_PUBLIC_SUPABASE_URL: "https://abc.supabase.co/",
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_abc",
    });
    expect(cfg.apiBaseUrl).toBe("https://api.example.com");
    expect(cfg.supabaseUrl).toBe("https://abc.supabase.co");
    expect(cfg.supabaseKey).toBe("sb_publishable_abc");
    expect(authConfigured(cfg)).toBe(true);
  });

  it("ignores the template placeholder project URL", () => {
    const cfg = buildConfig({ NEXT_PUBLIC_SUPABASE_URL: "https://PROJECT_REF.supabase.co" });
    expect(cfg.supabaseUrl).toBeNull();
    expect(cfg.problems).toEqual([]);
  });

  it.each([
    "http://api.example.com",
    "ftp://api.example.com",
    "https://user:pw@api.example.com",
    "not a url",
  ])("rejects an unsafe API URL: %s", (url) => {
    const cfg = buildConfig({ NEXT_PUBLIC_API_BASE_URL: url });
    expect(cfg.problems.join(" ")).toContain("NEXT_PUBLIC_API_BASE_URL");
    expect(cfg.apiBaseUrl).toBe("http://localhost:8000");
  });

  it("allows plain http only for localhost", () => {
    expect(buildConfig({ NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:54321" }).supabaseUrl).toBe(
      "http://127.0.0.1:54321",
    );
    expect(buildConfig({ NEXT_PUBLIC_SUPABASE_URL: "http://abc.supabase.co" }).supabaseUrl).toBeNull();
  });

  it("turns demo mode on only for the exact value true", () => {
    expect(buildConfig({ NEXT_PUBLIC_DEMO_MODE: "true" }).demo).toBe(true);
    for (const value of ["1", "TRUE", "yes", "", undefined]) {
      expect(buildConfig({ NEXT_PUBLIC_DEMO_MODE: value }).demo).toBe(false);
    }
  });
});

describe("secret keys never reach the browser", () => {
  it("refuses a Supabase secret key", () => {
    const secret = "sb_secret_abcdefghijklmnop";
    const cfg = buildConfig({ NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: secret });
    expect(cfg.supabaseKey).toBeNull();
    expect(cfg.problems.join(" ")).toMatch(/secret or service-role key/);
    expect(JSON.stringify(cfg)).not.toContain(secret);
  });

  it("refuses a legacy service_role JWT but accepts an anon JWT", () => {
    const service = jwt({ role: "service_role" });
    const anon = jwt({ role: "anon" });
    expect(looksLikeSecretKey(service)).toBe(true);
    expect(looksLikeSecretKey(anon)).toBe(false);
    const cfg = buildConfig({ NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: service });
    expect(cfg.supabaseKey).toBeNull();
    expect(JSON.stringify(cfg)).not.toContain(service);
    expect(buildConfig({ NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: anon }).supabaseKey).toBe(anon);
  });

  it("does not mistake an ordinary key for a secret", () => {
    expect(looksLikeSecretKey("sb_publishable_xyz")).toBe(false);
    expect(looksLikeSecretKey("a.b.c")).toBe(false);
  });
});
