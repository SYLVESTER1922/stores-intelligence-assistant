"""
LOBELS BISCUITS - STORES AI ASSISTANT
======================================
Netrisyl Insights.
Design replicated from JCC Assistant (navy + gold, Inter font).
Stack: Gradio + Supabase Postgres + GPT-4o-mini + Whisper (voice input).
"""

import os
import json
import base64
from pathlib import Path
from datetime import date, datetime

import gradio as gr
import plotly.graph_objects as go
from supabase import create_client, Client
from openai import OpenAI


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_SERVICE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

OPENAI_MODEL = "gpt-4o-mini"

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
oai = OpenAI(api_key=OPENAI_API_KEY)

CLIENT_ID = "lobels"
MONTH_ORDER = ["JAN","FEB","MAR","APR","MAY","JUN",
               "JUL","AUG","SEP","OCT","NOV","DEC"]


# ---------------------------------------------------------------------------
# Logos as base64
# ---------------------------------------------------------------------------
def _data_uri(path, mime):
    p = Path(__file__).parent / path
    if p.exists():
        with open(p, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")
    return ""

LOGO_DATA_URI     = _data_uri("logo.png", "image/png")
NETRISYL_DATA_URI = _data_uri("netrisyl_logo.png", "image/png")
LOGO_PATH = Path(__file__).parent / "logo.png"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _fmt(n):
    return f"{n:,.2f}".rstrip("0").rstrip(".") if n else "0"


# ---------------------------------------------------------------------------
# Material list for the finder dropdown
# ---------------------------------------------------------------------------
def load_material_names():
    rows = sb.table("lobels_stores").select("description").eq(
        "client_id", CLIENT_ID).execute().data
    names = sorted({r["description"] for r in rows if r.get("description")})
    return names

try:
    MATERIAL_NAMES = load_material_names()
except Exception as e:
    print("Could not load material names:", e)
    MATERIAL_NAMES = []


# ---------------------------------------------------------------------------
# Material Finder - latest day snapshot for one material
# ---------------------------------------------------------------------------
def material_snapshot(material_name):
    if not material_name:
        return "Select a material above to see its latest snapshot."
    rows = sb.table("lobels_stores").select("*").eq(
        "client_id", CLIENT_ID).eq("description", material_name).execute().data
    if not rows:
        return f"No data found for **{material_name}**."

    # Find the latest day. Dates are ISO (YYYY-MM-DD) so plain string
    # sorting is already chronological - no parsing needed.
    rows.sort(key=lambda r: str(r.get("txn_date", "")), reverse=True)
    latest = rows[0]

    # Month totals for context
    month = latest.get("month")
    month_rows = [r for r in rows if r.get("month") == month]
    month_issues = sum(_num(r["daily_issues"]) for r in month_rows)

    code = latest.get("stock_code", "")
    cat  = latest.get("category", "")
    dt   = latest.get("txn_date", "")
    return f"""### {material_name}
**Code:** {code}  ·  **Category:** {cat}

**Latest day on record — {dt}**
- Daily issues: **{_fmt(_num(latest.get('daily_issues')))} kg**
- Receipts: **{_fmt(_num(latest.get('receipts')))} kg**
- Opening stock: **{_fmt(_num(latest.get('opening_stock')))} kg**
- Physical closing: **{_fmt(_num(latest.get('physical_closing')))} kg**

**{month} 2026 summary**
- Total issued this month: **{_fmt(month_issues)} kg**
- Month variance: **{_fmt(_num(latest.get('variance')))} kg** ({latest.get('variance_flag','')})
"""


# ---------------------------------------------------------------------------
# Quick Reports — sidebar summary panels (one-click, no typing)
# ---------------------------------------------------------------------------
def _latest_month():
    """Most recent month present in the data."""
    rows = sb.table("lobels_stores").select("txn_date, month").eq(
        "client_id", CLIENT_ID).order("txn_date", desc=True).limit(1).execute().data
    return rows[0]["month"] if rows else "JUN"


def report_reorder():
    res = q_reorder_status()
    items = [i for i in res["items"] if i["status"] == "REORDER NOW"]
    if not items:
        return "### 🔄 Reorder Alerts\n\nAll materials are above their reorder points. Nothing to order right now."
    items = items[:12]
    lines = [f"### 🔄 Reorder Alerts",
             f"**{res['needing_reorder']} materials** are at or below reorder point.\n"]
    for i in items:
        lines.append(
            f"**{i['material']}** — {_fmt(i['current_stock_kg'])} kg left "
            f"(reorder at {_fmt(i['reorder_point_kg'])} kg)  \n"
            f"   _{i.get('supplier') or 'n/a'} · {i.get('lead_time_days') or '?'} day lead_")
    if res["needing_reorder"] > 12:
        lines.append(f"\n…and {res['needing_reorder'] - 12} more.")
    return "\n".join(lines)


def report_losses():
    month = _latest_month()
    res = q_variances(month=month, flag="LOSS", limit=8)
    items = [i for i in res["items"] if i["variance_kg"] < 0]
    if not items:
        return f"### 📉 Top Losses ({month} 2026)\n\nNo stock losses recorded this month."
    lines = [f"### 📉 Top Losses ({month} 2026)\n"]
    # Add USD value where we have a cost
    mats = {m["description"]: _num(m.get("unit_cost_usd"))
            for m in sb.table("lobels_materials").select(
                "description, unit_cost_usd").execute().data}
    for i in items:
        cost = mats.get(i["material"], 0)
        usd = abs(i["variance_kg"]) * cost
        usd_str = f" ≈ **${_fmt(usd)}**" if cost else ""
        lines.append(f"**{i['material']}** — {_fmt(i['variance_kg'])} kg{usd_str}")
    return "\n\n".join(lines)


def report_spend():
    month = _latest_month()
    # Spend = sum of (month issues x unit cost) across materials
    mats = sb.table("lobels_materials").select(
        "description, unit_cost_usd").execute().data
    cost_map = {m["description"]: _num(m.get("unit_cost_usd")) for m in mats}
    rows = sb.table("lobels_stores").select(
        "description, daily_issues").eq("client_id", CLIENT_ID
        ).eq("month", month).execute().data
    spend = {}
    for r in rows:
        d = r["description"]
        spend[d] = spend.get(d, 0) + _num(r["daily_issues"]) * cost_map.get(d, 0)
    total = sum(spend.values())
    top = sorted(spend.items(), key=lambda x: -x[1])[:8]
    lines = [f"### 💵 Monthly Spend ({month} 2026)",
             f"**Total consumption value: ${_fmt(total)}**\n",
             "Top materials by value:"]
    for name, val in top:
        if val <= 0:
            continue
        lines.append(f"**{name}** — ${_fmt(val)}")
    return "\n\n".join(lines)


def report_runout():
    """Materials that will run out before their lead time."""
    mats = sb.table("lobels_materials").select("*").execute().data
    risks = []
    for m in mats:
        name = m["description"]
        lead = _num(m.get("lead_time_days"))
        if lead <= 0:
            continue
        f = q_runout_forecast(name)
        dleft = f.get("estimated_days_left")
        if dleft is not None and dleft <= lead:
            risks.append((name, dleft, lead, m.get("supplier")))
    if not risks:
        return "### ⏳ Run-out Risks\n\nNo materials are at immediate run-out risk."
    risks.sort(key=lambda x: x[1])
    lines = [f"### ⏳ Run-out Risks",
             f"**{len(risks)} materials** will run out before new stock arrives.\n"]
    for name, dleft, lead, sup in risks[:12]:
        lines.append(f"**{name}** — ~{dleft} days left, {int(lead)} day lead  \n"
                     f"   _order from {sup or 'n/a'} now_")
    if len(risks) > 12:
        lines.append(f"\n…and {len(risks) - 12} more.")
    return "\n".join(lines)


def report_abc():
    """ABC classification summary."""
    res = q_abc_classification()
    if "error" in res:
        return f"### 🏷️ ABC Classification\n\n{res['error']}"
    total = res["total_value_usd"]
    a = res["A"]
    b = res["B"]
    c = res["C"]
    lines = [
        f"### 🏷️ ABC Inventory Classification",
        f"**Total inventory value: ${_fmt(total)}**\n",
        f"**Class A — Critical ({a['count']} materials, 80% of value)**",
        f"_{a['description']}_",
    ]
    for m in a["materials"][:5]:
        lines.append(f"- {m['material']} — ${_fmt(m['annual_value_usd'])} "
                     f"({m['value_pct']}%)")
    if len(a["materials"]) > 5:
        lines.append(f"  …and {len(a['materials'])-5} more Class A materials")
    lines += [
        f"\n**Class B — Important ({b['count']} materials, 15% of value)**",
        f"_{b['description']}_",
        f"\n**Class C — Low value ({c['count']} materials, 5% of value)**",
        f"_{c['description']}_",
    ]
    return "\n\n".join(lines)


def report_purchasing_priority():
    """Purchasing priority list summary."""
    res = q_purchasing_priority()
    items = res.get("priority_list", [])
    critical = [i for i in items if i["urgency_score"] >= 9]
    urgent   = [i for i in items if 6 <= i["urgency_score"] < 9]
    lines = [
        f"### 📋 Purchasing Priority List",
        f"**{res['critical_count']} critical · {res['urgent_count']} urgent** "
        f"out of {res['total_assessed']} assessed\n",
    ]
    if critical:
        lines.append("**🔴 Order Immediately:**")
        for i in critical[:8]:
            lines.append(
                f"**{i['material']}** — {_fmt(i['current_stock_kg'])} kg left, "
                f"{i['days_left']} days · {i['lead_time_days']}d lead · "
                f"_{i.get('supplier','?')}_")
    if urgent:
        lines.append("\n**🟠 Order Soon:**")
        for i in urgent[:5]:
            lines.append(
                f"**{i['material']}** — {_fmt(i['current_stock_kg'])} kg · "
                f"{i['days_left']} days left")
    return "\n\n".join(lines)


def report_production_risk():
    """Critical production risk summary."""
    res = q_production_risk()
    items = res.get("top_risks", [])
    lines = [
        f"### ⚠️ Critical Production Risk Monitor",
        f"**{res['total_at_risk']} materials** pose critical production risk.\n",
    ]
    for i in items[:10]:
        lines.append(
            f"{i['status']} **{i['material']}**  \n"
            f"   Stock: {_fmt(i['current_stock_kg'])} kg · "
            f"~{i['days_left']} days left · "
            f"Daily value: ${_fmt(i['daily_value_usd'])}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Voice input (Whisper transcription)
# ---------------------------------------------------------------------------
def transcribe(audio_path):
    if not audio_path:
        return ""
    try:
        with open(audio_path, "rb") as f:
            tr = oai.audio.transcriptions.create(model="whisper-1", file=f)
        return tr.text
    except Exception as e:
        print("Transcription failed:", e)
        return ""


# ---------------------------------------------------------------------------
# Data access for the chatbot (GPT tool-calling)
# ---------------------------------------------------------------------------
def q_material_total(description=None, month=None):
    qry = sb.table("lobels_stores").select(
        "description, daily_issues, month").eq("client_id", CLIENT_ID)
    if description:
        qry = qry.ilike("description", f"%{description}%")
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.execute().data
    if not rows:
        return {"found": False, "searched_for": description}
    total = sum(_num(r["daily_issues"]) for r in rows)
    return {"found": True, "material": rows[0]["description"],
            "total_issues_kg": round(total, 2),
            "period": month.upper() if month else "all months (Jan-Jun 2026)"}


def q_top_materials(month=None, limit=10):
    qry = sb.table("lobels_stores").select(
        "description, daily_issues, month").eq("client_id", CLIENT_ID)
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.limit(10000).execute().data
    agg = {}
    for r in rows:
        agg[r["description"]] = agg.get(r["description"], 0) + _num(r["daily_issues"])
    ranked = sorted(agg.items(), key=lambda x: -x[1])[:limit]
    return {"period": month.upper() if month else "all months",
            "top": [{"material": m, "issues_kg": round(v, 2)} for m, v in ranked]}


def q_variances(month=None, flag="LOSS", limit=10):
    qry = sb.table("lobels_stores").select(
        "description, variance, month").eq("client_id", CLIENT_ID)
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.execute().data
    seen = {}
    for r in rows:
        seen[(r["description"], r["month"])] = _num(r["variance"])
    agg = {}
    for (mat, mth), var in seen.items():
        agg[mat] = agg.get(mat, 0) + var
    if flag == "LOSS":
        ranked = sorted(agg.items(), key=lambda x: x[1])[:limit]
    else:
        ranked = sorted(agg.items(), key=lambda x: -x[1])[:limit]
    return {"period": month.upper() if month else "all months", "type": flag,
            "items": [{"material": m, "variance_kg": round(v, 2)} for m, v in ranked]}


def q_category_breakdown(month=None):
    qry = sb.table("lobels_stores").select(
        "category, daily_issues, month").eq("client_id", CLIENT_ID)
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.execute().data
    agg = {}
    for r in rows:
        cat = r["category"] or "Other"
        agg[cat] = agg.get(cat, 0) + _num(r["daily_issues"])
    ranked = sorted(agg.items(), key=lambda x: -x[1])
    return {"period": month.upper() if month else "all months",
            "categories": [{"category": c, "issues_kg": round(v, 2)} for c, v in ranked]}


def q_monthly_trend(description):
    rows = sb.table("lobels_stores").select(
        "description, daily_issues, month").eq("client_id", CLIENT_ID
        ).ilike("description", f"%{description}%").execute().data
    if not rows:
        return {"found": False, "searched_for": description}
    agg = {}
    for r in rows:
        agg[r["month"]] = agg.get(r["month"], 0) + _num(r["daily_issues"])
    ordered = [{"month": m, "issues_kg": round(agg.get(m, 0), 2)}
               for m in MONTH_ORDER if m in agg]
    return {"found": True, "material": rows[0]["description"], "trend": ordered}


# ---- Reference-data tools (use lobels_materials) ----------------

def _latest_closing(description):
    """Most recent physical closing stock for one material."""
    rows = sb.table("lobels_stores").select(
        "physical_closing, txn_date").eq("client_id", CLIENT_ID
        ).ilike("description", f"%{description}%").execute().data
    if not rows:
        return None
    rows.sort(key=lambda r: str(r.get("txn_date", "")), reverse=True)
    return _num(rows[0]["physical_closing"])


def _query_all(columns):
    """Paginate through lobels_stores to get every row, bypassing server row caps."""
    all_rows = []
    start = 0
    page = 1000
    while True:
        batch = sb.table("lobels_stores").select(columns).eq(
            "client_id", CLIENT_ID).range(start, start + page - 1).execute().data
        all_rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return all_rows


def _batch_store_data():
    """Fetch ALL store rows using pagination, return closing + avg-daily maps."""
    rows = _query_all("description, physical_closing, daily_issues, txn_date")
    closing_map = {}
    daily_sum   = {}
    daily_cnt   = {}
    for r in rows:
        d = r["description"]
        if d not in closing_map or str(r["txn_date"]) > str(closing_map[d][0]):
            closing_map[d] = (r["txn_date"], _num(r["physical_closing"]))
        v = _num(r["daily_issues"])
        if v > 0:
            daily_sum[d] = daily_sum.get(d, 0) + v
            daily_cnt[d] = daily_cnt.get(d, 0) + 1
    closing   = {d: v for d, (_, v) in closing_map.items()}
    avg_daily = {d: daily_sum[d] / daily_cnt[d] for d in daily_sum}
    return closing, avg_daily


def q_purchasing_priority():
    """Rank materials by replenishment urgency — BATCHED (2 Supabase calls)."""
    mats = sb.table("lobels_materials").select("*").execute().data
    closing, avg_daily = _batch_store_data()

    results = []
    for m in mats:
        name = m["description"]
        rop  = _num(m.get("reorder_point"))
        lead = _num(m.get("lead_time_days"))
        cost = _num(m.get("unit_cost_usd"))
        if rop <= 0 or lead <= 0:
            continue
        c = closing.get(name)
        if c is None:
            continue
        avg = avg_daily.get(name, 0)
        if avg <= 0:
            continue
        days_left = round(c / avg, 1)
        if c <= 0:
            score = 10
        elif days_left <= lead:
            score = 9
        elif c <= rop:
            score = 8
        elif days_left <= lead * 1.5:
            score = 6
        elif days_left <= lead * 2:
            score = 4
        else:
            score = 2
        results.append({
            "material": name,
            "urgency_score": score,
            "current_stock_kg": round(c, 1),
            "reorder_point_kg": round(rop, 1),
            "avg_daily_use_kg": round(avg, 1),
            "days_left": days_left,
            "lead_time_days": int(lead),
            "order_value_usd": round(rop * cost, 2),
            "supplier": m.get("supplier"),
            "status": ("🔴 CRITICAL" if score >= 9 else
                       "🟠 URGENT"   if score >= 6 else
                       "🟡 MONITOR"  if score >= 4 else "🟢 OK"),
        })
    results.sort(key=lambda x: -x["urgency_score"])
    critical = [r for r in results if r["urgency_score"] >= 9]
    urgent   = [r for r in results if 6 <= r["urgency_score"] < 9]
    return {"total_assessed": len(results),
            "critical_count": len(critical),
            "urgent_count": len(urgent),
            "priority_list": results[:20]}


def q_production_risk():
    """Materials posing greatest production risk — BATCHED."""
    priority = q_purchasing_priority()
    items = priority.get("priority_list", [])
    mats_cost = {m["description"]: _num(m.get("unit_cost_usd"))
                 for m in sb.table("lobels_materials").select(
                     "description, unit_cost_usd").execute().data}
    risk_items = []
    for item in items:
        cost = mats_cost.get(item["material"], 0)
        daily_value = item["avg_daily_use_kg"] * cost
        risk_score = item["urgency_score"] * (1 + min(daily_value / 1000, 5))
        item["daily_value_usd"] = round(daily_value, 2)
        item["risk_score"] = round(risk_score, 1)
        risk_items.append(item)
    risk_items.sort(key=lambda x: -x["risk_score"])
    critical = [r for r in risk_items if r["urgency_score"] >= 8]
    return {"total_at_risk": len(critical),
            "top_risks": risk_items[:10],
            "message": (f"{len(critical)} materials pose critical production risk"
                        if critical else "No critical production risks detected.")}
    """Materials at or below reorder point. If description given, just that one."""
    mats = sb.table("lobels_materials").select("*").execute().data
    if description:
        mats = [m for m in mats if description.lower() in (m.get("description") or "").lower()]
    out = []
    for m in mats:
        rop = _num(m.get("reorder_point"))
        if rop <= 0:
            continue
        closing = _latest_closing(m["description"])
        if closing is None:
            continue
        out.append({
            "material": m["description"],
            "current_stock_kg": round(closing, 1),
            "reorder_point_kg": round(rop, 1),
            "status": "REORDER NOW" if closing <= rop else "OK",
            "supplier": m.get("supplier"),
            "lead_time_days": m.get("lead_time_days"),
        })
    # Sort so REORDER NOW items come first
    out.sort(key=lambda x: (x["status"] != "REORDER NOW", x["material"]))
    needing = [o for o in out if o["status"] == "REORDER NOW"]
    return {"checked": len(out), "needing_reorder": len(needing),
            "items": out if description else (needing or out[:10])}


def q_material_cost(description, month=None):
    """Value of a material's consumption in USD (issues x unit cost)."""
    mat = sb.table("lobels_materials").select("*").ilike(
        "description", f"%{description}%").execute().data
    if not mat:
        return {"found": False, "searched_for": description}
    m = mat[0]
    cost = _num(m.get("unit_cost_usd"))
    total = q_material_total(description=description, month=month)
    if not total.get("found"):
        return {"found": False, "searched_for": description}
    kg = total["total_issues_kg"]
    return {"found": True, "material": m["description"],
            "unit_cost_usd": round(cost, 2),
            "kg_used": kg,
            "total_value_usd": round(kg * cost, 2),
            "period": total["period"]}


def q_supplier_lookup(description=None, supplier=None):
    """Find the supplier of a material, or all materials from a supplier."""
    mats = sb.table("lobels_materials").select(
        "description, supplier, category, lead_time_days").execute().data
    if supplier:
        hits = [m for m in mats if supplier.lower() in (m.get("supplier") or "").lower()]
        return {"supplier": supplier,
                "materials": [{"material": h["description"],
                               "category": h.get("category"),
                               "lead_time_days": h.get("lead_time_days")} for h in hits]}
    if description:
        hits = [m for m in mats if description.lower() in (m.get("description") or "").lower()]
        if not hits:
            return {"found": False, "searched_for": description}
        h = hits[0]
        return {"found": True, "material": h["description"],
                "supplier": h.get("supplier"),
                "lead_time_days": h.get("lead_time_days")}
    return {"error": "Provide a material or a supplier name."}


def q_runout_forecast(description, closing_map=None, avg_daily_map=None):
    """Estimate days until stock runs out vs lead time.
    Optionally accepts pre-fetched batch data to avoid extra queries."""
    mat = sb.table("lobels_materials").select("*").ilike(
        "description", f"%{description}%").execute().data
    if not mat:
        return {"found": False, "searched_for": description}
    m = mat[0]
    name = m["description"]
    if closing_map is not None:
        closing = closing_map.get(name)
    else:
        closing = _latest_closing(name)
    if closing is None:
        return {"found": False, "searched_for": description}
    if avg_daily_map is not None:
        avg_daily = avg_daily_map.get(name, 0)
    else:
        rows = sb.table("lobels_stores").select(
            "daily_issues").eq("client_id", CLIENT_ID
            ).ilike("description", f"%{name}%").execute().data
        issues = [_num(r["daily_issues"]) for r in rows if _num(r["daily_issues"]) > 0]
        avg_daily = sum(issues) / len(issues) if issues else 0
    if avg_daily <= 0:
        return {"found": True, "material": name, "note": "No usage recorded yet."}
    days_left = round(closing / avg_daily, 1)
    lead = m.get("lead_time_days")
    alert = None
    if lead is not None:
        alert = ("ORDER NOW — stock runs out before new delivery arrives"
                 if days_left <= _num(lead) else
                 "OK — enough stock to cover the lead time")
    return {"found": True, "material": name,
            "current_stock_kg": round(closing, 1),
            "avg_daily_use_kg": round(avg_daily, 1),
            "estimated_days_left": days_left,
            "lead_time_days": lead, "alert": alert}


def q_abc_classification():
    """ABC classification: A=top 80% of value, B=next 15%, C=bottom 5%."""
    mats = sb.table("lobels_materials").select(
        "description, unit_cost_usd").execute().data
    cost_map = {m["description"]: _num(m.get("unit_cost_usd")) for m in mats}
    rows = sb.table("lobels_stores").select(
        "description, daily_issues").eq("client_id", CLIENT_ID).execute().data
    # total issued value per material
    spend = {}
    for r in rows:
        d = r["description"]
        spend[d] = spend.get(d, 0) + _num(r["daily_issues"]) * cost_map.get(d, 0)
    ranked = sorted(spend.items(), key=lambda x: -x[1])
    total = sum(spend.values())
    if total == 0:
        return {"error": "No value data available."}
    cumulative = 0
    classes = []
    for name, val in ranked:
        cumulative += val
        pct = cumulative / total * 100
        cls = "A" if pct <= 80 else "B" if pct <= 95 else "C"
        classes.append({"material": name, "annual_value_usd": round(val, 2),
                        "value_pct": round(val / total * 100, 1), "class": cls})
    a = [c for c in classes if c["class"] == "A"]
    b = [c for c in classes if c["class"] == "B"]
    c = [c for c in classes if c["class"] == "C"]
    return {
        "total_value_usd": round(total, 2),
        "A": {"count": len(a), "materials": a,
              "description": "Critical — 80% of total value. Manage tightly."},
        "B": {"count": len(b), "materials": b,
              "description": "Important — 15% of value. Monitor regularly."},
        "C": {"count": len(c), "materials": c,
              "description": "Low value — 5% of value. Simple controls sufficient."},
    }


def q_reorder_status(description=None):
    """Materials at or below reorder point."""
    mats = sb.table("lobels_materials").select("*").execute().data
    closing, _ = _batch_store_data()
    if description:
        mats = [m for m in mats
                if description.lower() in (m.get("description") or "").lower()]
    out = []
    for m in mats:
        rop = _num(m.get("reorder_point"))
        if rop <= 0:
            continue
        c = closing.get(m["description"])
        if c is None:
            continue
        out.append({
            "material": m["description"],
            "current_stock_kg": round(c, 1),
            "reorder_point_kg": round(rop, 1),
            "status": "REORDER NOW" if c <= rop else "OK",
            "supplier": m.get("supplier"),
            "lead_time_days": m.get("lead_time_days"),
        })
    out.sort(key=lambda x: (x["status"] != "REORDER NOW", x["material"]))
    needing = [o for o in out if o["status"] == "REORDER NOW"]
    return {"checked": len(out), "needing_reorder": len(needing),
            "items": out if description else (needing or out[:10])}


TOOLS = [
    {"type": "function", "function": {
        "name": "q_material_total",
        "description": "Total kg issued for a specific raw material, optionally for one month. Use a SHORT distinctive part of the name e.g. 'National Foods', 'Sugar', 'Palm Oil', 'Hex Flour'.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "Distinctive part of material name."},
            "month": {"type": "string", "description": "Three-letter month e.g. JAN. Omit for full year."}
        }, "required": ["description"]}}},
    {"type": "function", "function": {
        "name": "q_top_materials",
        "description": "Top materials ranked by total kg issued.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string"}, "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "q_variances",
        "description": "Materials with biggest stock losses or gains.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string"},
            "flag": {"type": "string", "enum": ["LOSS", "GAIN"]},
            "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "q_category_breakdown",
        "description": "Total issues grouped by material category.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "q_monthly_trend",
        "description": "Month-by-month consumption trend for one material.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string"}}, "required": ["description"]}}},
    {"type": "function", "function": {
        "name": "q_reorder_status",
        "description": "Check which materials are at or below their reorder point and need reordering. Omit description to list all materials needing reorder; pass a material name to check just one.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "Optional material name to check one item."}}}}},
    {"type": "function", "function": {
        "name": "q_material_cost",
        "description": "Value in USD of a material's consumption (kg used x unit cost). Use for spend questions like 'what did we spend on flour'.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "Distinctive part of material name."},
            "month": {"type": "string", "description": "Three-letter month e.g. JAN. Omit for full year."}
        }, "required": ["description"]}}},
    {"type": "function", "function": {
        "name": "q_supplier_lookup",
        "description": "Find which supplier provides a material, OR list all materials from a given supplier.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "Material name (to find its supplier)."},
            "supplier": {"type": "string", "description": "Supplier name (to list their materials)."}}}}},
    {"type": "function", "function": {
        "name": "q_runout_forecast",
        "description": "Estimate how many days until a material runs out based on average usage, and whether to order now given its lead time.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "Distinctive part of material name."}
        }, "required": ["description"]}}},
    {"type": "function", "function": {
        "name": "q_abc_classification",
        "description": "ABC inventory classification — A (top 80% of value), B (next 15%), C (bottom 5%). Use for 'ABC analysis', 'which materials are most critical by value', 'inventory classification'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "q_purchasing_priority",
        "description": "Ranked purchasing priority list — scores all materials by urgency using stock, reorder point, daily consumption and lead time. Use for 'what should we order', 'purchasing priorities', 'what needs ordering urgently'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "q_production_risk",
        "description": "Critical production risk monitor — materials most likely to disrupt production, combining stock urgency with daily value impact. Use for 'production risk', 'what could stop production', 'critical materials'.",
        "parameters": {"type": "object", "properties": {}}}},
]

