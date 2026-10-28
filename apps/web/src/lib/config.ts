/**
 * Public browser configuration. Only NEXT_PUBLIC_ values exist here, and anything that looks like
 * a secret is refused: the Supabase secret key (sb_secret_...) or a legacy service_role key must
 * never reach the browser, so a misconfigured deployment fails loudly instead of leaking it.
 */

export interface RawEnv {
  NEXT_PUBLIC_API_BASE_URL?: string;
  NEXT_PUBLIC_SUPABASE_URL?: string;
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?: string;
  NEXT_PUBLIC_DEMO_MODE?: string;
}

export interface PublicConfig {
  apiBaseUrl: string;
  supabaseUrl: string | null;
  supabaseKey: string | null;
  /** Explicit demo mode: sample data, no network calls to any backend. */
  demo: boolean;
  /** Configuration problems to show the user (never contains a key). */
  problems: string[];
}

const DEFAULT_API = "http://localhost:8000";

function validUrl(value: string, allowLocalHttp: boolean): string | null {
  try {
    const url = new URL(value);
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    if (url.protocol === "https:" || (allowLocalHttp && local && url.protocol === "http:")) {
      if (url.username || url.password) return null;
      return value.replace(/\/+$/, "");
    }
  } catch {
    return null;
  }
  return null;
}

export function looksLikeSecretKey(key: string): boolean {
  if (key.startsWith("sb_secret_")) return true;
  const parts = key.split(".");
  if (parts.length === 3 && parts[1]) {
    try {
      const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
      return payload?.role === "service_role";
    } catch {
      return false;
    }
  }
  return false;
}

export function buildConfig(raw: RawEnv): PublicConfig {
  const problems: string[] = [];
  const demo = raw.NEXT_PUBLIC_DEMO_MODE === "true";

  const api = validUrl((raw.NEXT_PUBLIC_API_BASE_URL ?? "").trim() || DEFAULT_API, true);
  if (!api) problems.push("NEXT_PUBLIC_API_BASE_URL must be an https URL (http only for localhost).");

  let supabaseUrl: string | null = null;
  const rawUrl = (raw.NEXT_PUBLIC_SUPABASE_URL ?? "").trim();
  if (rawUrl && !rawUrl.includes("PROJECT_REF")) {
    supabaseUrl = validUrl(rawUrl, true);
    if (!supabaseUrl) problems.push("NEXT_PUBLIC_SUPABASE_URL must be an https URL.");
  }

  let supabaseKey: string | null = null;
  const rawKey = (raw.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "").trim();
  if (rawKey) {
    if (looksLikeSecretKey(rawKey)) {
      problems.push(
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY holds a secret or service-role key. Refusing to use it: " +
          "only the publishable key may be public.",
      );
    } else {
      supabaseKey = rawKey;
    }
  }

  return {
    apiBaseUrl: api ?? DEFAULT_API,
    supabaseUrl,
    supabaseKey,
    demo,
    problems,
  };
}

/** Next.js inlines NEXT_PUBLIC_ variables only when they are read by these exact names. */
export const config: PublicConfig = buildConfig({
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  NEXT_PUBLIC_DEMO_MODE: process.env.NEXT_PUBLIC_DEMO_MODE,
});

export function authConfigured(cfg: PublicConfig): boolean {
  return cfg.supabaseUrl !== null && cfg.supabaseKey !== null;
}
