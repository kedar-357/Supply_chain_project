import { ArrowUpDown, Calculator, FilePlus2, Pencil, Play, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, ChartTooltip, EmptyState, ErrorState, LoadingBlock, PageHeader, Pagination, Spinner, StatusBadge } from "@/components/common";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { GRID, SERIES, STATUS_LABEL, cn, fmt } from "@/lib/utils";

interface Row {
  product_id: number; warehouse_id: number; sku: string; name: string; category: string; warehouse_code: string;
  current_stock: number; incoming_units: number; avg_daily_demand: number; forecast_daily_demand: number; predicted_demand_30d: number;
  lead_time_days: number; lead_time_sd_days: number; safety_stock: number; reorder_point: number; eoq: number; abc_class: string;
  status: string; stockout_in_days: number | null; days_of_cover: number | null; inventory_value: number; supplier_name: string;
}
interface InvResp {
  items: Row[]; total: number; page: number; page_size: number; status_counts: Record<string, number>; abc_counts: Record<string, number>;
  as_of: string; forecast_source: string; parameters: Record<string, number>;
}
interface Warehouse { id: number; code: string }
interface OptItem {
  item_id?: number; product_id: number; sku: string; name: string; warehouse_code: string; supplier_name: string; current_stock: number;
  incoming_units?: number; recommended_qty: number; reorder_point: number; target_level: number; order_cost: number; reason: string;
  abc_class?: string; status?: string; eoq?: number; moq?: number; need_units?: number;
}
interface OptResp {
  optimization_id: number | null; created_at?: string; items: OptItem[];
  summary?: { solver_status: string; total_order_cost: number; total_units: number; items_ordered: number; unmet_need_units: number; candidates?: number };
  params?: Record<string, unknown>;
}

const COLUMNS: { key: keyof Row; label: string; num?: boolean }[] = [
  { key: "sku", label: "SKU" }, { key: "name", label: "Product" }, { key: "warehouse_code", label: "WH" },
  { key: "current_stock", label: "Stock", num: true }, { key: "avg_daily_demand", label: "Avg/day", num: true },
  { key: "predicted_demand_30d", label: "Pred. 30d", num: true }, { key: "lead_time_days", label: "Lead (d)", num: true },
  { key: "safety_stock", label: "Safety stock", num: true }, { key: "reorder_point", label: "ROP", num: true },
  { key: "eoq", label: "EOQ", num: true }, { key: "abc_class", label: "ABC" }, { key: "status", label: "Status" },
  { key: "stockout_in_days", label: "Stockout in", num: true },
];

export default function Inventory() {
  const [sp] = useSearchParams();
  return (
    <>
      <PageHeader
        title="Inventory Optimization"
        description="Safety stock, reorder points and EOQ per product and warehouse; ABC classification; stockout and overstock detection; and a linear-programming order optimizer."
      />
      <Tabs defaultValue={sp.get("tab") ?? "levels"}>
        <TabsList>
          <TabsTrigger value="levels">Inventory levels</TabsTrigger>
          <TabsTrigger value="optimize">Order optimization</TabsTrigger>
          <TabsTrigger value="abc">ABC analysis</TabsTrigger>
          <TabsTrigger value="orders">Purchase orders</TabsTrigger>
        </TabsList>
        <TabsContent value="levels"><LevelsTab initialWarehouse={sp.get("warehouse")} /></TabsContent>
        <TabsContent value="optimize"><OptimizeTab /></TabsContent>
        <TabsContent value="abc"><AbcTab /></TabsContent>
        <TabsContent value="orders"><OrdersTab /></TabsContent>
      </Tabs>
    </>
  );
}