FUNC_MAP = {
    "q_material_total": q_material_total,
    "q_top_materials": q_top_materials,
    "q_variances": q_variances,
    "q_category_breakdown": q_category_breakdown,
    "q_monthly_trend": q_monthly_trend,
    "q_reorder_status": q_reorder_status,
    "q_material_cost": q_material_cost,
    "q_supplier_lookup": q_supplier_lookup,
    "q_runout_forecast": q_runout_forecast,
    "q_abc_classification": q_abc_classification,
    "q_purchasing_priority": q_purchasing_priority,
    "q_production_risk": q_production_risk,
}

TODAY = date.today().strftime("%d %B %Y")
SYSTEM_PROMPT = f"""You are the Lobels Biscuits Stores AI Assistant, built by Netrisyl Insights.
Today's date is {TODAY}.
You answer questions about raw material stores data: consumption, variances, trends,
reorder status, costs/spend (in USD), suppliers, run-out forecasts, ABC classification,
purchasing priorities, and production risk.
You have data for January to June 2026 across 88 raw materials.

Rules:
- ALWAYS use a tool to fetch real figures. NEVER invent numbers.
- When searching for a material, pass a SHORT distinctive fragment of its name
  (e.g. 'National Foods', 'Sugar', 'Palm Oil') so partial matching works.
- Quote figures exactly as returned by the tools. Quantities in kilograms (kg), money in USD ($).
- For reorder, cost, supplier, run-out, ABC, purchasing priority or production risk questions,
  use the matching tool.
- Be concise and professional, in plain language a COO or stores manager understands.
- If a question is outside stores data, politely say it's out of scope.
- Months available: JAN, FEB, MAR, APR, MAY, JUN (2026).
- If no data is found for a material, say so clearly.
"""


