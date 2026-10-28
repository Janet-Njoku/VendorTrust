"use client";

import { useEffect, useId, useState } from "react";

import type { Me } from "@/lib/api/types";
import type { Enrollment, Factor } from "@/lib/auth/mfa";
import { useQuery } from "@/lib/hooks";

import { useApi, useAuth } from "./providers";
import { Button, Card, EmptyState, ErrorState, Field, INPUT, LoadingState, Notice } from "./ui";

const CODE = /^\d{6}$/;

/**
 * Multi-factor authentication for roles that need it. A user with a verified authenticator enters a
 * code; a user without one sets one up first. Supabase turns a correct code into a new session at
 * assurance level aal2, which is what the API checks. Nothing is stored here.
 */
export function MfaGate({ onDone }: { onDone: () => void }) {
  const auth = useAuth();
  const codeId = useId();
  const [factors, setFactors] = useState<Factor[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [code, setCode] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    auth.mfa
      .factors()
      .then((found) => {
        if (!cancelled) setFactors(found);
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "Something went wrong.");
      });
    return () => {
      cancelled = true;
    };
  }, [auth.mfa]);

  const verified = factors?.find((f) => f.verified);
  const factorId = enrollment?.factorId ?? verified?.id;

  async function start() {
    setBusy(true);
    setProblem(null);
    try {
      setEnrollment(await auth.mfa.enroll());
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!factorId) return;
    if (!CODE.test(code.trim())) return setProblem("Enter the 6-digit code from your authenticator.");
    setBusy(true);
    setProblem(null);
    try {
      await auth.mfa.verify(factorId, code.trim());
      onDone();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "Something went wrong.");
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <Notice tone="error">{loadError}</Notice>;
  if (!factors) return <LoadingState label="Checking your authenticator…" />;

  const enrolling = !verified && !enrollment;
  return (
    <div className="mx-auto max-w-md">
      <Card title={enrolling || enrollment ? "Set up two-factor authentication" : "Verify it is you"}>
        <p className="text-sm text-slate-600">
          Your role requires two-factor authentication before any data is shown.{" "}
          {enrolling
            ? "Link an authenticator app (such as 1Password, Authy or Google Authenticator) to continue."
            : enrollment
              ? "Add this account to your authenticator app, then enter the 6-digit code it shows."
              : "Enter the 6-digit code from your authenticator app."}
        </p>

        {enrolling ? (
          <div className="mt-5">
            <Button loading={busy} onClick={() => void start()}>
              Set up authenticator
            </Button>
          </div>
        ) : (
          <form onSubmit={submit} noValidate className="mt-4">
            {enrollment && (
              <div className="mb-4 space-y-3 rounded-lg bg-slate-50 p-4 text-sm">
                {enrollment.qrCode && (
                  // eslint-disable-next-line @next/next/no-img-element -- a data: image, nothing to optimise
                  <img src={enrollment.qrCode} alt="QR code for your authenticator app" className="mx-auto h-40 w-40" />
                )}
                <div>
                  <p className="font-medium text-slate-800">Setup key</p>
                  <code className="mt-1 block break-all rounded bg-white px-2 py-1.5 font-mono text-xs ring-1 ring-slate-200">
                    {enrollment.secret}
                  </code>
                </div>
                {enrollment.uri && (
                  <details>
                    <summary className="cursor-pointer text-xs text-cyan-700">Setup link (otpauth)</summary>
                    <code className="mt-1 block break-all font-mono text-xs text-slate-600">{enrollment.uri}</code>
                  </details>
                )}
              </div>
            )}
            <Field label="6-digit code" htmlFor={codeId} error={problem ?? undefined}>
              <input
                id={codeId}
                className={INPUT}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              />
            </Field>
            <Button type="submit" loading={busy} className="mt-4 w-full">
              Verify and continue
            </Button>
          </form>
        )}
        {enrolling && problem && (
          <div className="mt-4">
            <Notice tone="error">{problem}</Notice>
          </div>
        )}
        <button
          type="button"
          className="mt-5 text-sm text-slate-600 hover:underline"
          onClick={() => void auth.signOut()}
        >
          Sign out
        </button>
      </Card>
    </div>
  );
}

export function NoAccess({ me }: { me: Me }) {
  const auth = useAuth();
  return (
    <div className="mx-auto max-w-lg">
      <EmptyState
        title={me.status === "disabled" ? "Your account is disabled" : "You do not have access to an organization yet"}
        action={
          <Button variant="secondary" onClick={() => void auth.signOut()}>
            Sign out
          </Button>
        }
      >
        {me.status === "disabled"
          ? `${me.email} has been disabled. Contact an administrator.`
          : `You are signed in as ${me.email}, but no organization has granted this account a membership. An organization administrator must add you; signing in does not grant access.`}
      </EmptyState>
    </div>
  );
}

export function OrgChooser({ me }: { me: Me }) {
  const auth = useAuth();
  return (
    <div className="mx-auto max-w-md">
      <Card title="Choose an organization">
        <p className="mb-4 text-sm text-slate-600">
          Your account belongs to more than one organization. Pick the one to work in; you can change it later.
        </p>
        <ul className="space-y-2">
          {me.memberships.map((m) => (
            <li key={m.org_id}>
              <button
                type="button"
                className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-4 py-3 text-left text-sm hover:border-cyan-600 hover:bg-cyan-50"
                onClick={() => auth.setOrgId(m.org_id)}
              >
                <span className="font-mono text-xs text-slate-600">{m.org_id.slice(0, 8)}…</span>
                <span className="font-medium text-slate-900">{m.role.replace("_", " ")}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

/**
 * Sits between sign-in and every screen. It asks the API who the user is (which also provisions their
 * user row on first login) and shows the right thing: complete MFA, choose an organization, or an
 * honest "no access yet". Only when all is well do the real pages render.
 */
export function AccessGate({ children }: { children: React.ReactNode }) {
  const api = useApi();
  const auth = useAuth();
  const me = useQuery(() => api.getMe(), `me-${auth.orgId ?? "none"}-${auth.session?.expiresAt ?? 0}`);

  if (me.error) return <ErrorState error={me.error} onRetry={me.reload} />;
  if (!me.data) return <LoadingState label="Checking your access…" />;
  const data = me.data;
  if (data.mfa.required && !data.mfa.satisfied) return <MfaGate onDone={me.reload} />;
  if (data.needs_organization) return <OrgChooser me={data} />;
  if (!data.has_access) return <NoAccess me={data} />;
  return <>{children}</>;
}