function LevelsTab({ initialWarehouse }: { initialWarehouse: string | null }) {
  const warehouses = useApi<Warehouse[]>("/api/warehouses");
  const [filters, setFilters] = useState({ status: "", warehouse_id: initialWarehouse ?? "", abc_class: "", q: "" });
  const [sort, setSort] = useState("stockout_in_days");
  const [page, setPage] = useState(1);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, string>>({ service_level: "", ordering_cost: "", holding_cost_rate: "", lead_time_days: "" });
  const [applied, setApplied] = useState<Record<string, string>>({});
  const query = useMemo(() => {
    const q: Record<string, unknown> = { page, page_size: 25, sort };
    Object.entries(filters).forEach(([k, v]) => v && (q[k] = v));
    Object.entries(applied).forEach(([k, v]) => v && (q[k] = v));
    return q;
  }, [filters, sort, page, applied]);
  const { data, loading, error, reload } = useApi<InvResp>("/api/inventory", query);
  const { can } = useAuth();
  const canEdit = can("manage_master_data");
  const [editing, setEditing] = useState<{ row: Row; qty: string; reason: string } | null>(null);
  const [editErr, setEditErr] = useState<string | null>(null);
  const [savingStock, setSavingStock] = useState(false);
  const saveStock = async () => {
    if (!editing) return;
    const q = Number(editing.qty);
    if (editing.qty === "" || !Number.isInteger(q) || q < 0) return setEditErr("Quantity must be a whole number ≥ 0");
    if (editing.reason.trim().length < 3) return setEditErr("Give a reason (at least 3 characters)");
    setSavingStock(true);
    setEditErr(null);
    try {
      await api.put(`/api/inventory/${editing.row.product_id}/${editing.row.warehouse_id}`, { quantity_on_hand: q, reason: editing.reason.trim() });
      setEditing(null);
      reload();
    } catch (e) {
      setEditErr(errorMessage(e));
    } finally {
      setSavingStock(false);
    }
  };
  useEffect(() => setPage(1), [filters, applied]);

  const toggleSort = (k: string) => setSort((s) => (s === k ? `-${k}` : k));
  const overrideError = (() => {
    const sl = overrides.service_level;
    if (sl && (Number(sl) < 50 || Number(sl) >= 100)) return "Service level must be between 50 and 99.9 %";
    if (overrides.holding_cost_rate && (Number(overrides.holding_cost_rate) <= 0 || Number(overrides.holding_cost_rate) > 100)) return "Holding cost must be 0–100 % per year";
    if (overrides.lead_time_days && Number(overrides.lead_time_days) <= 0) return "Lead time must be positive";
    if (overrides.ordering_cost && Number(overrides.ordering_cost) < 0) return "Ordering cost cannot be negative";
    return null;
  })();

  return (
    <>
      <div className="mb-3 flex flex-wrap gap-2">
        {(["healthy", "low", "critical", "out_of_stock", "overstock"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setFilters((f) => ({ ...f, status: f.status === s ? "" : s }))}
            className={cn("rounded-full border bg-white px-3 py-1 text-xs", filters.status === s && "border-primary bg-accent text-accent-foreground")}
          >
            {STATUS_LABEL[s]} <span className="tabular ml-1 font-semibold">{data?.status_counts[s] ?? 0}</span>
          </button>
        ))}
        <span className="ml-auto self-center text-xs text-muted-foreground">
          ABC: A {data?.abc_counts.A ?? 0} · B {data?.abc_counts.B ?? 0} · C {data?.abc_counts.C ?? 0} products
        </span>
      </div>
      <Card>
        <CardContent className="pt-4">
          <div className="mb-3 flex flex-wrap items-end gap-2">
            <Input placeholder="Search SKU or product" className="w-56" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} aria-label="Search" />
            <Select className="w-40" value={filters.warehouse_id} onChange={(e) => setFilters({ ...filters, warehouse_id: e.target.value })} aria-label="Warehouse">
              <option value="">All warehouses</option>
              {warehouses.data?.map((w) => <option key={w.id} value={w.id}>{w.code}</option>)}
            </Select>
            <Select className="w-32" value={filters.abc_class} onChange={(e) => setFilters({ ...filters, abc_class: e.target.value })} aria-label="ABC class">
              <option value="">All classes</option>
              <option value="A">Class A</option>
              <option value="B">Class B</option>
              <option value="C">Class C</option>
            </Select>
            <Button variant="outline" className="ml-auto" onClick={() => setParamsOpen(true)}>
              <Calculator /> Adjust parameters
              {Object.values(applied).some(Boolean) && <Badge variant="warning">custom</Badge>}
            </Button>
          </div>
          {error && <ErrorState message={error} onRetry={reload} />}
          {loading && !data ? (
            <LoadingBlock rows={8} />
          ) : (
            <div className={cn(loading && "opacity-60")}>
              <Table>
                <THead>
                  <TR>
                    {COLUMNS.map((c) => (
                      <TH key={c.key} className={cn(c.num && "text-right")}>
                        <button className="inline-flex items-center gap-1 uppercase" onClick={() => toggleSort(c.key)}>
                          {c.label}
                          <ArrowUpDown className={cn("h-3 w-3", sort.replace("-", "") === c.key ? "text-primary" : "opacity-40")} />
                        </button>
                      </TH>
                    ))}
                    {canEdit && <TH><span className="sr-only">Actions</span></TH>}
                  </TR>
                </THead>
                <TBody>
                  {data?.items.map((r) => (
                    <TR key={`${r.product_id}-${r.warehouse_id}`}>
                      <TD className="whitespace-nowrap font-mono text-xs">{r.sku}</TD>
                      <TD className="max-w-[200px] truncate" title={`${r.name} · ${r.category} · supplier ${r.supplier_name}`}>{r.name}</TD>
                      <TD className="whitespace-nowrap text-xs">{r.warehouse_code}</TD>
                      <TD className="tabular text-right">{fmt.num(r.current_stock)}{r.incoming_units > 0 && <div className="text-[10px] text-muted-foreground">+{fmt.num(r.incoming_units)} incoming</div>}</TD>
                      <TD className="tabular text-right">{fmt.num1(r.avg_daily_demand)}</TD>
                      <TD className="tabular text-right">{fmt.num(r.predicted_demand_30d)}</TD>
                      <TD className="tabular text-right" title={`σ = ${r.lead_time_sd_days?.toFixed(1)} days`}>{fmt.num1(r.lead_time_days)}</TD>
                      <TD className="tabular text-right">{fmt.num(r.safety_stock)}</TD>
                      <TD className="tabular text-right">{fmt.num(r.reorder_point)}</TD>
                      <TD className="tabular text-right">{fmt.num(r.eoq)}</TD>
                      <TD><Badge variant="secondary">{r.abc_class}</Badge></TD>
                      <TD className="whitespace-nowrap"><StatusBadge status={r.status} /></TD>
                      <TD className={cn("tabular text-right", r.stockout_in_days != null && r.stockout_in_days < r.lead_time_days && "font-semibold text-red-700")}>
                        {r.stockout_in_days != null ? `${r.stockout_in_days} d` : "> 90 d"}
                      </TD>
                      {canEdit && (
                        <TD>
                          <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`Adjust stock of ${r.sku} at ${r.warehouse_code}`}
                            onClick={() => setEditing({ row: r, qty: String(r.current_stock), reason: "" })}>
                            <Pencil />
                          </Button>
                        </TD>
                      )}
                    </TR>
                  ))}
                </TBody>
              </Table>
              {data?.items.length === 0 && <EmptyState title="No items match the filters" />}
              {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
            </div>
          )}
          {data && (
            <p className="mt-3 text-[11px] text-muted-foreground">
              Safety stock = z·√(L·σd² + d²·σL²) at {((data.parameters.service_level ?? 0.95) * 100).toFixed(1)}% service level · ROP = d·L + SS ·
              EOQ = √(2DS/H). Demand rate d from {data.forecast_source}; lead time L from actual supplier deliveries. As of {fmt.date(data.as_of)}.
            </p>
          )}
        </CardContent>
      </Card>
      <Dialog
        open={!!editing}
        onOpenChange={(o) => { if (!o) { setEditing(null); setEditErr(null); } }}
        title={editing ? `Adjust stock · ${editing.row.sku} at ${editing.row.warehouse_code}` : "Adjust stock"}
        description="Records a stock correction (e.g. after a physical count). The backend writes inventory.csv and keeps a backup of the previous file."
      >
        {editing && (
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="adj-qty">Quantity on hand (currently {fmt.num(editing.row.current_stock)})</Label>
              <Input id="adj-qty" type="number" min={0} step={1} value={editing.qty} onChange={(e) => setEditing({ ...editing, qty: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="adj-reason">Reason</Label>
              <Input id="adj-reason" placeholder="e.g. cycle count, damaged goods" maxLength={200} value={editing.reason} onChange={(e) => setEditing({ ...editing, reason: e.target.value })} />
            </div>
            {editErr && <Alert variant="error">{editErr}</Alert>}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setEditing(null)}>Cancel</Button>
              <Button onClick={saveStock} disabled={savingStock}>{savingStock && <Spinner />} Save</Button>
            </div>
          </div>
        )}
      </Dialog>
      <Dialog open={paramsOpen} onOpenChange={setParamsOpen} title="Adjust inventory parameters" description="Recalculate safety stock, ROP and EOQ with manager-defined values. Leave a field blank to use the configured/per-product value.">
        <div className="grid grid-cols-2 gap-3">
          {[
            ["service_level", "Service level (%)", "95"],
            ["ordering_cost", "Ordering cost per order (₹)", "per product"],
            ["holding_cost_rate", "Holding cost (% of cost / yr)", "per product"],
            ["lead_time_days", "Lead time (days)", "from deliveries"],
          ].map(([k, label, ph]) => (
            <div key={k} className="space-y-1">
              <Label htmlFor={k}>{label}</Label>
              <Input id={k} type="number" step="any" placeholder={ph} value={overrides[k]} onChange={(e) => setOverrides({ ...overrides, [k]: e.target.value })} />
            </div>
          ))}
        </div>
        {overrideError && <Alert className="mt-3" variant="error">{overrideError}</Alert>}
        <div className="mt-4 flex justify-end gap-2">
          <Button
            variant="outline"
            onClick={() => {
              setOverrides({ service_level: "", ordering_cost: "", holding_cost_rate: "", lead_time_days: "" });
              setApplied({});
              setParamsOpen(false);
            }}
          >
            Reset
          </Button>
          <Button
            disabled={!!overrideError}
            onClick={() => {
              setApplied({
                service_level: overrides.service_level ? String(Number(overrides.service_level) / 100) : "",
                ordering_cost: overrides.ordering_cost,
                holding_cost_rate: overrides.holding_cost_rate ? String(Number(overrides.holding_cost_rate) / 100) : "",
                lead_time_days: overrides.lead_time_days,
              });
              setParamsOpen(false);
            }}
          >
            Recalculate
          </Button>
        </div>
      </Dialog>
    </>
  );
}