def chat_answer(message, history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or []):
        if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    resp = oai.chat.completions.create(
        model=OPENAI_MODEL, messages=messages,
        tools=TOOLS, tool_choice="auto", temperature=0)
    msg = resp.choices[0].message

    if msg.tool_calls:
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = FUNC_MAP.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = fn(**args) if fn else {"error": "unknown tool"}
            except Exception as e:
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result)})
        final = oai.chat.completions.create(
            model=OPENAI_MODEL, messages=messages, temperature=0)
        return final.choices[0].message.content
    return msg.content or "I couldn't find an answer to that."


SUGGESTED = [
    "What materials pose the highest production risk?",
    "Give me the purchasing priority list",
    "Run an ABC classification of our inventory",
    "Which materials need reordering?",
    "What did we spend on Flour National Foods in March?",
    "Which materials had the highest losses?",
]


# ---------------------------------------------------------------------------
# UI  (JCC design - navy + gold, Inter)
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
.gradio-container {
    font-family: 'Inter', 'Helvetica Neue', system-ui, sans-serif !important;
    max-width: 1500px !important;
    margin: 0 auto !important;
}
#lobels-hero {
    background: linear-gradient(135deg, #1B2A4E 0%, #2C4170 100%);
    border-radius: 16px;
    padding: 24px 32px;
    margin-bottom: 18px;
    color: white;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    box-shadow: 0 8px 24px rgba(27, 42, 78, 0.18);
    position: relative;
    overflow: hidden;
}
#lobels-hero::after {
    content: "";
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 4px;
    background: linear-gradient(90deg, #C9A55C 0%, #E4CC8E 50%, #C9A55C 100%);
}
#lobels-hero .hero-left { display: flex; align-items: center; gap: 24px; }
#lobels-hero img.logo {
    width: 84px; height: 84px;
    border-radius: 50%;
    background: white;
    padding: 6px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    flex-shrink: 0;
    object-fit: contain;
}
#lobels-hero .titles h1 {
    font-size: 1.8em !important;
    font-weight: 700 !important;
    margin: 0 0 4px 0 !important;
    color: white !important;
    letter-spacing: -0.5px;
}
#lobels-hero .titles .brand-name {
    font-size: 0.82em;
    color: #C9A55C;
    letter-spacing: 3px;
    font-weight: 600;
    margin-bottom: 6px;
    text-transform: uppercase;
}
#lobels-hero .titles .tagline {
    font-size: 0.92em;
    color: #cbd5e1;
    margin: 0;
}
#lobels-hero .powered {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
    flex-shrink: 0;
}
#lobels-hero .powered .label {
    font-size: 0.7em;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: #C9A55C;
    font-weight: 600;
}
#lobels-hero .powered .ni {
    font-size: 1.1em;
    font-weight: 700;
    color: white;
}
#lobels-hero .powered img { height: 48px; width: auto; }
.sidebar-card {
    background: white;
    border-radius: 12px;
    padding: 16px;
    border: 1px solid #e5e7eb;
    margin-bottom: 12px;
}
.sidebar-card h3 {
    color: #1B2A4E;
    font-size: 0.78em;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin: 0 0 12px 0;
    font-weight: 700;
    border-left: 3px solid #C9A55C;
    padding-left: 10px;
}
.suggest-btn button {
    background: white !important;
    border: 1px solid #e5e7eb !important;
    color: #1B2A4E !important;
    text-align: left !important;
    font-weight: 500 !important;
    font-size: 0.88em !important;
    padding: 10px 12px !important;
    line-height: 1.35 !important;
    white-space: normal !important;
    height: auto !important;
    min-height: 40px !important;
    transition: all 0.15s ease;
    width: 100% !important;
    justify-content: flex-start !important;
}
.suggest-btn button:hover {
    background: #1B2A4E !important;
    color: white !important;
    border-color: #1B2A4E !important;
    transform: translateX(2px);
}
#finder-result {
    background: #fefcf7;
    border: 1px solid #e8dfc7;
    border-radius: 12px;
    padding: 16px 18px;
    font-size: 0.9em;
    line-height: 1.5;
    color: #1f2937;
    margin-top: 10px;
}
#finder-result h3 { color: #1B2A4E; margin-top: 0; }
#netrisyl-footer {
    text-align: center;
    color: #9ca3af;
    font-size: 0.8em;
    padding: 20px 12px 12px;
    margin-top: 8px;
    border-top: 1px solid #e8e1cf;
}
footer { display: none !important; }
"""

theme = gr.themes.Soft(
    primary_hue=gr.themes.colors.slate,
    secondary_hue=gr.themes.colors.amber,
    neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
).set(
    button_primary_background_fill="#1B2A4E",
    button_primary_background_fill_hover="#0F1A35",
    button_primary_text_color="white",
    body_background_fill="#F7F3EC",
    block_background_fill="white",
    block_border_color="#e5e7eb",
)


# ---------------------------------------------------------------------------
# Admin functions
# ---------------------------------------------------------------------------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "netrisyl2026")


def get_sync_status():
    """Last sync date and row counts per month."""
    try:
        latest = sb.table("lobels_stores").select(
            "txn_date").eq("client_id", CLIENT_ID
            ).order("txn_date", desc=True).limit(1).execute().data
        last_date = latest[0]["txn_date"] if latest else "Unknown"
        counts = sb.table("lobels_stores").select(
            "month", count="exact").eq("client_id", CLIENT_ID
            ).execute()
        month_rows = sb.table("lobels_stores").select(
            "month").eq("client_id", CLIENT_ID).execute().data
        agg = {}
        for r in month_rows:
            m = r["month"]
            agg[m] = agg.get(m, 0) + 1
        month_lines = "\n".join(
            f"- **{m}:** {agg[m]:,} rows"
            for m in MONTH_ORDER if m in agg)
        mat_count = sb.table("lobels_materials").select(
            "stock_code", count="exact").execute()
        return (f"### ✅ Data Sync Status\n\n"
                f"**Last transaction date:** {last_date}\n\n"
                f"**Total daily rows:** {counts.count:,}\n\n"
                f"**Materials in master:** {mat_count.count}\n\n"
                f"**Rows by month:**\n{month_lines}")
    except Exception as e:
        return f"### ⚠️ Could not fetch sync status\n{str(e)}"


def get_data_quality():
    """Data quality checks."""
    try:
        rows = sb.table("lobels_stores").select(
            "description, variance_flag, variance, month"
        ).eq("client_id", CLIENT_ID).execute().data
        total = len(rows)
        losses = [r for r in rows if r.get("variance_flag") == "LOSS"]
        gains  = [r for r in rows if r.get("variance_flag") == "GAIN"]
        ok     = [r for r in rows if r.get("variance_flag") == "OK"]
        mats_no_cost = sb.table("lobels_materials").select(
            "description, unit_cost_usd").execute().data
        no_cost = [m["description"] for m in mats_no_cost
                   if not m.get("unit_cost_usd")]
        lines = [
            "### 📊 Data Quality Report\n",
            f"**Total rows:** {total:,}",
            f"**OK variance rows:** {len(ok):,}",
            f"**LOSS rows:** {len(losses):,}",
            f"**GAIN rows:** {len(gains):,}",
        ]
        if no_cost:
            lines.append(f"\n**⚠️ Materials missing unit cost ({len(no_cost)}):**")
            for n in no_cost[:10]:
                lines.append(f"- {n}")
            if len(no_cost) > 10:
                lines.append(f"- …and {len(no_cost)-10} more")
        else:
            lines.append("\n**✅ All materials have unit cost data**")
        return "\n\n".join(lines)
    except Exception as e:
        return f"### ⚠️ Could not run quality check\n{str(e)}"


def admin_login(password, action):
    if password != ADMIN_PASSWORD:
        return "### ❌ Incorrect password. Access denied."
    if action == "sync_status":
        return get_sync_status()
    elif action == "data_quality":
        return get_data_quality()
    return "### Select an action above."


# ---------------------------------------------------------------------------
# Material Lookup tab — full-page expanded finder with monthly trend
# ---------------------------------------------------------------------------
def material_full_lookup(material_name):
    if not material_name:
        return "Select a material to see its full profile.", ""
    # Snapshot (same as sidebar)
    snap = material_snapshot(material_name)
    # Monthly trend
    trend = q_monthly_trend(material_name)
    if not trend.get("found"):
        trend_md = "No monthly trend data found."
    else:
        lines = [f"### Monthly Trend — {trend['material']}\n",
                 "| Month | Issued (kg) |",
                 "|---|---|"]
        for t in trend["trend"]:
            lines.append(f"| {t['month']} | {_fmt(t['issues_kg'])} |")
        trend_md = "\n".join(lines)
    # Materials Master info
    mat = sb.table("lobels_materials").select("*").ilike(
        "description", f"%{material_name}%").execute().data
    if mat:
        m = mat[0]
        master_md = (
            f"### Reference Data\n"
            f"- **Supplier:** {m.get('supplier') or 'Not set'}\n"
            f"- **Unit Cost:** ${_fmt(_num(m.get('unit_cost_usd')))} /kg\n"
            f"- **Reorder Point:** {_fmt(_num(m.get('reorder_point')))} kg\n"
            f"- **Lead Time:** {m.get('lead_time_days') or '?'} days\n"
        )
    else:
        master_md = ""
    return snap + "\n\n" + master_md, trend_md


# ---------------------------------------------------------------------------
# Chart helpers — Plotly figures for the Reports tab
# ---------------------------------------------------------------------------
C_NAVY  = "#1B2A4E"
C_GOLD  = "#C9A55C"
C_RED   = "#C0392B"
C_ORANGE= "#E67E22"
C_YELLOW= "#F1C40F"
C_GREEN = "#27AE60"
C_CREAM = "#F7F3EC"

PLOTLY_LAYOUT = dict(
    font=dict(family="Inter, Arial", size=12),
    plot_bgcolor="white",
    paper_bgcolor="white",
)

def _layout(height=420, margin=None, **extra):
    """Build a layout dict merging base settings with per-chart overrides."""
    m = margin or dict(l=10, r=20, t=50, b=40)
    return dict(**PLOTLY_LAYOUT, height=height, margin=m, **extra)

def _empty_fig(msg="No data available"):
    fig = go.Figure()
    fig.add_annotation(text=msg, xref="paper", yref="paper",
                       x=0.5, y=0.5, showarrow=False,
                       font=dict(size=15, color="#999"))
    fig.update_layout(**_layout())
    return fig


def _safe(fn):
    """Wrap chart functions — errors return a figure with the error message."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"Chart error [{fn.__name__}]: {e}")
            return _empty_fig("Could not load report. Try again."), f"⚠️ {str(e)[:200]}"
    return wrapper


