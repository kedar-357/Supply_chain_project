import { ArrowDownRight, ArrowUpRight, Boxes, DollarSign, FlaskConical, Minus, Save, Trash2, TrendingUp, Truck, Warehouse } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip, EmptyState, ErrorState, PageHeader, Spinner } from "@/components/common";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { GRID, SERIES, STATUS, cn, fmt } from "@/lib/utils";

interface Metric { metric: string; label: string; unit: string; baseline: number; scenario: number; change: number; change_pct: number | null; favourable: boolean | null }
interface Result {
  id?: number; name?: string; scenario_type: string; scenario_label: string; config: Record<string, unknown>; as_of?: string;
  forecast_source?: string; metrics: Metric[]; details?: Record<string, unknown>; narrative?: string[]; created_at?: string;
}
interface Simple { id: number; code: string; name: string }

const TYPES = [
  { key: "supplier_delay", label: "Supplier delay", icon: Truck, desc: "A supplier's deliveries arrive late" },
  { key: "demand_increase", label: "Demand increase", icon: TrendingUp, desc: "Predicted demand rises suddenly" },
  { key: "transport_cost", label: "Transport cost", icon: DollarSign, desc: "Per-km freight tariffs change" },
  { key: "budget_limit", label: "Procurement budget", icon: Boxes, desc: "Purchasing budget is capped" },
  { key: "capacity_constraint", label: "Warehouse capacity", icon: Warehouse, desc: "Storage capacity is reduced" },
] as const;

export function formatMetric(v: number, unit: string): string {
  if (unit === "INR") return fmt.inrCompact(v);
  if (unit === "%") return `${v.toFixed(2)}%`;
  return fmt.num(v);
}

