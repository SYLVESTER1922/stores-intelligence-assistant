"""
LOBELS BISCUITS — STORES AI ASSISTANT
=====================================
Netrisyl Insights
Stack: Gradio + Supabase Postgres + GPT-4o-mini
Pattern: GPT tool-calling returns query params, Python runs the SQL.
Design: three-column layout, Lobels blue/red brand palette.
"""

import os
import json
import datetime
import gradio as gr
from supabase import create_client, Client
from openai import OpenAI

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_KEY   = os.environ.get("OPENAI_API_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_KEY)

CLIENT_ID = "lobels"
MONTH_ORDER = ["JAN","FEB","MAR","APR","MAY","JUN",
               "JUL","AUG","SEP","OCT","NOV","DEC"]

LOBELS_BLUE = "#1B4DB1"
LOBELS_RED  = "#E2231A"
LOBELS_DARK = "#0F2E6E"


def _num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def q_material_total(description=None, month=None):
    qry = supabase.table("lobels_stores").select(
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
    qry = supabase.table("lobels_stores").select(
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
    qry = supabase.table("lobels_stores").select(
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
    qry = supabase.table("lobels_stores").select(
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
    rows = supabase.table("lobels_stores").select(
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


TOOLS = [
    {"type": "function", "function": {
        "name": "q_material_total",
        "description": "Total kg issued for a specific raw material, optionally for one month. Use a SHORT distinctive part of the name e.g. 'National Foods', 'Sugar', 'Palm Oil', 'Hex Flour' - the search matches partially.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "Distinctive part of material name. Prefer short fragments e.g. 'National Foods' not 'Flour National Foods Industrial'."},
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
            "description": {"type": "string", "description": "Distinctive part of material name."}
        }, "required": ["description"]}}},
]

FUNC_MAP = {
    "q_material_total": q_material_total,
    "q_top_materials": q_top_materials,
    "q_variances": q_variances,
    "q_category_breakdown": q_category_breakdown,
    "q_monthly_trend": q_monthly_trend,
}

TODAY = datetime.date.today().strftime("%d %B %Y")
SYSTEM_PROMPT = f"""You are the Lobels Biscuits Stores AI Assistant, built by Netrisyl Insights.
Today's date is {TODAY}.
You answer questions about raw material stores data: consumption, variances, and trends.
You have data for January to June 2026 across 88 raw materials.

Rules:
- ALWAYS use a tool to fetch real figures. NEVER invent numbers.
- When searching for a material, pass a SHORT distinctive fragment of its name
  (e.g. 'National Foods', 'Sugar', 'Palm Oil') so partial matching works.
- Quote figures exactly as returned by the tools, in kilograms (kg).
- Be concise and professional, in plain language a stores manager understands.
- If a question is outside stores data (HR, finance, etc.), politely say it's out of scope.
- Months available: JAN, FEB, MAR, APR, MAY, JUN (2026).
- If no data is found for a material, say so clearly and suggest checking the name.
"""


def chat_fn(message, history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or []):
        if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    resp = client.chat.completions.create(
        model="gpt-4o-mini", messages=messages,
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
        final = client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=0)
        return final.choices[0].message.content
    return msg.content or "I couldn't find an answer to that."


LOGO_EXISTS = os.path.exists("logo.png")

QUICK_QUESTIONS = [
    "Which materials had the highest losses?",
    "How much Flour National Foods did we use in January?",
    "Top 10 materials by consumption",
    "Compare sugar use across months",
    "Break down consumption by category",
]

CSS = f"""
.gradio-container {{max-width: 1200px !important;}}
#lobels-header {{
    background: linear-gradient(135deg, {LOBELS_BLUE} 0%, {LOBELS_DARK} 100%);
    border-radius: 14px; padding: 18px 24px; margin-bottom: 14px;
    display: flex; align-items: center; gap: 18px;
}}
#lobels-header img {{height: 64px; width: auto;
    background: #fff; border-radius: 10px; padding: 4px;}}
#lobels-header .title {{color: #fff;}}
#lobels-header .title h1 {{margin: 0; font-size: 22px; font-weight: 700;}}
#lobels-header .title p {{margin: 2px 0 0; font-size: 13px; opacity: .85;}}
.side-label {{color: {LOBELS_BLUE}; font-size: 12px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px; margin: 8px 0 6px;}}
#lobels-footer {{text-align: center; color: #888; font-size: 12px;
    margin-top: 14px; padding-top: 10px; border-top: 1px solid #eee;}}
"""

with gr.Blocks(theme=gr.themes.Soft(primary_hue="blue"),
               css=CSS, title="Lobels Stores AI Assistant") as demo:

    logo_tag = '<img src="/gradio_api/file=logo.png" alt="Lobels"/>' if LOGO_EXISTS else ""
    gr.HTML(f"""
        <div id="lobels-header">
          {logo_tag}
          <div class="title">
            <h1>Lobels Biscuits &mdash; Stores AI Assistant</h1>
            <p>Powered by Netrisyl Insights &middot; Raw material data Jan&ndash;Jun 2026</p>
          </div>
        </div>
    """)

    with gr.Row():
        with gr.Column(scale=1, min_width=240):
            gr.HTML('<div class="side-label">Suggested questions</div>')
            q_buttons = [gr.Button(q, size="sm") for q in QUICK_QUESTIONS]
            gr.HTML('<div class="side-label" style="margin-top:18px;">What I can do</div>')
            gr.Markdown(
                "- Material consumption (daily & monthly)\n"
                "- Stock variances (losses & gains)\n"
                "- Top materials & category breakdowns\n"
                "- Month-by-month trends")

        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                height=480, type="messages",
                avatar_images=(None, "logo.png") if LOGO_EXISTS else None,
                show_copy_button=True)
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Ask about materials, variances, trends...",
                    show_label=False, scale=8)
                send = gr.Button("Ask", variant="primary", scale=1)

    gr.HTML('<div id="lobels-footer">Netrisyl Insights &middot; netrisyl.com</div>')

    def respond(message, chat_history):
        if not message or not message.strip():
            return "", chat_history or []
        chat_history = chat_history or []
        reply = chat_fn(message, chat_history)
        chat_history.append({"role": "user", "content": message})
        chat_history.append({"role": "assistant", "content": reply})
        return "", chat_history

    msg.submit(respond, [msg, chatbot], [msg, chatbot])
    send.click(respond, [msg, chatbot], [msg, chatbot])
    for btn, q in zip(q_buttons, QUICK_QUESTIONS):
        btn.click(lambda x=q: x, outputs=msg).then(
            respond, [msg, chatbot], [msg, chatbot])

if __name__ == "__main__":
    demo.launch()
