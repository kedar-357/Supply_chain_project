import { CheckCircle2, Download, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { EmptyState, ErrorState, PageHeader, SimulatedNote, Spinner } from "@/components/common";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api, download, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { cn, fmt } from "@/lib/utils";

interface Template { dataset: string; label: string; description: string; unique_key: string[]; import_order: number; columns: { name: string; type: string; required: boolean; choices: string[] | null; example: string }[] }
interface Validation {
  upload_id: number; status: string; dataset: string; row_count: number; valid_row_count: number; invalid_row_count: number;
  detected_columns: string[]; missing_required_columns: string[]; unknown_columns: string[]; missing_values: Record<string, number>;
  duplicate_rows: number; error_count: number; errors: { row: number | null; column: string; value: string | null; message: string }[];
  errors_truncated: boolean; preview: Record<string, unknown>[]; column_types: Record<string, string>;
}

const STEPS = ["Upload", "Detect columns", "Preview", "Validate types", "Missing values", "Duplicates", "Errors", "Confirm", "Store"];

export function validateFile(file: File | null): string | null {
  if (!file) return "Choose a file to upload";
  if (!/\.(csv|xlsx|xls)$/i.test(file.name)) return "Only .csv and .xlsx files are supported";
  if (file.size === 0) return "The file is empty";
  if (file.size > 25 * 1024 * 1024) return "File exceeds the 25 MB limit";
  return null;
}

export default function DataManagement() {
  const { can } = useAuth();
  const templates = useApi<Template[]>("/api/data/templates");
  const summary = useApi<{
    tables: { table: string; file: string; rows: number; simulated_rows: number | null; size_kb: number }[];
    sales_range: { from: string; to: string };
    storage: { data_dir: string; format: string; backup_files: number };
    demo_mode: boolean;
  }>("/api/data/summary");
  const uploads = useApi<{ id: number; dataset: string; filename: string; status: string; rows: number; errors: number; imported: number; created_at: string }[]>("/api/data/uploads");
  const [dataset, setDataset] = useState("products");
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<Validation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [skipInvalid, setSkipInvalid] = useState(false);
  const [imported, setImported] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const spec = templates.data?.find((t) => t.dataset === dataset);
  const step = imported ? 9 : report ? (report.error_count ? 7 : 8) : file ? 1 : 0;

  const upload = async () => {
    const v = validateFile(file);
    if (v) return setError(v);
    setBusy(true);
    setError(null);
    setReport(null);
    setImported(null);
    try {
      const fd = new FormData();
      fd.append("dataset", dataset);
      fd.append("file", file!);
      const r = await api.post<Validation>("/api/data/upload", fd);
      setReport(r.data);
      uploads.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const confirmImport = async () => {
    if (!report) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ imported: number; skipped: number }>("/api/data/import", { upload_id: report.upload_id, skip_invalid: skipInvalid });
      setImported(`Imported ${r.data.imported} rows into ${spec?.label ?? dataset}${r.data.skipped ? ` (${r.data.skipped} invalid rows skipped)` : ""}.`);
      uploads.reload();
      summary.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Data Management" description="Upload CSV or Excel datasets. Files are validated (types, missing values, duplicates, references) and nothing is written to the data files until you confirm the import." />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Upload dataset</CardTitle>
            <ol className="mt-2 flex flex-wrap gap-1 text-[11px]" aria-label="Upload workflow">
              {STEPS.map((s, i) => (
                <li key={s} className={cn("rounded-full px-2 py-0.5", i < step ? "bg-green-50 text-green-800" : "bg-secondary text-muted-foreground")}>{i + 1}. {s}</li>
              ))}
            </ol>
          </CardHeader>
          <CardContent className="space-y-4">
            {!can("upload_data") && <Alert variant="warning">Your role cannot upload data. Analysts and administrators can.</Alert>}
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-56 space-y-1"><Label htmlFor="ds">Dataset</Label>
                <Select id="ds" value={dataset} onChange={(e) => { setDataset(e.target.value); setReport(null); setImported(null); }}>
                  {templates.data?.sort((a, b) => a.import_order - b.import_order).map((t) => <option key={t.dataset} value={t.dataset}>{t.import_order}. {t.label}</option>)}
                </Select></div>
              <div className="space-y-1">
                <Label htmlFor="file">File (.csv / .xlsx, max 25 MB)</Label>
                <input ref={inputRef} id="file" type="file" accept=".csv,.xlsx,.xls" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setReport(null); setImported(null); setError(null); }}
                  className="block text-sm file:mr-3 file:rounded-md file:border-0 file:bg-secondary file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-secondary/70" />
              </div>
              <Button onClick={upload} disabled={busy || !can("upload_data")}>{busy && !report ? <Spinner /> : <Upload />} Upload & validate</Button>
              <Button variant="outline" onClick={() => download(`/api/data/templates/${dataset}`, {})}><Download /> Template</Button>
            </div>
            {spec && (
              <div className="text-xs text-muted-foreground">
                {spec.description}. Unique key: <code>{spec.unique_key.join(" + ")}</code>. Existing rows with the same key are updated.
                <div className="mt-1 flex flex-wrap gap-1">{spec.columns.map((c) => <Badge key={c.name} variant={c.required ? "default" : "secondary"}>{c.name}: {c.type}{c.required ? "" : " (optional)"}</Badge>)}</div>
              </div>
            )}
            {error && <Alert variant="error" title="Upload problem">{error}</Alert>}
            {imported && <Alert variant="success" title="Import complete">{imported}</Alert>}

            {report && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                  {[["Rows", report.row_count], ["Valid", report.valid_row_count], ["Invalid", report.invalid_row_count], ["Duplicates", report.duplicate_rows], ["Errors", report.error_count]].map(([l, v]) => (
                    <div key={l as string} className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">{l}</div><div className={cn("tabular text-lg font-semibold", (l === "Invalid" || l === "Errors") && Number(v) > 0 && "text-red-700")}>{fmt.num(v as number)}</div></div>
                  ))}
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="mb-1 text-xs font-medium">Detected columns</div>
                    <div className="flex flex-wrap gap-1">
                      {report.detected_columns.map((c) => (
                        <Badge key={c} variant={report.unknown_columns.includes(c) ? "warning" : "success"} title={report.unknown_columns.includes(c) ? "Not part of the schema; ignored" : report.column_types[c]}>
                          {c}{report.missing_values[c] ? ` · ${report.missing_values[c]} missing` : ""}
                        </Badge>
                      ))}
                      {report.missing_required_columns.map((c) => <Badge key={c} variant="critical">missing: {c}</Badge>)}
                    </div>
                  </div>
                  {report.error_count === 0 ? (
                    <Alert variant="success" title="Validation passed">All {report.row_count} rows are valid and ready to import.</Alert>
                  ) : (
                    <Alert variant="error" title={`${report.error_count} validation errors in ${report.invalid_row_count} rows`}>Malformed rows are never imported silently. Fix the file, or explicitly import only the valid rows.</Alert>
                  )}
                </div>
                {report.errors.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium">Validation errors {report.errors_truncated && "(first 200 shown)"}</div>
                    <div className="max-h-56 overflow-auto rounded-md border">
                      <Table>
                        <THead><TR><TH>Row</TH><TH>Column</TH><TH>Value</TH><TH>Problem</TH></TR></THead>
                        <TBody>{report.errors.map((e, i) => <TR key={i}><TD className="tabular text-xs">{e.row ?? "–"}</TD><TD className="font-mono text-xs">{e.column}</TD><TD className="max-w-[160px] truncate text-xs">{e.value ?? "∅"}</TD><TD className="text-xs text-red-800">{e.message}</TD></TR>)}</TBody>
                      </Table>
                    </div>
                  </div>
                )}
                <div>
                  <div className="mb-1 text-xs font-medium">Preview (first {report.preview.length} rows)</div>
                  <div className="max-h-64 overflow-auto rounded-md border">
                    <Table>
                      <THead><TR>{report.detected_columns.map((c) => <TH key={c}>{c}</TH>)}</TR></THead>
                      <TBody>{report.preview.map((r, i) => <TR key={i}>{report.detected_columns.map((c) => <TD key={c} className="whitespace-nowrap text-xs">{r[c] == null ? <span className="text-red-600">∅</span> : String(r[c])}</TD>)}</TR>)}</TBody>
                    </Table>
                  </div>
                </div>
                {!imported && report.missing_required_columns.length === 0 && (
                  <div className="flex flex-wrap items-center gap-3 border-t pt-3">
                    {report.error_count > 0 && report.valid_row_count > 0 && (
                      <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={skipInvalid} onChange={(e) => setSkipInvalid(e.target.checked)} />
                        Import only the {report.valid_row_count} valid rows and skip {report.invalid_row_count} invalid rows
                      </label>
                    )}
                    <Button className="ml-auto" onClick={confirmImport} disabled={busy || (report.error_count > 0 && !skipInvalid) || report.valid_row_count === 0}>
                      {busy ? <Spinner /> : <CheckCircle2 />} Confirm import
                    </Button>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Data files</CardTitle><CardDescription>Sales history {fmt.date(summary.data?.sales_range.from)} – {fmt.date(summary.data?.sales_range.to)} · {summary.data?.storage.format}</CardDescription></CardHeader>
            <CardContent>
              {summary.error && <ErrorState message={summary.error} onRetry={summary.reload} />}
              <Table>
                <THead><TR><TH>File</TH><TH className="text-right">Rows</TH><TH className="text-right">Simulated</TH><TH className="text-right">Size</TH></TR></THead>
                <TBody>{summary.data?.tables.map((t) => <TR key={t.table}><TD className="font-mono text-[11px]">{t.file}</TD><TD className="tabular text-right text-xs">{fmt.num(t.rows)}</TD><TD className="tabular text-right text-xs">{t.simulated_rows == null ? "–" : fmt.num(t.simulated_rows)}</TD><TD className="tabular whitespace-nowrap text-right text-xs">{t.size_kb >= 1024 ? `${(t.size_kb / 1024).toFixed(1)} MB` : `${t.size_kb} KB`}</TD></TR>)}</TBody>
              </Table>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Imports and edits rewrite these files atomically; the previous version is kept in <code>data/backups/</code>
                ({fmt.num(summary.data?.storage.backup_files)} backup files). The original sample datasets in <code>datasets/sample/</code> are never modified.
              </p>
              <SimulatedNote className="mt-1" />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Recent uploads</CardTitle></CardHeader>
            <CardContent>
              {uploads.data?.length === 0 && <EmptyState title="No uploads yet" />}
              <ul className="divide-y">
                {uploads.data?.map((u) => (
                  <li key={u.id} className="flex items-center justify-between gap-2 py-2 text-xs">
                    <div className="min-w-0"><div className="truncate font-medium">{u.filename}</div><div className="text-muted-foreground">{u.dataset} · {u.rows} rows · {fmt.datetime(u.created_at)}</div></div>
                    <Badge variant={u.status === "imported" ? "success" : u.status === "has_errors" ? "critical" : "info"}>{u.status.replace("_", " ")}</Badge>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
