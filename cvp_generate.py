#!/usr/bin/env python3
"""Generate Ukraine CVP report aligned with Looker dashboard 32511.

Метрики з fact_provider_weekly (Store Provider = Yes), monthly + weekly rolling.
Порівняння країн, всі партнери. Період: з червня 2026.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from databricks import sql as dbsql

_ROOT = Path(__file__).parent
DATA_START = "2026-06-01"
DATA_END = "2026-09-01"
COUNTRIES = ["ua", "ee", "lv", "lt", "pl", "cz", "sk", "ro"]
COUNTRY_NAMES = {
    "ua": "Україна", "ee": "Естонія", "lv": "Латвія", "lt": "Литва",
    "pl": "Польща", "cz": "Чехія", "sk": "Словаччина", "ro": "Румунія",
}
MONTH_LBL = {"06": "Чер", "07": "Лип", "08": "Сер", "09": "Вер"}
EXTRA_PARTNERS = ["ANRI-PHARM", "BRSM", "VAPORS", "PIVASOV"]
VERTICAL_LIST = "('store_3p_ent', 'store_3p_mm_smb')"
EXTRA_SQL = ",".join(f"'{p}'" for p in EXTRA_PARTNERS)
VERTICAL_WHERE = f"(p.delivery_vertical IN {VERTICAL_LIST} OR p.group_name IN ({EXTRA_SQL}))"

METRIC_DEFS = [
    ("orders", "Замовлення", 1, "int", "Обсяг"),
    ("acceptance_rate", "Acceptance Rate, %", 1, "pct", "Доступність"),
    ("availability_rate", "Availability Rate, %", 1, "pct", "Доступність"),
    ("sku_availability_pct", "SKU Availability, %", 1, "pct", "Доступність"),
    ("avg_delivery_minutes", "Avg Delivery Time, хв", -1, "min", "Швидкість"),
    ("courier_minutes_per_order", "Courier Minutes / Order", -1, "min", "Швидкість"),
    ("late_delivery_rate", "Late Delivery Rate, %", -1, "pct", "Швидкість"),
    ("late_pickup_rate", "Late Pickup Rate, %", -1, "pct", "Швидкість"),
    ("bad_order_rate", "Bad Order Rate, %", -1, "pct", "Якість"),
    ("honey_order_rate", "Honey Order Rate, %", 1, "pct", "Якість"),
    ("avg_rating", "Avg Rating", 1, "dec", "Якість"),
    ("adjustment_rate", "Order Discrepancies (Adjustment), %", -1, "pct", "CVP Input"),
    ("replacement_rate", "Replacements, %", -1, "pct", "CVP Input"),
    ("item_discount_promo_share", "Promotional Share, %", -1, "pct", "CVP Input"),
    ("courier_acceptance_rate", "Courier Acceptance, %", 1, "pct", "Операції"),
    ("batching_rate", "Batching Rate, %", 1, "pct", "Операції"),
]


def _load_env():
    for env in (_ROOT / ".env", _ROOT.parent / "VARUS" / ".env"):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def connect():
    kwargs = {}
    if os.environ.get("DATABRICKS_TLS_NO_VERIFY", "").lower() in ("1", "true", "yes"):
        kwargs["_tls_no_verify"] = True
    return dbsql.connect(
        server_hostname=os.environ["DATABRICKS_HOST"],
        http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
        access_token=os.environ["DATABRICKS_TOKEN"],
        **kwargs,
    )


def q(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        d = {}
        for k, v in zip(cols, row):
            if hasattr(v, "__float__") and not isinstance(v, bool):
                d[k] = float(v)
            else:
                d[k] = v
        out.append(d)
    return out


def rnd(v, n=1):
    return round(v, n) if v is not None else None


OPS_SELECT = """
  SUM(f.delivered_orders_count) AS orders,
  ROUND(SUM(f.provider_acceptance_rate_value * f.provider_acceptance_rate_weight)
    / NULLIF(SUM(f.provider_acceptance_rate_weight), 0) * 100, 1) AS acceptance_rate,
  ROUND(SUM(f.provider_active_rate_value * f.provider_active_rate_weight)
    / NULLIF(SUM(f.provider_active_rate_weight), 0) * 100, 1) AS availability_rate,
  ROUND(SUM(f.provider_sku_session_availability_rate_value)
    / NULLIF(SUM(f.provider_sku_session_availability_rate_weight), 0) * 100, 1) AS sku_availability_pct,
  ROUND(SUM(f.order_total_minutes_per_order_value * f.order_total_minutes_per_order_weight)
    / NULLIF(SUM(f.order_total_minutes_per_order_weight), 0), 1) AS avg_delivery_minutes,
  ROUND(SUM(f.courier_minutes_per_order_value * f.courier_minutes_per_order_weight)
    / NULLIF(SUM(f.courier_minutes_per_order_weight), 0), 1) AS courier_minutes_per_order,
  ROUND(SUM(f.late_delivery_order_rate_value * f.late_delivery_order_rate_weight)
    / NULLIF(SUM(f.late_delivery_order_rate_weight), 0) * 100, 1) AS late_delivery_rate,
  ROUND(SUM(f.late_pickup_order_rate_value * f.late_pickup_order_rate_weight)
    / NULLIF(SUM(f.late_pickup_order_rate_weight), 0) * 100, 1) AS late_pickup_rate,
  ROUND(SUM(f.bad_order_rate_value * f.bad_order_rate_weight)
    / NULLIF(SUM(f.bad_order_rate_weight), 0) * 100, 2) AS bad_order_rate,
  ROUND(SUM(f.honey_order_rate_value * f.honey_order_rate_weight)
    / NULLIF(SUM(f.honey_order_rate_weight), 0) * 100, 1) AS honey_order_rate,
  ROUND(SUM(f.provider_rating_per_order_value * f.provider_rating_per_order_weight)
    / NULLIF(SUM(f.provider_rating_per_order_weight), 0), 2) AS avg_rating,
  ROUND(SUM(f.order_item_adjustment_rate_value * f.order_item_adjustment_rate_weight)
    / NULLIF(SUM(f.order_item_adjustment_rate_weight), 0) * 100, 2) AS adjustment_rate,
  ROUND(SUM(f.order_item_replacement_rate_value * f.order_item_replacement_rate_weight)
    / NULLIF(SUM(f.order_item_replacement_rate_weight), 0) * 100, 2) AS replacement_rate,
  ROUND(SUM(f.provider_campaign_discount_gmv_share_value * f.provider_campaign_discount_gmv_share_weight)
    / NULLIF(SUM(f.provider_campaign_discount_gmv_share_weight), 0) * 100, 2) AS item_discount_promo_share,
  ROUND(SUM(f.courier_acceptance_rate_value * f.courier_acceptance_rate_weight)
    / NULLIF(SUM(f.courier_acceptance_rate_weight), 0) * 100, 1) AS courier_acceptance_rate,
  ROUND(SUM(f.batched_order_rate_value * f.batched_order_rate_weight)
    / NULLIF(SUM(f.batched_order_rate_weight), 0) * 100, 1) AS batching_rate
