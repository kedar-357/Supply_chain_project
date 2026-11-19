import { Check, Download, FileSpreadsheet, FileText, Lightbulb, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, PriorityBadge, Spinner } from "@/components/common";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, download, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { cn, fmt } from "@/lib/utils";

interface Report { report: string; title: string; period: { start: string; end: string }; generated_at: string; notes: string[]; columns: string[]; rows: Record<string, unknown>[] }
interface Rec { id: number; category: string; title: string; reason: string; expected_impact: string; priority: string; priority_score: number; supporting_data: Record<string, unknown>; status: string; generated_at: string }

export default function Reports() {
  return (
    <>
      <PageHeader title="Reports & Insights" description="Operational reports with CSV, Excel and PDF export, and management recommendations generated from calculated results by transparent business rules." />
      <Tabs defaultValue="recommendations">
        <TabsList>
          <TabsTrigger value="recommendations"><Lightbulb className="mr-1.5 h-3.5 w-3.5" />Recommendations</TabsTrigger>
          <TabsTrigger value="reports"><FileText className="mr-1.5 h-3.5 w-3.5" />Reports</TabsTrigger>
        </TabsList>
        <TabsContent value="recommendations"><RecommendationsTab /></TabsContent>
        <TabsContent value="reports"><ReportsTab /></TabsContent>
      </Tabs>
    </>
  );
}

