# ============================================================
# LOBELS BISCUITS — STORES AI ASSISTANT
# Netrisyl Insights
# ============================================================
# Stack: Gradio + Supabase Postgres + GPT-4o-mini
# Pattern: GPT tool-calling returns query params, Python runs
#          the SQL — figures stay out of GPT's hands.
# ============================================================

import os
import json
import gradio as gr
from supabase import create_client, Client
from openai import OpenAI

# ── Credentials (set as Secrets in HF Space settings) ───────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_KEY   = os.environ.get("OPENAI_API_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_KEY)

CLIENT_ID = "lobels"
MONTH_ORDER = ["JAN","FEB","MAR","APR","MAY","JUN",
               "JUL","AUG","SEP","OCT","NOV","DEC"]

# ============================================================
# SECTION 1 — DATA ACCESS FUNCTIONS (Python runs the queries)
# ============================================================

def q_material_total(stock_code=None, description=None, month=None):
    """Total issues for a material, optionally filtered by month."""
    qry = supabase.table("lobels_stores").select(
        "description, daily_issues, total_issues, month"
    ).eq("client_id", CLIENT_ID)
    if stock_code:
        qry = qry.eq("stock_code", str(stock_code))
    if description:
        qry = qry.ilike("description", f"%{description}%")
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.execute().data
    if not rows:
        return {"found": False}
    total = sum(float(r["daily_issues"] or 0) for r in rows)
    name = rows[0]["description"]
    return {"found": True, "material": name, "total_issues_kg": round(total, 2),
            "month": month or "all months", "rows": len(rows)}


def q_top_materials(month=None, limit=10):
    """Top materials by total issues."""
    qry = supabase.table("lobels_stores").select(
        "description, daily_issues, month"
    ).eq("client_id", CLIENT_ID)
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.execute().data
    agg = {}
    for r in rows:
        agg[r["description"]] = agg.get(r["description"], 0) + float(r["daily_issues"] or 0)
    ranked = sorted(agg.items(), key=lambda x: -x[1])[:limit]
    return {"month": month or "all months",
            "top": [{"material": m, "issues_kg": round(v, 2)} for m, v in ranked]}


def q_variances(month=None, flag="LOSS", limit=10):
    """Materials with biggest losses or gains."""
    qry = supabase.table("lobels_stores").select(
        "description, variance, month, txn_date"
    ).eq("client_id", CLIENT_ID)
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.execute().data
    agg = {}
    for r in rows:
        agg[r["description"]] = agg.get(r["description"], 0) + float(r["variance"] or 0)
    if flag == "LOSS":
        ranked = sorted(agg.items(), key=lambda x: x[1])[:limit]
    else:
        ranked = sorted(agg.items(), key=lambda x: -x[1])[:limit]
    return {"month": month or "all months", "type": flag,
            "items": [{"material": m, "variance_kg": round(v, 2)} for m, v in ranked]}


def q_category_breakdown(month=None):
    """Total issues grouped by category."""
    qry = supabase.table("lobels_stores").select(
        "category, daily_issues, month"
    ).eq("client_id", CLIENT_ID)
    if month:
        qry = qry.eq("month", month.upper())
    rows = qry.execute().data
    agg = {}
    for r in rows:
        cat = r["category"] or "Other"
        agg[cat] = agg.get(cat, 0) + float(r["daily_issues"] or 0)
    ranked = sorted(agg.items(), key=lambda x: -x[1])
    return {"month": month or "all months",
            "categories": [{"category": c, "issues_kg": round(v, 2)} for c, v in ranked]}


def q_monthly_trend(description):
    """Month-by-month issues for one material."""
    rows = supabase.table("lobels_stores").select(
        "description, daily_issues, month"
    ).eq("client_id", CLIENT_ID).ilike("description", f"%{description}%").execute().data
    if not rows:
        return {"found": False}
    agg = {}
    for r in rows:
        agg[r["month"]] = agg.get(r["month"], 0) + float(r["daily_issues"] or 0)
    ordered = [{"month": m, "issues_kg": round(agg.get(m, 0), 2)}
               for m in MONTH_ORDER if m in agg]
    return {"found": True, "material": rows[0]["description"], "trend": ordered}


def q_reorder_status():
    """Materials and their latest physical closing vs reorder point."""
    mats = supabase.table("lobels_materials").select("*").execute().data
    out = []
    for m in mats:
        if not m.get("reorder_point"):
            continue
        latest = supabase.table("lobels_stores").select(
            "physical_closing, txn_date"
        ).eq("client_id", CLIENT_ID).eq("stock_code", m["stock_code"]
        ).order("txn_date", desc=True).limit(1).execute().data
        closing = float(latest[0]["physical_closing"]) if latest else 0
        rop = float(m["reorder_point"])
        status = "REORDER NOW" if closing <= rop else "OK"
        out.append({"material": m["description"], "closing_kg": round(closing, 1),
                    "reorder_point_kg": round(rop, 1), "status": status})
    return {"reorder_status": out}