"""


def ops_monthly(cur, country=None, partner=None):
    cc = f"AND p.country_code = '{country}'" if country else ""
    pg = f"AND p.group_name = '{partner}'" if partner else ""
    group = "DATE_FORMAT(DATE_TRUNC('month', f.metric_timestamp_local), 'yyyy-MM') AS m"
    if partner:
        group = "p.group_name AS partner, " + group
    return q(cur, f"""
      SELECT {group}, {OPS_SELECT}
      FROM main.ng_delivery.fact_provider_weekly f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
      WHERE f.metric_timestamp_local >= '{DATA_START}' AND f.metric_timestamp_local < '{DATA_END}'
        AND {VERTICAL_WHERE} {cc} {pg}
      GROUP BY {('p.group_name, ' if partner else '')}DATE_TRUNC('month', f.metric_timestamp_local)
      HAVING SUM(f.delivered_orders_count) > 0
      ORDER BY {('partner, ' if partner else '')}m
    """)


def ops_weekly(cur, country="ua"):
    return q(cur, f"""
      SELECT CAST(DATE_TRUNC('week', f.metric_timestamp_local) AS STRING) AS w, {OPS_SELECT}
      FROM main.ng_delivery.fact_provider_weekly f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
      WHERE p.country_code = '{country}'
        AND f.metric_timestamp_local >= '{DATA_START}' AND f.metric_timestamp_local < '{DATA_END}'
        AND {VERTICAL_WHERE}
      GROUP BY DATE_TRUNC('week', f.metric_timestamp_local)
      ORDER BY w
    """)


def country_bench(cur):
    cc = ",".join(f"'{c}'" for c in COUNTRIES)
    rows = q(cur, f"""
      SELECT p.country_code AS cc, {OPS_SELECT}
      FROM main.ng_delivery.fact_provider_weekly f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
      WHERE f.metric_timestamp_local >= '{DATA_START}' AND f.metric_timestamp_local < '{DATA_END}'
        AND p.country_code IN ({cc})
        AND {VERTICAL_WHERE}
      GROUP BY p.country_code
    """)
    return {r["cc"]: {k: rnd(r[k], 2 if k == "avg_rating" else 1) for k in r if k != "cc"} for r in rows}


def build_insights(months, ua_by_m, bench, partners_agg):
    insights = []
    ua = bench.get("ua", {})
    peers = [v for cc, v in bench.items() if cc != "ua"]

    def peer_med(key):
        vals = [p[key] for p in peers if p.get(key) is not None]
        return median(vals) if vals else None

    gaps = []
    for key, label, dirn in [
        ("adjustment_rate", "Order Discrepancies", -1),
        ("replacement_rate", "Replacements", -1),
        ("bad_order_rate", "Bad Order Rate", -1),
        ("late_pickup_rate", "Late Pickup", -1),
        ("sku_availability_pct", "SKU Availability", 1),
        ("acceptance_rate", "Acceptance Rate", 1),
    ]:
        ua_v, med_v = ua.get(key), peer_med(key)
        if ua_v is None or med_v is None:
            continue
        worse = (dirn == 1 and ua_v < med_v) or (dirn == -1 and ua_v > med_v)
        if worse:
            gaps.append(f"{label}: UA {ua_v}% vs медіана {round(med_v, 1)}%")

    if gaps:
        insights.append({"level": "crit", "kind": "Відставання від регіону",
                         "text": "Україна гірша за медіану Bolt Market (черв–сер 2026): " + "; ".join(gaps[:5]) + "."})

    wins = []
    for key, label, dirn in [("late_delivery_rate", "Late Delivery", -1), ("avg_rating", "Rating", 1)]:
        ua_v, med_v = ua.get(key), peer_med(key)
        if ua_v is None or med_v is None:
            continue
        if (dirn == 1 and ua_v > med_v) or (dirn == -1 and ua_v < med_v):
            wins.append(f"{label} ({ua_v})")
    if wins:
        insights.append({"level": "win", "kind": "Сильні сторони",
                         "text": "UA краща за медіану країн: " + ", ".join(wins) + "."})

    if len(months) >= 2:
        o1, o2 = ua_by_m[months[0]].get("orders"), ua_by_m[months[-1]].get("orders")
        if o1 and o2:
            chg = (o2 - o1) / o1 * 100
            insights.append({"level": "win" if chg > 0 else "watch", "kind": "Динаміка",
                             "text": f"Замовлення {MONTH_LBL[months[0][5:]]}→{MONTH_LBL[months[-1][5:]]}: {int(o1):,}→{int(o2):,} ({chg:+.0f}%)."})

    cvp_bad = sorted(
        [(p, d) for p, d in partners_agg.items() if d["orders"] >= 500],
        key=lambda x: (x[1].get("adjustment_rate") or 0, x[1].get("replacement_rate") or 0),
        reverse=True,
    )[:5]
    if cvp_bad:
        txt = "; ".join(
            f"{p} (adj {d.get('adjustment_rate') or '—'}%, repl {d.get('replacement_rate') or '—'}%)"
            for p, d in cvp_bad
        )
        insights.append({"level": "crit", "kind": "CVP Input — пріоритет партнерів",
                         "text": f"Найвищі Order Discrepancies / Replacements: {txt}."})

    recs = []
    if ua.get("adjustment_rate") and peer_med("adjustment_rate") and ua["adjustment_rate"] > peer_med("adjustment_rate"):
        recs.append("Order Discrepancies: синхронізація залишків, аудит топ-SKU з найвищим adjustment rate.")
    if ua.get("replacement_rate") and peer_med("replacement_rate") and ua["replacement_rate"] > peer_med("replacement_rate"):
        recs.append("Replacements: покращити точність наявності, альтернативи в каталозі.")
    if ua.get("late_pickup_rate") and peer_med("late_pickup_rate") and ua["late_pickup_rate"] > peer_med("late_pickup_rate"):
        recs.append("Late Pickup: SLA збору замовлень у магазинах, staffing у пікові години.")
    if ua.get("sku_availability_pct") and peer_med("sku_availability_pct") and ua["sku_availability_pct"] < peer_med("sku_availability_pct"):
        recs.append("SKU Availability: розширити асортимент online, прибрати ghost SKU.")
    if recs:
        insights.append({"level": "watch", "kind": "Як покращити", "text": " ".join(recs)})
    return insights


def main():
    conn = connect()
    cur = conn.cursor()

    print("UA monthly (Looker 32511)...")
    ua_monthly = ops_monthly(cur, country="ua")
    months = sorted({r["m"] for r in ua_monthly})
    ua_by_m = {r["m"]: r for r in ua_monthly}
    month_lbl = [MONTH_LBL.get(m[5:], m[5:]) for m in months]

    print("Country benchmarks...")
    bench = country_bench(cur)
    bench_rows = [{"cc": cc, "name": COUNTRY_NAMES[cc], **bench.get(cc, {})} for cc in COUNTRIES]

    print("Partners monthly...")
    partners_raw = q(cur, f"""
      SELECT p.group_name AS partner,
        DATE_FORMAT(DATE_TRUNC('month', f.metric_timestamp_local), 'yyyy-MM') AS m,
        {OPS_SELECT}
      FROM main.ng_delivery.fact_provider_weekly f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
      WHERE p.country_code = 'ua'
        AND f.metric_timestamp_local >= '{DATA_START}' AND f.metric_timestamp_local < '{DATA_END}'
        AND {VERTICAL_WHERE}
      GROUP BY p.group_name, DATE_TRUNC('month', f.metric_timestamp_local)
      HAVING SUM(f.delivered_orders_count) >= 50
      ORDER BY partner, m
    """)

    print("Weekly UA...")
    weekly = ops_weekly(cur)
    cur.close()
    conn.close()

    metrics = []
    for key, label, dirn, fmt, group in METRIC_DEFS:
        series = []
        for m in months:
            v = ua_by_m.get(m, {}).get(key)
            if v is not None:
                v = int(v) if key == "orders" else rnd(v, 2 if key == "avg_rating" else 1)
            series.append(v)
        peer_vals = [bench.get(cc, {}).get(key) for cc in COUNTRIES if cc != "ua"]
        peer_vals = [v for v in peer_vals if v is not None]
        bench_v = rnd(median(peer_vals), 2 if key == "avg_rating" else 1) if peer_vals else None
        metrics.append({"key": key, "label": label, "dir": dirn, "fmt": fmt, "group": group,
                        "bench": bench_v, "series": series})

    by_partner = {}
    for r in partners_raw:
        p = r["partner"]
        by_partner.setdefault(p, {"orders": 0, "_vals": {k: [] for k, *_ in METRIC_DEFS if k != "orders"}})
        by_partner[p]["orders"] += r.get("orders") or 0
        for key, *_ in METRIC_DEFS:
            if key == "orders":
                continue
            if r.get(key) is not None:
                by_partner[p]["_vals"][key].append(r[key])

    partners_agg = {}
    partner_table = []
    for p, d in sorted(by_partner.items(), key=lambda x: -x[1]["orders"]):
        row = {"partner": p, "orders": int(d["orders"])}
        for key, *_ in METRIC_DEFS:
            if key == "orders":
                continue
            vals = d["_vals"][key]
            row[key] = rnd(sum(vals) / len(vals), 2 if key == "avg_rating" else 1) if vals else None
        partners_agg[p] = row
        partner_table.append(row)

    top15 = [p["partner"] for p in partner_table[:15]]

    def partner_series(partner, key):
        return [rnd(next((r[key] for r in partners_raw if r["partner"] == partner and r["m"] == m), None),
                    2 if key == "avg_rating" else 1) for m in months]

    partner_metrics = []
    for key, label, dirn, fmt, group in METRIC_DEFS:
        if key == "orders":
            continue
        partner_metrics.append({
            "key": key, "label": label, "dir": dirn, "fmt": fmt, "group": group,
            "brands": {p: partner_series(p, key) for p in top15},
        })

    insights = build_insights(months, ua_by_m, bench, partners_agg)

    R = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "looker_dashboard": "https://bolt.cloud.looker.com/dashboards/32511",
        "period": f"червень–{month_lbl[-1].lower()} 2026",
        "data_start": DATA_START,
        "months": months,
        "month_lbl": month_lbl,
        "metrics": metrics,
        "bench_rows": bench_rows,
        "bench_note": "орієнтир = медіана Bolt Market (EE, LV, LT, PL, CZ, SK, RO), Looker dashboard 32511, черв–сер 2026",
        "insights": insights,
        "partners": partner_table,
        "top_partners": top15,
        "partner_metrics": partner_metrics,
        "weekly": [{"w": r["w"][:10], "orders": int(r["orders"]),
                    "adjustment_rate": rnd(r.get("adjustment_rate")),
                    "replacement_rate": rnd(r.get("replacement_rate")),
                    "bad_order_rate": rnd(r.get("bad_order_rate"), 2),
                    "late_delivery_rate": rnd(r.get("late_delivery_rate"))}
                   for r in weekly],
    }

    (_ROOT / "cvp_data.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved cvp_data.json | months={months} | partners={len(partner_table)}")

    tpl = (_ROOT / "cvp_template.html").read_text(encoding="utf-8")
    (_ROOT / "index.html").write_text(tpl.replace("/*__CVP_DATA__*/", json.dumps(R, ensure_ascii=False)), encoding="utf-8")
    print("Saved index.html")


if __name__ == "__main__":
    main()
