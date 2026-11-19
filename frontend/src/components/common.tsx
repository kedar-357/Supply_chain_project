import { AlertTriangle, ArrowDownRight, ArrowUpRight, Inbox, Loader2, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { Alert, Skeleton } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { STATUS_LABEL, cn } from "@/lib/utils";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function KpiCard({
  label,
  value,
  hint,
  icon,
  delta,
  deltaGoodWhenUp = true,
  tone,
  loading,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  delta?: number | null;
  deltaGoodWhenUp?: boolean;
  tone?: "critical" | "warning" | "good";
  loading?: boolean;
}) {
  const up = (delta ?? 0) >= 0;
  const good = up === deltaGoodWhenUp;
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        {icon && (
          <span
            className={cn(
              "rounded-md p-1.5 [&_svg]:h-4 [&_svg]:w-4",
              tone === "critical" ? "bg-red-50 text-red-700" : tone === "warning" ? "bg-amber-50 text-amber-700" : tone === "good" ? "bg-green-50 text-green-700" : "bg-blue-50 text-blue-700",
            )}
          >
            {icon}
          </span>
        )}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-24" />
      ) : (
        <div className="tabular mt-1.5 text-2xl font-semibold tracking-tight">{value}</div>
      )}
      <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
        {delta != null && !loading && (
          <span className={cn("inline-flex items-center font-medium", good ? "text-green-700" : "text-red-700")}>
            {up ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
            {Math.abs(delta).toFixed(1)}%
          </span>
        )}
        {hint}
      </div>
    </Card>
  );
}

export function ChartCard({
  title,
  description,
  loading,
  error,
  onRetry,
  actions,
  children,
  className,
  height = 280,
}: {
  title: string;
  description?: ReactNode;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  height?: number;
}) {
  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="flex-row items-start justify-between gap-2 space-y-0">
        <div className="min-w-0">
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription className="mt-1">{description}</CardDescription>}
        </div>
        {actions}
      </CardHeader>
      <CardContent className="flex-1">
        {error ? (
          <ErrorState message={error} onRetry={onRetry} />
        ) : loading ? (
          <div style={{ height }} className="w-full animate-pulse rounded-md bg-secondary" aria-busy="true" aria-label="Loading chart" />
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Alert variant="error" title="Could not load data">
      <div className="flex flex-wrap items-center gap-2">
        <span>{message}</span>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw /> Retry
          </Button>
        )}
      </div>
    </Alert>
  );
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <div className="rounded-full bg-secondary p-3 text-muted-foreground">{icon ?? <Inbox className="h-5 w-5" />}</div>
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="max-w-md text-xs text-muted-foreground">{children}</div>}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin", className)} />;
}

export function LoadingBlock({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

const statusVariant: Record<string, "success" | "warning" | "critical" | "info" | "secondary"> = {
  healthy: "success",
  low: "warning",
  critical: "critical",
  out_of_stock: "critical",
  overstock: "info",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge variant={statusVariant[status] ?? "secondary"}>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function RiskBadge({ risk }: { risk: string | null | undefined }) {
  if (!risk) return <span className="text-xs text-muted-foreground">–</span>;
  const v = risk === "High" ? "critical" : risk === "Medium" ? "warning" : "success";
  return (
    <Badge variant={v}>
      {risk === "High" && <AlertTriangle className="h-3 w-3" />}
      {risk} risk
    </Badge>
  );
}

export function PriorityBadge({ priority }: { priority: string }) {
  const v = priority === "high" ? "critical" : priority === "medium" ? "warning" : "secondary";
  return <Badge variant={v} className="capitalize">{priority}</Badge>;
}

export function SimulatedNote({ className }: { className?: string }) {
  return (
    <p className={cn("text-[11px] text-muted-foreground", className)}>
      Demo data is <strong>simulated</strong> (synthetic, generated with a fixed seed) and flagged as such in the data files.
    </p>
  );
}

/** Recharts tooltip body with consistent styling: text uses ink colours, swatch carries identity. */
export function ChartTooltip({
  active,
  payload,
  label,
  formatter,
  labelFormatter,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number | string | [number, number]; color?: string; dataKey?: string }[];
  label?: string | number;
  formatter?: (v: number, name: string) => string;
  labelFormatter?: (l: string | number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-white px-3 py-2 text-xs shadow-md">
      {label != null && <div className="mb-1 font-medium text-foreground">{labelFormatter ? labelFormatter(label) : label}</div>}
      {payload
        .filter((p) => p.value != null)
        .map((p) => (
          <div key={String(p.dataKey ?? p.name)} className="flex items-center gap-2 py-0.5">
            <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
            <span className="text-muted-foreground">{p.name}</span>
            <span className="tabular ml-auto pl-3 font-medium text-foreground">
              {Array.isArray(p.value)
                ? p.value.map((x) => (formatter ? formatter(x, p.name ?? "") : x)).join(" – ")
                : formatter && typeof p.value === "number"
                  ? formatter(p.value, p.name ?? "")
                  : p.value}
            </span>
          </div>
        ))}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between gap-2 pt-3 text-xs text-muted-foreground">
      <span>
        {total === 0 ? 0 : (page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
      </span>
      <div className="flex gap-1.5">
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}
