import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { ErrorState, LoadingBlock, PageHeader, Spinner } from "@/components/common";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { useAuth, type User } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { fmt } from "@/lib/utils";

type Settings = Record<string, Record<string, number | string>>;

const GROUPS: Record<string, { title: string; description: string; fields: Record<string, string> }> = {
  inventory: {
    title: "Inventory policy",
    description: "Used for safety stock, reorder points, EOQ and overstock detection.",
    fields: { service_level: "Cycle service level (0.5–0.999)", review_period_days: "Review period (days)", overstock_cover_multiplier: "Overstock threshold (× lead-time + review cover)", demand_window_days: "Demand statistics window (days)" },
  },
  supplier_weights: {
    title: "Supplier score weights",
    description: "Must be non-negative and sum to 1.",
    fields: { on_time: "On-time delivery", quality: "Product quality", cost: "Cost competitiveness", lead_time_consistency: "Lead-time consistency" },
  },
  procurement: { title: "Procurement", description: "Default budget used by planning screens.", fields: { monthly_budget: "Monthly budget (₹)" } },
  logistics: {
    title: "Logistics",
    description: "Vehicle and tariff assumptions for route planning and transport cost estimates.",
    fields: { vehicle_capacity_units: "Vehicle capacity (units)", store_replenishment_days: "Store replenishment cycle (days)", fuel_cost_multiplier: "Per-km tariff multiplier" },
  },
  finance: { title: "Financial assumptions", description: "Used by scenario impact estimates.", fields: { overhead_cost_per_month: "Fixed overhead per month (₹)" } },
};

export function validateSettings(group: string, values: Record<string, number | string>): string | null {
  for (const [k, v] of Object.entries(values)) {
    if (typeof v === "number" && (Number.isNaN(v) || v < 0)) return `${k.replace(/_/g, " ")} must be a non-negative number`;
  }
  if (group === "supplier_weights") {
    const sum = Object.values(values).reduce<number>((a, b) => a + Number(b), 0);
    if (Math.abs(sum - 1) > 1e-6) return `Weights must sum to 1 (currently ${sum.toFixed(3)})`;
  }
  if (group === "inventory") {
    const sl = Number(values.service_level);
    if (!(sl >= 0.5 && sl < 1)) return "Service level must be between 0.5 and 0.999";
  }
  return null;
}

export default function SettingsPage() {
  const { user, can } = useAuth();
  const { data, loading, error, reload } = useApi<Settings>("/api/settings");
  return (
    <>
      <PageHeader title="Settings" description="Business parameters, users and profile." />
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          {error && <ErrorState message={error} onRetry={reload} />}
          {loading && <LoadingBlock rows={6} />}
          {!can("configure") && <Alert variant="info">Only administrators can change business parameters. Values are shown read-only.</Alert>}
          {data && Object.entries(GROUPS).map(([key, g]) => <SettingsGroup key={key} group={key} meta={g} values={data[key]} editable={can("configure")} />)}
        </div>
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Your profile</CardTitle></CardHeader>
            <CardContent className="space-y-1 text-sm">
              <div className="font-medium">{user?.full_name}</div>
              <div className="text-muted-foreground">{user?.email}</div>
              <Badge>{user?.role}</Badge>
              <div className="pt-2 text-xs text-muted-foreground">Permissions: {user?.permissions.map((p) => p.replace(/_/g, " ")).join(", ")}</div>
            </CardContent>
          </Card>
          {can("manage_users") && <UserManagement />}
        </div>
      </div>
    </>
  );
}

function SettingsGroup({ group, meta, values, editable }: { group: string; meta: (typeof GROUPS)[string]; values: Record<string, number | string>; editable: boolean }) {
  const [form, setForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setForm(Object.fromEntries(Object.keys(meta.fields).map((k) => [k, String(values?.[k] ?? "")]))), [values, meta.fields]);

  const save = async () => {
    const payload = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, Number(v)]));
    const err = validateSettings(group, payload);
    if (err) return setMsg({ ok: false, text: err });
    setBusy(true);
    try {
      await api.put(`/api/settings/${group}`, payload);
      setMsg({ ok: true, text: "Saved." });
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader><CardTitle>{meta.title}</CardTitle><CardDescription>{meta.description}</CardDescription></CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(meta.fields).map(([k, label]) => (
            <div key={k} className="space-y-1">
              <Label htmlFor={`${group}-${k}`}>{label}</Label>
              <Input id={`${group}-${k}`} type="number" step="any" disabled={!editable} value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
            </div>
          ))}
        </div>
        {editable && (
          <div className="mt-3 flex items-center gap-3">
            <Button size="sm" onClick={save} disabled={busy}>{busy ? <Spinner /> : <Save />} Save</Button>
            {msg && <span className={msg.ok ? "text-xs text-green-700" : "text-xs text-red-700"}>{msg.text}</span>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function UserManagement() {
  const { data, error, reload } = useApi<(User & { last_login_at: string | null })[]>("/api/users");
  const [msg, setMsg] = useState<string | null>(null);
  const update = async (id: number, body: Record<string, unknown>) => {
    setMsg(null);
    try {
      await api.patch(`/api/users/${id}`, body);
      reload();
    } catch (e) {
      setMsg(errorMessage(e));
    }
  };
  return (
    <Card>
      <CardHeader><CardTitle>Users & roles</CardTitle><CardDescription>Self-registered users start as Analysts.</CardDescription></CardHeader>
      <CardContent>
        {error && <ErrorState message={error} onRetry={reload} />}
        {msg && <Alert variant="error" className="mb-2">{msg}</Alert>}
        <Table>
          <THead><TR><TH>User</TH><TH>Role</TH><TH>Active</TH></TR></THead>
          <TBody>
            {data?.map((u) => (
              <TR key={u.id}>
                <TD className="text-xs"><div className="font-medium">{u.full_name}</div><div className="text-muted-foreground">{u.email}</div><div className="text-[10px] text-muted-foreground">last login {fmt.datetime(u.last_login_at)}</div></TD>
                <TD>
                  <Select className="h-8 text-xs" value={u.role} onChange={(e) => update(u.id, { role: e.target.value })} aria-label={`Role for ${u.email}`}>
                    <option>Administrator</option><option>Supply Chain Manager</option><option>Analyst</option>
                  </Select>
                </TD>
                <TD><input type="checkbox" checked={u.is_active} onChange={(e) => update(u.id, { is_active: e.target.checked })} aria-label={`Active ${u.email}`} /></TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}
