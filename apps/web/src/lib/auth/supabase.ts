/**
 * Supabase Auth over its public REST API (GoTrue), using only the project URL and the publishable
 * key. No secret key, no service-role key, no database credential is involved. The backend verifies
 * the resulting access token against the project's JWKS; this module never decides authorization.
 */

export interface AuthSettings {
  url: string;
  key: string;
  fetchImpl?: typeof fetch;
}

export interface Session {
  accessToken: string;
  refreshToken: string;
  /** Expiry as epoch seconds. */
  expiresAt: number;
  userId: string;
  email: string | null;
}

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthError";
  }
}

interface TokenResponse {
  access_token?: unknown;
  refresh_token?: unknown;
  expires_in?: unknown;
  expires_at?: unknown;
  user?: { id?: unknown; email?: unknown };
}

function headers(settings: AuthSettings, extra: Record<string, string> = {}) {
  return { apikey: settings.key, "Content-Type": "application/json", ...extra };
}

export async function call(
  settings: AuthSettings,
  method: "GET" | "POST",
  path: string,
  body?: unknown,
  extra?: Record<string, string>,
): Promise<{ status: number; json: Record<string, unknown> }> {
  let response: Response;
  try {
    response = await (settings.fetchImpl ?? fetch)(`${settings.url}/auth/v1${path}`, {
      method,
      headers: headers(settings, extra),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new AuthError("Could not reach the authentication service.");
  }
  let json: Record<string, unknown> = {};
  try {
    const parsed: unknown = await response.json();
    if (typeof parsed === "object" && parsed !== null) json = parsed as Record<string, unknown>;
  } catch {
    json = {};
  }
  return { status: response.status, json };
}

export function toSession(json: TokenResponse, now: number): Session {
  const { access_token, refresh_token, expires_in, expires_at, user } = json;
  if (
    typeof access_token !== "string" ||
    typeof refresh_token !== "string" ||
    typeof user?.id !== "string"
  ) {
    throw new AuthError("The authentication service returned an unexpected response.");
  }
  const expiry =
    typeof expires_at === "number"
      ? expires_at
      : now + (typeof expires_in === "number" ? expires_in : 3600);
  return {
    accessToken: access_token,
    refreshToken: refresh_token,
    expiresAt: expiry,
    userId: user.id,
    email: typeof user.email === "string" ? user.email : null,
  };
}

/** Error text is fixed and generic: it never reveals which part of a login was wrong. */
function failure(status: number, json: Record<string, unknown>): AuthError {
  const code = String(json.error_code ?? json.error ?? "");
  if (code === "email_not_confirmed") return new AuthError("Please confirm your email address first.");
  if (status === 400 || status === 401 || code === "invalid_credentials") {
    return new AuthError("Invalid email or password.");
  }
  if (status === 429) return new AuthError("Too many attempts. Please wait a moment.");
  return new AuthError(`Authentication failed (${status}).`);
}

export async function signInWithPassword(
  settings: AuthSettings,
  email: string,
  password: string,
  now = Math.floor(Date.now() / 1000),
): Promise<Session> {
  const { status, json } = await call(settings, "POST", "/token?grant_type=password", { email, password });
  if (status !== 200) throw failure(status, json);
  return toSession(json as TokenResponse, now);
}

export async function refreshSession(
  settings: AuthSettings,
  refreshToken: string,
  now = Math.floor(Date.now() / 1000),
): Promise<Session> {
  const { status, json } = await call(settings, "POST", "/token?grant_type=refresh_token", {
    refresh_token: refreshToken,
  });
  if (status !== 200) throw new AuthError("Your session has expired. Please sign in again.");
  return toSession(json as TokenResponse, now);
}

/** Registration needs email confirmation; access still needs a membership an admin grants. */
export async function signUp(settings: AuthSettings, email: string, password: string): Promise<void> {
  const { status, json } = await call(settings, "POST", "/signup", { email, password });
  if (status >= 400) {
    const code = String(json.error_code ?? "");
    if (code === "weak_password") throw new AuthError("That password is too weak.");
    if (status === 429) throw new AuthError("Too many attempts. Please wait a moment.");
    throw new AuthError("Sign-up failed. Check the details and try again.");
  }
}

export async function signOut(settings: AuthSettings, accessToken: string): Promise<void> {
  try {
    await call(settings, "POST", "/logout", {}, { Authorization: `Bearer ${accessToken}` });
  } catch {
    // best effort: the local session is cleared regardless
  }
}
