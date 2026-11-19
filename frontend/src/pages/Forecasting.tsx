import { Brain, Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartCard, ChartTooltip, EmptyState, ErrorState, PageHeader, Spinner } from "@/components/common";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { GRID, SERIES, fmt } from "@/lib/utils";

interface Product { id: number; sku: string; name: string; category: string }
interface Metric { mae: number; rmse: number; mape: number | null; wape: number | null; bias: number; n: number; mape_excluded_zero_actuals: number; products_won?: number }
interface RunSummary { id: number; version: number; status: string; started_at: string | null; finished_at: string | null; error: string | null }
interface MetricsResp {
  trained: boolean;
  latest_run: RunSummary | null;
  completed_run?: RunSummary;
  portfolio?: Record<string, Metric>;
  per_product?: Record<string, { sku: string; best_model: string } & Record<string, Metric>>;
  trained_through?: string;
  holdout_days?: number;
  global_feature_importance?: Record<string, number>;
  model_labels?: Record<string, string>;
  runs?: RunSummary[];
}
interface Prediction {
  product: { sku: string; name: string; category: string };
  model: string;
  model_label: string;
  recommended_model: string;
  run_version: number;
  trained_through: string;
  history: { date: string; units: number }[];
  forecast: { date: string; predicted: number; lower: number | null; upper: number | null }[];
  total_predicted: number;
  holdout: Record<string, (number | string)[]> & { dates: string[]; actual: number[] };
  metrics: Record<string, Metric>;
  feature_importance: Record<string, number>;
  explanation: Record<string, number> | null;
  interval_method: string;
  assumptions: string;
}

const MODEL_NAMES: Record<string, string> = { xgboost: "XGBoost", sarima: "SARIMA", seasonal_naive: "Seasonal naive" };
const FEATURE_LABELS: Record<string, string> = {
  lag_1: "Demand yesterday", lag_7: "Demand 7 days ago", lag_14: "Demand 14 days ago", lag_28: "Demand 28 days ago",
  ma_7: "7-day moving avg", ma_30: "30-day moving avg", std_7: "7-day volatility", dow: "Day of week", month: "Month",
  is_weekend: "Weekend", doy_sin: "Season (sin)", doy_cos: "Season (cos)", discount_pct: "Discount %", promotion: "Promotion",
};

const maxAbs = (e: Record<string, number>) =>
  Math.max(1e-9, ...Object.entries(e).filter(([k]) => k !== "_bias").map(([, v]) => Math.abs(v)));