export default function Scenarios() {
  const { can } = useAuth();
  const suppliers = useApi<Simple[]>("/api/suppliers");
  const warehouses = useApi<Simple[]>("/api/warehouses");
  const categories = useApi<{ id: number; name: string }[]>("/api/categories");
  const saved = useApi<Result[]>("/api/scenarios");
  const [type, setType] = useState<string>("supplier_delay");
  const [cfg, setCfg] = useState<Record<string, string>>({ supplier_id: "", delay_days: "7", increase_pct: "20", category: "", transport_pct: "15", budget: "500000", warehouse_id: "", reduction_pct: "20" });
  const [name, setName] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [comparison, setComparison] = useState<{ scenarios: Result[]; table: (Record<string, number | string | boolean | null> & { metric: string; label: string; unit: string; baseline: number })[] } | null>(null);

  const buildConfig = (): Record<string, unknown> | string => {
    const n = (k: string) => Number(cfg[k]);
    switch (type) {
      case "supplier_delay":
        if (!(n("delay_days") >= 1 && n("delay_days") <= 90)) return "Delay must be between 1 and 90 days";
        return { supplier_id: cfg.supplier_id ? n("supplier_id") : null, delay_days: n("delay_days") };
      case "demand_increase":
        if (!(n("increase_pct") > -90 && n("increase_pct") <= 300)) return "Change must be between -90% and 300%";
        return { increase_pct: n("increase_pct"), category: cfg.category || null };
      case "transport_cost":
        if (!(n("transport_pct") > -50 && n("transport_pct") <= 300)) return "Change must be between -50% and 300%";
        return { increase_pct: n("transport_pct") };
      case "budget_limit":
        if (!(n("budget") >= 0) || cfg.budget === "") return "Enter a non-negative budget";
        return { budget: n("budget") };
      default:
        if (!(n("reduction_pct") > 0 && n("reduction_pct") < 100)) return "Reduction must be between 0 and 100%";
        return { reduction_pct: n("reduction_pct"), warehouse_id: cfg.warehouse_id ? n("warehouse_id") : null };
    }
  };

  const run = async (save: boolean) => {
    const config = buildConfig();
    if (typeof config === "string") return setError(config);
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<Result>("/api/scenarios/simulate", { scenario_type: type, config, save, name: name || null });
      setResult(r.data);
      if (save) saved.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const compare = async () => {
    try {
      const r = await api.post("/api/scenarios/compare", { scenario_ids: compareIds });
      setComparison(r.data);
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  const remove = async (id: number) => {
    await api.delete(`/api/scenarios/${id}`).catch((e) => setError(errorMessage(e)));
    setCompareIds((c) => c.filter((x) => x !== id));
    saved.reload();
  };
  const open = async (id: number) => {
    try {
      const r = await api.get<Result>(`/api/scenarios/${id}`);
      setResult(r.data);
      setType(r.data.scenario_type);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const field = (k: string, label: string, props: Record<string, unknown> = {}) => (
    <div className="space-y-1">
      <Label htmlFor={`s-${k}`}>{label}</Label>
      <Input id={`s-${k}`} type="number" value={cfg[k]} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })} {...props} />
    </div>
  );

  return (
    <>
      <PageHeader
        title="Scenario Simulator"
        description="What-if analysis on current inventory, forecasts, suppliers and the logistics network. Each scenario reruns the same backend calculations with modified inputs — deterministic, no random numbers."
      />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>1. Choose a scenario</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-2">
              {TYPES.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setType(t.key)}
                  className={cn("flex items-start gap-3 rounded-md border p-2.5 text-left transition-colors hover:bg-secondary/60", type === t.key && "border-primary bg-accent/60")}
                >
                  <t.icon className="mt-0.5 h-4 w-4 text-primary" />
                  <div><div className="text-sm font-medium">{t.label}</div><div className="text-xs text-muted-foreground">{t.desc}</div></div>
                </button>
              ))}
            </div>
            <div className="space-y-3 border-t pt-3">
              <div className="text-sm font-medium">2. Set parameters</div>
              {type === "supplier_delay" && (
                <>
                  <div className="space-y-1"><Label htmlFor="s-sup">Supplier</Label>
                    <Select id="s-sup" value={cfg.supplier_id} onChange={(e) => setCfg({ ...cfg, supplier_id: e.target.value })}><option value="">All suppliers</option>{suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</Select></div>
                  <div className="flex gap-1.5">{[3, 7, 15].map((d) => <Button key={d} size="sm" variant={cfg.delay_days === String(d) ? "default" : "outline"} onClick={() => setCfg({ ...cfg, delay_days: String(d) })}>+{d} days</Button>)}</div>
                  {field("delay_days", "Delay (days)", { min: 1, max: 90 })}
                </>
              )}
              {type === "demand_increase" && (
                <>
                  <div className="flex gap-1.5">{[10, 20, 30].map((d) => <Button key={d} size="sm" variant={cfg.increase_pct === String(d) ? "default" : "outline"} onClick={() => setCfg({ ...cfg, increase_pct: String(d) })}>+{d}%</Button>)}</div>
                  {field("increase_pct", "Demand change (%) — custom")}
                  <div className="space-y-1"><Label htmlFor="s-cat">Category</Label>
                    <Select id="s-cat" value={cfg.category} onChange={(e) => setCfg({ ...cfg, category: e.target.value })}><option value="">All categories</option>{categories.data?.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}</Select></div>
                </>
              )}
              {type === "transport_cost" && field("transport_pct", "Per-km tariff change (%)")}
              {type === "budget_limit" && field("budget", "Procurement budget (₹)", { min: 0, step: 10000 })}
              {type === "capacity_constraint" && (
                <>
                  {field("reduction_pct", "Capacity reduction (%)", { min: 1, max: 99 })}
                  <div className="space-y-1"><Label htmlFor="s-wh">Warehouse</Label>
                    <Select id="s-wh" value={cfg.warehouse_id} onChange={(e) => setCfg({ ...cfg, warehouse_id: e.target.value })}><option value="">All warehouses</option>{warehouses.data?.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}</Select></div>
                </>
              )}
              <div className="space-y-1"><Label htmlFor="s-name">Scenario name (for saving)</Label><Input id="s-name" placeholder="optional" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} /></div>
              <div className="flex gap-2">
                <Button className="flex-1" onClick={() => run(false)} disabled={busy || !can("simulate")}>{busy ? <Spinner /> : <FlaskConical />} Simulate</Button>
                <Button variant="outline" onClick={() => run(true)} disabled={busy || !can("simulate")}><Save /> Simulate & save</Button>
              </div>
              {!can("simulate") && <p className="text-xs text-muted-foreground">Your role can view saved scenarios but not run simulations.</p>}
              {error && <Alert variant="error">{error}</Alert>}
            </div>
          </CardContent>
        </Card>

        <div className="space-y-4 xl:col-span-2">
          {!result && !busy && <Card><EmptyState title="Run a scenario to see its impact" icon={<FlaskConical className="h-5 w-5" />}>Results compare the baseline (current plan) with the modified scenario on the same KPIs.</EmptyState></Card>}
          {busy && <Card className="p-10 text-center text-sm text-muted-foreground"><Spinner className="mx-auto mb-2" />Running backend calculations…</Card>}
          {result && !busy && <ScenarioResult result={result} />}
        </div>
      </div>

      <Card className="mt-4">
        <CardHeader className="flex-row items-center justify-between">
          <div><CardTitle>Saved scenarios</CardTitle><CardDescription>Select two or more to compare side by side.</CardDescription></div>
          <Button size="sm" disabled={compareIds.length < 2} onClick={compare}>Compare {compareIds.length || ""}</Button>
        </CardHeader>
        <CardContent>
          {saved.error && <ErrorState message={saved.error} onRetry={saved.reload} />}
          {saved.data?.length === 0 && <EmptyState title="No saved scenarios yet">Use “Simulate & save”.</EmptyState>}
          {!!saved.data?.length && (
            <Table>
              <THead><TR><TH className="w-8" /><TH>Name</TH><TH>Type</TH><TH>Created</TH><TH className="text-right">Service level</TH><TH className="text-right">Items at risk</TH><TH className="text-right">Revenue at risk</TH><TH /></TR></THead>
              <TBody>
                {saved.data?.map((s) => {
                  const m = (k: string) => s.metrics.find((x) => x.metric === k);
                  return (
                    <TR key={s.id}>
                      <TD><input type="checkbox" aria-label={`Compare ${s.name}`} checked={compareIds.includes(s.id!)} onChange={(e) => setCompareIds(e.target.checked ? [...compareIds, s.id!] : compareIds.filter((x) => x !== s.id))} /></TD>
                      <TD><button className="font-medium text-primary hover:underline" onClick={() => open(s.id!)}>{s.name}</button></TD>
                      <TD><Badge variant="secondary">{s.scenario_label}</Badge></TD>
                      <TD className="text-xs">{fmt.datetime(s.created_at)}</TD>
                      <TD className="tabular text-right text-xs">{m("expected_service_level") ? `${m("expected_service_level")!.scenario.toFixed(2)}%` : "–"}</TD>
                      <TD className="tabular text-right text-xs">{m("items_at_risk")?.scenario ?? "–"}</TD>
                      <TD className="tabular text-right text-xs">{fmt.inrCompact(m("revenue_at_risk")?.scenario)}</TD>
                      <TD>{can("simulate") && <Button size="icon" variant="ghost" onClick={() => remove(s.id!)} aria-label={`Delete ${s.name}`}><Trash2 className="text-red-700" /></Button>}</TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
          {comparison && <ComparisonView data={comparison} />}
        </CardContent>
      </Card>
    </>
  );
}

function Change({ m }: { m: Metric }) {
  if (Math.abs(m.change) < 1e-9) return <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Minus className="h-3 w-3" /> no change</span>;
  const Icon = m.change > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium", m.favourable ? "text-green-700" : "text-red-700")}>
      <Icon className="h-3.5 w-3.5" />
      {m.unit === "%" ? `${m.change > 0 ? "+" : ""}${m.change.toFixed(2)} pp` : `${m.change > 0 ? "+" : ""}${formatMetric(m.change, m.unit)}`}
      {m.change_pct != null && m.unit !== "%" && <span className="opacity-75">({m.change_pct > 0 ? "+" : ""}{m.change_pct.toFixed(1)}%)</span>}
      <span className="sr-only">{m.favourable ? "favourable" : "unfavourable"}</span>
    </span>
  );
}

function ScenarioResult({ result }: { result: Result }) {
  const chart = result.metrics
    .filter((m) => m.change_pct != null && Math.abs(m.change_pct) > 0.001)
    .map((m) => ({ name: m.label, value: m.unit === "%" ? m.change : (m.change_pct as number), favourable: m.favourable, unit: m.unit === "%" ? "pp" : "%" }));
  const d = result.details ?? {};
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>{result.name ?? result.scenario_label}{result.id && <Badge variant="success" className="ml-2">saved</Badge>}</CardTitle>
          <CardDescription>
            Config: {Object.entries(result.config).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k.replace(/_/g, " ")} = ${v}`).join(", ") || "defaults"}
            {result.as_of && ` · as of ${fmt.date(result.as_of)}`} {result.forecast_source && `· demand: ${result.forecast_source}`}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {result.narrative && (
            <ul className="space-y-1.5 rounded-md bg-secondary/50 p-3 text-sm">
              {result.narrative.map((n, i) => <li key={i} className="flex gap-2"><span className="text-primary">•</span>{n}</li>)}
            </ul>
          )}
          <div className="grid gap-4 2xl:grid-cols-2">
            <Table>
              <THead><TR><TH>KPI</TH><TH className="text-right">Baseline</TH><TH className="text-right">Scenario</TH><TH>Change</TH></TR></THead>
              <TBody>
                {result.metrics.map((m) => (
                  <TR key={m.metric}>
                    <TD className="text-xs font-medium">{m.label}</TD>
                    <TD className="tabular whitespace-nowrap text-right text-xs">{formatMetric(m.baseline, m.unit)}</TD>
                    <TD className="tabular whitespace-nowrap text-right text-xs font-semibold">{formatMetric(m.scenario, m.unit)}</TD>
                    <TD className="whitespace-nowrap"><Change m={m} /></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <div>
              <div className="mb-1 text-xs font-medium">Change vs baseline (% for amounts, percentage points for rates)</div>
              {chart.length === 0 ? <EmptyState title="No KPI changed" /> : (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={chart} layout="vertical" margin={{ left: 10, right: 24 }}>
                    <CartesianGrid stroke={GRID} horizontal={false} />
                    <XAxis type="number" tickFormatter={(v) => `${v}`} tickLine={false} axisLine={{ stroke: GRID }} />
                    <YAxis type="category" dataKey="name" width={150} fontSize={10} tickLine={false} axisLine={false} />
                    <ReferenceLine x={0} stroke="#6b7280" />
                    <Tooltip cursor={{ fill: "#f3f4f6" }} content={({ active, payload }) => active && payload?.length ? (
                      <div className="rounded-md border bg-white px-3 py-2 text-xs shadow-md">
                        <div className="font-medium">{payload[0].payload.name}</div>
                        <div>{(payload[0].value as number) > 0 ? "+" : ""}{(payload[0].value as number).toFixed(2)} {payload[0].payload.unit} · {payload[0].payload.favourable ? "favourable" : "unfavourable"}</div>
                      </div>
                    ) : null} />
                    <Bar dataKey="value" radius={4} barSize={14}>
                      {chart.map((c, i) => <Cell key={i} fill={c.favourable ? STATUS.good : STATUS.critical} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
              <p className="text-[11px] text-muted-foreground">Green = favourable, red = unfavourable (also shown with arrows in the table).</p>
            </div>
          </div>
        </CardContent>
      </Card>
      <ScenarioDetails type={result.scenario_type} d={d} />
    </>
  );
}

type Row = Record<string, string | number | boolean | null>;
function DetailTable({ title, rows, cols, description }: { title: string; rows?: Row[]; cols: [string, string, ((v: Row) => ReactNode)?][]; description?: string }) {
  if (!rows?.length) return null;
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle>{description && <CardDescription>{description}</CardDescription>}</CardHeader>
      <CardContent className="max-h-96 overflow-auto">
        <Table>
          <THead><TR>{cols.map(([, l]) => <TH key={l}>{l}</TH>)}</TR></THead>
          <TBody>
            {rows.map((r, i) => (
              <TR key={i}>{cols.map(([k, l, f]) => <TD key={l} className={cn("text-xs", k !== "reason" && k !== "new_route" && "whitespace-nowrap")}>{f ? f(r) : typeof r[k] === "number" ? fmt.num1(r[k] as number) : String(r[k] ?? "–")}</TD>)}</TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function ScenarioDetails({ type, d }: { type: string; d: Record<string, unknown> }) {
  const atRisk = d.at_risk_items as Row[] | undefined;
  const riskCols: [string, string, ((v: Row) => ReactNode)?][] = [
    ["sku", "SKU"], ["warehouse_code", "WH"], ["supplier_name", "Supplier"], ["current_stock", "Stock"],
    ["stockout_in_days_base", "Stockout (base)", (r) => (r.stockout_in_days_base == null ? "> 90 d" : `${r.stockout_in_days_base} d`)],
    ["stockout_in_days", "Stockout (scenario)", (r) => (r.stockout_in_days == null ? "> 90 d" : `${r.stockout_in_days} d`)],
    ["additional_shortage", "Extra shortage", (r) => fmt.num(r.additional_shortage as number)],
    ["revenue_at_risk", "Revenue at risk", (r) => fmt.inr(r.revenue_at_risk as number)],
    ["newly_at_risk", "New critical", (r) => (r.newly_at_risk ? <Badge variant="critical">yes</Badge> : "–")],
  ];
  return (
    <div className="space-y-4">
      {type === "supplier_delay" && (
        <>
          <DetailTable title="Products at risk of stockout" rows={atRisk} cols={riskCols} />
          <DetailTable title="Alternative supplier requirements" description="Other approved suppliers for affected items and whether their lead time beats the projected stockout." rows={d.alternative_suppliers as Row[]} cols={[
            ["sku", "SKU"], ["warehouse_code", "WH"], ["alternative_supplier", "Alternative"], ["alternative_lead_time_days", "Lead (d)"],
            ["arrives_before_stockout", "In time?", (r) => <Badge variant={r.arrives_before_stockout ? "success" : "critical"}>{r.arrives_before_stockout ? "yes" : "no"}</Badge>],
            ["quantity_needed", "Qty needed"], ["extra_cost_vs_primary", "Extra cost", (r) => fmt.inr(r.extra_cost_vs_primary as number)],
          ]} />
        </>
      )}
      {type === "demand_increase" && (
        <>
          {d.revised_requirements != null && (
            <Card className="p-4 text-sm">
              Target inventory: <strong>{fmt.num((d.revised_requirements as Row).baseline_target_units as number)}</strong> →{" "}
              <strong>{fmt.num((d.revised_requirements as Row).target_inventory_units as number)}</strong> units (safety stock change{" "}
              {fmt.num((d.revised_requirements as Row).safety_stock_change_units as number)}).
            </Card>
          )}
          <DetailTable title="Recommended replenishment" rows={d.recommended_replenishment as Row[]} cols={[
            ["sku", "SKU"], ["warehouse_code", "WH"], ["current_stock", "Stock"], ["reorder_point", "ROP"], ["recommended_qty", "Order qty"],
            ["order_cost", "Cost", (r) => fmt.inr(r.order_cost as number)], ["reason", "Reason"],
          ]} />
          <DetailTable title="Stockout risk under higher demand" rows={atRisk} cols={riskCols} />
        </>
      )}
      {type === "transport_cost" && (
        <>
          <DetailTable title="Lane costs" rows={d.lanes as Row[]} cols={[
            ["store", "Store"], ["serving_warehouse", "From"], ["new_route", "Route"],
            ["baseline_monthly_cost", "Baseline / mo", (r) => fmt.inr(r.baseline_monthly_cost as number)],
            ["new_monthly_cost", "Scenario / mo", (r) => fmt.inr(r.new_monthly_cost as number)],
            ["utilisation", "Truck util.", (r) => (r.utilisation == null ? "–" : `${((r.utilisation as number) * 100).toFixed(0)}%`)],
            ["route_changed", "Re-routed", (r) => (r.route_changed ? <Badge variant="warning">yes</Badge> : "–")],
          ]} />
          {!!(d.suggestions as string[] | undefined)?.length && (
            <Card><CardHeader><CardTitle>Alternative route suggestions</CardTitle></CardHeader><CardContent><ul className="list-disc space-y-1 pl-5 text-sm">{(d.suggestions as string[]).map((s, i) => <li key={i}>{s}</li>)}</ul></CardContent></Card>
          )}
        </>
      )}
      {type === "budget_limit" && (
        <DetailTable title="Best feasible purchasing plan within budget" description="Prioritised by stockout urgency × margin × ABC importance per rupee (MILP)." rows={d.purchasing_plan as Row[]} cols={[
          ["sku", "SKU"], ["warehouse_code", "WH"], ["abc_class", "ABC"], ["status", "Status"], ["need_units", "Need"],
          ["recommended_qty", "Funded qty"], ["unmet_units", "Unmet"], ["order_cost", "Cost", (r) => fmt.inr(r.order_cost as number)], ["reason", "Reason"],
        ]} />
      )}
      {type === "capacity_constraint" && (
        <>
          <DetailTable title="Warehouse capacity" rows={d.warehouses as Row[]} cols={[
            ["code", "WH"], ["capacity_units", "Capacity"], ["new_capacity", "New capacity"], ["occupancy", "Stock + in transit"],
            ["overflow_units", "Overflow", (r) => <span className={cn((r.overflow_units as number) > 0 && "font-semibold text-red-700")}>{fmt.num(r.overflow_units as number)}</span>],
            ["free_after", "Free space"],
          ]} />
          <DetailTable title="Products that cannot be stored" rows={d.cannot_store as Row[]} cols={[
            ["sku", "SKU"], ["warehouse_code", "WH"], ["abc_class", "ABC"], ["status", "Status"], ["units_to_release", "Units to release"],
            ["units_redistributed", "Redistributed"], ["units_without_space", "No space"],
          ]} />
          <DetailTable title="Redistribution recommendations" rows={d.redistribution as Row[]} cols={[["sku", "SKU"], ["from", "From"], ["to", "To"], ["units", "Units"], ["to_days_of_cover_before", "Target cover before (d)"]]} />
        </>
      )}
    </div>
  );
}

function ComparisonView({ data }: { data: { scenarios: Result[]; table: (Record<string, number | string | boolean | null> & { metric: string; label: string; unit: string; baseline: number })[] } }) {
  const chartMetrics = ["expected_service_level", "items_at_risk", "revenue_at_risk", "monthly_operational_cost"];
  const chart = data.table
    .filter((r) => chartMetrics.includes(r.metric))
    .map((r) => {
      const o: Record<string, number | string> = { name: r.label };
      data.scenarios.forEach((s) => {
        const v = r[String(s.id)] as number | null;
        o[s.name ?? String(s.id)] = r.unit === "%" ? +((v ?? 0) - r.baseline).toFixed(2) : r.baseline ? +((((v ?? 0) - r.baseline) / r.baseline) * 100).toFixed(2) : 0;
      });
      return o;
    });
  return (
    <div className="mt-4 grid gap-4 border-t pt-4 lg:grid-cols-2">
      <Table>
        <THead><TR><TH>KPI</TH><TH className="text-right">Baseline</TH>{data.scenarios.map((s) => <TH key={s.id} className="text-right">{s.name}</TH>)}</TR></THead>
        <TBody>
          {data.table.map((r) => (
            <TR key={r.metric}>
              <TD className="text-xs font-medium">{r.label}</TD>
              <TD className="tabular text-right text-xs">{formatMetric(r.baseline, r.unit)}</TD>
              {data.scenarios.map((s) => <TD key={s.id} className="tabular text-right text-xs">{r[String(s.id)] == null ? "–" : formatMetric(r[String(s.id)] as number, r.unit)}</TD>)}
            </TR>
          ))}
        </TBody>
      </Table>
      <div>
        <div className="mb-1 text-xs font-medium">Change vs baseline (% · pp for service level)</div>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={chart} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="name" fontSize={10} interval={0} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis width={44} tickLine={false} axisLine={false} />
            <ReferenceLine y={0} stroke="#6b7280" />
            <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip formatter={(v) => v.toFixed(2)} />} />
            <Legend />
            {data.scenarios.slice(0, 6).map((s, i) => <Bar key={s.id} dataKey={s.name ?? String(s.id)} fill={SERIES[i]} radius={[3, 3, 0, 0]} />)}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
