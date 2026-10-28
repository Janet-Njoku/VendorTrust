"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AccessGate } from "./access";
import { useAuth } from "./providers";
import { LoadingState, cx } from "./ui";

export const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/vendors", label: "Vendors" },
  { href: "/questionnaires", label: "Questionnaires" },
  { href: "/assessments", label: "Assessments" },
] as const;

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5 text-white">
      <svg viewBox="0 0 24 24" className="h-7 w-7 text-cyan-400" fill="none" aria-hidden="true">
        <path
          d="M12 2.5 4 5.5v6c0 4.7 3.2 8.4 8 10 4.8-1.6 8-5.3 8-10v-6l-8-3Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
        <path d="m8.5 12 2.4 2.4L15.8 9.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="text-lg font-semibold tracking-tight">VendorTrust</span>
    </Link>
  );
}

/** Navigation, the demo banner and the account menu around every signed-in screen. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const signedIn = auth.session !== null;

  useEffect(() => {
    if (auth.ready && !signedIn) router.replace("/login");
  }, [auth.ready, signedIn, router]);

  if (!auth.ready || !signedIn) return <LoadingState label="Checking your session…" />;

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {NAV.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          onClick={() => setOpen(false)}
          aria-current={isActive(pathname, item.href) ? "page" : undefined}
          className={cx(
            "rounded-md px-3 py-2 text-sm font-medium transition",
            isActive(pathname, item.href)
              ? "bg-slate-800 text-white"
              : "text-slate-300 hover:bg-slate-800/60 hover:text-white",
          )}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );

  return (
    <div className="min-h-screen bg-slate-50 lg:flex">
      <aside className="hidden w-60 shrink-0 flex-col bg-slate-900 px-4 py-5 lg:flex">
        <Logo />
        <div className="mt-8">{nav}</div>
        <p className="mt-auto px-3 text-xs text-slate-500">Third-party risk and control validation</p>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {auth.demo && (
          <div role="status" className="bg-amber-400 px-4 py-1.5 text-center text-xs font-semibold text-amber-950">
            DEMO MODE: sample data in your browser only. Nothing here is connected to a real backend.
          </div>
        )}
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 lg:px-8">
          <button
            type="button"
            className="rounded-md p-2 text-slate-700 hover:bg-slate-100 lg:hidden"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d={open ? "M6 6l12 12M18 6 6 18" : "M4 7h16M4 12h16M4 17h16"} strokeLinecap="round" />
            </svg>
          </button>
          <div className="lg:hidden">
            <span className="text-base font-semibold text-slate-900">VendorTrust</span>
          </div>
          <div className="ml-auto flex items-center gap-3 text-sm">
            {auth.orgId && (
              <button
                type="button"
                className="hidden rounded-md bg-slate-100 px-2 py-1 font-mono text-xs text-slate-600 hover:bg-slate-200 sm:block"
                title="Change organization"
                onClick={() => auth.setOrgId(null)}
              >
                org {auth.orgId.slice(0, 8)}…
              </button>
            )}
            <span className="hidden max-w-[16rem] truncate text-slate-600 sm:block">{auth.session?.email}</span>
            <button
              type="button"
              className="rounded-md px-2.5 py-1.5 font-medium text-slate-700 hover:bg-slate-100"
              onClick={() => void auth.signOut()}
            >
              Sign out
            </button>
          </div>
        </header>
        {open && (
          <div className="border-b border-slate-200 bg-slate-900 px-4 py-3 lg:hidden">{nav}</div>
        )}
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:px-8 lg:py-8">
          <AccessGate>{children}</AccessGate>
        </main>
      </div>
    </div>
  );
}
