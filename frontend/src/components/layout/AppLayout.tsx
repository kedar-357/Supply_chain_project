import {
  Bell,

  Boxes,
  Database,
  FileText,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Menu,
  RefreshCw,
  Search,
  Settings,
  TrendingUp,
  Truck,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { cn, fmt } from "@/lib/utils";

export const NAV = [
  { to: "/", label: "Overview Dashboard", icon: LayoutDashboard, end: true },
  { to: "/forecasting", label: "Demand Forecasting", icon: TrendingUp },
  { to: "/inventory", label: "Inventory Optimization", icon: Boxes },
  { to: "/suppliers", label: "Supplier Analytics", icon: Users },
  { to: "/logistics", label: "Logistics & Routes", icon: Truck },
  { to: "/scenarios", label: "Scenario Simulator", icon: FlaskConical },
  { to: "/reports", label: "Reports & Insights", icon: FileText },
  { to: "/data", label: "Data Management", icon: Database },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Logo({ dark = true }: { dark?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <img src="/logo.svg" alt="" className="h-8 w-8" />
      <div className="leading-tight">
        <div className={cn("text-[15px] font-semibold tracking-tight", dark ? "text-white" : "text-foreground")}>SupplyChainX</div>
        <div className={cn("text-[10px] uppercase tracking-wider", dark ? "text-slate-400" : "text-muted-foreground")}>Decision intelligence</div>
      </div>
    </div>
  );
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user, logout } = useAuth();
  return (
    <>
      <div className={cn("fixed inset-0 z-[900] bg-black/40 lg:hidden", open ? "block" : "hidden")} onClick={onClose} />
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-[901] flex w-64 flex-col bg-navy-800 text-slate-300 transition-transform lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 items-center justify-between border-b border-white/10 px-5">
          <Logo />
          <button className="rounded p-1 text-slate-400 hover:bg-white/10 lg:hidden" onClick={onClose} aria-label="Close menu">
            <X className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4" aria-label="Main navigation">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onClose}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive ? "bg-primary text-white shadow-sm" : "hover:bg-white/5 hover:text-white",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 p-3">
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-600 text-xs font-semibold text-white">
              {user?.full_name.split(" ").map((s) => s[0]).slice(0, 2).join("")}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-white">{user?.full_name}</div>
              <div className="truncate text-[11px] text-slate-400">{user?.role}</div>
            </div>
            <button onClick={logout} className="rounded p-1.5 text-slate-400 hover:bg-white/10 hover:text-white" title="Sign out" aria-label="Sign out">
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

interface SearchResult {
  type: string;
  id: number;
  label: string;
  path: string;
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      api
        .get<{ results: SearchResult[] }>("/api/search", { params: { q } })
        .then((r) => setResults(r.data.results))
        .catch(() => setResults([]));
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  return (
    <div ref={ref} className="relative hidden w-full max-w-sm md:block">
      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="Search products, suppliers, warehouses…"
        aria-label="Global search"
        className="h-9 w-full rounded-md border border-input bg-secondary/60 pl-9 pr-3 text-sm outline-none focus:bg-white focus:ring-2 focus:ring-ring"
      />
      {open && q.trim().length >= 2 && (
        <div className="absolute left-0 right-0 top-11 z-[950] max-h-80 overflow-auto rounded-md border bg-white py-1 shadow-lg">
          {results.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted-foreground">No matches</div>
          ) : (
            results.map((r) => (
              <button
                key={`${r.type}-${r.id}`}
                onClick={() => {
                  navigate(r.path);
                  setOpen(false);
                  setQ("");
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-secondary"
              >
                <span className="w-16 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">{r.type}</span>
                <span className="truncate">{r.label}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

interface AlertItem {
  severity: string;
  message: string;
}

function Notifications() {
  const [open, setOpen] = useState(false);
  const { data } = useApi<AlertItem[]>("/api/dashboard/alerts", { limit: 8 });
  const critical = (data ?? []).filter((a) => a.severity === "critical").length;
  const navigate = useNavigate();
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative rounded-md p-2 text-muted-foreground hover:bg-secondary"
        aria-label={`Notifications${critical ? `, ${critical} critical` : ""}`}
      >
        <Bell className="h-5 w-5" />
        {critical > 0 && (
          <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white">
            {critical}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-[950] w-80 rounded-md border bg-white shadow-lg">
          <div className="border-b px-3 py-2 text-sm font-medium">Supply chain alerts</div>
          <div className="max-h-80 overflow-auto">
            {(data ?? []).map((a, i) => (
              <div key={i} className="flex gap-2 border-b px-3 py-2 text-xs last:border-0">
                <span
                  className={cn("mt-1 h-2 w-2 shrink-0 rounded-full", a.severity === "critical" ? "bg-red-600" : a.severity === "warning" ? "bg-amber-500" : "bg-blue-500")}
                />
                <span>{a.message}</span>
              </div>
            ))}
            {data?.length === 0 && <div className="px-3 py-3 text-xs text-muted-foreground">No alerts</div>}
          </div>
          <button
            className="w-full border-t px-3 py-2 text-xs font-medium text-primary hover:bg-secondary"
            onClick={() => {
              setOpen(false);
              navigate("/");
            }}
          >
            View all on dashboard
          </button>
        </div>
      )}
    </div>
  );
}

function Topbar({ onMenu }: { onMenu: () => void }) {
  const { pathname } = useLocation();
  const { user } = useAuth();
  const title = useMemo(() => NAV.find((n) => (n.end ? pathname === n.to : pathname.startsWith(n.to)))?.label ?? "SupplyChainX", [pathname]);
  const { data: status, loading, reload } = useApi<{ completed: { finished_at: string | null } | null }>("/api/forecast/status");
  const { data: summary } = useApi<{ sales_range: { to: string | null } }>("/api/data/summary");
  return (
    <header className="sticky top-0 z-[800] flex h-16 items-center gap-3 border-b bg-white/95 px-4 backdrop-blur sm:px-6">
      <button className="rounded-md p-2 hover:bg-secondary lg:hidden" onClick={onMenu} aria-label="Open menu">
        <Menu className="h-5 w-5" />
      </button>
      <h2 className="mr-2 hidden shrink-0 text-base font-semibold sm:block">{title}</h2>
      <GlobalSearch />
      <div className="ml-auto flex items-center gap-1 sm:gap-3">
        <div className="hidden text-right text-[11px] leading-tight text-muted-foreground xl:block">
          <div>{new Date().toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}</div>
          <div>Data through {fmt.date(summary?.sales_range.to)}</div>
        </div>
        <button
          onClick={reload}
          className="hidden items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted-foreground hover:bg-secondary md:flex"
          title="Forecast model last trained"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          <span>Models: {status?.completed?.finished_at ? fmt.datetime(status.completed.finished_at) : "not trained"}</span>
        </button>
        <Notifications />
        <div className="hidden items-center gap-2 border-l pl-3 sm:flex">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
            {user?.full_name.split(" ").map((s) => s[0]).slice(0, 2).join("")}
          </div>
          <div className="hidden leading-tight lg:block">
            <div className="text-sm font-medium">{user?.full_name}</div>
            <div className="text-[11px] text-muted-foreground">{user?.role}</div>
          </div>
        </div>
      </div>
    </header>
  );
}

export function AppLayout() {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen">
      <Sidebar open={open} onClose={() => setOpen(false)} />
      <div className="lg:pl-64">
        <Topbar onMenu={() => setOpen(true)} />
        <main className="mx-auto max-w-[1600px] p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