@_safe
def chart_abc():
    res = q_abc_classification()
    if "error" in res:
        return _empty_fig(res["error"]), "No ABC data."
    all_mats = (res["A"]["materials"] + res["B"]["materials"] +
                res["C"]["materials"])[:20]
    names  = [m["material"][:35] for m in reversed(all_mats)]
    values = [m["annual_value_usd"] for m in reversed(all_mats)]
    cls    = [m["class"] for m in reversed(all_mats)]
    colors = [C_RED if c=="A" else C_ORANGE if c=="B" else C_GREEN for c in cls]
    fig = go.Figure(go.Bar(
        x=values, y=names, orientation="h", marker_color=colors,
        text=[f"${_fmt(v)} · {c}" for v,c in zip(values,cls)],
        textposition="auto", textfont=dict(color="white", size=10)))
    fig.update_layout(title="ABC Classification — Value by Material (USD)",
                      xaxis_title="Total Value (USD)",
                      **_layout(height=520, margin=dict(l=220, r=20, t=50, b=40)))
    a,b,c_ = res["A"], res["B"], res["C"]
    summary = (
        f"### 🏷️ ABC Classification\n**Total: ${_fmt(res['total_value_usd'])}**\n\n"
        f"| Class | Count | Share | Action |\n|---|---|---|---|\n"
        f"| 🔴 **A** | {a['count']} | 80% | Daily monitoring |\n"
        f"| 🟠 **B** | {b['count']} | 15% | Weekly review |\n"
        f"| 🟢 **C** | {c_['count']} | 5% | Simple controls |\n\n"
        "**Top Class A:**\n" +
        "\n".join(f"- **{m['material']}** — ${_fmt(m['annual_value_usd'])} ({m['value_pct']}%)"
                  for m in a["materials"][:6]))
    return fig, summary


