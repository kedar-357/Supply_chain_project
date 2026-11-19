import { AlertTriangle, Boxes, Clock, IndianRupee, PackageX, Repeat, ShoppingCart, Truck } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartCard, ChartTooltip, EmptyState, KpiCard, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { GRID, SERIES, STATUS, cn, fmt } from "@/lib/utils";

interface Summary {
  period: { start: string; end: string; days: number };
  total_inventory_value: number;
  total_products: number;
  below_reorder_level: number;
  predicted_stockouts: number;
  avg_supplier_delivery_days: number | null;
  on_time_delivery_rate: number | null;
  deliveries_in_period: number;
  monthly_demand_units: number;
  demand_change_pct: number | null;
  inventory_turnover: number | null;
  turnover_note: string;
  forecast_source: string;
}
interface Trends {
  granularity: string;
  series: { date: string; actual?: number; backtest?: number; forecast?: number }[];
  note: string;
}
interface Distribution {
  inventory_status: { name: string; key: string; value: number }[];
  warehouses: { warehouse_code: string; warehouse_name: string; inventory_value: number; utilization_pct: number; units: number; capacity_units: number }[];
  suppliers: { name: string; code: string; on_time_rate: number; performance_score: number; risk_category: string | null }[];
}
interface AlertItem {
  severity: "critical" | "warning" | "info";
  type: string;
  message: string;
}
interface Activity {
  activities: { id: number; action: string; description: string; user: string; created_at: string }[];
  purchase_orders: { po_number: string; supplier: string; warehouse: string; order_date: string; status: string; source: string; total_cost: number }[];
}

const RANGES = [
  { label: "7D", days: 7 },
  { label: "30D", days: 30 },
  { label: "90D", days: 90 },
  { label: "1Y", days: 365 },
];
const STATUS_COLOR: Record<string, string> = { healthy: STATUS.good, low: STATUS.warning, overstock: STATUS.info };

