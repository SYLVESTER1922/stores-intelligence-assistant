"""
Check the app's Supabase queries against the source workbook.

    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python3 scripts/verify.py --file <register.xlsx>

Imports app.py (which builds the UI but does not launch it) and calls the
real q_* functions. No OpenAI calls are made; a placeholder key is used if
OPENAI_API_KEY is unset.
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "unused-placeholder")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app  # noqa: E402
from ingest import load  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def attempt(name, fn):
    try:
        return fn()
    except Exception as e:  # report and continue
        check(name, False, f"raised {type(e).__name__}: {e}")
        return None


def close(a, b, tol=0.05):
    return abs(a - b) <= tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()
    stores, materials = load(args.file)

    by_mat = defaultdict(float)
    by_mat_month = defaultdict(float)
    for s in stores:
        by_mat[s["description"]] += s["daily_issues"] or 0
        by_mat_month[(s["description"], s["month"])] += s["daily_issues"] or 0
    top_mat = max(by_mat, key=by_mat.get)

    cov = attempt("coverage", app.get_data_coverage)
    if cov:
        check("coverage: no error", "error" not in cov, cov.get("error", ""))
        check("coverage: total rows", cov.get("total_rows") == len(stores),
              f"app={cov.get('total_rows')} sheet={len(stores)}")
        check("coverage: master materials", cov.get("master_materials") == len(materials),
              f"app={cov.get('master_materials')} sheet={len(materials)}")

    # All-months queries are the ones exposed to the 1000-row API cap.
    r = attempt("q_material_total (all months)", lambda: app.q_material_total(top_mat))
    if r:
        check(f"q_material_total all months: {top_mat}", r.get("found") and close(r["total_issues_kg"], by_mat[top_mat]),
              f"app={r.get('total_issues_kg')} sheet={round(by_mat[top_mat], 2)}")

    r = attempt("q_material_total (month)", lambda: app.q_material_total(top_mat, "MAR"))
    if r:
        exp = by_mat_month[(top_mat, "MAR")]
        check(f"q_material_total MAR: {top_mat}", r.get("found") and close(r["total_issues_kg"], exp),
              f"app={r.get('total_issues_kg')} sheet={round(exp, 2)}")

    r = attempt("q_top_materials", lambda: app.q_top_materials(limit=3))
    if r:
        exp = sorted(by_mat.items(), key=lambda x: -x[1])[:3]
        got = [(t["material"], t["issues_kg"]) for t in r["top"]]
        check("q_top_materials all months", [g[0] for g in got] == [e[0] for e in exp]
              and all(close(g[1], e[1]) for g, e in zip(got, exp)), f"app={got} sheet={[(m, round(v, 2)) for m, v in exp]}")

    r = attempt("q_monthly_trend", lambda: app.q_monthly_trend(top_mat))
    if r:
        got = {t["month"]: t["issues_kg"] for t in r.get("trend", [])}
        ok = all(close(got.get(m, 0), by_mat_month[(top_mat, m)]) for m in app.MONTH_ORDER[:6])
        check(f"q_monthly_trend: {top_mat}", ok, f"app={got}")

    for label, fn in [
        ("q_variances LOSS", lambda: app.q_variances(flag="LOSS")),
        ("q_category_breakdown", lambda: app.q_category_breakdown()),
        ("q_purchasing_priority", lambda: app.q_purchasing_priority()),
        ("q_production_risk", lambda: app.q_production_risk()),
        ("q_abc_classification", lambda: app.q_abc_classification()),
        ("q_reorder_status", lambda: app.q_reorder_status()),
        ("q_material_cost", lambda: app.q_material_cost(top_mat)),
        ("q_runout_forecast", lambda: app.q_runout_forecast(top_mat)),
        ("q_supplier_lookup", lambda: app.q_supplier_lookup(description=top_mat)),
    ]:
        r = attempt(label, fn)
        if r is not None:
            check(label, bool(r) and "error" not in r, str(r)[:160].replace("\n", " "))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
