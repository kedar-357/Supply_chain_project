import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorState, KpiCard, StatusBadge } from "@/components/common";
import { AppLayout, NAV } from "@/components/layout/AppLayout";
import { AuthProvider } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { fmt } from "@/lib/utils";
import { validateFile } from "@/pages/DataManagement";
import { validateLogin, validateRegister } from "@/pages/Login";
import Scenarios, { formatMetric } from "@/pages/Scenarios";
import { validateSettings } from "@/pages/Settings";

// ---- API mock --------------------------------------------------------------
const getMock = vi.fn();
const postMock = vi.fn();
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { get: (...a: unknown[]) => getMock(...a), post: (...a: unknown[]) => postMock(...a), delete: vi.fn(), patch: vi.fn(), put: vi.fn() },
    tokenStore: { get: () => "token", set: vi.fn(), clear: vi.fn() },
  };
});

const USER = { id: 2, email: "m@x.io", full_name: "Manoj Manager", role: "Supply Chain Manager", is_active: true, permissions: ["view_analytics", "simulate", "optimize"] };

function routeGet(url: string) {
  if (url === "/api/auth/me") return Promise.resolve({ data: USER });
  if (url === "/api/dashboard/alerts") return Promise.resolve({ data: [{ severity: "critical", message: "SKU-1 out of stock" }] });
  if (url === "/api/forecast/status") return Promise.resolve({ data: { completed: { finished_at: "2026-11-19T10:00:00Z" } } });
  if (url === "/api/data/summary") return Promise.resolve({ data: { sales_range: { to: "2026-11-18" } } });
  if (url === "/api/suppliers" || url === "/api/warehouses" || url === "/api/categories" || url === "/api/scenarios") return Promise.resolve({ data: [] });
  return Promise.resolve({ data: {} });
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation((url: string) => routeGet(url));
});

// ---- form validation -------------------------------------------------------
describe("form validation", () => {
  it("validates login input", () => {
    expect(validateLogin("bad", "")).toEqual({ email: "Enter a valid email address", password: "Password is required" });
    expect(validateLogin("a@b.co", "x")).toEqual({});
  });
  it("validates registration input", () => {
    const e = validateRegister("A", "nope", "short", "other");
    expect(Object.keys(e).sort()).toEqual(["confirm", "email", "name", "password"]);
    expect(validateRegister("Ann Lee", "ann@x.io", "lettersonly", "lettersonly").password).toBe("Use letters and digits");
    expect(validateRegister("Ann Lee", "ann@x.io", "Passw0rd1", "Passw0rd1")).toEqual({});
  });
  it("validates uploaded files", () => {
    expect(validateFile(null)).toMatch(/Choose/);
    expect(validateFile(new File(["x"], "a.pdf"))).toMatch(/csv/);
    expect(validateFile(new File([], "a.csv"))).toMatch(/empty/);
    expect(validateFile(new File(["sku\n1"], "a.csv"))).toBeNull();
  });
  it("validates business settings", () => {
    expect(validateSettings("supplier_weights", { on_time: 0.5, quality: 0.6 })).toMatch(/sum to 1/);
    expect(validateSettings("inventory", { service_level: 1.2 })).toMatch(/Service level/);
    expect(validateSettings("inventory", { service_level: 0.95 })).toBeNull();
    expect(validateSettings("procurement", { monthly_budget: -1 })).toMatch(/non-negative/);
  });
});

describe("formatting", () => {
  it("formats INR compactly in Indian units", () => {
    expect(fmt.inrCompact(20_000_000)).toBe("₹2.00 Cr");
    expect(fmt.inrCompact(250_000)).toBe("₹2.5 L");
    expect(fmt.inrCompact(null)).toBe("–");
    expect(formatMetric(99.5, "%")).toBe("99.50%");
  });
});

// ---- components --------------------------------------------------------------
describe("components", () => {
  it("renders KPI value, hint and labelled status", () => {
    render(<KpiCard label="Predicted stockouts" value="4" hint="before replenishment" delta={-5} deltaGoodWhenUp={false} />);
    expect(screen.getByText("Predicted stockouts")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("5.0%")).toHaveClass("text-green-700");
    render(<StatusBadge status="out_of_stock" />);
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
  });
  it("shows an error state with retry", async () => {
    const retry = vi.fn();
    render(<ErrorState message="Cannot reach the API" onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the API");
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(retry).toHaveBeenCalled();
  });
});

// ---- data loading ------------------------------------------------------------
function Probe({ path }: { path: string }) {
  const { data, error, loading } = useApi<{ v: number }>(path);
  if (loading) return <span>loading</span>;
  if (error) return <span>error: {error}</span>;
  return <span>value {data?.v}</span>;
}

describe("useApi", () => {
  it("loads data", async () => {
    getMock.mockResolvedValueOnce({ data: { v: 42 } });
    render(<Probe path="/x" />);
    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(await screen.findByText("value 42")).toBeInTheDocument();
  });
  it("surfaces API errors", async () => {
    getMock.mockRejectedValueOnce(new Error("boom"));
    render(<Probe path="/x" />);
    expect(await screen.findByText("error: boom")).toBeInTheDocument();
  });
});

// ---- navigation ----------------------------------------------------------------
describe("navigation", () => {
  it("renders the sidebar with all modules and the page title", async () => {
    render(
      <AuthProvider>
        <MemoryRouter initialEntries={["/scenarios"]}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="scenarios" element={<div>scenario page</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>,
    );
    const nav = await screen.findByRole("navigation", { name: /main navigation/i });
    for (const item of NAV) expect(within(nav).getByText(item.label)).toBeInTheDocument();
    expect(within(nav).getByText("Scenario Simulator").closest("a")).toHaveClass("bg-primary");
    expect((await screen.findAllByText("Manoj Manager")).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByLabelText(/1 critical/)).toBeInTheDocument());
  });
});

// ---- scenario interaction ---------------------------------------------------------
describe("scenario simulator", () => {
  it("validates parameters and submits a simulation", async () => {
    postMock.mockResolvedValue({
      data: {
        scenario_type: "demand_increase", scenario_label: "Sudden demand increase", config: { increase_pct: 30 },
        metrics: [{ metric: "items_at_risk", label: "Items at stockout risk", unit: "items", baseline: 4, scenario: 10, change: 6, change_pct: 150, favourable: false }],
        narrative: ["Demand +30% raises target inventory levels"], details: {},
      },
    });
    render(
      <AuthProvider>
        <MemoryRouter>
          <Scenarios />
        </MemoryRouter>
      </AuthProvider>,
    );
    await screen.findByText("Supplier delay");
    await userEvent.click(screen.getByText("Demand increase"));
    const input = screen.getByLabelText(/Demand change/);
    await userEvent.clear(input);
    await userEvent.type(input, "500");
    await userEvent.click(screen.getByRole("button", { name: /^Simulate$/ }));
    expect(await screen.findByText(/between -90% and 300%/)).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "+30%" }));
    await userEvent.click(screen.getByRole("button", { name: /^Simulate$/ }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/api/scenarios/simulate", { scenario_type: "demand_increase", config: { increase_pct: 30, category: null }, save: false, name: null }),
    );
    expect(await screen.findByText("Items at stockout risk")).toBeInTheDocument();
    expect(screen.getByText(/raises target inventory levels/)).toBeInTheDocument();
    expect(screen.getByText("unfavourable")).toBeInTheDocument();
  });
});
