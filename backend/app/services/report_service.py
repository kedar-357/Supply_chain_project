"""Reports (tabular) and CSV / Excel / PDF export."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import pandas as pd

from app.core.exceptions import ValidationFailed
from app.core.config import get_settings
from app.storage.store import Store
from app.services import forecasting_service, inventory_service, logistics_service, recommendation_service, scenario_service, supplier_service
from app.services.scenario_service import KPI_DEFS

REPORTS = {
    "inventory": "Monthly inventory report",
    "forecasting": "Demand forecasting report",
    "suppliers": "Supplier performance report",
    "stockout_risk": "Stockout risk report",
    "transportation": "Transportation cost report",
    "optimization": "Inventory optimization report",
    "scenarios": "Scenario comparison report",
    "recommendations": "Management recommendations",
}


def _range(start: date | None, end: date | None, store: Store) -> tuple[date, date]:
    data_end = forecasting_service.data_end_date(store) or date.today()
    end = end or data_end
    start = start or (end - timedelta(days=29))
    if start > end:
        raise ValidationFailed("start date must be before end date")
    return start, end


def build(store: Store, report: str, start: date | None = None, end: date | None = None) -> dict:
    if report not in REPORTS:
        raise ValidationFailed(f"Unknown report. Expected one of: {', '.join(REPORTS)}")
    s, e = _range(start, end, store)
    df, notes = {
        "inventory": _inventory, "forecasting": _forecasting, "suppliers": _suppliers, "stockout_risk": _stockout,
        "transportation": _transport, "optimization": _optimization, "scenarios": _scenarios, "recommendations": _recs,
    }[report](store, s, e)
    df = df.where(pd.notna(df), None) if not df.empty else df
    return {"report": report, "title": REPORTS[report], "period": {"start": s.isoformat(), "end": e.isoformat()},
            "generated_at": datetime.now().isoformat(timespec="seconds"), "notes": notes,
            "columns": list(df.columns), "rows": inventory_service.records(df) if not df.empty else []}


def _inventory(store, s, e):
    _, snap = inventory_service.snapshot(store)
    raw = store.read("sales")
    raw = raw[(raw["date"] >= s) & (raw["date"] <= e)]
    raw = raw.assign(revenue=raw["units_sold"] * raw["unit_price"])
    sales = (raw.groupby(["product_id", "warehouse_id"]).agg(units_sold_in_period=("units_sold", "sum"),
                                                              revenue_in_period=("revenue", "sum")).reset_index())
    m = snap.merge(sales, on=["product_id", "warehouse_id"], how="left").fillna({"units_sold_in_period": 0, "revenue_in_period": 0})
    cols = ["sku", "name", "category", "warehouse_code", "abc_class", "current_stock", "incoming_units", "inventory_value",
            "units_sold_in_period", "revenue_in_period", "avg_daily_demand", "days_of_cover", "safety_stock", "reorder_point",
            "eoq", "status"]
    return m[cols].sort_values(["sku", "warehouse_code"]), [f"Sales columns cover {s} to {e}; stock levels are current."]


def _forecasting(store, s, e):
    m = forecasting_service.metrics(store)
    if not m.get("trained"):
        return pd.DataFrame(), ["No trained forecasting model yet."]
    totals = forecasting_service.forecast_totals(store, 30)
    rows = []
    for pid, r in m["per_product"].items():
        rows.append({"sku": r["sku"], "recommended_model": r["best_model"], "forecast_next_30d": round(totals.get(int(pid), 0), 1),
                     "xgboost_mae": r["xgboost"]["mae"], "xgboost_wape": r["xgboost"]["wape"],
                     "sarima_mae": r["sarima"]["mae"], "sarima_wape": r["sarima"]["wape"],
                     "naive_mae": r["seasonal_naive"]["mae"], "naive_wape": r["seasonal_naive"]["wape"]})
    port = m["portfolio"]
    notes = [f"Holdout: last {m['holdout_days']} days before {m['trained_through']} (chronological split).",
             *[f"{k}: MAE {v['mae']}, RMSE {v['rmse']}, MAPE {v['mape']}% (zero-demand days excluded), WAPE {v['wape']}%"
               for k, v in port.items()]]
    return pd.DataFrame(rows).sort_values("sku"), notes


def _suppliers(store, s, e):
    p = supplier_service.performance(store, since=s)
    cols = ["rank", "code", "name", "region", "performance_score", "on_time_rate", "avg_delay_days", "avg_lead_time_days",
            "lead_time_std_days", "avg_quality_score", "avg_unit_cost_index", "purchase_order_count", "total_spend",
            "ml_delay_probability", "risk_category", "trend"]
    df = pd.DataFrame(p["suppliers"])
    return (df[cols] if not df.empty else df), [p["methodology"], f"Delivery metrics computed for deliveries since {s}."]


def _stockout(store, s, e):
    _, snap = inventory_service.snapshot(store)
    df = snap[snap["predicted_stockout"] | snap["status"].isin(["critical", "out_of_stock", "low"])]
    cols = ["sku", "name", "warehouse_code", "supplier_name", "abc_class", "current_stock", "incoming_units",
            "forecast_daily_demand", "lead_time_days", "stockout_in_days", "unmet_units_before_replenishment", "revenue_at_risk", "status"]
    return df[cols].sort_values("stockout_in_days"), ["Projection uses the latest stored forecast and open purchase orders."]


def _transport(store, s, e):
    m = logistics_service.distribution_cost_model(store)
    df = pd.DataFrame(m["lanes"])
    if not df.empty:
        df["route"] = df["route"].map(lambda r: " → ".join(r))
    return df, [m["note"], f"Total estimated monthly cost: ₹{m['total_monthly_cost']:,.0f}"]


def _optimization(store, s, e):
    opt = inventory_service.latest_optimization(store)
    if not opt:
        return pd.DataFrame(), ["No optimization run yet."]
    df = pd.DataFrame(opt["items"]).drop(columns=["item_id", "product_id", "warehouse_id", "supplier_id"], errors="ignore")
    return df, [f"Optimization #{opt['optimization_id']} at {opt['created_at']}, solver {opt['summary']['solver_status']}, "
                f"total ₹{opt['summary']['total_order_cost']:,.0f}"]


def _scenarios(store, s, e):
    rows = []
    for sc in scenario_service.list_saved(store)[:20]:
        row = {"scenario": sc["name"], "type": sc["scenario_type"], "created_at": sc["created_at"][:16].replace("T", " ")}
        for r in sc["results"]:
            row[f"{r['metric']} (base)"] = r["baseline_value"]
            row[f"{r['metric']} (scenario)"] = r["scenario_value"]
        rows.append(row)
    return pd.DataFrame(rows), [f"{len(rows)} saved scenarios. KPIs: {', '.join(l for _, l, _, _ in KPI_DEFS)}"]


def _recs(store, s, e):
    recs = recommendation_service.list_recommendations(store)
    df = pd.DataFrame([{k: r[k] for k in ("priority", "category", "title", "reason", "expected_impact", "status")} for r in recs])
    return df, ["Generated by transparent business rules from calculated analytics."]


# ---------------------------------------------------------------- export
def export(store: Store, report: str, fmt: str, start: date | None, end: date | None) -> tuple[bytes, str, str]:
    """Builds the file and also keeps a copy under data/reports/ (generated report archive)."""
    content, media, filename = _export(store, report, fmt, start, end)
    out = get_settings().reports_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / filename).write_bytes(content)
    return content, media, filename


def _export(store: Store, report: str, fmt: str, start: date | None, end: date | None) -> tuple[bytes, str, str]:
    data = build(store, report, start, end)
    df = pd.DataFrame(data["rows"], columns=data["columns"])
    base = f"supplychainx_{report}_{data['period']['end']}"
    if fmt == "csv":
        return df.to_csv(index=False).encode("utf-8-sig"), "text/csv", f"{base}.csv"
    if fmt == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            df.to_excel(xw, sheet_name="Report", index=False)
            pd.DataFrame({"notes": [data["title"], f"Period {data['period']['start']} to {data['period']['end']}",
                                    f"Generated {data['generated_at']}", *data["notes"]]}).to_excel(xw, sheet_name="Notes", index=False)
            ws = xw.sheets["Report"]
            for i, col in enumerate(df.columns, 1):
                width = min(max(len(str(col)), *(len(str(v)) for v in df[col].head(200))) + 2, 50) if len(df) else len(col) + 2
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{base}.xlsx"
    if fmt == "pdf":
        return _pdf(data, df), "application/pdf", f"{base}.pdf"
    raise ValidationFailed("format must be csv, xlsx or pdf")


def _pdf(data: dict, df: pd.DataFrame) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=data["title"])
    st = getSampleStyleSheet()
    small = st["BodyText"].clone("small", fontSize=6.5, leading=8)
    story = [Paragraph(f"SupplyChainX — {data['title']}", st["Title"]),
             Paragraph(f"Period {data['period']['start']} to {data['period']['end']} · generated {data['generated_at']}", st["Normal"])]
    for n in data["notes"]:
        story.append(Paragraph(str(n).replace("₹", "INR "), small))
    story.append(Spacer(1, 4 * mm))
    if df.empty:
        story.append(Paragraph("No data.", st["Normal"]))
    else:
        cols = list(df.columns)[:14]
        def fmt(v):
            if v is None:
                return "–"
            if isinstance(v, float):
                return f"{v:,.2f}"
            return str(v).replace("₹", "INR ")
        rows = [[Paragraph(f"<b>{c}</b>", small) for c in cols]] + [[Paragraph(fmt(v), small) for v in r]
                                                                    for r in df[cols].head(400).itertuples(index=False)]
        t = Table(rows, repeatRows=1, colWidths=[(277 * mm) / len(cols)] * len(cols))
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f1b33")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f9")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dae3")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)
        if len(df) > 400 or len(df.columns) > 14:
            story.append(Paragraph("PDF truncated to 400 rows × 14 columns; use CSV/Excel for the full report.", small))
    doc.build(story)
    return buf.getvalue()


__all__ = ["REPORTS", "build", "export"]
