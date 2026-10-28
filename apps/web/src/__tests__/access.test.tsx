import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import VendorsPage from "@/app/(app)/vendors/page";
import { AccessGate } from "@/components/access";
import { TestProviders, type AuthState } from "@/components/providers";
import { ApiError } from "@/lib/api/errors";
import type { Me } from "@/lib/api/types";
import { AuthError } from "@/lib/auth/supabase";
import { createDemoClient } from "@/lib/demo/client";
import { resetNav } from "@/test/router";

beforeEach(() => resetNav());

const ORG_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ORG_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function me(overrides: Partial<Me> = {}): Me {
  const active = { org_id: ORG_A, role: "analyst" as const, vendor_id: null };
  return {
    user_id: "11111111-1111-4111-8111-111111111111",
    email: "ana@example.invalid",
    status: "active",
    memberships: [active],
    active,
    has_access: true,
    needs_organization: false,
    mfa: { required: true, satisfied: true, level: "aal2" },
    ...overrides,
  };
}

function gate(answers: Me | Me[], auth: Partial<AuthState> = {}) {
  const queue = Array.isArray(answers) ? [...answers] : [answers];
  const base = createDemoClient({ seed: false });
  const getMe = vi.fn(async () => queue.length > 1 ? (queue.shift() as Me) : (queue[0] as Me));
  const view = render(
    <TestProviders client={{ ...base, getMe }} auth={auth}>
      <AccessGate>
        <p>the real page</p>
      </AccessGate>
    </TestProviders>,
  );
  return { getMe, ...view };
}

const mfaStubs = (overrides: Partial<AuthState["mfa"]> = {}): AuthState["mfa"] => ({
  factors: vi.fn(async () => []),
  enroll: vi.fn(async () => ({
    factorId: "factor-1",
    secret: "JBSWY3DPEHPK3PXP",
    uri: "otpauth://totp/VendorTrust:ana@example.invalid?secret=JBSWY3DPEHPK3PXP",
    qrCode: "data:image/svg+xml;utf8,<svg></svg>",
  })),
  verify: vi.fn(async () => {}),
  ...overrides,
});

describe("Access gate", () => {
  it("checks access first, then shows the page", async () => {
    gate(me());
    expect(screen.getByRole("status")).toHaveTextContent(/checking your access/i);
    expect(await screen.findByText("the real page")).toBeInTheDocument();
  });

  it("shows a user with no membership an honest explanation, never the pages", async () => {
    const signOut = vi.fn(async () => {});
    gate(me({ memberships: [], active: null, has_access: false, mfa: { required: false, satisfied: true, level: "aal1" } }), { signOut });
    expect(await screen.findByText(/do not have access to an organization yet/i)).toBeInTheDocument();
    expect(screen.getByText(/ana@example.invalid/)).toHaveTextContent(/signing in does not grant access/i);
    expect(screen.queryByText("the real page")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalled();
  });

  it("says when an account is disabled", async () => {
    gate(me({ status: "disabled", memberships: [], active: null, has_access: false }));
    expect(await screen.findByText("Your account is disabled")).toBeInTheDocument();
  });

  it("lets a user in several organizations choose one", async () => {
    const setOrgId = vi.fn(() => true);
    const memberships = [
      { org_id: ORG_A, role: "analyst" as const, vendor_id: null },
      { org_id: ORG_B, role: "auditor" as const, vendor_id: null },
    ];
    gate(me({ memberships, active: null, has_access: false, needs_organization: true }), { setOrgId });
    expect(await screen.findByText("Choose an organization")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /auditor/ }));
    expect(setOrgId).toHaveBeenCalledWith(ORG_B);
    expect(screen.queryByText("the real page")).not.toBeInTheDocument();
  });

  it("shows a failure to reach the API with a retry", async () => {
    const base = createDemoClient({ seed: false });
    let fail = true;
    const getMe = vi.fn(async () => {
      if (fail) throw new ApiError(0, "down");
      return me();
    });
    render(
      <TestProviders client={{ ...base, getMe }}>
        <AccessGate>
          <p>the real page</p>
        </AccessGate>
      </TestProviders>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be reached/i);
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("the real page")).toBeInTheDocument();
  });
});

