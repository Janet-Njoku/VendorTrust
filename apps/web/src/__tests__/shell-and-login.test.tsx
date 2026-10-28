import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import LoginPage from "@/app/login/page";
import { AppShell } from "@/components/app-shell";
import { TestProviders } from "@/components/providers";
import { AuthError } from "@/lib/auth/supabase";
import { createDemoClient } from "@/lib/demo/client";
import type { AuthState } from "@/components/providers";
import { nav, resetNav } from "@/test/router";

beforeEach(() => resetNav());

async function shell(auth: Partial<AuthState> = {}, pathname = "/") {
  resetNav({}, pathname);
  const view = render(
    <TestProviders client={createDemoClient({ seed: false })} auth={auth}>
      <AppShell>
        <p>page content</p>
      </AppShell>
    </TestProviders>,
  );
  const signedIn = auth.session !== null && auth.ready !== false;
  if (signedIn) await screen.findByText("page content"); // the access check passes first
  return view;
}

describe("App shell", () => {
  it("offers navigation between Dashboard, Vendors, Questionnaires and Assessments", async () => {
    await shell({}, "/vendors/123");
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0] as HTMLElement;
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => [l.textContent, l.getAttribute("href")])).toEqual([
      ["Dashboard", "/"],
      ["Vendors", "/vendors"],
      ["Questionnaires", "/questionnaires"],
      ["Assessments", "/assessments"],
    ]);
    expect(within(nav).getByRole("link", { name: "Vendors" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("page content")).toBeInTheDocument();
    expect(screen.getByText("analyst@example.invalid")).toBeInTheDocument();
  });

  it("marks the dashboard active only on the exact root", async () => {
    await shell({}, "/");
    const dashboard = within(screen.getAllByRole("navigation", { name: "Main" })[0] as HTMLElement).getByRole("link", { name: "Dashboard" });
    expect(dashboard).toHaveAttribute("aria-current", "page");
  });

  it("sends a signed-out visitor to the login page and shows no content", async () => {
    await shell({ session: null });
    expect(screen.queryByText("page content")).not.toBeInTheDocument();
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login"));
  });

  it("waits for the browser before deciding", async () => {
    await shell({ ready: false, session: null });
    expect(screen.getByRole("status")).toHaveTextContent(/checking your session/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("labels demo mode on every screen", async () => {
    await shell({ demo: true });
    expect(screen.getByRole("status")).toHaveTextContent(/DEMO MODE: sample data in your browser only/);
  });

  it("shows no demo banner for a real session", async () => {
    await shell({ demo: false });
    expect(screen.queryByText(/DEMO MODE/)).not.toBeInTheDocument();
  });

  it("signs out and lets the user pick another organization", async () => {
    const signOut = vi.fn(async () => {});
    const setOrgId = vi.fn(() => true);
    await shell({ signOut, orgId: "11111111-1111-4111-8111-111111111111", setOrgId });
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalled();
    fireEvent.click(screen.getByTitle("Change organization"));
    expect(setOrgId).toHaveBeenCalledWith(null);
  });

  it("has a mobile menu that opens and closes", async () => {
    await shell();
    const toggle = screen.getByRole("button", { name: "Open menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(2);
    const link = within(screen.getAllByRole("navigation", { name: "Main" })[1] as HTMLElement).getByRole("link", { name: "Assessments" });
    link.addEventListener("click", (event) => event.preventDefault()); // jsdom cannot navigate
    fireEvent.click(link);
    expect(screen.getByRole("button", { name: "Open menu" })).toBeInTheDocument();
  });
});

function login(auth: Partial<AuthState> = {}) {
  return render(
    <TestProviders client={createDemoClient({ seed: false })} auth={{ session: null, ...auth }}>
      <LoginPage />
    </TestProviders>,
  );
}

describe("Login", () => {
  it("explains that sign-in is not configured instead of showing a broken form", () => {
    login({ configured: false });
    expect(screen.getByText(/Sign-in is not configured/)).toHaveTextContent(/publishable key only/);
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
  });

  it("shows configuration problems such as a secret key in the browser settings", () => {
    login({ problems: ["NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY holds a secret or service-role key."] });
    expect(screen.getByRole("alert")).toHaveTextContent(/secret or service-role key/);
  });

  it("validates before contacting the auth service", async () => {
    const signIn = vi.fn(async () => {});
    login({ signIn });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ana@example.invalid" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/at least 8 characters/)).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("signs in and shows a generic failure message", async () => {
    const signIn = vi.fn(async () => {
      throw new AuthError("Invalid email or password.");
    });
    login({ signIn });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: " ana@example.invalid " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(signIn).toHaveBeenCalledWith("ana@example.invalid", "correct horse battery");
  });

  it("registers and says that access still needs an administrator", async () => {
    const signUp = vi.fn(async () => {});
    login({ signUp });
    fireEvent.click(screen.getByRole("button", { name: "Need an account? Register" }));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.invalid" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText(/administrator must grant you access/)).toBeInTheDocument();
    expect(signUp).toHaveBeenCalledWith("new@example.invalid", "a long password");
  });

  it("leaves for the dashboard once signed in", async () => {
    login({ session: { accessToken: "t", refreshToken: "r", expiresAt: 1, userId: "u", email: null } });
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/"));
  });

  it("in demo mode explains the data is fictional and needs no account", () => {
    login({ demo: true, session: null });
    expect(screen.getByText(/fictional sample data held in your browser/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enter the demo" }));
    expect(nav.replace).toHaveBeenCalledWith("/");
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });
});