function cell(v: unknown): string {
  if (v == null) return "–";
  if (typeof v === "number") return Number.isInteger(v) ? fmt.num(v) : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function ReportsTab() {
  const { can } = useAuth();
  const types = useApi<{ key: string; title: string }[]>("/api/reports");
  const [report, setReport] = useState("inventory");
  const [range, setRange] = useState({ start: "", end: "" });
  const params = { start: range.start || undefined, end: range.end || undefined };
  const { data, loading, error, reload } = useApi<Report>(`/api/reports/${report}`, params);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportErr, setExportErr] = useState<string | null>(null);
  const rangeInvalid = range.start && range.end && range.start > range.end;

  const doExport = async (format: string) => {
    setExporting(format);
    setExportErr(null);
    try {
      await download("/api/reports/export", { report, format, ...params });
    } catch (e) {
      setExportErr(errorMessage(e));
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-4">
      <Card>
        <CardHeader><CardTitle>Report</CardTitle></CardHeader>
        <CardContent className="space-y-1">
          {types.data?.map((t) => (
            <button key={t.key} onClick={() => setReport(t.key)} className={cn("w-full rounded-md px-3 py-2 text-left text-sm hover:bg-secondary", report === t.key && "bg-accent font-medium text-accent-foreground")}>
              {t.title}
            </button>
          ))}
          <div className="space-y-2 border-t pt-3">
            <div className="space-y-1"><Label htmlFor="rs">From</Label><Input id="rs" type="date" value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="re">To</Label><Input id="re" type="date" value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} /></div>
            {rangeInvalid && <p className="text-xs text-red-700">Start must be before end</p>}
            <p className="text-[11px] text-muted-foreground">Default: last 30 days of data. Applies to period-based columns (sales, deliveries).</p>
          </div>
        </CardContent>
      </Card>
      <Card className="xl:col-span-3">
        <CardHeader className="flex-row items-start justify-between gap-2">
          <div>
            <CardTitle>{data?.title ?? "Report"}</CardTitle>
            {data && <CardDescription>Period {fmt.date(data.period.start)} – {fmt.date(data.period.end)} · {data.rows.length} rows · generated {fmt.datetime(data.generated_at)}</CardDescription>}
          </div>
          <div className="flex gap-1.5">
            <Button size="sm" variant="outline" onClick={reload}><RefreshCw /> Refresh</Button>
            {(["csv", "xlsx", "pdf"] as const).map((f) => (
              <Button key={f} size="sm" variant="outline" disabled={!!exporting || !can("generate_reports") || !!rangeInvalid} onClick={() => doExport(f)}>
                {exporting === f ? <Spinner /> : f === "xlsx" ? <FileSpreadsheet /> : <Download />} {f.toUpperCase()}
              </Button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {exportErr && <Alert variant="error" className="mb-3">{exportErr}</Alert>}
          {data?.notes.map((n, i) => <p key={i} className="text-xs text-muted-foreground">{n}</p>)}
          {error && <ErrorState message={error} onRetry={reload} />}
          {loading && !data ? <LoadingBlock rows={8} /> : data && data.rows.length === 0 ? <EmptyState title="No rows for this report" /> : (
            <div className={cn("mt-3 max-h-[560px] overflow-auto", loading && "opacity-60")}>
              <Table>
                <THead className="sticky top-0"><TR>{data?.columns.map((c) => <TH key={c}>{c.replace(/_/g, " ")}</TH>)}</TR></THead>
                <TBody>
                  {data?.rows.slice(0, 300).map((r, i) => (
                    <TR key={i}>{data.columns.map((c) => <TD key={c} className={cn("whitespace-nowrap text-xs", typeof r[c] === "number" && "tabular text-right")}>{cell(r[c])}</TD>)}</TR>
                  ))}
                </TBody>
              </Table>
              {data && data.rows.length > 300 && <p className="mt-2 text-xs text-muted-foreground">Showing 300 of {data.rows.length} rows — export for the full report.</p>}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

const CATEGORY_LABEL: Record<string, string> = { replenishment: "Replenishment", demand: "Demand", redistribution: "Redistribution", overstock: "Overstock", supplier: "Supplier", logistics: "Logistics" };

function RecommendationsTab() {
  const { can } = useAuth();
  const [filters, setFilters] = useState({ category: "", priority: "" });
  const { data, loading, error, reload } = useApi<Rec[]>("/api/reports/recommendations", { category: filters.category || undefined, priority: filters.priority || undefined });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const generate = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.post<Rec[]>("/api/reports/recommendations/generate");
      setMsg(`Generated ${r.data.length} recommendations from current data.`);
      reload();
    } catch (e) {
      setMsg(`Error: ${errorMessage(e)}`);
    } finally {
      setBusy(false);
    }
  };
  const setStatus = async (id: number, status: string) => {
    try {
      await api.patch(`/api/reports/recommendations/${id}`, { status });
      reload();
    } catch (e) {
      setMsg(`Error: ${errorMessage(e)}`);
    }
  };
  const counts = (data ?? []).reduce<Record<string, number>>((a, r) => ({ ...a, [r.priority]: (a[r.priority] ?? 0) + 1 }), {});

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <div className="w-44 space-y-1"><Label htmlFor="rc">Category</Label>
            <Select id="rc" value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })}><option value="">All</option>{Object.entries(CATEGORY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></div>
          <div className="w-36 space-y-1"><Label htmlFor="rp">Priority</Label>
            <Select id="rp" value={filters.priority} onChange={(e) => setFilters({ ...filters, priority: e.target.value })}><option value="">All</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></Select></div>
          <div className="flex gap-2 text-xs text-muted-foreground">
            <Badge variant="critical">{counts.high ?? 0} high</Badge><Badge variant="warning">{counts.medium ?? 0} medium</Badge><Badge variant="secondary">{counts.low ?? 0} low</Badge>
          </div>
          {can("generate_reports") && <Button className="ml-auto" onClick={generate} disabled={busy}>{busy ? <Spinner /> : <Lightbulb />} Regenerate recommendations</Button>}
        </CardContent>
      </Card>
      {msg && <Alert variant={msg.startsWith("Error") ? "error" : "success"}>{msg}</Alert>}
      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingBlock rows={6} />}
      {data?.length === 0 && <Card><EmptyState title="No recommendations yet">Click “Regenerate recommendations” to analyse current inventory, forecasts, suppliers and routes.</EmptyState></Card>}
      <div className="grid gap-3 lg:grid-cols-2">
        {data?.map((r) => (
          <Card key={r.id} className={cn(r.status === "accepted" && "border-green-300")}>
            <CardContent className="space-y-2 pt-4">
              <div className="flex flex-wrap items-center gap-2">
                <PriorityBadge priority={r.priority} />
                <Badge variant="secondary">{CATEGORY_LABEL[r.category] ?? r.category}</Badge>
                {r.status === "accepted" && <Badge variant="success">accepted</Badge>}
              </div>
              <div className="text-sm font-semibold">{r.title}</div>
              <div className="text-xs"><span className="font-medium">Reason: </span>{r.reason}</div>
              <div className="text-xs"><span className="font-medium">Expected impact: </span>{r.expected_impact}</div>
              <button className="text-xs font-medium text-primary hover:underline" onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
                {expanded === r.id ? "Hide" : "Show"} supporting data
              </button>
              {expanded === r.id && (
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-md bg-secondary/60 p-2 text-[11px]">
                  {Object.entries(r.supporting_data).map(([k, v]) => (
                    <div key={k} className="contents"><dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt><dd className="tabular">{typeof v === "object" ? JSON.stringify(v) : cell(v)}</dd></div>
                  ))}
                </dl>
              )}
              {can("generate_reports") && r.status === "open" && (
                <div className="flex gap-2 pt-1">
                  <Button size="sm" variant="outline" onClick={() => setStatus(r.id, "accepted")}><Check /> Accept</Button>
                  <Button size="sm" variant="ghost" onClick={() => setStatus(r.id, "dismissed")}><X /> Dismiss</Button>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