export default function Forecasting() {
  const { can } = useAuth();
  const [params] = useSearchParams();
  const products = useApi<{ items: Product[] }>("/api/products", { page_size: 500, active: true });
  const metrics = useApi<MetricsResp>("/api/forecast/metrics");
  const [productId, setProductId] = useState<number | null>(params.get("product") ? Number(params.get("product")) : null);
  const [horizon, setHorizon] = useState(30);
  const [model, setModel] = useState("");
  const [pred, setPred] = useState<Prediction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [training, setTraining] = useState(false);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);

  useEffect(() => {
    if (productId == null && products.data?.items.length) setProductId(products.data.items[0].id);
  }, [products.data, productId]);

  const runForecast = async () => {
    if (!productId) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<Prediction>("/api/forecast/predict", { product_id: productId, horizon_days: horizon, model: model || null });
      setPred(r.data);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (productId && metrics.data?.trained) void runForecast();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId, metrics.data?.trained]);

  // Poll training status while a run is in progress
  useEffect(() => {
    if (!training) return;
    const t = setInterval(async () => {
      const r = await api.get<{ latest: RunSummary | null }>("/api/forecast/status");
      const st = r.data.latest?.status;
      if (st && st !== "running") {
        setTraining(false);
        setTrainMsg(st === "completed" ? `Training v${r.data.latest?.version} completed.` : `Training failed: ${r.data.latest?.error}`);
        metrics.reload();
      }
    }, 3000);
    return () => clearInterval(t);
  }, [training, metrics]);

  const startTraining = async () => {
    setTrainMsg(null);
    try {
      await api.post("/api/forecast/train", {});
      setTraining(true);
    } catch (e) {
      setTrainMsg(errorMessage(e));
    }
  };

  const latest = metrics.data?.latest_run;
  const isRunning = training || latest?.status === "running";

  const chartData = useMemo(() => {
    if (!pred) return [];
    const hist = pred.history.slice(-120).map((h) => ({ date: h.date, actual: h.units }));
    const last = hist[hist.length - 1];
    const fc = pred.forecast.map((f) => ({
      date: f.date,
      predicted: f.predicted,
      band: f.lower != null && f.upper != null ? [f.lower, f.upper] : undefined,
    }));
    return [...hist.slice(0, -1), { ...last, predicted: last?.actual }, ...fc];
  }, [pred]);

  const holdoutData = useMemo(
    () =>
      pred
        ? pred.holdout.dates.map((d, i) => ({
            date: d,
            actual: pred.holdout.actual[i],
            xgboost: pred.holdout.xgboost?.[i],
            sarima: pred.holdout.sarima?.[i],
            seasonal_naive: pred.holdout.seasonal_naive?.[i],
          }))
        : [],
    [pred],
  );

  return (
    <>
      <PageHeader
        title="Demand Forecasting"
        description="Per-product forecasts from an XGBoost regressor (lag, moving-average, calendar and promotion features) compared against SARIMA and seasonal-naive baselines on a chronological holdout."
        actions={
          can("train_models") && (
            <Button variant="outline" onClick={startTraining} disabled={isRunning}>
              {isRunning ? <Spinner /> : <Brain />} {isRunning ? "Training…" : "Retrain models"}
            </Button>
          )
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant={latest?.status === "completed" ? "success" : latest?.status === "running" ? "info" : latest?.status === "failed" ? "critical" : "secondary"}>
          {latest ? `Run v${latest.version}: ${latest.status}` : "No model trained"}
        </Badge>
        {metrics.data?.completed_run?.finished_at && <span>Last trained {fmt.datetime(metrics.data.completed_run.finished_at)}</span>}
        {metrics.data?.trained_through && <span>· data through {fmt.date(metrics.data.trained_through)}</span>}
        {metrics.data?.holdout_days && <span>· holdout: last {metrics.data.holdout_days} days</span>}
      </div>
      {trainMsg && <Alert className="mb-4" variant={trainMsg.startsWith("Training v") ? "success" : "error"}>{trainMsg}</Alert>}
      {isRunning && <Alert className="mb-4" variant="info">Training runs in the background (≈3 minutes for 40 products). This page updates automatically.</Alert>}
      {metrics.error && <ErrorState message={metrics.error} onRetry={metrics.reload} />}
      {metrics.data && !metrics.data.trained && !isRunning && (
        <Card>
          <EmptyState title="No trained forecasting model yet">
            {can("train_models") ? "Click “Retrain models” to train XGBoost, SARIMA and the naive baseline." : "Ask a manager or administrator to train the models."}
          </EmptyState>
        </Card>
      )}

      {metrics.data?.trained && (
        <Tabs defaultValue="forecast">
          <TabsList>
            <TabsTrigger value="forecast">Product forecast</TabsTrigger>
            <TabsTrigger value="evaluation">Model evaluation</TabsTrigger>
            <TabsTrigger value="seasonality">Seasonal trends</TabsTrigger>
            <TabsTrigger value="comparison">Product comparison</TabsTrigger>
          </TabsList>

          <TabsContent value="forecast">
            <Card className="mb-4">
              <CardContent className="flex flex-wrap items-end gap-3 pt-4">
                <div className="w-full space-y-1 sm:w-72">
                  <Label htmlFor="product">Product</Label>
                  <Select id="product" value={productId ?? ""} onChange={(e) => setProductId(Number(e.target.value))}>
                    {products.data?.items.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.sku} · {p.name}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="w-32 space-y-1">
                  <Label htmlFor="horizon">Forecast period</Label>
                  <Select id="horizon" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                    {[7, 30, 60, 90].map((h) => (
                      <option key={h} value={h}>Next {h} days</option>
                    ))}
                  </Select>
                </div>
                <div className="w-48 space-y-1">
                  <Label htmlFor="model">Model</Label>
                  <Select id="model" value={model} onChange={(e) => setModel(e.target.value)}>
                    <option value="">Recommended (lowest holdout MAE)</option>
                    <option value="xgboost">XGBoost Regressor</option>
                    <option value="sarima">SARIMA baseline</option>
                    <option value="seasonal_naive">Seasonal naive</option>
                  </Select>
                </div>
                <Button onClick={runForecast} disabled={busy || !productId || !can("run_predictions")}>
                  {busy ? <Spinner /> : <Play />} Run forecast
                </Button>
              </CardContent>
            </Card>
            {error && <ErrorState message={error} onRetry={runForecast} />}
            {pred && (
              <div className="grid gap-4 xl:grid-cols-3">
                <ChartCard
                  className="xl:col-span-2"
                  title={`${pred.product.sku} · ${pred.product.name}`}
                  description={`${pred.model_label} · last 120 days of history and ${pred.forecast.length}-day forecast. ${pred.interval_method}.`}
                >
                  <ResponsiveContainer width="100%" height={300}>
                    <ComposedChart data={chartData} margin={{ top: 8, right: 16, left: 0 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="date" tickFormatter={fmt.dateShort} minTickGap={30} tickLine={false} axisLine={{ stroke: GRID }} />
                      <YAxis width={44} tickLine={false} axisLine={false} />
                      <Tooltip content={<ChartTooltip formatter={(v) => fmt.num1(v)} labelFormatter={(l) => fmt.date(String(l))} />} />
                      <Legend iconType="plainline" />
                      <Area name="95% interval" dataKey="band" stroke="none" fill={SERIES[1]} fillOpacity={0.15} />
                      <Line name="Historical demand" dataKey="actual" stroke={SERIES[0]} strokeWidth={1.5} dot={false} />
                      <Line name="Predicted demand" dataKey="predicted" stroke={SERIES[1]} strokeWidth={2} dot={false} />
                      <ReferenceLine x={pred.trained_through} stroke="#9ca3af" strokeDasharray="3 3" label={{ value: "today", fontSize: 10, fill: "#6b7280", position: "insideTopRight" }} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>
                <Card>
                  <CardHeader>
                    <CardTitle>Forecast summary</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3 text-sm">
                    <div>
                      <div className="text-xs text-muted-foreground">Total predicted demand, next {pred.forecast.length} days</div>
                      <div className="tabular text-2xl font-semibold">{fmt.num(pred.total_predicted)} units</div>
                      <div className="text-xs text-muted-foreground">≈ {fmt.num1(pred.total_predicted / pred.forecast.length)} units/day</div>
                    </div>
                    <div className="text-xs">
                      Recommended model for this product: <Badge>{MODEL_NAMES[pred.recommended_model]}</Badge>
                    </div>
                    <Table>
                      <THead>
                        <TR>
                          <TH>Holdout</TH>
                          <TH className="text-right">MAE</TH>
                          <TH className="text-right">RMSE</TH>
                          <TH className="text-right">MAPE</TH>
                        </TR>
                      </THead>
                      <TBody>
                        {Object.entries(pred.metrics).map(([m, v]) => (
                          <TR key={m} className={m === pred.model ? "bg-accent/50" : ""}>
                            <TD className="text-xs font-medium">{MODEL_NAMES[m]}</TD>
                            <TD className="tabular text-right text-xs">{v.mae.toFixed(2)}</TD>
                            <TD className="tabular text-right text-xs">{v.rmse.toFixed(2)}</TD>
                            <TD className="tabular text-right text-xs">{fmt.pct(v.mape)}</TD>
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                    <p className="text-[11px] text-muted-foreground">
                      MAPE excludes {pred.metrics.xgboost?.mape_excluded_zero_actuals ?? 0} zero-demand days. {pred.assumptions}
                    </p>
                  </CardContent>
                </Card>

                <ChartCard className="xl:col-span-2" title="Actual vs predicted on the holdout" description="Recursive multi-step predictions over the last 60 days, which no model saw during training.">
                  <ResponsiveContainer width="100%" height={260}>
                    <LineChart data={holdoutData} margin={{ top: 8, right: 16, left: 0 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="date" tickFormatter={fmt.dateShort} minTickGap={30} tickLine={false} axisLine={{ stroke: GRID }} />
                      <YAxis width={44} tickLine={false} axisLine={false} />
                      <Tooltip content={<ChartTooltip formatter={(v) => fmt.num1(v)} labelFormatter={(l) => fmt.date(String(l))} />} />
                      <Legend iconType="plainline" />
                      <Line name="Actual" dataKey="actual" stroke={SERIES[0]} strokeWidth={2} dot={false} />
                      <Line name="XGBoost" dataKey="xgboost" stroke={SERIES[1]} strokeWidth={1.5} dot={false} />
                      <Line name="SARIMA" dataKey="sarima" stroke={SERIES[2]} strokeWidth={1.5} dot={false} />
                      <Line name="Seasonal naive" dataKey="seasonal_naive" stroke={SERIES[3]} strokeWidth={1.5} dot={false} strokeDasharray="4 3" />
                    </LineChart>
                  </ResponsiveContainer>
                </ChartCard>
                <Card>
                  <CardHeader>
                    <CardTitle>Why this forecast?</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {pred.explanation ? (
                      <>
                        <p className="text-xs text-muted-foreground">SHAP contributions (units) for the first forecast day; baseline {pred.explanation._bias?.toFixed(1)}.</p>
                        <ul className="space-y-1.5">
                          {Object.entries(pred.explanation)
                            .filter(([k]) => k !== "_bias")
                            .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                            .slice(0, 6)
                            .map(([k, v]) => (
                              <li key={k} className="flex items-center gap-2 text-xs">
                                <span className="w-32 shrink-0 truncate">{FEATURE_LABELS[k] ?? k}</span>
                                <div className="relative h-2 flex-1 rounded bg-secondary">
                                  <div
                                    className="absolute top-0 h-2 rounded"
                                    style={{
                                      width: `${(Math.abs(v) / maxAbs(pred.explanation!)) * 50}%`,
                                      left: v >= 0 ? "50%" : undefined,
                                      right: v < 0 ? "50%" : undefined,
                                      background: v >= 0 ? SERIES[0] : SERIES[1],
                                    }}
                                  />
                                </div>
                                <span className="tabular w-12 text-right">{v > 0 ? "+" : ""}{v.toFixed(1)}</span>
                              </li>
                            ))}
                        </ul>
                      </>
                    ) : (
                      <p className="text-xs text-muted-foreground">Per-prediction explanations are available for XGBoost. Global feature importance:</p>
                    )}
                    <div>
                      <div className="mb-1 text-xs font-medium">XGBoost feature importance (gain)</div>
                      {Object.entries(pred.feature_importance).slice(0, 6).map(([k, v]) => (
                        <div key={k} className="flex items-center gap-2 py-0.5 text-xs">
                          <span className="w-32 shrink-0 truncate">{FEATURE_LABELS[k] ?? k}</span>
                          <div className="h-2 flex-1 rounded bg-secondary">
                            <div className="h-2 rounded" style={{ width: `${v * 100 * 2.5}%`, maxWidth: "100%", background: SERIES[0] }} />
                          </div>
                          <span className="tabular w-12 text-right">{(v * 100).toFixed(1)}%</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>

          <TabsContent value="evaluation">
            <EvaluationTab m={metrics.data} />
          </TabsContent>
          <TabsContent value="seasonality">
            <SeasonalityTab />
          </TabsContent>
          <TabsContent value="comparison">
            <ComparisonTab />
          </TabsContent>
        </Tabs>
      )}
    </>
  );
}

function EvaluationTab({ m }: { m: MetricsResp }) {
  const rows = Object.entries(m.portfolio ?? {});
  const per = Object.entries(m.per_product ?? {});
  const fi = Object.entries(m.global_feature_importance ?? {}).map(([k, v]) => ({ name: FEATURE_LABELS[k] ?? k, value: +(v * 100).toFixed(2) }));
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle>Portfolio holdout performance</CardTitle>
          <p className="text-xs text-muted-foreground">
            Errors pooled across all {per.length} products over the last {m.holdout_days} days (chronological split; recursive multi-step forecasts).
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>Model</TH>
                <TH className="text-right">MAE</TH>
                <TH className="text-right">RMSE</TH>
                <TH className="text-right">MAPE</TH>
                <TH className="text-right">WAPE</TH>
                <TH className="text-right">Bias</TH>
                <TH className="text-right">Best on</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map(([k, v]) => (
                <TR key={k}>
                  <TD className="font-medium">{m.model_labels?.[k] ?? k}</TD>
                  <TD className="tabular text-right">{v.mae.toFixed(2)}</TD>
                  <TD className="tabular text-right">{v.rmse.toFixed(2)}</TD>
                  <TD className="tabular text-right">{fmt.pct(v.mape)}</TD>
                  <TD className="tabular text-right">{fmt.pct(v.wape)}</TD>
                  <TD className="tabular text-right">{v.bias.toFixed(2)}</TD>
                  <TD className="tabular text-right">{v.products_won} products</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <p className="mt-2 text-[11px] text-muted-foreground">
            MAPE excludes {rows[0]?.[1].mape_excluded_zero_actuals ?? 0} zero-demand observations (undefined); WAPE = Σ|error| ÷ Σ actual is robust to zeros. Negative bias = under-forecasting.
          </p>
          <div className="mt-4 max-h-80 overflow-auto">
            <Table>
              <THead>
                <TR>
                  <TH>SKU</TH>
                  <TH>Recommended</TH>
                  <TH className="text-right">XGB WAPE</TH>
                  <TH className="text-right">SARIMA WAPE</TH>
                  <TH className="text-right">Naive WAPE</TH>
                </TR>
              </THead>
              <TBody>
                {per.map(([pid, r]) => (
                  <TR key={pid}>
                    <TD className="font-mono text-xs">{r.sku}</TD>
                    <TD><Badge variant="secondary">{MODEL_NAMES[r.best_model]}</Badge></TD>
                    <TD className="tabular text-right text-xs">{fmt.pct(r.xgboost.wape)}</TD>
                    <TD className="tabular text-right text-xs">{fmt.pct(r.sarima.wape)}</TD>
                    <TD className="tabular text-right text-xs">{fmt.pct(r.seasonal_naive.wape)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        </CardContent>
      </Card>
      <ChartCard title="Global feature importance" description="XGBoost gain, averaged across products">
        <ResponsiveContainer width="100%" height={380}>
          <BarChart data={fi} layout="vertical" margin={{ left: 10, right: 20 }}>
            <CartesianGrid stroke={GRID} horizontal={false} />
            <XAxis type="number" tickFormatter={(v) => `${v}%`} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis type="category" dataKey="name" width={120} tickLine={false} axisLine={false} />
            <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip formatter={(v) => `${v}%`} />} />
            <Bar name="Importance" dataKey="value" fill={SERIES[0]} radius={[0, 4, 4, 0]} barSize={12} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}

function SeasonalityTab() {
  const { data, loading, error, reload } = useApi<{ categories: string[]; monthly: Record<string, number | string>[]; weekday: Record<string, number | string>[] }>(
    "/api/forecast/seasonality",
  );
  const cats = data?.categories ?? [];
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ChartCard title="Monthly seasonality by category" description="Average daily units in each calendar month (exploratory analysis of 2 years of sales)" loading={loading} error={error} onRetry={reload}>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={data?.monthly ?? []} margin={{ top: 8, right: 16, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="month" tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis width={44} tickLine={false} axisLine={false} />
            <Tooltip content={<ChartTooltip formatter={(v) => `${fmt.num(v)} /day`} />} />
            <Legend iconType="plainline" />
            {cats.map((c, i) => (
              <Line key={c} dataKey={c} name={c} stroke={SERIES[i]} strokeWidth={2} dot={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Day-of-week pattern" description="Average daily units by weekday" loading={loading} error={error}>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={data?.weekday ?? []} margin={{ top: 8, right: 16, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="day" tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis width={44} tickLine={false} axisLine={false} />
            <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip formatter={(v) => `${fmt.num(v)} /day`} />} />
            <Legend />
            {cats.map((c, i) => (
              <Bar key={c} dataKey={c} name={c} fill={SERIES[i]} radius={[2, 2, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}

function ComparisonTab() {
  const [days, setDays] = useState(30);
  const { data, loading, error, reload } = useApi<{ sku: string; name: string; category: string; last_period_units: number; next_period_forecast: number | null; change_pct: number | null }[]>(
    "/api/forecast/comparison",
    { days },
  );
  const top = (data ?? []).slice(0, 20);
  return (
    <ChartCard
      title="Product-wise demand comparison"
      description={`Units in the last ${days} days vs forecast for the next ${days} days (top 20 products by volume)`}
      loading={loading}
      error={error}
      onRetry={reload}
      height={420}
      actions={
        <Select className="h-8 w-32 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Comparison window">
          {[7, 30, 60, 90].map((d) => (
            <option key={d} value={d}>{d} days</option>
          ))}
        </Select>
      }
    >
      <ResponsiveContainer width="100%" height={420}>
        <BarChart data={top} margin={{ top: 8, right: 16, left: 0, bottom: 40 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="sku" angle={-45} textAnchor="end" interval={0} tickLine={false} axisLine={{ stroke: GRID }} />
          <YAxis width={56} tickFormatter={(v) => fmt.num(v)} tickLine={false} axisLine={false} />
          <Tooltip cursor={{ fill: "#f3f4f6" }} content={<ChartTooltip formatter={(v) => `${fmt.num(v)} units`} labelFormatter={(l) => `${l} · ${top.find((t) => t.sku === l)?.name ?? ""}`} />} />
          <Legend verticalAlign="top" />
          <Bar name={`Last ${days} days`} dataKey="last_period_units" fill={SERIES[0]} radius={[3, 3, 0, 0]} />
          <Bar name={`Forecast next ${days} days`} dataKey="next_period_forecast" fill={SERIES[1]} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
