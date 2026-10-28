import { enrollTotp, listFactors, safeQrCode, verifyTotp } from "./mfa";
import { AuthError } from "./supabase";

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

function server(responses: { status?: number; body?: unknown }[]) {
  const calls: Call[] = [];
  const queue = [...responses];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push({
      url,
      method: init.method ?? "GET",
      headers: init.headers as Record<string, string>,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
    });
    const next = queue.shift() ?? { status: 500 };
    return new Response(JSON.stringify(next.body ?? {}), { status: next.status ?? 200 });
  }) as unknown as typeof fetch;
  return { settings: { url: "https://abc.supabase.co", key: "sb_publishable_test", fetchImpl }, calls };
}

const TOKENS = {
  access_token: "aal2-token",
  refresh_token: "refresh-2",
  expires_in: 3600,
  user: { id: "11111111-1111-4111-8111-111111111111", email: "ana@example.invalid" },
};

describe("MFA over the Supabase REST API", () => {
  it("lists TOTP factors with the user's own token and the public key", async () => {
    const { settings, calls } = server([
      {
        body: {
          factors: [
            { id: "f1", factor_type: "totp", friendly_name: "phone", status: "verified" },
            { id: "f2", factor_type: "totp", status: "unverified" },
            { id: "f3", factor_type: "phone", status: "verified" },
            "garbage",
          ],
        },
      },
    ]);
    const factors = await listFactors(settings, "access-1");
    expect(factors).toEqual([
      { id: "f1", friendlyName: "phone", verified: true },
      { id: "f2", friendlyName: "Authenticator", verified: false },
    ]);
    expect(calls[0]).toMatchObject({ method: "GET", url: "https://abc.supabase.co/auth/v1/user" });
    expect(calls[0]?.headers).toMatchObject({ apikey: "sb_publishable_test", Authorization: "Bearer access-1" });
  });

  it("enrolls a TOTP factor and returns the secret and setup link", async () => {
    const { settings, calls } = server([
      { body: { id: "f1", totp: { secret: "ABCDEF", uri: "otpauth://totp/x?secret=ABCDEF", qr_code: "data:image/svg+xml;utf8,<svg/>" } } },
    ]);
    const enrollment = await enrollTotp(settings, "access-1", "My phone");
    expect(enrollment).toEqual({
      factorId: "f1",
      secret: "ABCDEF",
      uri: "otpauth://totp/x?secret=ABCDEF",
      qrCode: "data:image/svg+xml;utf8,<svg/>",
    });
    expect(calls[0]).toMatchObject({ method: "POST", url: "https://abc.supabase.co/auth/v1/factors" });
    expect(calls[0]?.body).toEqual({ factor_type: "totp", friendly_name: "My phone", issuer: "VendorTrust" });
  });

  it("challenges and then verifies, returning the upgraded session", async () => {
    const { settings, calls } = server([{ body: { id: "challenge-1" } }, { body: TOKENS }]);
    const session = await verifyTotp(settings, "access-1", "f1", "123456", 1000);
    expect(calls.map((c) => c.url)).toEqual([
      "https://abc.supabase.co/auth/v1/factors/f1/challenge",
      "https://abc.supabase.co/auth/v1/factors/f1/verify",
    ]);
    expect(calls[1]?.body).toEqual({ challenge_id: "challenge-1", code: "123456" });
    expect(calls.every((c) => c.headers.Authorization === "Bearer access-1")).toBe(true);
    expect(session).toMatchObject({ accessToken: "aal2-token", refreshToken: "refresh-2", expiresAt: 4600 });
  });

  it("gives a plain message for a wrong code and never echoes the code", async () => {
    const { settings } = server([{ body: { id: "c" } }, { status: 422, body: { error_code: "mfa_verification_failed" } }]);
    const error = await verifyTotp(settings, "t", "f1", "000000").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AuthError);
    expect((error as Error).message).toBe("That code was not accepted. Check the code and try again.");
    expect((error as Error).message).not.toContain("000000");
  });

  it.each([
    [() => server([{ status: 500 }]), "list", /authenticator setup/],
  ])("handles server errors (%#)", async (make) => {
    const { settings } = make();
    await expect(listFactors(settings, "t")).rejects.toThrow(/authenticator setup/);
  });

  it("handles failures and rate limits at each step", async () => {
    await expect(enrollTotp(server([{ status: 500 }]).settings, "t", "n")).rejects.toThrow(/Could not start authenticator setup/);
    await expect(enrollTotp(server([{ status: 200, body: { id: "x" } }]).settings, "t", "n")).rejects.toThrow(/Could not start/);
    await expect(verifyTotp(server([{ status: 500 }]).settings, "t", "f", "123456")).rejects.toThrow(/Could not start the verification/);
    await expect(verifyTotp(server([{ status: 429 }]).settings, "t", "f", "123456")).rejects.toThrow(/Too many attempts/);
    await expect(verifyTotp(server([{ body: { id: "c" } }, { status: 500 }]).settings, "t", "f", "123456")).rejects.toThrow(/Verification failed/);
    await expect(verifyTotp(server([{ body: { id: "c" } }, { status: 200, body: { access_token: "x" } }]).settings, "t", "f", "123456")).rejects.toThrow(/unexpected response/);
  });

  it("encodes the factor id in the path", async () => {
    const { settings, calls } = server([{ body: { id: "c" } }, { body: TOKENS }]);
    await verifyTotp(settings, "t", "a/b?c", "123456");
    expect(calls[0]?.url).toBe("https://abc.supabase.co/auth/v1/factors/a%2Fb%3Fc/challenge");
  });
});

describe("safeQrCode", () => {
  it("accepts only image data URLs", () => {
    expect(safeQrCode("data:image/svg+xml;utf8,<svg/>")).not.toBeNull();
    expect(safeQrCode("data:image/png;base64,AAAA")).not.toBeNull();
    for (const bad of ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "https://evil.example/qr.svg", "", null, 5, undefined]) {
      expect(safeQrCode(bad)).toBeNull();
    }
    expect(safeQrCode(`data:image/svg+xml;utf8,${"x".repeat(100_001)}`)).toBeNull();
  });
});
