import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

import DashboardPage from "@/app/(app)/page";
import { ApiError } from "@/lib/api/errors";
import { createDemoClient } from "@/lib/demo/client";
import { failingClient, renderApp } from "@/test/helpers";
import { resetNav } from "@/test/router";

beforeEach(() => resetNav());

describe("Dashboard", () => {
  it("shows a loading state, then totals, distribution, high-risk vendors, progress and activity", async () => {
    renderApp(<DashboardPage />);
    expect(screen.getByRole("status", { name: /loading dashboard/i })).toBeInTheDocument();

    expect(await screen.findByText("Risk distribution (active vendors)")).toBeInTheDocument();
    const stat = (label: string) => screen.getByText(label).closest("div") as HTMLElement;
    expect(within(stat("Vendors")).getByText("9")).toBeInTheDocument();
    expect(within(stat("Assessments in progress")).getByText("1")).toBeInTheDocument();
    expect(within(stat("Not yet assessed")).getByText("3")).toBeInTheDocument();

    const distribution = screen.getByRole("img", { name: /Critical: \d+, High: \d+/ });
    expect(distribution).toHaveAccessibleName(/Not assessed: 3/);

    const risky = screen.getByText("High-risk vendors").closest("section") as HTMLElement;
    expect(within(risky).getByRole("link", { name: "Tailspin HR Suite" })).toHaveAttribute("href", expect.stringMatching(/^\/vendors\//));
    expect(within(risky).queryByText("Northwind Payments")).not.toBeInTheDocument();

    const progress = screen.getByRole("progressbar", { name: "Assessments scored" });
    expect(progress).toHaveAttribute("aria-valuenow", "5");
    expect(progress).toHaveAttribute("aria-valuemax", "6");

    const activity = screen.getByText("Recent activity").closest("section") as HTMLElement;
    expect(within(activity).getAllByRole("listitem").length).toBeGreaterThan(3);
    expect(within(activity).getByText(/audit log is written by the API but is not exposed/i)).toBeInTheDocument();
  });

  it("explains an empty organization and points to the vendor register", async () => {
    renderApp(<DashboardPage />, { client: createDemoClient({ seed: false }) });
    expect(await screen.findByText("No vendors yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /add your first vendor/i })).toHaveAttribute("href", "/vendors");
  });

  it("explains a missing membership instead of showing an error", async () => {
    renderApp(<DashboardPage />, { client: failingClient(new ApiError(403, "No active membership")) });
    expect(await screen.findByText(/do not have access to an organization yet/i)).toBeInTheDocument();
    expect(screen.getByText(/not available in the app yet/i)).toBeInTheDocument();
  });

  it("asks for an organization when the user belongs to several, and remembers the choice", async () => {
    const setOrgId = vi.fn(() => true);
    renderApp(<DashboardPage />, {
      client: failingClient(new ApiError(400, "Choose an organization with the X-Org-Id header")),
      auth: { setOrgId },
    });
    const input = await screen.findByLabelText("Organization ID");
    fireEvent.change(input, { target: { value: "not-a-uuid" } });
    fireEvent.click(screen.getByRole("button", { name: /use this organization/i }));
    expect(await screen.findByText(/as a UUID/i)).toBeInTheDocument();
    expect(setOrgId).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "11111111-1111-4111-8111-111111111111" } });
    // Saving the choice reloads the dashboard. An async act waits for that reload (whose result this
    // stub client answers with "choose an organization" again) to settle inside act's scope, so no state
    // update lands after the test has finished.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /use this organization/i }));
    });
    expect(setOrgId).toHaveBeenCalledWith("11111111-1111-4111-8111-111111111111");
    expect(screen.getByLabelText("Organization ID")).toBeInTheDocument();
  });

  it("shows a network failure with a retry that recovers", async () => {
    const real = createDemoClient();
    let failing = true;
    const client = {
      ...real,
      listVendors: (p?: Parameters<typeof real.listVendors>[0]) =>
        failing ? Promise.reject(new ApiError(0, "The API could not be reached.")) : real.listVendors(p),
    };
    renderApp(<DashboardPage />, { client });
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be reached/i);
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(screen.getByText("Risk distribution (active vendors)")).toBeInTheDocument());
  });

  it("sends an expired session back to sign-in", async () => {
    const signOut = vi.fn(async () => {});
    renderApp(<DashboardPage />, {
      client: failingClient(new ApiError(401, "Not authenticated")),
      auth: { signOut },
    });
    fireEvent.click(await screen.findByRole("button", { name: /sign in/i }));
    expect(signOut).toHaveBeenCalled();
  });
});