function OptimizeTab() {
  const { can } = useAuth();
  const latest = useApi<OptResp>("/api/inventory/recommendations");
  const [result, setResult] = useState<OptResp | null>(null);
  const [budget, setBudget] = useState("");
  const [serviceLevel, setServiceLevel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [poMsg, setPoMsg] = useState<string | null>(null);
  const shown = result ?? latest.data;

  const run = async () => {
    if (budget && Number(budget) < 0) return setError("Budget cannot be negative");
    setBusy(true);
    setError(null);
    setPoMsg(null);
    try {
      const r = await api.post<OptResp>("/api/inventory/optimize", {
        budget: budget ? Number(budget) : null,
        service_level: serviceLevel ? Number(serviceLevel) / 100 : null,
      });
      setResult(r.data);
      setSelected(new Set());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const createDrafts = async () => {
    if (!shown?.optimization_id) return;
    setPoMsg(null);
    const chosen = shown.items.filter((i, idx) => i.recommended_qty > 0 && (selected.size === 0 || selected.has(idx)));
    try {
      const r = await api.post<{ po_number: string; lines: number; total_cost: number }[]>("/api/inventory/draft-purchase-orders", {
        optimization_id: shown.optimization_id,
        item_ids: chosen.map((i) => i.item_id).filter((x): x is number => x != null),
      });
      setPoMsg(`Created ${r.data.length} draft purchase order(s): ${r.data.map((p) => p.po_number).join(", ")}. Drafts are not sent to suppliers — review them under Purchase orders.`);
    } catch (e) {
      setPoMsg(`Error: ${errorMessage(e)}`);
    }
  };

  const items = (shown?.items ?? []).filter((i) => i.recommended_qty > 0 || i.reason.includes("Not funded"));
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <div className="w-48 space-y-1">
            <Label htmlFor="budget">Purchasing budget (₹)</Label>
            <Input id="budget" type="number" min={0} placeholder="Unlimited" value={budget} onChange={(e) => setBudget(e.target.value)} />
          </div>
          <div className="w-40 space-y-1">
            <Label htmlFor="sl">Service level (%)</Label>
            <Input id="sl" type="number" min={50} max={99.9} step="0.5" placeholder="95" value={serviceLevel} onChange={(e) => setServiceLevel(e.target.value)} />
          </div>
          <Button onClick={run} disabled={busy || !can("optimize")}>
            {busy ? <Spinner /> : <Play />} Run optimizer
          </Button>
          <p className="basis-full text-[11px] text-muted-foreground">
            Mixed-integer LP (PuLP/CBC): minimises unmet need (weighted by margin × ABC class × urgency) + ordering cost, subject to budget, warehouse capacity and supplier MOQs. Lots are rounded toward EOQ.
          </p>
        </CardContent>
      </Card>
      {error && <Alert variant="error">{error}</Alert>}
      {latest.loading && !shown && <LoadingBlock />}
      {shown?.summary && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            ["Solver status", shown.summary.solver_status],
            ["Items to order", fmt.num(shown.summary.items_ordered)],
            ["Units", fmt.num(shown.summary.total_units)],
            ["Order value", fmt.inrCompact(shown.summary.total_order_cost)],
            ["Unmet need (units)", fmt.num(shown.summary.unmet_need_units)],
          ].map(([l, v]) => (
            <Card key={l} className="p-3">
              <div className="text-xs text-muted-foreground">{l}</div>
              <div className="tabular mt-1 text-lg font-semibold">{v}</div>
            </Card>
          ))}
        </div>
      )}
      {shown && shown.optimization_id == null && <Card><EmptyState title="No optimization run yet">Run the optimizer to get recommended order quantities.</EmptyState></Card>}
      {items.length > 0 && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recommended orders {shown?.created_at && <span className="ml-2 text-xs font-normal text-muted-foreground">run #{shown.optimization_id} · {fmt.datetime(shown.created_at)}</span>}</CardTitle>
            {can("optimize") && (
              <Button size="sm" onClick={createDrafts}>
                <FilePlus2 /> Create draft PO{selected.size ? ` (${selected.size} selected)` : "s"}
              </Button>
            )}
          </CardHeader>
          <CardContent>
            {poMsg && <Alert className="mb-3" variant={poMsg.startsWith("Error") ? "error" : "success"}>{poMsg}</Alert>}
            <Table>
              <THead>
                <TR>
                  <TH className="w-8"><span className="sr-only">Select</span></TH>
                  <TH>Product</TH>
                  <TH>WH</TH>
                  <TH>Supplier</TH>
                  <TH className="text-right">Current stock</TH>
                  <TH className="text-right">Recommended order</TH>
                  <TH className="text-right">Reorder point</TH>
                  <TH className="text-right">Cost</TH>
                  <TH>Reason</TH>
                </TR>
              </THead>
              <TBody>
                {(shown?.items ?? []).map((i, idx) =>
                  i.recommended_qty > 0 || i.reason.includes("Not funded") ? (
                    <TR key={idx}>
                      <TD>
                        <input
                          type="checkbox"
                          aria-label={`Select ${i.sku}`}
                          disabled={i.recommended_qty === 0}
                          checked={selected.has(idx)}
                          onChange={(e) => {
                            const s = new Set(selected);
                            if (e.target.checked) s.add(idx);
                            else s.delete(idx);
                            setSelected(s);
                          }}
                        />
                      </TD>
                      <TD><div className="whitespace-nowrap font-mono text-xs">{i.sku}</div><div className="max-w-[180px] truncate text-xs text-muted-foreground">{i.name}</div></TD>
                      <TD className="whitespace-nowrap text-xs">{i.warehouse_code}</TD>
                      <TD className="max-w-[140px] truncate text-xs">{i.supplier_name}</TD>
                      <TD className="tabular text-right">{fmt.num(i.current_stock)}</TD>
                      <TD className="tabular text-right font-semibold">{fmt.num(i.recommended_qty)}</TD>
                      <TD className="tabular text-right">{fmt.num(i.reorder_point)}</TD>
                      <TD className="tabular text-right">{fmt.inr(i.order_cost)}</TD>
                      <TD className="min-w-[280px] max-w-[420px] text-xs">{i.reason}</TD>
                    </TR>
                  ) : null,
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function AbcTab() {
  const { data, loading, error, reload } = useApi<{ items: { sku: string; name: string; annual_consumption_value: number; cumulative_share: number; value_share: number; abc_class: string; annual_units: number }[]; method: string }>(
    "/api/inventory/abc",
  );
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <ChartCard className="xl:col-span-2" title="Pareto of annual consumption value" description={data?.method} loading={loading} error={error} onRetry={reload} height={340}>
        <ResponsiveContainer width="100%" height={340}>
          <ComposedChart data={data?.items ?? []} margin={{ top: 8, right: 8, left: 0, bottom: 30 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="sku" angle={-60} textAnchor="end" interval={1} fontSize={10} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} width={44} tickLine={false} axisLine={false} />
            <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip formatter={(v) => `${v.toFixed(1)}%`} />} />
            <Legend verticalAlign="top" />
            <ReferenceLine y={80} stroke="#9ca3af" strokeDasharray="3 3" label={{ value: "A / B (80%)", fontSize: 10, fill: "#6b7280", position: "insideBottomRight" }} />
            <ReferenceLine y={95} stroke="#9ca3af" strokeDasharray="3 3" label={{ value: "B / C (95%)", fontSize: 10, fill: "#6b7280", position: "insideBottomRight" }} />
            <Bar name="Share of annual consumption value" dataKey="value_share" fill={SERIES[0]} radius={[3, 3, 0, 0]} />
            <Line name="Cumulative share" dataKey="cumulative_share" stroke={SERIES[1]} strokeWidth={2} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartCard>
      <Card>
        <CardHeader><CardTitle>Classes</CardTitle></CardHeader>
        <CardContent className="max-h-[420px] overflow-auto">
          <Table>
            <THead><TR><TH>SKU</TH><TH className="text-right">Value</TH><TH className="text-right">Cum.</TH><TH>Class</TH></TR></THead>
            <TBody>
              {data?.items.map((i) => (
                <TR key={i.sku}>
                  <TD className="text-xs" title={i.name}><span className="font-mono">{i.sku}</span></TD>
                  <TD className="tabular text-right text-xs">{fmt.inrCompact(i.annual_consumption_value)}</TD>
                  <TD className="tabular text-right text-xs">{i.cumulative_share.toFixed(1)}%</TD>
                  <TD><Badge variant={i.abc_class === "A" ? "info" : "secondary"}>{i.abc_class}</Badge></TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function OrdersTab() {
  const { can } = useAuth();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const { data, loading, error, reload } = useApi<{ total: number; page: number; page_size: number; items: { id: number; po_number: string; supplier: string; warehouse: string; order_date: string; expected_date: string; status: string; source: string; total_cost: number; lines: { sku: string; quantity: number }[] }[] }>(
    "/api/purchase-orders",
    { status: status || undefined, page, page_size: 20 },
  );
  const del = async (id: number) => {
    try {
      await api.delete(`/api/purchase-orders/${id}`);
      reload();
    } catch (e) {
      alert(errorMessage(e));
    }
  };
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="mb-3 flex gap-2">
          <Select className="w-44" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status filter">
            <option value="">All statuses</option>
            {["draft", "placed", "in_transit", "received", "cancelled"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </Select>
        </div>
        {error && <ErrorState message={error} onRetry={reload} />}
        {loading && !data ? <LoadingBlock /> : (
          <>
            <Table>
              <THead><TR><TH>PO number</TH><TH>Supplier</TH><TH>WH</TH><TH>Ordered</TH><TH>Expected</TH><TH>Lines</TH><TH className="text-right">Value</TH><TH>Source</TH><TH>Status</TH><TH /></TR></THead>
              <TBody>
                {data?.items.map((p) => (
                  <TR key={p.id}>
                    <TD className="font-mono text-xs">{p.po_number}</TD>
                    <TD className="max-w-[160px] truncate">{p.supplier}</TD>
                    <TD>{p.warehouse}</TD>
                    <TD className="text-xs">{fmt.date(p.order_date)}</TD>
                    <TD className="text-xs">{fmt.date(p.expected_date)}</TD>
                    <TD className="text-xs" title={p.lines.map((l) => `${l.sku}×${l.quantity}`).join(", ")}>{p.lines.length}</TD>
                    <TD className="tabular text-right">{fmt.inr(p.total_cost)}</TD>
                    <TD className="text-xs capitalize">{p.source}</TD>
                    <TD><Badge variant={p.status === "draft" ? "warning" : p.status === "received" ? "success" : "info"} className="capitalize">{p.status.replace("_", " ")}</Badge></TD>
                    <TD>
                      {p.status === "draft" && can("optimize") && (
                        <Button size="icon" variant="ghost" onClick={() => del(p.id)} aria-label={`Delete draft ${p.po_number}`}><Trash2 className="text-red-700" /></Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
          </>
        )}
      </CardContent>
    </Card>
  );
}
