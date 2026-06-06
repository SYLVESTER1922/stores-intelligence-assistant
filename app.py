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
    rows = qry.execute().data
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
    """Most recent physical closing stock for a material."""
    rows = sb.table("lobels_stores").select(
        "physical_closing, txn_date").eq("client_id", CLIENT_ID
        ).ilike("description", f"%{description}%").execute().data
    if not rows:
        return None
    rows.sort(key=lambda r: str(r.get("txn_date", "")), reverse=True)
    return _num(rows[0]["physical_closing"])


def q_reorder_status(description=None):
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


def q_runout_forecast(description):
    """Estimate days until stock runs out, vs lead time."""
    mat = sb.table("lobels_materials").select("*").ilike(
        "description", f"%{description}%").execute().data
    if not mat:
        return {"found": False, "searched_for": description}
    m = mat[0]
    name = m["description"]
    closing = _latest_closing(name)
    if closing is None:
        return {"found": False, "searched_for": description}
    # Average daily usage across the days it was actually issued
    rows = sb.table("lobels_stores").select(
        "daily_issues, txn_date").eq("client_id", CLIENT_ID
        ).ilike("description", f"%{name}%").execute().data
    issues = [_num(r["daily_issues"]) for r in rows if _num(r["daily_issues"]) > 0]
    if not issues:
        return {"found": True, "material": name, "note": "No usage recorded yet."}
    avg_daily = sum(issues) / len(issues)
    days_left = round(closing / avg_daily, 1) if avg_daily > 0 else None
    lead = m.get("lead_time_days")
    alert = None
    if days_left is not None and lead is not None:
        alert = ("ORDER NOW — stock runs out before new delivery arrives"
                 if days_left <= _num(lead) else
                 "OK — enough stock to cover the lead time")
    return {"found": True, "material": name,
            "current_stock_kg": round(closing, 1),
            "avg_daily_use_kg": round(avg_daily, 1),
            "estimated_days_left": days_left,
            "lead_time_days": lead, "alert": alert}


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
}

TODAY = date.today().strftime("%d %B %Y")
SYSTEM_PROMPT = f"""You are the Lobels Biscuits Stores AI Assistant, built by Netrisyl Insights.
Today's date is {TODAY}.
You answer questions about raw material stores data: consumption, variances, trends,
reorder status, costs/spend (in USD), suppliers, and run-out forecasts.
You have data for January to June 2026 across 88 raw materials.

Rules:
- ALWAYS use a tool to fetch real figures. NEVER invent numbers.
- When searching for a material, pass a SHORT distinctive fragment of its name
  (e.g. 'National Foods', 'Sugar', 'Palm Oil') so partial matching works.
- Quote figures exactly as returned by the tools. Quantities in kilograms (kg), money in USD ($).
- For reorder, cost, supplier, or run-out questions, use the matching tool.
- Be concise and professional, in plain language a stores manager understands.
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
    "Which materials need reordering?",
    "What did we spend on Flour National Foods in March?",
    "Who supplies our sugar?",
    "When will we run out of Palm Oil?",
    "Which materials had the highest losses?",
    "Top 10 materials by consumption",
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


with gr.Blocks(title="Lobels Stores AI Assistant", theme=theme, css=CUSTOM_CSS) as demo:

    logo_img_html = (f'<img class="logo" src="{LOGO_DATA_URI}" alt="Lobels"/>'
                     if LOGO_DATA_URI else "")
    powered_html = (f'<img src="{NETRISYL_DATA_URI}" alt="Netrisyl"/>'
                    if NETRISYL_DATA_URI else '<div class="ni">Netrisyl Insights</div>')
    gr.HTML(f"""
    <div id="lobels-hero">
        <div class="hero-left">
            {logo_img_html}
            <div class="titles">
                <div class="brand-name">Lobels Biscuits &amp; Sweets</div>
                <h1>Stores AI Assistant</h1>
                <p class="tagline">Raw material consumption, stock variances and trends &middot; 2026</p>
            </div>
        </div>
        <div class="powered">
            <span class="label">Powered by</span>
            {powered_html}
        </div>
    </div>
    """)

    with gr.Row():
        # LEFT SIDEBAR - Material Finder + Suggested Questions
        with gr.Column(scale=1, min_width=260):
            with gr.Group(elem_classes=["sidebar-card"]):
                gr.HTML("<h3>Raw Material Finder</h3>")
                material_dd = gr.Dropdown(
                    choices=MATERIAL_NAMES,
                    label="Select a material",
                    show_label=False,
                    container=False,
                    filterable=True,
                )
                finder_btn = gr.Button("Get latest snapshot", variant="primary", size="sm")
                finder_out = gr.Markdown("", elem_id="finder-result")

        # MAIN CHAT - wider (scale 3)
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                type="messages",
                height=560,
                avatar_images=(None, str(LOGO_PATH) if LOGO_PATH.exists() else None),
                show_label=False,
                show_copy_button=True,
            )
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Ask about materials, variances, trends...",
                    show_label=False, container=False, scale=8, autofocus=True,
                )
                send = gr.Button("Ask", variant="primary", scale=1, min_width=80)
            mic = gr.Audio(sources=["microphone"], type="filepath",
                           label="Or speak your question", show_label=True)

        # RIGHT SIDEBAR - Quick Reports + Suggested Questions
        with gr.Column(scale=1, min_width=260):
            with gr.Group(elem_classes=["sidebar-card"]):
                gr.HTML("<h3>Quick Reports</h3>")
                with gr.Row():
                    btn_reorder = gr.Button("🔄 Reorder Alerts", size="sm")
                    btn_losses  = gr.Button("📉 Top Losses", size="sm")
                with gr.Row():
                    btn_spend   = gr.Button("💵 Monthly Spend", size="sm")
                    btn_runout  = gr.Button("⏳ Run-out Risks", size="sm")
                report_out = gr.Markdown("", elem_id="finder-result")

            with gr.Group(elem_classes=["sidebar-card"]):
                gr.HTML("<h3>Suggested Questions</h3>")
                suggest_btns = [
                    gr.Button(q, elem_classes=["suggest-btn"]) for q in SUGGESTED
                ]

    gr.HTML('<div id="netrisyl-footer">Lobels Stores Assistant — Prototype · '
            'Answers only from loaded stores data · Powered by Netrisyl Insights</div>')

    # ---------- plumbing ----------
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

    # voice -> transcribe -> put in textbox -> answer
    mic.stop_recording(transcribe, inputs=mic, outputs=msg).then(
        respond, [msg, chatbot], [msg, chatbot])

    # material finder
    finder_btn.click(material_snapshot, inputs=material_dd, outputs=finder_out)
    material_dd.change(material_snapshot, inputs=material_dd, outputs=finder_out)

    # quick reports
    btn_reorder.click(report_reorder, outputs=report_out)
    btn_losses.click(report_losses, outputs=report_out)
    btn_spend.click(report_spend, outputs=report_out)
    btn_runout.click(report_runout, outputs=report_out)

    # suggested questions
    for btn, q in zip(suggest_btns, SUGGESTED):
        btn.click(lambda x=q: x, outputs=msg).then(
            respond, [msg, chatbot], [msg, chatbot])


if __name__ == "__main__":
    demo.launch()