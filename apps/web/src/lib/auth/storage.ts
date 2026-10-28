/**
 * Browser storage for the session and the chosen organization. The session holds the user's own
 * access and refresh tokens (as the official Supabase client also does in localStorage); a content
 * security policy and not rendering untrusted HTML are what protect them. Nothing else is stored.
 */

import type { Session } from "./supabase";

const SESSION_KEY = "vt.session";
const ORG_KEY = "vt.orgId";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function loadSession(): Session | null {
  try {
    const raw = storage()?.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const s = parsed as Record<string, unknown>;
    if (
      typeof s.accessToken === "string" &&
      typeof s.refreshToken === "string" &&
      typeof s.expiresAt === "number" &&
      typeof s.userId === "string"
    ) {
      return {
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        expiresAt: s.expiresAt,
        userId: s.userId,
        email: typeof s.email === "string" ? s.email : null,
      };
    }
  } catch {
    return null;
  }
  return null;
}

export function saveSession(session: Session | null): void {
  try {
    if (session) storage()?.setItem(SESSION_KEY, JSON.stringify(session));
    else storage()?.removeItem(SESSION_KEY);
  } catch {
    // storage unavailable (private mode): the session just lives in memory
  }
}

export function isValidOrgId(value: string): boolean {
  return UUID.test(value.trim());
}

export function loadOrgId(): string | null {
  const value = storage()?.getItem(ORG_KEY) ?? null;
  return value && isValidOrgId(value) ? value : null;
}

export function saveOrgId(value: string | null): void {
  try {
    if (value && isValidOrgId(value)) storage()?.setItem(ORG_KEY, value.trim().toLowerCase());
    else storage()?.removeItem(ORG_KEY);
  } catch {
    // ignore
  }
}