@_safe
def chart_purchasing_priority():
    res = q_purchasing_priority()
    items = res.get("priority_list", [])[:15]
    if not items:
        return _empty_fig("No materials assessed."), "No data."
    names  = [i["material"][:35] for i in reversed(items)]
    scores = [i["urgency_score"] for i in reversed(items)]
    days   = [i["days_left"] for i in reversed(items)]
    colors = [C_RED if s>=9 else C_ORANGE if s>=6 else C_YELLOW if s>=4 else C_GREEN
              for s in scores]
    fig = go.Figure(go.Bar(
        x=scores, y=names, orientation="h", marker_color=colors,
        text=[f"{d}d left · score {s}" for s,d in zip(scores,days)],
        textposition="auto", textfont=dict(size=10)))
    fig.update_layout(title="Purchasing Priority — Urgency Score (10=Critical)",
                      xaxis=dict(title="Urgency Score", range=[0,10]),
                      **_layout(height=520, margin=dict(l=220, r=20, t=50, b=40)))
    critical = [i for i in items if i["urgency_score"] >= 9]
    urgent   = [i for i in items if 6 <= i["urgency_score"] < 9]
    summary = (
        f"### 📋 Purchasing Priority\n"
        f"**{res['critical_count']} critical · {res['urgent_count']} urgent** "
        f"of {res['total_assessed']} assessed\n\n"
        "**🔴 Order Immediately:**\n" +
        "\n".join(f"- **{i['material']}** — {_fmt(i['current_stock_kg'])} kg, "
                  f"{i['days_left']}d left, {i['lead_time_days']}d lead · "
                  f"_{i.get('supplier','?')}_" for i in critical[:6]) +
        ("\n\n**🟠 Order Soon:**\n" +
         "\n".join(f"- **{i['material']}** — {i['days_left']}d left"
                   for i in urgent[:4]) if urgent else ""))
    return fig, summary


