import { Brain, GitCompare, History, Play, Scale } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, ChartTooltip, ErrorState, LoadingBlock, PageHeader, RiskBadge, SimulatedNote, Spinner } from "@/components/common";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { GRID, SERIES, cn, fmt } from "@/lib/utils";

interface SupplierPerf {
  supplier_id: number; code: string; name: string; city: string; region: string; rank: number; performance_score: number;
  on_time_rate: number | null; avg_delay_days: number; avg_lead_time_days: number | null; lead_time_std_days: number | null;
  avg_quality_score: number | null; quality_rating: number; avg_unit_cost_index: number | null; purchase_order_count: number;
  products_supplied: number; total_spend: number; ml_delay_probability: number | null; risk_category: string | null; trend: string | null;
  score_on_time: number; score_quality: number; score_cost: number; score_lead_time_consistency: number; product_unit_cost?: number | null;
}
interface PerfResp { weights: Record<string, number>; methodology: string; suppliers: SupplierPerf[] }
interface Simple { id: number; code?: string; sku?: string; name: string }

const WEIGHT_LABELS: Record<string, string> = { on_time: "On-time delivery", quality: "Product quality", cost: "Cost competitiveness", lead_time_consistency: "Lead-time consistency" };

export default function Suppliers() {
  const [sp] = useSearchParams();
  return (
    <>
      <PageHeader
        title="Supplier Analytics"
        description="Historical performance scoring (descriptive) and ML-based late-delivery prediction (predictive) are shown separately."
      />
      <Tabs defaultValue={sp.get("tab") ?? "performance"}>
        <TabsList>
          <TabsTrigger value="performance"><Scale className="mr-1.5 h-3.5 w-3.5" />Historical performance</TabsTrigger>
          <TabsTrigger value="prediction"><Brain className="mr-1.5 h-3.5 w-3.5" />Delay prediction (ML)</TabsTrigger>
          <TabsTrigger value="compare"><GitCompare className="mr-1.5 h-3.5 w-3.5" />Compare</TabsTrigger>
          <TabsTrigger value="history"><History className="mr-1.5 h-3.5 w-3.5" />Delivery history</TabsTrigger>
        </TabsList>
        <TabsContent value="performance"><PerformanceTab highlight={sp.get("supplier")} /></TabsContent>
        <TabsContent value="prediction"><PredictionTab /></TabsContent>
        <TabsContent value="compare"><CompareTab /></TabsContent>
        <TabsContent value="history"><HistoryTab /></TabsContent>
      </Tabs>
    </>
  );
}

