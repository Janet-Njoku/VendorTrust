/**
 * Session and organization state as small external stores (read with useSyncExternalStore), so the
 * server render and the first client render agree and nothing reads localStorage during SSR.
 */

import { type Enrollment, type Factor, enrollTotp, listFactors, verifyTotp } from "./mfa";
import { isValidOrgId, loadOrgId, loadSession, saveOrgId, saveSession } from "./storage";
import {
  AuthError,
  type AuthSettings,
  type Session,
  refreshSession,
  signInWithPassword,
  signOut as remoteSignOut,
  signUp as remoteSignUp,
} from "./supabase";

type Listener = () => void;

/** Refresh the access token when it has less than this many seconds left. */
const REFRESH_MARGIN = 60;

export class AuthStore {
  private listeners = new Set<Listener>();
  private session: Session | null | undefined;
  private refreshing: Promise<Session | null> | null = null;

  constructor(
    private readonly settings: AuthSettings | null,
    private readonly now: () => number = () => Math.floor(Date.now() / 1000),
  ) {}

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = (): Session | null => {
    if (this.session === undefined) this.session = loadSession();
    return this.session;
  };
  getServerSnapshot = (): Session | null => null;

  private set(session: Session | null) {
    this.session = session;
    saveSession(session);
    this.listeners.forEach((l) => l());
  }

  private requireSettings(): AuthSettings {
    if (!this.settings) throw new AuthError("Sign-in is not configured for this deployment.");
    return this.settings;
  }

  async signIn(email: string, password: string): Promise<void> {
    this.set(await signInWithPassword(this.requireSettings(), email, password, this.now()));
  }

  async signUp(email: string, password: string): Promise<void> {
    await remoteSignUp(this.requireSettings(), email, password);
  }

  private async authed(): Promise<{ settings: AuthSettings; token: string }> {
    const settings = this.requireSettings();
    const token = await this.getToken();
    if (!token) throw new AuthError("Your session has expired. Please sign in again.");
    return { settings, token };
  }

  /** Factors the signed-in user has enrolled, and whether each is verified. */
  async mfaFactors(): Promise<Factor[]> {
    const { settings, token } = await this.authed();
    return listFactors(settings, token);
  }

  async mfaEnroll(): Promise<Enrollment> {
    const { settings, token } = await this.authed();
    return enrollTotp(settings, token, `VendorTrust ${new Date().toISOString().slice(0, 16)}`);
  }

  /** Verify a code; the response is a new session at aal2, which replaces the current one. */
  async mfaVerify(factorId: string, code: string): Promise<void> {
    const { settings, token } = await this.authed();
    this.set(await verifyTotp(settings, token, factorId, code, this.now()));
  }

  async signOut(): Promise<void> {
    const current = this.getSnapshot();
    this.set(null);
    if (current && this.settings) await remoteSignOut(this.settings, current.accessToken);
  }

  /** A valid access token (refreshing it if needed), or null when signed out. */
  getToken = async (): Promise<string | null> => {
    const current = this.getSnapshot();
    if (!current) return null;
    if (current.expiresAt - this.now() > REFRESH_MARGIN) return current.accessToken;
    if (!this.settings) return null;
    this.refreshing ??= refreshSession(this.settings, current.refreshToken, this.now())
      .then((fresh) => {
        this.set(fresh);
        return fresh;
      })
      .catch(() => {
        this.set(null);
        return null;
      })
      .finally(() => {
        this.refreshing = null;
      });
    return (await this.refreshing)?.accessToken ?? null;
  };
}

// --- the chosen organization (only needed by users who belong to several) ---

const orgListeners = new Set<Listener>();
let orgSnapshot: string | null | undefined;

export const orgStore = {
  subscribe(listener: Listener) {
    orgListeners.add(listener);
    return () => orgListeners.delete(listener);
  },
  getSnapshot(): string | null {
    if (orgSnapshot === undefined) orgSnapshot = loadOrgId();
    return orgSnapshot;
  },
  getServerSnapshot: (): string | null => null,
  set(value: string | null): boolean {
    if (value !== null && !isValidOrgId(value)) return false;
    orgSnapshot = value ? value.trim().toLowerCase() : null;
    saveOrgId(orgSnapshot);
    orgListeners.forEach((l) => l());
    return true;
  },
};