@_safe
def chart_production_risk():
    res = q_production_risk()
    items = res.get("top_risks", [])[:12]
    if not items:
        return _empty_fig("No production risks detected."), "All clear."
    names  = [i["material"][:35] for i in reversed(items)]
    scores = [i["risk_score"] for i in reversed(items)]
    vals   = [i["daily_value_usd"] for i in reversed(items)]
    colors = [C_RED if i["urgency_score"]>=9 else
              C_ORANGE if i["urgency_score"]>=6 else C_YELLOW
              for i in reversed(items)]
    fig = go.Figure(go.Bar(
        x=scores, y=names, orientation="h", marker_color=colors,
        text=[f"${_fmt(v)}/day" for v in vals],
        textposition="auto", textfont=dict(size=10)))
    fig.update_layout(title="Critical Production Risk — Combined Risk Score",
                      xaxis_title="Risk Score",
                      **_layout(height=480, margin=dict(l=220, r=20, t=50, b=40)))
    summary = (
        f"### ⚠️ Production Risk\n{res['message']}\n\n"
        "**Highest risk:**\n" +
        "\n".join(f"- {i['status']} **{i['material']}** — "
                  f"{_fmt(i['current_stock_kg'])} kg · {i['days_left']}d left · "
                  f"${_fmt(i['daily_value_usd'])}/day" for i in items[:8]))
    return fig, summary