function PerformanceTab({ highlight }: { highlight: string | null }) {
  const [weights, setWeights] = useState<Record<string, number> | null>(null);
  const [applied, setApplied] = useState<Record<string, number> | undefined>(undefined);
  const { data, loading, error, reload } = useApi<PerfResp>("/api/suppliers/performance", applied);
  useEffect(() => {
    if (data && !weights) setWeights(Object.fromEntries(Object.entries(data.weights).map(([k, v]) => [k, Math.round(v * 100)])));
  }, [data, weights]);
  const total = weights ? Object.values(weights).reduce((a, b) => a + b, 0) : 100;

  return (
    <div className="grid gap-4 xl:grid-cols-4">
      <Card className="xl:col-span-1">
        <CardHeader>
          <CardTitle>Score weights</CardTitle>
          <CardDescription>Adjust and re-rank. Weights are normalised to 100%.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {weights &&
            Object.entries(weights).map(([k, v]) => (
              <div key={k}>
                <div className="flex justify-between text-xs">
                  <label htmlFor={`w-${k}`}>{WEIGHT_LABELS[k]}</label>
                  <span className="tabular font-medium">{total ? Math.round((v / total) * 100) : 0}%</span>
                </div>
                <input id={`w-${k}`} type="range" min={0} max={100} value={v} onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })} className="w-full accent-blue-600" />
              </div>
            ))}
          {total === 0 && <Alert variant="error">At least one weight must be above zero.</Alert>}
          <Button
            className="w-full"
            disabled={!weights || total === 0}
            onClick={() => weights && setApplied(Object.fromEntries(Object.entries(weights).map(([k, v]) => [k, v / total])))}
          >
            Apply weights
          </Button>
          <p className="text-[11px] text-muted-foreground">{data?.methodology}</p>
        </CardContent>
      </Card>
      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle>Supplier ranking</CardTitle>
          <CardDescription>
            <Badge variant="secondary" className="mr-1">Historical score</Badge> from past deliveries ·
            <Badge variant="info" className="mx-1">ML prediction</Badge> delay probability for a typical next order
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error && <ErrorState message={error} onRetry={reload} />}
          {loading && !data ? <LoadingBlock rows={8} /> : (
            <div className={cn(loading && "opacity-60")}>
              <Table>
                <THead>
                  <TR>
                    <TH>#</TH><TH>Supplier</TH><TH>Score</TH><TH className="text-right">On-time</TH><TH className="text-right">Avg delay</TH>
                    <TH className="text-right">Lead time</TH><TH className="text-right">Quality</TH><TH className="text-right">Cost index</TH>
                    <TH className="text-right">POs</TH><TH>ML risk</TH><TH>YoY trend</TH>
                  </TR>
                </THead>
                <TBody>
                  {data?.suppliers.map((s) => (
                    <TR key={s.supplier_id} className={cn(String(s.supplier_id) === highlight && "bg-accent")}>
                      <TD className="tabular font-semibold">{s.rank}</TD>
                      <TD className="min-w-[200px]"><div className="font-medium">{s.name}</div><div className="text-[11px] text-muted-foreground">{s.code} · {s.city}, {s.region}</div></TD>
                      <TD className="min-w-[120px]">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-16 rounded bg-secondary"><div className="h-1.5 rounded bg-primary" style={{ width: `${s.performance_score}%` }} /></div>
                          <span className="tabular text-xs font-semibold">{s.performance_score.toFixed(1)}</span>
                        </div>
                      </TD>
                      <TD className="tabular text-right">{fmt.ratioPct(s.on_time_rate)}</TD>
                      <TD className="tabular text-right">{s.avg_delay_days.toFixed(1)} d</TD>
                      <TD className="tabular whitespace-nowrap text-right">{fmt.num1(s.avg_lead_time_days)} ± {fmt.num1(s.lead_time_std_days)} d</TD>
                      <TD className="tabular text-right">{fmt.num1(s.avg_quality_score)}%</TD>
                      <TD className="tabular text-right">{s.avg_unit_cost_index?.toFixed(3)}</TD>
                      <TD className="tabular text-right">{s.purchase_order_count}</TD>
                      <TD>
                        <RiskBadge risk={s.risk_category} />
                        {s.ml_delay_probability != null && <div className="tabular mt-0.5 text-[10px] text-muted-foreground">p(late) {fmt.ratioPct(s.ml_delay_probability)}</div>}
                      </TD>
                      <TD>
                        {s.trend && <Badge variant={s.trend === "deteriorating" ? "critical" : s.trend === "improving" ? "success" : "secondary"}>{s.trend}</Badge>}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              <p className="mt-2 text-[11px] text-muted-foreground">Cost index = supplier unit cost ÷ cheapest supplier for the same product (1.000 = cheapest). Trend compares the last 90 days with the same period last year.</p>
            </div>
          )}
          <SimulatedNote className="mt-2" />
        </CardContent>
      </Card>
    </div>
  );
}

interface DelayModel {
  trained: boolean; version?: number; deployed_model?: string; labels?: Record<string, string>;
  models?: Record<string, { accuracy: number; precision: number; recall: number; f1: number; roc_auc: number | null; n: number; positive_rate: number }>;
  feature_importance?: Record<string, number>; train_from?: string; train_to?: string; test_from?: string; test_to?: string; n_train?: number; n_test?: number;
  calibration?: { bin: string; predicted: number; observed: number; n: number }[];
}
interface DelayPrediction {
  delay_probability: number; risk_category: string; contributing_factors: { factor: string; effect: number; direction: string }[];
  supplier: { name: string }; model: string; model_version: number;
  inputs: { distance_km: number; quoted_lead_days: number; supplier_history_late_rate: number; supplier_history_deliveries: number; category: string };
}

function PredictionTab() {
  const { can } = useAuth();
  const model = useApi<DelayModel>("/api/suppliers/delay-model");
  const suppliers = useApi<Simple[]>("/api/suppliers");
  const warehouses = useApi<Simple[]>("/api/warehouses");
  const products = useApi<{ items: Simple[] }>("/api/products", { page_size: 500 });
  const openRisk = useApi<{ po_number: string; supplier: string; expected_date: string; quantity: number; delay_probability: number; risk_category: string; contributing_factors: { factor: string; direction: string }[] }[]>("/api/suppliers/open-order-risk");
  const [form, setForm] = useState({ supplier_id: "", warehouse_id: "", product_id: "", quantity: "500", order_date: "" });
  const [result, setResult] = useState<DelayPrediction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [training, setTraining] = useState(false);

  const predict = async () => {
    if (!form.supplier_id || !form.warehouse_id) return setError("Choose a supplier and a destination warehouse");
    if (!(Number(form.quantity) > 0)) return setError("Quantity must be a positive number");
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<DelayPrediction>("/api/suppliers/predict-delay", {
        supplier_id: Number(form.supplier_id), warehouse_id: Number(form.warehouse_id), quantity: Number(form.quantity),
        product_id: form.product_id ? Number(form.product_id) : null, order_date: form.order_date || null,
      });
      setResult(r.data);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const retrain = async () => {
    setTraining(true);
    try {
      await api.post("/api/suppliers/train-delay-model");
      model.reload();
      openRisk.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setTraining(false);
    }
  };
  const m = model.data;
  const fi = Object.entries(m?.feature_importance ?? {}).map(([k, v]) => ({ name: k, value: +(v * 100).toFixed(1) }));

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">Predict a delivery delay <Badge variant="info">ML prediction</Badge></CardTitle>
          <CardDescription>Probability that a new purchase order arrives after the promised date.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1"><Label htmlFor="d-sup">Supplier</Label>
            <Select id="d-sup" value={form.supplier_id} onChange={(e) => setForm({ ...form, supplier_id: e.target.value })}>
              <option value="">Select…</option>{suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}
            </Select></div>
          <div className="space-y-1"><Label htmlFor="d-wh">Destination warehouse</Label>
            <Select id="d-wh" value={form.warehouse_id} onChange={(e) => setForm({ ...form, warehouse_id: e.target.value })}>
              <option value="">Select…</option>{warehouses.data?.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
            </Select></div>
          <div className="space-y-1"><Label htmlFor="d-prod">Product (optional)</Label>
            <Select id="d-prod" value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })}>
              <option value="">Any</option>{products.data?.items.map((p) => <option key={p.id} value={p.id}>{p.sku} · {p.name}</option>)}
            </Select></div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1"><Label htmlFor="d-qty">Order quantity</Label><Input id="d-qty" type="number" min={1} value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="d-date">Order date</Label><Input id="d-date" type="date" value={form.order_date} onChange={(e) => setForm({ ...form, order_date: e.target.value })} /></div>
          </div>
          <Button className="w-full" onClick={predict} disabled={busy || !m?.trained || !can("run_predictions")}>{busy ? <Spinner /> : <Play />} Predict delay risk</Button>
          {error && <Alert variant="error">{error}</Alert>}
          {result && (
            <div className="rounded-md border p-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-xs text-muted-foreground">Delay probability</div>
                  <div className="tabular text-3xl font-semibold">{(result.delay_probability * 100).toFixed(0)}%</div>
                </div>
                <RiskBadge risk={result.risk_category} />
              </div>
              <div className="mt-3 text-xs font-medium">Main contributing factors</div>
              <ul className="mt-1 space-y-1">
                {result.contributing_factors.map((f) => (
                  <li key={f.factor} className="flex justify-between text-xs">
                    <span>{f.factor}</span>
                    <span className={cn("tabular font-medium", f.effect > 0 ? "text-red-700" : "text-green-700")}>{f.effect > 0 ? "+" : ""}{(f.effect * 100).toFixed(1)} pp</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] text-muted-foreground">
                {result.model} v{result.model_version}. Effect = change in probability if the factor were at its typical value. Inputs: {result.inputs.distance_km} km,
                quoted lead {result.inputs.quoted_lead_days} d, supplier history {fmt.ratioPct(result.inputs.supplier_history_late_rate)} late over {result.inputs.supplier_history_deliveries} deliveries.
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="xl:col-span-2">
        <CardHeader className="flex-row items-start justify-between">
          <div>
            <CardTitle>Model evaluation (held-out test set)</CardTitle>
            <CardDescription>
              {m?.trained ? `Chronological split: train ${m.train_from} → ${m.train_to} (${m.n_train} orders), test ${m.test_from} → ${m.test_to} (${m.n_test} orders). Deployed: ${m.labels?.[m.deployed_model ?? ""]}.` : "Not trained yet"}
            </CardDescription>
          </div>
          {can("train_models") && <Button size="sm" variant="outline" onClick={retrain} disabled={training}>{training ? <Spinner /> : <Brain />} Retrain</Button>}
        </CardHeader>
        <CardContent>
          {model.loading && <LoadingBlock rows={4} />}
          {m?.models && (
            <Table>
              <THead><TR><TH>Model</TH><TH className="text-right">ROC-AUC</TH><TH className="text-right">F1</TH><TH className="text-right">Precision</TH><TH className="text-right">Recall</TH><TH className="text-right">Accuracy</TH></TR></THead>
              <TBody>
                {Object.entries(m.models).map(([k, v]) => (
                  <TR key={k} className={k === m.deployed_model ? "bg-accent/50" : ""}>
                    <TD className="font-medium">{m.labels?.[k] ?? k} {k === m.deployed_model && <Badge className="ml-1">deployed</Badge>}</TD>
                    <TD className="tabular text-right">{v.roc_auc?.toFixed(3) ?? "–"}</TD>
                    <TD className="tabular text-right">{v.f1.toFixed(3)}</TD>
                    <TD className="tabular text-right">{v.precision.toFixed(3)}</TD>
                    <TD className="tabular text-right">{v.recall.toFixed(3)}</TD>
                    <TD className="tabular text-right">{v.accuracy.toFixed(3)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div>
              <div className="mb-1 text-xs font-medium">Feature importance (deployed model)</div>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={fi.slice(0, 9)} layout="vertical" margin={{ left: 10, right: 16 }}>
                  <CartesianGrid stroke={GRID} horizontal={false} />
                  <XAxis type="number" tickFormatter={(v) => `${v}%`} tickLine={false} axisLine={{ stroke: GRID }} />
                  <YAxis type="category" dataKey="name" width={150} fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip formatter={(v) => `${v}%`} />} />
                  <Bar name="Importance" dataKey="value" fill={SERIES[0]} radius={[0, 4, 4, 0]} barSize={11} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium">Calibration on the test set</div>
              <Table>
                <THead><TR><TH>Predicted band</TH><TH className="text-right">Mean predicted</TH><TH className="text-right">Observed late</TH><TH className="text-right">n</TH></TR></THead>
                <TBody>
                  {m?.calibration?.map((c) => (
                    <TR key={c.bin}><TD className="text-xs">{c.bin}</TD><TD className="tabular text-right text-xs">{fmt.ratioPct(c.predicted)}</TD><TD className="tabular text-right text-xs">{fmt.ratioPct(c.observed)}</TD><TD className="tabular text-right text-xs">{c.n}</TD></TR>
                  ))}
                </TBody>
              </Table>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">Open purchase orders at risk <Badge variant="info">ML prediction</Badge></CardTitle>
        </CardHeader>
        <CardContent>
          {openRisk.error && <ErrorState message={openRisk.error} onRetry={openRisk.reload} />}
          <Table>
            <THead><TR><TH>PO</TH><TH>Supplier</TH><TH>Expected</TH><TH className="text-right">Qty</TH><TH className="text-right">p(late)</TH><TH>Risk</TH><TH>Top factors</TH></TR></THead>
            <TBody>
              {(openRisk.data ?? []).slice(0, 15).map((o) => (
                <TR key={o.po_number}>
                  <TD className="font-mono text-xs">{o.po_number}</TD><TD>{o.supplier}</TD><TD className="text-xs">{fmt.date(o.expected_date)}</TD>
                  <TD className="tabular text-right">{fmt.num(o.quantity)}</TD><TD className="tabular text-right">{fmt.ratioPct(o.delay_probability)}</TD>
                  <TD><RiskBadge risk={o.risk_category} /></TD>
                  <TD className="text-xs text-muted-foreground">{o.contributing_factors.map((f) => `${f.factor} (${f.direction})`).join("; ")}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function CompareTab() {
  const suppliers = useApi<Simple[]>("/api/suppliers");
  const products = useApi<{ items: Simple[] }>("/api/products", { page_size: 500 });
  const [ids, setIds] = useState<number[]>([]);
  const [productId, setProductId] = useState("");
  const [res, setRes] = useState<{ suppliers: SupplierPerf[]; recommendation: { recommended_supplier_id: number; summary: string; trade_offs: string[] } } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (ids.length < 2) return setError("Select at least two suppliers");
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/api/suppliers/compare", { supplier_ids: ids, product_id: productId ? Number(productId) : null });
      setRes(r.data);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const chart = res?.suppliers.map((s) => ({
    name: s.code,
    "On-time": +(s.score_on_time * 100).toFixed(0),
    Quality: +(s.score_quality * 100).toFixed(0),
    Cost: +(s.score_cost * 100).toFixed(0),
    "Lead-time consistency": +(s.score_lead_time_consistency * 100).toFixed(0),
  }));
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-4">
          <div className="mb-2 text-xs font-medium text-muted-foreground">Select 2–8 suppliers</div>
          <div className="flex flex-wrap gap-2">
            {suppliers.data?.map((s) => (
              <label key={s.id} className={cn("flex cursor-pointer items-center gap-1.5 rounded-full border bg-white px-3 py-1 text-xs", ids.includes(s.id) && "border-primary bg-accent")}>
                <input type="checkbox" className="accent-blue-600" checked={ids.includes(s.id)} onChange={(e) => setIds(e.target.checked ? [...ids, s.id] : ids.filter((x) => x !== s.id))} />
                {s.code} · {s.name}
              </label>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <div className="w-72 space-y-1"><Label htmlFor="c-prod">Compare cost for product (optional)</Label>
              <Select id="c-prod" value={productId} onChange={(e) => setProductId(e.target.value)}><option value="">Average cost index</option>{products.data?.items.map((p) => <option key={p.id} value={p.id}>{p.sku} · {p.name}</option>)}</Select>
            </div>
            <Button onClick={run} disabled={busy}>{busy ? <Spinner /> : <GitCompare />} Compare</Button>
          </div>
          {error && <Alert className="mt-3" variant="error">{error}</Alert>}
        </CardContent>
      </Card>
      {res && (
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2">
            <CardHeader><CardTitle>Side-by-side</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <THead><TR><TH>Metric</TH>{res.suppliers.map((s) => <TH key={s.supplier_id} className="text-right">{s.code}</TH>)}</TR></THead>
                <TBody>
                  {([
                    ["Performance score", (s: SupplierPerf) => s.performance_score.toFixed(1)],
                    ["On-time rate", (s: SupplierPerf) => fmt.ratioPct(s.on_time_rate)],
                    ["Avg delay when late", (s: SupplierPerf) => `${s.avg_delay_days.toFixed(1)} d`],
                    ["Avg lead time", (s: SupplierPerf) => `${fmt.num1(s.avg_lead_time_days)} d`],
                    ["Lead-time std dev", (s: SupplierPerf) => `${fmt.num1(s.lead_time_std_days)} d`],
                    ["Quality (inspection pass)", (s: SupplierPerf) => `${fmt.num1(s.avg_quality_score)}%`],
                    [res.suppliers[0].product_unit_cost !== undefined ? "Unit cost (selected product)" : "Cost index", (s: SupplierPerf) => s.product_unit_cost !== undefined ? fmt.inr(s.product_unit_cost) : s.avg_unit_cost_index?.toFixed(3) ?? "–"],
                    ["ML delay probability", (s: SupplierPerf) => fmt.ratioPct(s.ml_delay_probability)],
                    ["Risk", (s: SupplierPerf) => s.risk_category ?? "–"],
                  ] as [string, (s: SupplierPerf) => string][]).map(([label, f]) => (
                    <TR key={label}><TD className="text-xs font-medium">{label}</TD>{res.suppliers.map((s) => <TD key={s.supplier_id} className={cn("tabular text-right text-xs", s.supplier_id === res.recommendation.recommended_supplier_id && "bg-accent/50 font-semibold")}>{f(s)}</TD>)}</TR>
                  ))}
                </TBody>
              </Table>
              <div className="mt-4">
                <div className="mb-1 text-xs font-medium">Normalised scores (0–100, higher is better)</div>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={chart} margin={{ top: 4, right: 8, left: 0 }}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="name" tickLine={false} axisLine={{ stroke: GRID }} />
                    <YAxis domain={[0, 100]} width={36} tickLine={false} axisLine={false} />
                    <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip />} />
                    <Legend />
                    {["On-time", "Quality", "Cost", "Lead-time consistency"].map((k, i) => <Bar key={k} dataKey={k} fill={SERIES[i]} radius={[3, 3, 0, 0]} />)}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Recommendation</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              <p className="font-medium">{res.recommendation.summary}</p>
              <ul className="list-disc space-y-2 pl-4 text-[13px] text-muted-foreground">
                {res.recommendation.trade_offs.map((t, i) => <li key={i}>{t}</li>)}
              </ul>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

function HistoryTab() {
  const suppliers = useApi<Simple[]>("/api/suppliers");
  const [sid, setSid] = useState("");
  const { data, loading, error, reload } = useApi<{ month: string; deliveries: number; on_time_rate: number; avg_delay: number; quality: number }[]>(
    "/api/suppliers/delivery-history",
    sid ? { supplier_id: sid } : {},
  );
  const rows = (data ?? []).map((r) => ({ ...r, on_time_pct: +(r.on_time_rate * 100).toFixed(1) }));
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ChartCard
        title="On-time delivery rate by month"
        description="Seasonal dips (monsoon Jun–Sep, festive Oct–Nov) are visible in the history"
        loading={loading}
        error={error}
        onRetry={reload}
        actions={
          <Select className="h-8 w-56 text-xs" value={sid} onChange={(e) => setSid(e.target.value)} aria-label="Supplier">
            <option value="">All suppliers</option>
            {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}
          </Select>
        }
      >
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="month" minTickGap={20} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} width={44} tickLine={false} axisLine={false} />
            <Tooltip content={<ChartTooltip formatter={(v) => `${v}%`} />} />
            <Line name="On-time rate" dataKey="on_time_pct" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Average delay by month" description="Mean (actual − promised) days; negative = early" loading={loading} error={error}>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={rows} margin={{ top: 8, right: 16, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="month" minTickGap={20} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis width={36} tickLine={false} axisLine={false} />
            <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip formatter={(v) => `${v.toFixed(2)} days`} />} />
            <Bar name="Average delay" dataKey="avg_delay" fill={SERIES[1]} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}
