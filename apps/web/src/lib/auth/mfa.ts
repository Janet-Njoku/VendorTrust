/**
 * Supabase multi-factor authentication (TOTP) over its public REST API. The API refuses internal roles
 * unless their access token has assurance level aal2, which only a completed challenge produces. All
 * calls use the signed-in user's own access token and the publishable key; nothing here is privileged.
 *
 *   enroll   -> POST /auth/v1/factors                      (returns the secret and otpauth URI)
 *   verify   -> POST /auth/v1/factors/{id}/challenge, then /verify (returns a new aal2 session)
 *   status   -> GET  /auth/v1/user                         (lists factors and whether they are verified)
 */

import { AuthError, type AuthSettings, type Session, call, toSession } from "./supabase";

export interface Factor {
  id: string;
  friendlyName: string;
  verified: boolean;
}

export interface Enrollment {
  factorId: string;
  /** Base32 secret to type into an authenticator app that cannot scan. */
  secret: string;
  /** otpauth:// URI that authenticator apps understand. */
  uri: string;
  /** A QR code as an image data URL, only if Supabase returned an image type we accept. */
  qrCode: string | null;
}

const SAFE_QR = /^data:image\/(svg\+xml|png)[;,]/;

/** Only an image data URL is ever put in an <img>; anything else is dropped. */
export function safeQrCode(value: unknown): string | null {
  return typeof value === "string" && SAFE_QR.test(value) && value.length < 100_000 ? value : null;
}

function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

function failure(status: number, fallback: string): AuthError {
  if (status === 429) return new AuthError("Too many attempts. Please wait a moment and try again.");
  return new AuthError(fallback);
}

export async function listFactors(settings: AuthSettings, token: string): Promise<Factor[]> {
  const { status, json } = await call(settings, "GET", "/user", undefined, bearer(token));
  if (status !== 200) throw failure(status, "Could not check your authenticator setup.");
  const factors = Array.isArray(json.factors) ? json.factors : [];
  return factors
    .filter((f): f is Record<string, unknown> => typeof f === "object" && f !== null)
    .filter((f) => f.factor_type === "totp" && typeof f.id === "string")
    .map((f) => ({
      id: String(f.id),
      friendlyName: typeof f.friendly_name === "string" ? f.friendly_name : "Authenticator",
      verified: f.status === "verified",
    }));
}

export async function enrollTotp(
  settings: AuthSettings,
  token: string,
  friendlyName: string,
): Promise<Enrollment> {
  const { status, json } = await call(
    settings,
    "POST",
    "/factors",
    { factor_type: "totp", friendly_name: friendlyName, issuer: "VendorTrust" },
    bearer(token),
  );
  const totp = (json.totp ?? {}) as Record<string, unknown>;
  if (status !== 200 || typeof json.id !== "string" || typeof totp.secret !== "string") {
    throw failure(status, "Could not start authenticator setup.");
  }
  return {
    factorId: json.id,
    secret: totp.secret,
    uri: typeof totp.uri === "string" ? totp.uri : "",
    qrCode: safeQrCode(totp.qr_code),
  };
}

/** Challenge the factor, then verify the six-digit code. Returns the upgraded (aal2) session. */
export async function verifyTotp(
  settings: AuthSettings,
  token: string,
  factorId: string,
  code: string,
  now = Math.floor(Date.now() / 1000),
): Promise<Session> {
  const challenge = await call(settings, "POST", `/factors/${encodeURIComponent(factorId)}/challenge`, {}, bearer(token));
  if (challenge.status !== 200 || typeof challenge.json.id !== "string") {
    throw failure(challenge.status, "Could not start the verification. Please try again.");
  }
  const verified = await call(
    settings,
    "POST",
    `/factors/${encodeURIComponent(factorId)}/verify`,
    { challenge_id: challenge.json.id, code },
    bearer(token),
  );
  if (verified.status === 422 || verified.status === 400) {
    throw new AuthError("That code was not accepted. Check the code and try again.");
  }
  if (verified.status !== 200) throw failure(verified.status, "Verification failed. Please try again.");
  return toSession(verified.json, now);
}