@_safe
def chart_losses():
    month = _latest_month()
    res = q_variances(month=month, flag="LOSS", limit=12)
    items = [i for i in res["items"] if i["variance_kg"] < 0]
    if not items:
        return _empty_fig("No losses recorded this month."), "No losses."
    mats_cost = {m["description"]: _num(m.get("unit_cost_usd"))
                 for m in sb.table("lobels_materials").select(
                     "description, unit_cost_usd").execute().data}
    names = [i["material"][:35] for i in reversed(items)]
    usd   = [abs(i["variance_kg"]) * mats_cost.get(i["material"], 0)
             for i in reversed(items)]
    kgs   = [abs(i["variance_kg"]) for i in reversed(items)]
    fig = go.Figure(go.Bar(
        x=usd, y=names, orientation="h", marker_color=C_RED,
        text=[f"${_fmt(u)} ({_fmt(k)} kg)" for u,k in zip(usd,kgs)],
        textposition="auto", textfont=dict(color="white", size=10)))
    fig.update_layout(title=f"Top Losses by Value — {month} 2026 (USD)",
                      xaxis_title="Loss Value (USD)",
                      **_layout(height=460, margin=dict(l=220, r=20, t=50, b=40)))
    total_usd = sum(abs(i["variance_kg"]) * mats_cost.get(i["material"], 0)
                    for i in items)
    summary = (
        f"### 📉 Top Losses — {month} 2026\n"
        f"**Total loss value: ${_fmt(total_usd)}**\n\n" +
        "\n".join(f"- **{i['material']}** — {_fmt(i['variance_kg'])} kg · "
                  f"≈ ${_fmt(abs(i['variance_kg']) * mats_cost.get(i['material'], 0))}"
                  for i in items[:8]))
    return fig, summary


@_safe
def chart_monthly_spend():
    # Get unit costs from materials master
    mats = sb.table("lobels_materials").select(
        "description, unit_cost_usd").execute().data
    cost_map = {m["description"]: _num(m.get("unit_cost_usd")) for m in mats}
    # Paginate to get ALL rows (bypasses Supabase's server-side row cap)
    rows = _query_all("month, description, daily_issues")
    # Compute value = daily_issues × unit_cost per month
    agg = {}
    for r in rows:
        m = r["month"]
        v = _num(r["daily_issues"]) * cost_map.get(r["description"], 0)
        agg[m] = agg.get(m, 0) + v
    months = [m for m in MONTH_ORDER if m in agg]
    values = [agg[m] for m in months]
    month  = _latest_month()
    colors = [C_GOLD if m == month else C_NAVY for m in months]
    fig = go.Figure(go.Bar(
        x=months, y=values, marker_color=colors,
        text=[f"${_fmt(v)}" for v in values],
        textposition="outside", textfont=dict(size=10)))
    fig.update_layout(title="Monthly Materials Cost (USD) — 2026",
                      yaxis_title="Value (USD)", **_layout(height=420))
    total = sum(values)
    summary = (
        f"### 💵 Monthly Spend\n**Total: ${_fmt(total)}**\n\n"
        "| Month | Value (USD) |\n|---|---|\n" +
        "\n".join(f"| **{m}** | ${_fmt(v)} |" for m, v in zip(months, values)))
    return fig, summary


@_safe
def chart_reorder():
    res = q_reorder_status()
    items = [i for i in res["items"] if i["status"]=="REORDER NOW"][:12]
    if not items:
        return _empty_fig("All materials above reorder point ✅"), "Nothing to reorder."
    names = [i["material"][:35] for i in reversed(items)]
    curr  = [i["current_stock_kg"] for i in reversed(items)]
    rop   = [i["reorder_point_kg"] for i in reversed(items)]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Current Stock (kg)", x=curr, y=names,
                         orientation="h", marker_color=C_RED))
    fig.add_trace(go.Bar(name="Reorder Point (kg)", x=rop, y=names,
                         orientation="h", marker_color=C_NAVY, opacity=0.35))
    fig.update_layout(title="Reorder Alerts — Current Stock vs Reorder Point",
                      barmode="overlay", xaxis_title="Quantity (kg)",
                      legend=dict(orientation="h", y=1.12),
                      **_layout(height=480, margin=dict(l=220, r=20, t=50, b=40)))
    summary = (
        f"### 🔄 Reorder Alerts\n"
        f"**{res['needing_reorder']} of {res['checked']} materials** need reordering.\n\n" +
        "\n".join(f"- **{i['material']}** — {_fmt(i['current_stock_kg'])} kg "
                  f"(ROP: {_fmt(i['reorder_point_kg'])} kg) · "
                  f"{i.get('supplier','?')} · {i.get('lead_time_days','?')}d lead"
                  for i in items[:8]))
    return fig, summary


@_safe
def chart_runout():
    """Run-out forecast — BATCHED: 2 Supabase calls only."""
    mats = sb.table("lobels_materials").select("*").execute().data
    closing, avg_daily = _batch_store_data()
    risks = []
    for m in mats:
        name = m["description"]
        lead = _num(m.get("lead_time_days"))
        if lead <= 0:
            continue
        c = closing.get(name)
        a = avg_daily.get(name, 0)
        if c is None or a <= 0:
            continue
        dleft = round(c / a, 1)
        if dleft < lead:   # only materials that run out BEFORE lead time
            risks.append({"material": name[:30], "days_left": dleft,
                          "lead_time": lead, "supplier": m.get("supplier")})
    risks.sort(key=lambda x: x["days_left"])
    risks = risks[:10]    # top 10 most critical only — keeps chart clean
    if not risks:
        return _empty_fig("No run-out risks detected ✅"), "All materials have adequate stock."
    names = [r["material"] for r in risks]
    days  = [r["days_left"] for r in risks]
    leads = [r["lead_time"] for r in risks]
    # Grouped bars: red/orange = stock days, navy = lead time required
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Days of Stock Left", x=names, y=days,
        marker_color=[C_RED if d <= l * 0.5 else C_ORANGE
                      for d, l in zip(days, leads)],
        text=[f"{d}d" for d in days], textposition="outside"))
    fig.add_trace(go.Bar(
        name="Lead Time Required (days)", x=names, y=leads,
        marker_color=C_NAVY, opacity=0.5,
        text=[f"{l}d" for l in leads], textposition="outside"))
    fig.update_layout(
        title="Run-out Risk — Stock Days vs Lead Time",
        yaxis_title="Days", barmode="group",
        legend=dict(orientation="h", y=1.12),
        **_layout(height=440, margin=dict(l=20, r=20, t=70, b=100)))
    critical = [r for r in risks if r["days_left"] <= r["lead_time"]]
    summary = (
        f"### ⏳ Run-out Forecast\n"
        f"**{len(critical)} materials** will run out before new stock arrives.\n\n" +
        "\n".join(f"- **{r['material']}** — ~{r['days_left']}d left · "
                  f"{r['lead_time']}d lead · _{r.get('supplier','?')}_"
                  for r in risks[:8]))
    return fig, summary


def chart_material_trend(material_name):
    """Monthly trend chart for Material Lookup tab."""
    if not material_name:
        return _empty_fig("Select a material to see its trend.")
    trend = q_monthly_trend(material_name)
    if not trend.get("found"):
        return _empty_fig(f"No trend data for {material_name}.")
    months = [t["month"] for t in trend["trend"]]
    values = [t["issues_kg"] for t in trend["trend"]]
    fig = go.Figure(go.Bar(
        x=months, y=values,
        marker_color=C_NAVY,
        text=[_fmt(v) for v in values],
        textposition="outside", textfont=dict(size=10),
    ))
    fig.update_layout(
        title=f"Monthly Issued (kg) — {trend['material']}",
        yaxis_title="Issued (kg)",
        **_layout())
    return fig


