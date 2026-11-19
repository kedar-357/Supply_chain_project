import { Navigation, Route as RouteIcon, Truck } from "lucide-react";
import { useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip as MapTooltip } from "react-leaflet";
import { ErrorState, LoadingBlock, PageHeader, SimulatedNote, Spinner } from "@/components/common";
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
import { SERIES, cn, fmt } from "@/lib/utils";

interface Loc { id: number; code: string; name: string; type: string; city: string; latitude: number; longitude: number; daily_demand_units: number }
interface Link { id: number; origin_id: number; destination_id: number; distance_km: number; travel_time_hours: number; cost: number }
interface Network { locations: Loc[]; routes: Link[]; note: string; stats: { nodes: number; links: number; connected: boolean } }
interface PathSummary { path: number[]; codes: string[]; names: string[]; coordinates: [number, number][]; total_distance_km: number; total_time_h: number; total_cost: number; stops: number; legs: { from: string; to: string; distance_km: number; time_h: number; cost: number }[] }
interface ShortestResp { selected: PathSummary; alternatives: (PathSummary & { extra_distance_km: number; extra_cost: number; extra_time_h: number; is_selected: boolean })[]; algorithm: string; note: string; optimize_for: string }
interface VrpResp { vehicles: (PathSummary & { vehicle: number; load: number; utilization: number; stop_codes: string[] })[]; total_distance_km: number; total_time_h: number; total_cost: number; total_demand: number; direct_trips_cost: number; saving_vs_direct_trips: number; algorithm: string; vehicle_capacity: number; note: string }

// Node types are categories → categorical slots in fixed order.
const TYPE_STYLE: Record<string, { color: string; label: string; r: number }> = {
  warehouse: { color: SERIES[0], label: "Warehouse", r: 9 },
  distribution_center: { color: SERIES[1], label: "Distribution centre", r: 7 },
  retail_store: { color: SERIES[2], label: "Retail store", r: 6 },
  supplier: { color: SERIES[6], label: "Supplier", r: 6 },
};