export default function Dashboard() {
  const [days, setDaysState] = useState<number | null>(30);
  // The selected range is a per-user preference persisted by the backend (data/preferences.json).
  useEffect(() => {
    api
      .get<{ dashboard_range_days?: number }>("/api/preferences")
      .then((r) => r.data.dashboard_range_days && setDaysState(r.data.dashboard_range_days))
      .catch(() => undefined);
  }, []);
  const setDays = (d: number | null) => {
    setDaysState(d);
    if (d) api.put("/api/preferences", { dashboard_range_days: d }).catch(() => undefined);
  };
  const [custom, setCustom] = useState<{ start: string; end: string }>({ start: "", end: "" });
  const params = days ? { days } : { start: custom.start, end: custom.end };
  const ready = days != null || (custom.start && custom.end);
  const trendParams = days ? { days: Math.max(days, 90) } : params;

  const summary = useApi<Summary>(ready ? "/api/dashboard/summary" : null, params);
  const trends = useApi<Trends>(ready ? "/api/dashboard/trends" : null, trendParams);
  const dist = useApi<Distribution>("/api/dashboard/distribution");
  const alerts = useApi<AlertItem[]>("/api/dashboard/alerts", { limit: 12 });
  const activity = useApi<Activity>("/api/dashboard/activity");
  const s = summary.data;

  return (
    <>
      <PageHeader
        title="Overview Dashboard"
        description="Management view of inventory health, demand, supplier reliability and operational alerts — computed live by the backend from the data files."
        actions={
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Date range">
            <div className="inline-flex rounded-md border bg-white p-0.5">
              {RANGES.map((r) => (
                <button
                  key={r.label}
                  onClick={() => setDays(r.days)}
                  className={cn("rounded px-2.5 py-1 text-xs font-medium", days === r.days ? "bg-primary text-white" : "text-muted-foreground hover:bg-secondary")}
                >
                  {r.label}
                </button>
              ))}
              <button
                onClick={() => setDays(null)}
                className={cn("rounded px-2.5 py-1 text-xs font-medium", days == null ? "bg-primary text-white" : "text-muted-foreground hover:bg-secondary")}
              >
                Custom
              </button>
            </div>
            {days == null && (
              <div className="flex items-center gap-1.5">
                <Input type="date" className="h-8 w-36 text-xs" value={custom.start} onChange={(e) => setCustom({ ...custom, start: e.target.value })} aria-label="Start date" />
                <span className="text-xs text-muted-foreground">to</span>
                <Input type="date" className="h-8 w-36 text-xs" value={custom.end} onChange={(e) => setCustom({ ...custom, end: e.target.value })} aria-label="End date" />
              </div>
            )}
          </div>
        }
      />

      {summary.error && <p className="mb-3 text-sm text-red-700">{summary.error}</p>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard loading={summary.loading} label="Total inventory value" icon={<IndianRupee />} value={fmt.inrCompact(s?.total_inventory_value)} hint="at unit cost, all warehouses" />
        <KpiCard loading={summary.loading} label="Total products" icon={<Boxes />} value={fmt.num(s?.total_products)} hint="active SKUs" />
        <KpiCard
          loading={summary.loading}
          label="Below reorder level"
          icon={<AlertTriangle />}
          tone={s && s.below_reorder_level > 0 ? "warning" : "good"}
          value={fmt.num(s?.below_reorder_level)}
          hint="item-locations at/below ROP"
        />
        <KpiCard
          loading={summary.loading}
          label="Predicted stockouts"
          icon={<PackageX />}
          tone={s && s.predicted_stockouts > 0 ? "critical" : "good"}
          value={fmt.num(s?.predicted_stockouts)}
          hint="before next replenishment"
        />
        <KpiCard loading={summary.loading} label="Avg supplier delivery time" icon={<Clock />} value={s?.avg_supplier_delivery_days != null ? `${s.avg_supplier_delivery_days} days` : "–"} hint={`order → receipt, ${s?.deliveries_in_period ?? 0} deliveries`} />
        <KpiCard
          loading={summary.loading}
          label="On-time delivery rate"
          icon={<Truck />}
          tone={s?.on_time_delivery_rate != null && s.on_time_delivery_rate < 70 ? "warning" : "good"}
          value={fmt.pct(s?.on_time_delivery_rate)}
          hint="in selected period"
        />
        <KpiCard loading={summary.loading} label="Monthly demand" icon={<ShoppingCart />} value={`${fmt.num(s?.monthly_demand_units)} units`} delta={s?.demand_change_pct} hint="vs previous period" />
        <KpiCard loading={summary.loading} label="Inventory turnover" icon={<Repeat />} value={s?.inventory_turnover != null ? `${s.inventory_turnover}×` : "–"} hint="annual COGS ÷ inventory" />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <ChartCard
          className="xl:col-span-2"
          title="Sales trend: actual vs predicted demand"
          description={trends.data ? `${trends.data.granularity === "weekly" ? "Weekly" : "Daily"} units · ${trends.data.note}` : undefined}
          loading={trends.loading}
          error={trends.error}
          onRetry={trends.reload}
        >
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={trends.data?.series ?? []} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="date" tickFormatter={fmt.dateShort} minTickGap={28} tickLine={false} axisLine={{ stroke: GRID }} />
              <YAxis tickFormatter={(v) => fmt.num(v)} width={56} tickLine={false} axisLine={false} />
              <Tooltip content={<ChartTooltip formatter={(v) => `${fmt.num(v)} units`} labelFormatter={(l) => fmt.date(String(l))} />} />
              <Legend iconType="plainline" />
              <Line name="Actual sales" dataKey="actual" stroke={SERIES[0]} strokeWidth={2} dot={false} connectNulls={false} />
              <Line name="Model backtest (holdout)" dataKey="backtest" stroke={SERIES[1]} strokeWidth={2} dot={false} strokeDasharray="4 3" />
              <Line name="Forecast" dataKey="forecast" stroke={SERIES[2]} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Inventory distribution" description="Item-locations by stock status" loading={dist.loading} error={dist.error} onRetry={dist.reload}>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={dist.data?.inventory_status ?? []} dataKey="value" nameKey="name" innerRadius={60} outerRadius={90} paddingAngle={2} stroke="#fff" strokeWidth={2}>
                {(dist.data?.inventory_status ?? []).map((d) => (
                  <Cell key={d.key} fill={STATUS_COLOR[d.key]} />
                ))}
              </Pie>
              <Tooltip content={<ChartTooltip formatter={(v) => `${v} item-locations`} />} />
            </PieChart>
          </ResponsiveContainer>
          <div className="mt-1 grid grid-cols-3 gap-2 text-center">
            {(dist.data?.inventory_status ?? []).map((d) => (
              <div key={d.key}>
                <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
                  <span className="h-2 w-2 rounded-full" style={{ background: STATUS_COLOR[d.key] }} />
                  {d.name}
                </div>
                <div className="tabular text-lg font-semibold">{d.value}</div>
              </div>
            ))}
          </div>
        </ChartCard>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <ChartCard title="Supplier reliability" description="Historical on-time delivery rate (%)" loading={dist.loading} error={dist.error}>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={dist.data?.suppliers ?? []} layout="vertical" margin={{ left: 8, right: 24 }}>
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tickLine={false} axisLine={{ stroke: GRID }} />
              <YAxis type="category" dataKey="code" width={58} tickLine={false} axisLine={false} />
              <Tooltip
                cursor={{ fill: "#f3f4f6" }}
                content={<ChartTooltip formatter={(v) => `${v}%`} labelFormatter={(l) => dist.data?.suppliers.find((x) => x.code === l)?.name ?? String(l)} />}
              />
              <Bar name="On-time rate" dataKey="on_time_rate" fill={SERIES[0]} radius={[0, 4, 4, 0]} barSize={14} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Warehouse inventory" description="Inventory value by location (utilisation in tooltip)" loading={dist.loading} error={dist.error}>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={dist.data?.warehouses ?? []} margin={{ top: 8, right: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="warehouse_code" tickLine={false} axisLine={{ stroke: GRID }} />
              <YAxis tickFormatter={(v) => fmt.inrCompact(v)} width={70} tickLine={false} axisLine={false} />
              <Tooltip
                cursor={{ fill: "#f3f4f6" }}
                content={({ active, payload, label }) => {
                  const w = dist.data?.warehouses.find((x) => x.warehouse_code === label);
                  if (!active || !payload?.length || !w) return null;
                  return (
                    <div className="rounded-md border bg-white px-3 py-2 text-xs shadow-md">
                      <div className="font-medium">{w.warehouse_name}</div>
                      <div>Value: {fmt.inr(w.inventory_value)}</div>
                      <div>
                        Units: {fmt.num(w.units)} / {fmt.num(w.capacity_units)} ({w.utilization_pct}% utilised)
                      </div>
                    </div>
                  );
                }}
              />
              <Bar name="Inventory value" dataKey="inventory_value" fill={SERIES[0]} radius={[4, 4, 0, 0]} barSize={36} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <Card className="flex flex-col">
          <CardHeader>
            <CardTitle>Supply chain alerts</CardTitle>
          </CardHeader>
          <CardContent className="max-h-[300px] flex-1 overflow-auto">
            {alerts.data?.length === 0 && <EmptyState title="No active alerts" />}
            <ul className="space-y-2">
              {(alerts.data ?? []).map((a, i) => (
                <li key={i} className="flex gap-2.5 rounded-md border px-2.5 py-2 text-xs">
                  <Badge variant={a.severity === "critical" ? "critical" : a.severity === "warning" ? "warning" : "info"} className="h-fit capitalize">
                    {a.severity}
                  </Badge>
                  <span>{a.message}</span>
                </li>
              ))}
            </ul>
            {alerts.error && <p className="text-xs text-red-700">{alerts.error}</p>}
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {(activity.data?.activities ?? []).map((a) => (
                <li key={a.id} className="flex items-start justify-between gap-3 py-2 text-sm">
                  <div>
                    <div>{a.description}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {a.user} · <span className="capitalize">{a.action.replace(/_/g, " ")}</span>
                    </div>
                  </div>
                  <span className="shrink-0 text-[11px] text-muted-foreground">{fmt.datetime(a.created_at)}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recent purchase orders</CardTitle>
            <Button variant="link" size="sm" asChild>
              <a href="/inventory?tab=orders">View all</a>
            </Button>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR>
                  <TH>PO</TH>
                  <TH>Supplier</TH>
                  <TH>WH</TH>
                  <TH>Ordered</TH>
                  <TH className="text-right">Value</TH>
                  <TH>Status</TH>
                </TR>
              </THead>
              <TBody>
                {(activity.data?.purchase_orders ?? []).map((p) => (
                  <TR key={p.po_number}>
                    <TD className="font-mono text-xs">{p.po_number}</TD>
                    <TD className="max-w-[160px] truncate">{p.supplier}</TD>
                    <TD>{p.warehouse}</TD>
                    <TD className="text-xs">{fmt.date(p.order_date)}</TD>
                    <TD className="tabular text-right">{fmt.inrCompact(p.total_cost)}</TD>
                    <TD>
                      <Badge variant={p.status === "draft" ? "warning" : p.status === "received" ? "success" : "info"} className="capitalize">
                        {p.status.replace("_", " ")}
                      </Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>
      {s && <p className="mt-4 text-[11px] text-muted-foreground">Demand figures use: {s.forecast_source}. Inventory turnover = {s.turnover_note}.</p>}
    </>
  );
}
