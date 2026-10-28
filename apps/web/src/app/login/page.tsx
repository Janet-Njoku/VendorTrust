"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";

import { useAuth } from "@/components/providers";
import { Button, Field, INPUT, Notice } from "@/components/ui";

export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const emailId = useId();
  const passwordId = useId();

  useEffect(() => {
    if (auth.ready && auth.session) router.replace("/");
  }, [auth.ready, auth.session, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setInfo(null);
    if (!email.includes("@")) return setError("Enter a valid email address.");
    if (password.length < 8) return setError("The password must be at least 8 characters.");
    setPending(true);
    try {
      if (mode === "signin") {
        await auth.signIn(email.trim(), password);
      } else {
        await auth.signUp(email.trim(), password);
        setInfo(
          "Check your email to confirm your address. After that, an organization administrator must grant you access before any data appears.",
        );
        setMode("signin");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-cyan-500/10 ring-1 ring-cyan-400/30">
            <svg viewBox="0 0 24 24" className="h-7 w-7 text-cyan-400" fill="none" aria-hidden="true">
              <path d="M12 2.5 4 5.5v6c0 4.7 3.2 8.4 8 10 4.8-1.6 8-5.3 8-10v-6l-8-3Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
              <path d="m8.5 12 2.4 2.4L15.8 9.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">VendorTrust</h1>
          <p className="mt-1 text-sm text-slate-400">Third-party security due diligence</p>
        </div>

        <div className="rounded-2xl bg-white p-6 shadow-xl">
          {auth.problems.length > 0 && (
            <div className="mb-4 space-y-2">
              {auth.problems.map((p) => (
                <Notice key={p} tone="error">
                  {p}
                </Notice>
              ))}
            </div>
          )}

          {auth.demo ? (
            <div>
              <Notice tone="warning">
                Demo mode is on. You will see fictional sample data held in your browser. Nothing is sent to a
                backend and no account is needed.
              </Notice>
              <Button className="mt-4 w-full" onClick={() => router.replace("/")}>
                Enter the demo
              </Button>
            </div>
          ) : !auth.configured ? (
            <Notice tone="warning">
              Sign-in is not configured. Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
              <code>NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code> (the publishable key only, never a secret or
              service-role key) in <code>apps/web/.env.local</code>, then restart.
            </Notice>
          ) : (
            <form onSubmit={submit} noValidate>
              <h2 className="mb-4 text-lg font-semibold text-slate-900">
                {mode === "signin" ? "Sign in" : "Create an account"}
              </h2>
              {info && <Notice tone="success">{info}</Notice>}
              <Field label="Email" htmlFor={emailId}>
                <input
                  id={emailId}
                  type="email"
                  autoComplete="username"
                  className={INPUT}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Field>
              <Field label="Password" htmlFor={passwordId}>
                <input
                  id={passwordId}
                  type="password"
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                  className={INPUT}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              {error && (
                <div className="mt-4">
                  <Notice tone="error">{error}</Notice>
                </div>
              )}
              <Button type="submit" loading={pending} className="mt-5 w-full">
                {mode === "signin" ? "Sign in" : "Create account"}
              </Button>
              <button
                type="button"
                className="mt-4 w-full text-center text-sm text-cyan-700 hover:underline"
                onClick={() => {
                  setMode(mode === "signin" ? "signup" : "signin");
                  setError(null);
                }}
              >
                {mode === "signin" ? "Need an account? Register" : "Already registered? Sign in"}
              </button>
            </form>
          )}
        </div>
        <p className="mt-6 text-center text-xs text-slate-500">
          Access to data requires an organization membership granted by an administrator.
        </p>
      </div>
    </div>
  );
}