# ---------------------------------------------------------------------------
# UI — Four tabs: Chat · Reports · Material Lookup · Admin
# ---------------------------------------------------------------------------
with gr.Blocks(title="Lobels Stores AI Assistant", theme=theme, css=CUSTOM_CSS) as demo:

    logo_img_html = (f'<img class="logo" src="{LOGO_DATA_URI}" alt="Lobels"/>'
                     if LOGO_DATA_URI else "")
    powered_html  = (f'<img src="{NETRISYL_DATA_URI}" alt="Netrisyl"/>'
                     if NETRISYL_DATA_URI else '<div class="ni">Netrisyl Insights</div>')
    gr.HTML(f"""
    <div id="lobels-hero">
        <div class="hero-left">
            {logo_img_html}
            <div class="titles">
                <div class="brand-name">Lobels Biscuits &amp; Sweets</div>
                <h1>Stores Intelligence Platform</h1>
                <p class="tagline">Production cost intelligence &middot; Inventory risk &middot; Procurement analytics</p>
            </div>
        </div>
        <div class="powered">
            <span class="label">Powered by</span>
            {powered_html}
        </div>
    </div>
    """)

    with gr.Tabs():

        # ================================================================
        # TAB 1 — CHAT (simplified — just the AI assistant)
        # ================================================================
        with gr.Tab("💬 Chat"):
            with gr.Row():
                # LEFT: Material Finder
                with gr.Column(scale=1, min_width=240):
                    with gr.Group(elem_classes=["sidebar-card"]):
                        gr.HTML("<h3>Raw Material Finder</h3>")
                        material_dd = gr.Dropdown(
                            choices=MATERIAL_NAMES, show_label=False,
                            container=False, filterable=True)
                        finder_btn = gr.Button(
                            "Get snapshot", variant="primary", size="sm")
                        finder_out = gr.Markdown("", elem_id="finder-result")

                # CENTER: Chat
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(
                        type="messages", height=500,
                        avatar_images=(
                            None, str(LOGO_PATH) if LOGO_PATH.exists() else None),
                        show_label=False, show_copy_button=True)
                    with gr.Row():
                        msg  = gr.Textbox(
                            placeholder="Ask about materials, costs, risks, trends...",
                            show_label=False, container=False,
                            scale=8, autofocus=True)
                        send = gr.Button(
                            "Ask", variant="primary", scale=1, min_width=80)
                    mic = gr.Audio(
                        sources=["microphone"], type="filepath",
                        label="Or speak your question", show_label=True)

                # RIGHT: Suggested Questions
                with gr.Column(scale=1, min_width=240):
                    with gr.Group(elem_classes=["sidebar-card"]):
                        gr.HTML("<h3>Suggested Questions</h3>")
                        suggest_btns = [
                            gr.Button(q, elem_classes=["suggest-btn"])
                            for q in SUGGESTED]

        # ================================================================
        # TAB 2 — INTELLIGENCE REPORTS (charts + summaries)
        # ================================================================
        with gr.Tab("📊 Intelligence Reports"):
            gr.HTML('<div class="side-label" style="margin:10px 0 6px;">Select a report:</div>')
            with gr.Row():
                rb_abc      = gr.Button("🏷️ ABC Classification",    variant="secondary")
                rb_priority = gr.Button("📋 Purchasing Priority",    variant="secondary")
                rb_risk     = gr.Button("⚠️ Production Risk",        variant="secondary")
                rb_reorder  = gr.Button("🔄 Reorder Alerts",         variant="secondary")
            with gr.Row():
                rb_losses   = gr.Button("📉 Top Losses",             variant="secondary")
                rb_spend    = gr.Button("💵 Monthly Spend",          variant="secondary")
                rb_runout   = gr.Button("⏳ Run-out Forecast",        variant="secondary")

            with gr.Row():
                rpt_chart   = gr.Plot(label="", show_label=False)
                rpt_summary = gr.Markdown(
                    "👆 Click a report above to load insights.",
                    elem_id="finder-result")

        # ================================================================
        # TAB 3 — MATERIAL LOOKUP (with chart)
        # ================================================================
        with gr.Tab("🔍 Material Lookup"):
            with gr.Row():
                with gr.Column(scale=1, min_width=240):
                    with gr.Group(elem_classes=["sidebar-card"]):
                        gr.HTML("<h3>Select Material</h3>")
                        lookup_dd  = gr.Dropdown(
                            choices=MATERIAL_NAMES, show_label=False,
                            container=False, filterable=True)
                        lookup_btn = gr.Button(
                            "Get full profile", variant="primary")
                with gr.Column(scale=2):
                    with gr.Group(elem_classes=["sidebar-card"]):
                        gr.HTML("<h3>Snapshot &amp; Reference Data</h3>")
                        lookup_snap = gr.Markdown(
                            "Select a material to see its full profile.",
                            elem_id="finder-result")
                with gr.Column(scale=2):
                    lookup_chart = gr.Plot(label="Monthly Trend", show_label=False)

        # ================================================================
        # TAB 4 — ADMIN
        # ================================================================
        with gr.Tab("⚙️ Admin"):
            gr.Markdown(
                "### Admin Panel\nPassword-protected. "
                "Enter the admin password to access data tools.")
            with gr.Row():
                with gr.Column(scale=1):
                    admin_pwd = gr.Textbox(
                        label="Admin Password", type="password")
                    with gr.Row():
                        btn_sync_status = gr.Button("📡 Sync Status",
                                                    variant="primary")
                        btn_quality     = gr.Button("📊 Data Quality",
                                                    variant="secondary")
                with gr.Column(scale=2):
                    admin_out = gr.Markdown(
                        "Enter password and select an action.",
                        elem_id="finder-result")

    gr.HTML(f"""
    <div id="netrisyl-footer">
        <p class="prototype-note">Lobels Stores Intelligence Platform &mdash; Prototype &middot;
        Data: Jan–Jun 2026</p>
        <div class="powered-row">
            <span class="label">Powered by</span>
            {powered_html}
        </div>
    </div>
    """)

    # ── Chat plumbing ──────────────────────────────────────────
    def respond(message, history):
        if not message or not message.strip():
            return "", history or []
        history = history or []
        reply = chat_answer(message, history)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        return "", history

    msg.submit(respond, [msg, chatbot], [msg, chatbot])
    send.click(respond, [msg, chatbot], [msg, chatbot])
    mic.stop_recording(transcribe, inputs=mic, outputs=msg).then(
        respond, [msg, chatbot], [msg, chatbot])
    finder_btn.click(material_snapshot, inputs=material_dd, outputs=finder_out)
    material_dd.change(material_snapshot, inputs=material_dd, outputs=finder_out)
    for btn, q in zip(suggest_btns, SUGGESTED):
        btn.click(lambda x=q: x, outputs=msg).then(
            respond, [msg, chatbot], [msg, chatbot])

    # ── Reports plumbing ───────────────────────────────────────
    rb_abc.click(chart_abc,               outputs=[rpt_chart, rpt_summary])
    rb_priority.click(chart_purchasing_priority, outputs=[rpt_chart, rpt_summary])
    rb_risk.click(chart_production_risk,  outputs=[rpt_chart, rpt_summary])
    rb_reorder.click(chart_reorder,       outputs=[rpt_chart, rpt_summary])
    rb_losses.click(chart_losses,         outputs=[rpt_chart, rpt_summary])
    rb_spend.click(chart_monthly_spend,   outputs=[rpt_chart, rpt_summary])
    rb_runout.click(chart_runout,         outputs=[rpt_chart, rpt_summary])

    # ── Material Lookup plumbing ───────────────────────────────
    def full_lookup(name):
        snap, trend_md = material_full_lookup(name)
        fig = chart_material_trend(name)
        return snap, fig

    lookup_btn.click(full_lookup, inputs=lookup_dd,
                     outputs=[lookup_snap, lookup_chart])
    lookup_dd.change(full_lookup, inputs=lookup_dd,
                     outputs=[lookup_snap, lookup_chart])

    # ── Admin plumbing ─────────────────────────────────────────
    btn_sync_status.click(
        lambda pwd: admin_login(pwd, "sync_status"),
        inputs=admin_pwd, outputs=admin_out)
    btn_quality.click(
        lambda pwd: admin_login(pwd, "data_quality"),
        inputs=admin_pwd, outputs=admin_out)


if __name__ == "__main__":
    demo.launch()