export default function Logistics() {
  const { can } = useAuth();
  const net = useApi<Network>("/api/logistics/network");
  const [route, setRoute] = useState<ShortestResp | null>(null);
  const [altIdx, setAltIdx] = useState(0);
  const [vrp, setVrp] = useState<VrpResp | null>(null);
  const [tab, setTab] = useState("route");
  const locs = net.data?.locations ?? [];
  const byId = useMemo(() => Object.fromEntries(locs.map((l) => [l.id, l])), [locs]);

  return (
    <>
      <PageHeader
        title="Logistics & Route Optimization"
        description="Weighted graph of warehouses, distribution centres, stores and suppliers. Dijkstra shortest paths, k-shortest alternatives, inefficiency detection and OR-Tools vehicle routing."
      />
      <Alert variant="warning" className="mb-4">Distances, travel times and costs are <strong>estimates</strong> from a simulated road network (average speeds and per-km tariffs) — not live traffic information.</Alert>
      {net.error && <ErrorState message={net.error} onRetry={net.reload} />}
      <div className="grid gap-4 xl:grid-cols-5">
        <Card className="overflow-hidden xl:col-span-3">
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle>Network map</CardTitle>
              <CardDescription>{net.data ? `${net.data.stats.nodes} locations · ${net.data.stats.links} road links` : "Loading…"}</CardDescription>
            </div>
            <div className="flex flex-wrap gap-3 text-[11px]">
              {Object.values(TYPE_STYLE).map((t) => (
                <span key={t.label} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-2 border-white shadow" style={{ background: t.color }} />{t.label}</span>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            {net.loading ? <LoadingBlock rows={10} /> : (
              <div className="h-[520px]">
                <MapContainer center={[19.8, 80]} zoom={4.75} zoomSnap={0.25} scrollWheelZoom className="h-full w-full">
                  <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  {net.data?.routes.map((r) => byId[r.origin_id] && byId[r.destination_id] && (
                    <Polyline key={r.id} positions={[[byId[r.origin_id].latitude, byId[r.origin_id].longitude], [byId[r.destination_id].latitude, byId[r.destination_id].longitude]]} pathOptions={{ color: "#475569", weight: 1.5, opacity: 0.55 }}>
                      <MapTooltip sticky>{byId[r.origin_id].code} ↔ {byId[r.destination_id].code}: {r.distance_km} km · {r.travel_time_hours} h · {fmt.inr(r.cost)}</MapTooltip>
                    </Polyline>
                  ))}
                  {tab === "route" && route && (
                    <Polyline positions={(route.alternatives[altIdx] ?? route.selected).coordinates} pathOptions={{ color: SERIES[0], weight: 5, opacity: 0.9 }} />
                  )}
                  {tab === "vrp" && vrp?.vehicles.map((v, i) => (
                    <Polyline key={v.vehicle} positions={v.coordinates} pathOptions={{ color: SERIES[(i + 1) % SERIES.length], weight: 4, opacity: 0.9 }} />
                  ))}
                  {locs.map((l) => {
                    const st = TYPE_STYLE[l.type] ?? TYPE_STYLE.retail_store;
                    return (
                      <CircleMarker key={l.id} center={[l.latitude, l.longitude]} radius={st.r} pathOptions={{ color: "#fff", weight: 2, fillColor: st.color, fillOpacity: 1 }}>
                        <MapTooltip>
                          <strong>{l.code}</strong> · {l.name}<br />{st.label}, {l.city}{l.daily_demand_units ? ` · ${l.daily_demand_units} units/day` : ""}
                        </MapTooltip>
                      </CircleMarker>
                    );
                  })}
                </MapContainer>
              </div>
            )}
            <SimulatedNote className="mt-2" />
          </CardContent>
        </Card>

        <div className="xl:col-span-2">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="w-full">
              <TabsTrigger value="route" className="flex-1">Shortest route</TabsTrigger>
              <TabsTrigger value="vrp" className="flex-1">Multi-stop (VRP)</TabsTrigger>
            </TabsList>
            <TabsContent value="route">
              <ShortestRoutePanel locs={locs} onResult={(r) => { setRoute(r); setAltIdx(0); }} result={route} altIdx={altIdx} setAltIdx={setAltIdx} />
            </TabsContent>
            <TabsContent value="vrp">
              <VrpPanel locs={locs} onResult={setVrp} result={vrp} canRun={can("optimize")} />
            </TabsContent>
          </Tabs>
        </div>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <InefficientRoutes />
        <CostModel />
      </div>
    </>
  );
}

function ShortestRoutePanel({ locs, onResult, result, altIdx, setAltIdx }: { locs: Loc[]; onResult: (r: ShortestResp) => void; result: ShortestResp | null; altIdx: number; setAltIdx: (i: number) => void }) {
  const [f, setF] = useState({ origin: "", destination: "", optimize_for: "cost", multiplier: "1" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    if (!f.origin || !f.destination) return setError("Choose an origin and a destination");
    if (f.origin === f.destination) return setError("Origin and destination must differ");
    const mult = Number(f.multiplier);
    if (!(mult > 0 && mult <= 5)) return setError("Cost multiplier must be between 0 and 5");
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<ShortestResp>("/api/logistics/shortest-route", { origin_id: Number(f.origin), destination_id: Number(f.destination), optimize_for: f.optimize_for, cost_multiplier: mult });
      onResult(r.data);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const origins = locs.filter((l) => l.type === "warehouse" || l.type === "distribution_center" || l.type === "supplier");
  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1"><Label htmlFor="r-o">Start</Label>
            <Select id="r-o" value={f.origin} onChange={(e) => setF({ ...f, origin: e.target.value })}><option value="">Select…</option>{origins.map((l) => <option key={l.id} value={l.id}>{l.code} · {l.city}</option>)}</Select></div>
          <div className="space-y-1"><Label htmlFor="r-d">Destination</Label>
            <Select id="r-d" value={f.destination} onChange={(e) => setF({ ...f, destination: e.target.value })}><option value="">Select…</option>{locs.map((l) => <option key={l.id} value={l.id}>{l.code} · {l.city}</option>)}</Select></div>
          <div className="space-y-1"><Label htmlFor="r-w">Optimise for</Label>
            <Select id="r-w" value={f.optimize_for} onChange={(e) => setF({ ...f, optimize_for: e.target.value })}><option value="cost">Lowest cost</option><option value="distance">Shortest distance</option><option value="time">Fastest time</option></Select></div>
          <div className="space-y-1"><Label htmlFor="r-m">Per-km cost multiplier</Label><Input id="r-m" type="number" step="0.05" value={f.multiplier} onChange={(e) => setF({ ...f, multiplier: e.target.value })} /></div>
        </div>
        <Button className="w-full" onClick={run} disabled={busy}>{busy ? <Spinner /> : <Navigation />} Find route</Button>
        {error && <Alert variant="error">{error}</Alert>}
        {result && (
          <>
            <div className="grid grid-cols-3 gap-2 rounded-md bg-secondary/60 p-3 text-center">
              <div><div className="text-[11px] text-muted-foreground">Distance</div><div className="tabular font-semibold">{fmt.num(result.selected.total_distance_km)} km</div></div>
              <div><div className="text-[11px] text-muted-foreground">Est. time</div><div className="tabular font-semibold">{result.selected.total_time_h.toFixed(1)} h</div></div>
              <div><div className="text-[11px] text-muted-foreground">Est. cost</div><div className="tabular font-semibold">{fmt.inr(result.selected.total_cost)}</div></div>
            </div>
            <div className="text-xs"><span className="font-medium">Selected route:</span> {result.selected.codes.join(" → ")}</div>
            <div className="text-xs font-medium">Alternatives (click to show on map)</div>
            <Table>
              <THead><TR><TH>#</TH><TH>Path</TH><TH className="text-right">km</TH><TH className="text-right">h</TH><TH className="text-right">Cost</TH></TR></THead>
              <TBody>
                {result.alternatives.map((a, i) => (
                  <TR key={i} onClick={() => setAltIdx(i)} className={cn("cursor-pointer", altIdx === i && "bg-accent")}>
                    <TD className="text-xs">{i + 1}{a.is_selected && <Badge className="ml-1">best</Badge>}</TD>
                    <TD className="text-[11px]">{a.codes.join(" → ")}</TD>
                    <TD className="tabular text-right text-xs">{fmt.num(a.total_distance_km)}{a.extra_distance_km > 0 && <div className="text-[10px] text-muted-foreground">+{fmt.num(a.extra_distance_km)}</div>}</TD>
                    <TD className="tabular text-right text-xs">{a.total_time_h.toFixed(1)}</TD>
                    <TD className="tabular text-right text-xs">{fmt.inrCompact(a.total_cost)}{a.extra_cost > 0 && <div className="text-[10px] text-muted-foreground">+{fmt.inrCompact(a.extra_cost)}</div>}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <p className="text-[11px] text-muted-foreground">{result.algorithm}.</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function VrpPanel({ locs, onResult, result, canRun }: { locs: Loc[]; onResult: (r: VrpResp) => void; result: VrpResp | null; canRun: boolean }) {
  const [depot, setDepot] = useState("");
  const [stops, setStops] = useState<number[]>([]);
  const [vehicles, setVehicles] = useState("2");
  const [capacity, setCapacity] = useState("4000");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    if (!depot) return setError("Choose a starting warehouse");
    if (stops.length === 0) return setError("Select at least one destination");
    if (!(Number(vehicles) >= 1 && Number(vehicles) <= 20)) return setError("Vehicles must be between 1 and 20");
    if (!(Number(capacity) > 0)) return setError("Vehicle capacity must be positive");
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<VrpResp>("/api/logistics/optimize", { depot_id: Number(depot), stop_ids: stops, num_vehicles: Number(vehicles), vehicle_capacity: Number(capacity) });
      onResult(r.data);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const stores = locs.filter((l) => l.type === "retail_store" || l.type === "distribution_center");
  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="grid grid-cols-3 gap-2">
          <div className="col-span-3 space-y-1"><Label htmlFor="v-d">Starting warehouse</Label>
            <Select id="v-d" value={depot} onChange={(e) => setDepot(e.target.value)}><option value="">Select…</option>{locs.filter((l) => l.type === "warehouse").map((l) => <option key={l.id} value={l.id}>{l.code} · {l.name}</option>)}</Select></div>
          <div className="space-y-1"><Label htmlFor="v-n">Vehicles</Label><Input id="v-n" type="number" min={1} max={20} value={vehicles} onChange={(e) => setVehicles(e.target.value)} /></div>
          <div className="col-span-2 space-y-1"><Label htmlFor="v-c">Vehicle capacity (units)</Label><Input id="v-c" type="number" min={1} value={capacity} onChange={(e) => setCapacity(e.target.value)} /></div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-muted-foreground">Destinations (demand = daily units)</div>
          <div className="flex max-h-32 flex-wrap gap-1.5 overflow-auto">
            {stores.map((s) => (
              <label key={s.id} className={cn("flex cursor-pointer items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]", stops.includes(s.id) && "border-primary bg-accent")}>
                <input type="checkbox" className="accent-blue-600" checked={stops.includes(s.id)} onChange={(e) => setStops(e.target.checked ? [...stops, s.id] : stops.filter((x) => x !== s.id))} />
                {s.code} ({s.daily_demand_units})
              </label>
            ))}
          </div>
        </div>
        <Button className="w-full" onClick={run} disabled={busy || !canRun}>{busy ? <Spinner /> : <Truck />} Optimise vehicle routes</Button>
        {error && <Alert variant="error">{error}</Alert>}
        {result && (
          <>
            <div className="grid grid-cols-3 gap-2 rounded-md bg-secondary/60 p-3 text-center">
              <div><div className="text-[11px] text-muted-foreground">Total distance</div><div className="tabular font-semibold">{fmt.num(result.total_distance_km)} km</div></div>
              <div><div className="text-[11px] text-muted-foreground">Est. cost</div><div className="tabular font-semibold">{fmt.inr(result.total_cost)}</div></div>
              <div><div className="text-[11px] text-muted-foreground">Saving vs direct</div><div className="tabular font-semibold text-green-700">{fmt.inr(result.saving_vs_direct_trips)}</div></div>
            </div>
            {result.vehicles.map((v, i) => (
              <div key={v.vehicle} className="rounded-md border p-2 text-xs">
                <div className="flex items-center gap-2 font-medium">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: SERIES[(i + 1) % SERIES.length] }} />
                  Vehicle {v.vehicle} · load {fmt.num(v.load)} ({(v.utilization * 100).toFixed(0)}%)
                </div>
                <div className="mt-1">{v.stop_codes.join(" → ")}</div>
                <div className="text-muted-foreground">{fmt.num(v.total_distance_km)} km · {v.total_time_h.toFixed(1)} h · {fmt.inr(v.total_cost)}</div>
              </div>
            ))}
            <p className="text-[11px] text-muted-foreground">{result.algorithm}. Direct-trip baseline: one round trip per stop ({fmt.inr(result.direct_trips_cost)}).</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function InefficientRoutes() {
  const { data, loading, error, reload } = useApi<{ route_id: number; from: string; to: string; distance_km: number; cost: number; detour_ratio: number; best_alternative_cost: number | null; potential_saving: number | null; reasons: string[] }[]>(
    "/api/logistics/inefficient-routes",
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><RouteIcon className="h-4 w-4" /> Inefficient routes</CardTitle>
        <CardDescription>Direct links costing &gt;1.15× the cheapest alternative, or with a detour ratio &gt;1.5 (road ÷ straight-line distance).</CardDescription>
      </CardHeader>
      <CardContent>
        {error && <ErrorState message={error} onRetry={reload} />}
        {loading ? <LoadingBlock /> : (
          <Table>
            <THead><TR><TH>Link</TH><TH className="text-right">Cost</TH><TH className="text-right">Alternative</TH><TH className="text-right">Saving</TH><TH>Why</TH></TR></THead>
            <TBody>
              {data?.map((r) => (
                <TR key={r.route_id}>
                  <TD className="text-xs font-medium">{r.from} → {r.to}<div className="text-[10px] text-muted-foreground">{fmt.num(r.distance_km)} km · detour {r.detour_ratio}×</div></TD>
                  <TD className="tabular text-right text-xs">{fmt.inr(r.cost)}</TD>
                  <TD className="tabular text-right text-xs">{fmt.inr(r.best_alternative_cost)}</TD>
                  <TD className="tabular text-right text-xs font-medium text-green-700">{fmt.inr(r.potential_saving)}</TD>
                  <TD className="max-w-[260px] text-[11px] text-muted-foreground">{r.reasons.join(" ")}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function CostModel() {
  const { data, loading, error, reload } = useApi<{ lanes: { store: string; store_name: string; serving_warehouse: string; route: string[]; one_way_distance_km: number; trips_per_month: number; load_per_trip: number; monthly_cost: number; cost_per_unit: number | null }[]; total_monthly_cost: number; vehicle_capacity: number; replenishment_cycle_days: number }>(
    "/api/logistics/cost-model",
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Distribution cost model</CardTitle>
        <CardDescription>
          Each store is served from its cheapest warehouse every {data?.replenishment_cycle_days ?? 7} days with full-truck loads (capacity {fmt.num(data?.vehicle_capacity)}). Estimated total:{" "}
          <strong>{fmt.inr(data?.total_monthly_cost)}</strong>/month.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && <ErrorState message={error} onRetry={reload} />}
        {loading ? <LoadingBlock /> : (
          <Table>
            <THead><TR><TH>Store</TH><TH>From</TH><TH className="text-right">km</TH><TH className="text-right">Trips/mo</TH><TH className="text-right">Load/trip</TH><TH className="text-right">Monthly</TH><TH className="text-right">₹/unit</TH></TR></THead>
            <TBody>
              {data?.lanes.map((l) => (
                <TR key={l.store}>
                  <TD className="text-xs" title={l.route.join(" → ")}><span className="font-medium">{l.store}</span><div className="text-[10px] text-muted-foreground">{l.store_name}</div></TD>
                  <TD className="text-xs">{l.serving_warehouse}</TD>
                  <TD className="tabular text-right text-xs">{fmt.num(l.one_way_distance_km)}</TD>
                  <TD className="tabular text-right text-xs">{l.trips_per_month}</TD>
                  <TD className="tabular text-right text-xs">{fmt.num(l.load_per_trip)}</TD>
                  <TD className="tabular text-right text-xs">{fmt.inrCompact(l.monthly_cost)}</TD>
                  <TD className="tabular text-right text-xs">{l.cost_per_unit?.toFixed(2)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