describe("MFA", () => {
  const needsMfa = me({ mfa: { required: true, satisfied: false, level: "aal1" } });

  it("walks a user without an authenticator through enrollment, then lets them in", async () => {
    const mfa = mfaStubs();
    const { getMe } = gate([needsMfa, me()], { mfa });
    expect(await screen.findByRole("heading", { name: "Set up two-factor authentication" })).toBeInTheDocument();
    expect(screen.queryByText("the real page")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Set up authenticator" }));
    expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(screen.getByAltText("QR code for your authenticator app")).toHaveAttribute("src", expect.stringMatching(/^data:image\/svg\+xml/));

    fireEvent.click(screen.getByRole("button", { name: "Verify and continue" }));
    expect(await screen.findByText(/Enter the 6-digit code/)).toBeInTheDocument();
    expect(mfa.verify).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("6-digit code"), { target: { value: "12ab34" } });
    expect(screen.getByLabelText("6-digit code")).toHaveValue("1234"); // non-digits are dropped
    fireEvent.change(screen.getByLabelText("6-digit code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and continue" }));
    await waitFor(() => expect(mfa.verify).toHaveBeenCalledWith("factor-1", "123456"));
    expect(await screen.findByText("the real page")).toBeInTheDocument();
    expect(getMe).toHaveBeenCalledTimes(2); // the access check ran again with the aal2 session
  });

  it("asks a user who already has an authenticator only for a code", async () => {
    const mfa = mfaStubs({ factors: vi.fn(async () => [{ id: "f-9", friendlyName: "phone", verified: true }]) });
    gate([needsMfa, me()], { mfa });
    expect(await screen.findByRole("heading", { name: "Verify it is you" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set up authenticator" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("6-digit code"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and continue" }));
    await waitFor(() => expect(mfa.verify).toHaveBeenCalledWith("f-9", "654321"));
    expect(await screen.findByText("the real page")).toBeInTheDocument();
    expect(mfa.enroll).not.toHaveBeenCalled();
  });

  it("ignores an unverified leftover factor and enrolls a fresh one", async () => {
    const mfa = mfaStubs({ factors: vi.fn(async () => [{ id: "old", friendlyName: "abandoned", verified: false }]) });
    gate(needsMfa, { mfa });
    fireEvent.click(await screen.findByRole("button", { name: "Set up authenticator" }));
    expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(mfa.enroll).toHaveBeenCalledOnce();
  });

  it("shows a rejected code, clears the field and stays blocked", async () => {
    const mfa = mfaStubs({
      factors: vi.fn(async () => [{ id: "f-9", friendlyName: "phone", verified: true }]),
      verify: vi.fn(async () => {
        throw new AuthError("That code was not accepted. Check the code and try again.");
      }),
    });
    gate(needsMfa, { mfa });
    fireEvent.change(await screen.findByLabelText("6-digit code"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and continue" }));
    expect(await screen.findByText(/code was not accepted/)).toBeInTheDocument();
    expect(screen.getByLabelText("6-digit code")).toHaveValue("");
    expect(screen.queryByText("the real page")).not.toBeInTheDocument();
  });

  it("shows enrollment failures and lets the user sign out", async () => {
    const signOut = vi.fn(async () => {});
    const mfa = mfaStubs({
      enroll: vi.fn(async () => {
        throw new AuthError("Could not start authenticator setup.");
      }),
    });
    gate(needsMfa, { mfa, signOut });
    fireEvent.click(await screen.findByRole("button", { name: "Set up authenticator" }));
    expect(await screen.findByText("Could not start authenticator setup.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalled();
  });

  it("reports a failure to read the authenticator state", async () => {
    gate(needsMfa, { mfa: mfaStubs({ factors: vi.fn(async () => { throw new AuthError("Could not check your authenticator setup."); }) }) });
    expect(await screen.findByText("Could not check your authenticator setup.")).toBeInTheDocument();
  });

  it("does not ask users whose role does not need MFA", async () => {
    gate(me({ mfa: { required: false, satisfied: true, level: "aal1" } }));
    expect(await screen.findByText("the real page")).toBeInTheDocument();
  });

  it("turns a mid-session 'MFA required' answer on any page into the MFA screen", async () => {
    const base = createDemoClient({ seed: false });
    const client = {
      ...base,
      listVendors: () => Promise.reject(new ApiError(403, "Multi-factor authentication required")),
      listAssessments: () => Promise.reject(new ApiError(403, "Multi-factor authentication required")),
    };
    render(
      <TestProviders client={client} auth={{ mfa: mfaStubs({ factors: vi.fn(async () => [{ id: "f", friendlyName: "p", verified: true }]) }) }}>
        <VendorsPage />
      </TestProviders>,
    );
    const heading = await screen.findByRole("heading", { name: "Verify it is you" });
    expect(within(heading.closest("section") as HTMLElement).getByLabelText("6-digit code")).toBeInTheDocument();
  });
});