# ============================================================
# SECTION 2 — GPT TOOL DEFINITIONS
# ============================================================
TOOLS = [
    {"type": "function", "function": {
        "name": "q_material_total",
        "description": "Total kg issued for a specific raw material, optionally for one month.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "Material name e.g. 'Flour National Foods', 'Sugar', 'Palm Oil'"},
            "month": {"type": "string", "description": "Three-letter month e.g. JAN, FEB. Omit for full year."}
        }, "required": ["description"]}}},
    {"type": "function", "function": {
        "name": "q_top_materials",
        "description": "Top materials ranked by total kg issued.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string", "description": "Three-letter month. Omit for all months."},
            "limit": {"type": "integer", "description": "How many to return, default 10."}
        }}}},
    {"type": "function", "function": {
        "name": "q_variances",
        "description": "Materials with biggest stock losses or gains (variances).",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string"},
            "flag": {"type": "string", "enum": ["LOSS", "GAIN"], "description": "LOSS for shortages, GAIN for surplus."},
            "limit": {"type": "integer"}
        }}}},
    {"type": "function", "function": {
        "name": "q_category_breakdown",
        "description": "Total issues grouped by material category (Flour, Sugar, Fats, etc).",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string"}
        }}}},
    {"type": "function", "function": {
        "name": "q_monthly_trend",
        "description": "Month-by-month consumption trend for one material.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string"}
        }, "required": ["description"]}}},
    {"type": "function", "function": {
        "name": "q_reorder_status",
        "description": "Which materials are at or below their reorder point and need reordering.",
        "parameters": {"type": "object", "properties": {}}}},
]

FUNC_MAP = {
    "q_material_total": q_material_total,
    "q_top_materials": q_top_materials,
    "q_variances": q_variances,
    "q_category_breakdown": q_category_breakdown,
    "q_monthly_trend": q_monthly_trend,
    "q_reorder_status": q_reorder_status,
}

SYSTEM_PROMPT = """You are the Lobels Biscuits Stores AI Assistant, built by Netrisyl Insights.
You answer questions about raw material stores data: consumption, variances, reorder status, and trends.
You have data for January to June 2026 across 88 raw materials.

Rules:
- ALWAYS use a tool to fetch real figures. Never invent numbers.
- Quote figures exactly as returned by the tools, in kilograms (kg).
- Be concise and professional. Use plain language a stores manager understands.
- If a question is outside stores data (e.g. HR, finance), politely say it's outside your scope.
- Months in the data: JAN, FEB, MAR, APR, MAY, JUN (2026).
- If no data is found for a material, say so clearly rather than guessing.
"""


# ============================================================
# SECTION 3 — CHAT HANDLER
# ============================================================
def chat_fn(message, history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history:
        if isinstance(h, dict):
            messages.append(h)
    messages.append({"role": "user", "content": message})

    # First call — let GPT pick a tool
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=0,
    )
    msg = resp.choices[0].message

    # If GPT called tools, run them in Python and feed results back
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
        final = client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=0)
        return final.choices[0].message.content

    return msg.content or "I couldn't find an answer to that."


# ============================================================
# SECTION 4 — GRADIO UI
# ============================================================
NETRISYL_ORANGE = "#B85C2A"

QUICK_QUESTIONS = [
    "Which materials had the highest losses?",
    "How much Flour National Foods did we use in January?",
    "Top 10 materials by consumption",
    "Compare sugar use across months",
    "Which materials need reordering?",
    "Break down consumption by category",
]

with gr.Blocks(theme=gr.themes.Soft(primary_hue="orange"),
               title="Lobels Stores AI Assistant") as demo:
    gr.Markdown(
        f"""
        <div style="text-align:center;padding:8px;">
          <h2 style="color:{NETRISYL_ORANGE};margin-bottom:0;">Lobels Biscuits — Stores AI Assistant</h2>
          <p style="color:#666;margin-top:4px;">Powered by Netrisyl Insights · Jan–Jun 2026 data</p>
        </div>
        """)

    chatbot = gr.Chatbot(height=420, type="messages",
                         avatar_images=(None, "logo.png") if os.path.exists("logo.png") else None)
    msg = gr.Textbox(placeholder="Ask about materials, variances, reorder alerts, trends...",
                     label="", scale=8)

    with gr.Row():
        for q in QUICK_QUESTIONS[:3]:
            gr.Button(q, size="sm").click(
                lambda x=q: x, outputs=msg)
    with gr.Row():
        for q in QUICK_QUESTIONS[3:]:
            gr.Button(q, size="sm").click(
                lambda x=q: x, outputs=msg)

    def respond(message, chat_history):
        chat_history = chat_history or []
        reply = chat_fn(message, chat_history)
        chat_history.append({"role": "user", "content": message})
        chat_history.append({"role": "assistant", "content": reply})
        return "", chat_history

    msg.submit(respond, [msg, chatbot], [msg, chatbot])

    gr.Markdown(
        f"""<div style="text-align:center;color:#999;font-size:12px;margin-top:10px;">
        Netrisyl Insights · netrisyl.com</div>""")

if __name__ == "__main__":
    demo.launch()
