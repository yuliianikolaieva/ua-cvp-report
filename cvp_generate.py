#!/usr/bin/env python3
"""Build UA CVP report from Looker dashboard 32511 CSV exports.

Джерело: CSV з Looker (CVP Input / Output). Період: останні 3 місяці.
Порівняння з попереднім місяцем (PP) — з колонок Δ у CSV.
Країни: Databricks (той самий набір CVP Input метрик).
Partner CVP Input Jul–Aug: Databricks (у CSV лише Jun у input_partner).
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import csv

_ROOT = Path(__file__).parent
_DATA = _ROOT / "data"
MONTHS = ["2026-06", "2026-07", "2026-08"]
MONTH_LBL = {"2026-06": "Чер", "2026-07": "Лип", "2026-08": "Сер"}
COUNTRIES = ["ua", "ee", "lv", "lt", "pl", "cz", "sk", "ro"]
COUNTRY_NAMES = {
    "ua": "Україна", "ee": "Естонія", "lv": "Латвія", "lt": "Литва",
    "pl": "Польща", "cz": "Чехія", "sk": "Словаччина", "ro": "Румунія",
}

# Value-only metrics (без Δ PP) — як у Looker
INPUT_VAL = [
    ("active_merchants", "Active Merchants", "int"),
    ("merchant_availability", "Merchant Availability Rate", "pct"),
    ("sku_availability", "SKU Availability", "pct"),
    ("eater_fees_aov", "Eater Fees, AOV %", "pct"),
    ("item_promo_gmv", "Item-Level Promo, GMV %", "pct"),
    ("campaigns_discount", "Campaigns Discount, GMV %", "pct"),
    ("delivery_fee_campaign", "Orders with Delivery Fee Campaign", "pct"),
    ("not_delivered", "Not-delivered orders, %", "pct"),
    ("delivery_time", "Total Delivery Time, min", "min"),
    ("late_delivery_10", "Late Delivery (10+min) Rate", "pct"),
    ("order_defect", "Order Defect Rate", "pct"),
    ("order_replacement", "Order Replacement Rate", "pct"),
    ("cs_ticket", "CS Ticket Order Rate", "pct"),
]
INPUT_DELTA = {
    "active_merchants": "Δ %, Active Merchants PP",
    "merchant_availability": "Δ ppt, Merchant Availability Rate PP",
    "sku_availability": "Δ ppt, SKU Availability PP",
    "eater_fees_aov": "Δ %, Eater Fees, AOV % PP",
    "item_promo_gmv": "Δ %, Item-Level Promo Share PP",
    "campaigns_discount": "Δ %, Campaigns Promo Share PP",
    "delivery_fee_campaign": "Δ ppt, Orders with Delivery Fee Campaign PP",
    "not_delivered": "Δ ppt, Not-Delivered Orders PP",
    "delivery_time": "Δ %, Total Delivery Time PP",
    "late_delivery_10": "Δ ppt, Late Delivery (10+min) Rate PP",
    "order_defect": "Δ ppt, Order Defect Rate PP",
    "order_replacement": "Δ ppt, Order Replacement Rate PP",
    "cs_ticket": "Δ ppt, CS Ticket Rate PP",
}
GMV_VAL = [
    ("gmv", "GMV", "eur"), ("gmv_per_merchant", "GMV per Merchant", "eur"),
    ("orders", "Orders", "int"), ("orders_per_merchant", "Orders per Merchant", "int"),
    ("gmv_per_order", "GMV per Order", "eur"), ("bolt_plus_share", "Bolt Plus GMV Share", "pct"),
]
GMV_KEYS = {
    "gmv": "GMV", "gmv_per_merchant": "GMV per Merchant", "orders": "Orders",
    "orders_per_merchant": "Orders per Merchant", "gmv_per_order": "GMV per Order",
    "bolt_plus_share": "Bolt Plus GMV Share",
}
USERS_VAL = [("active_users", "Active Users", "int"), ("frequency", "Frequency", "dec")]
FUNNEL_VAL = [
    ("impressions", "Impressions", "int"), ("discovery_rate", "Discovery Rate", "pct"),
    ("menu_views", "Menu views", "int"), ("menu_engagement", "Menu Engagement Rate", "pct"),
    ("cart_abandonment", "Cart Abandonment Rate", "pct"), ("conversion_rate", "Conversion Rate", "pct"),
]
LOWER_BETTER = {
    "not_delivered", "delivery_time", "late_delivery_10", "order_defect",
    "order_replacement", "cs_ticket", "cart_abandonment", "campaigns_discount",
    "item_promo_gmv", "delivery_fee_campaign",
}


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


def parse_looker_csv(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "Country Name")
    id_cols = 3 if len(rows[hi]) > 2 and rows[hi][2] == "Brand Name" else 1
    months_row = rows[hi - 1][id_cols:]
    headers = rows[hi][id_cols:]
    groups = []
    i = 0
    while i < len(months_row):
        m = months_row[i]
        j = i
        while j < len(months_row) and months_row[j] == m:
            j += 1
        groups.append((m, i, j))
        i = j
    out = []
    for r in rows[hi + 1:]:
        if not r or not r[0]:
            continue
        rec = {"id": r[:id_cols], "brand": r[2] if id_cols == 3 else r[0]}
        for m, a, b in groups:
            rec[m] = {headers[a + k]: (r[id_cols + a + k].strip() if id_cols + a + k < len(r) else None)
                      for k in range(b - a)}
        out.append(rec)
    return [g[0] for g in groups], out


def num(v):
    if v is None or v == "":
        return None
    v = str(v).replace("€", "").replace(",", "").replace("%", "").strip()
    if v in ("", "—", "-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def pick_val(row, month, looker_name):
    return row.get(month, {}).get(looker_name)


def map_input_row(row, month):
    inv = {v: k for k, v, _ in INPUT_VAL}
    # Looker CSV uses typo "Eater Frees"
    m = row.get(month, {})
    return {
        "active_merchants": num(m.get("Active Merchants")),
        "merchant_availability": num(m.get("Merchant Availability Rate")),
        "sku_availability": num(m.get("SKU Availability")),
        "eater_fees_aov": num(m.get("Eater Frees, AOV %") or m.get("Eater Fees, AOV %")),
        "item_promo_gmv": num(m.get("Item-Level Promo, GMV %")),
        "campaigns_discount": num(m.get("Campaigns Discount, GMV %")),
        "delivery_fee_campaign": num(m.get("Orders with Delivery Fee Campaign")),
        "not_delivered": num(m.get("Not-delivered orders, %")),
        "delivery_time": num(m.get("Total Delivery Time, min")),
        "late_delivery_10": num(m.get("Late Delivery (10+min) Rate")),
        "order_defect": num(m.get("Order Defect Rate")),
        "order_replacement": num(m.get("Order Replacement Rate")),
        "cs_ticket": num(m.get("CS Ticket Order Rate")),
    }


def map_input_delta(row, month):
    m = row.get(month, {})
    return {k: m.get(dn) for k, dn in INPUT_DELTA.items()}


def map_gmv_row(row, month):
    m = row.get(month, {})
    return {k: num(m.get(GMV_KEYS[k])) for k in GMV_KEYS}


def map_users_row(row, month):
    m = row.get(month, {})
    return {"active_users": num(m.get("Active Users")), "frequency": num(m.get("Frequency"))}


def map_funnel_row(row, month):
    m = row.get(month, {})
    return {
        "impressions": num(m.get("Impressions")),
        "discovery_rate": num(m.get("Discovery Rate")),
        "menu_views": num(m.get("Menu views")),
        "menu_engagement": num(m.get("Menu Engagement Rate")),
        "cart_abandonment": num(m.get("Cart Abandonment Rate")),
        "conversion_rate": num(m.get("Conversion Rate")),
    }


def series_country(rows, mapper, key):
    r = rows[0]
    return [mapper(r, m).get(key) for m in MONTHS]


def build_country_section():
    _, input_rows = parse_looker_csv(_DATA / "📥 CVP Input (4).csv")
    _, gmv_rows = parse_looker_csv(_DATA / "📤 CVP Output - GMV (4).csv")
    _, users_rows = parse_looker_csv(_DATA / "📤 CVP Output - Users (7).csv")
    _, funnel_rows = parse_looker_csv(_DATA / "📤 CVP Output - Funnel (5).csv")

    inp = input_rows[0]
    gmv = gmv_rows[0]
    usr = users_rows[0]
    fun = funnel_rows[0]

    def block(metrics, mapper):
        items = []
        for key, label, fmt in metrics:
            vals = [mapper(inp if mapper == map_input_row else
                           gmv if mapper == map_gmv_row else
                           usr if mapper == map_users_row else fun, m).get(key)
                    for m in MONTHS]
            deltas = None
            if mapper == map_input_row:
                deltas = [map_input_delta(inp, m).get(key) for m in MONTHS]
            items.append({"key": key, "label": label, "fmt": fmt,
                          "values": vals, "deltas": deltas,
                          "lower_better": key in LOWER_BETTER})
        return items

    return {
        "input": block(INPUT_VAL, map_input_row),
        "gmv": block(GMV_VAL, map_gmv_row),
        "users": block(USERS_VAL, map_users_row),
        "funnel": block(FUNNEL_VAL, map_funnel_row),
        "aug_deltas": map_input_delta(inp, "2026-08"),
    }


def build_partners():
    _, users = parse_looker_csv(_DATA / "📤 CVP Output - Users (8).csv")
    _, funnel = parse_looker_csv(_DATA / "📤 CVP Output - Funnel (6).csv")
    _, input_p = parse_looker_csv(_DATA / "📥 CVP Input (3).csv")

    by_brand = {}
    for r in users:
        b = r["brand"]
        by_brand.setdefault(b, {"brand": b, "mission": r["id"][1]})
        by_brand[b]["users"] = {m: map_users_row(r, m) for m in MONTHS if m in r}

    for r in funnel:
        b = r["brand"]
        if b not in by_brand:
            by_brand[b] = {"brand": b, "mission": r["id"][1]}
        by_brand[b]["funnel"] = {m: map_funnel_row(r, m) for m in MONTHS if m in r}

    for r in input_p:
        b = r["brand"]
        if b not in by_brand:
            by_brand[b] = {"brand": b, "mission": r["id"][1]}
        by_brand[b]["input"] = {m: map_input_row(r, m) for m in r if m.startswith("2026-")}

    # Databricks: partner CVP Input Jul/Aug + GMV all months
    db_partners = fetch_partner_db()
    for b, db in db_partners.items():
        if b not in by_brand:
            by_brand[b] = {"brand": b, "mission": db.get("mission", "")}
        by_brand[b].setdefault("input", {})
        by_brand[b].setdefault("gmv", {})
        for m in MONTHS:
            if m in db.get("input", {}):
                by_brand[b]["input"][m] = {**by_brand[b]["input"].get(m, {}), **db["input"][m]}
            if m in db.get("gmv", {}):
                by_brand[b]["gmv"][m] = db["gmv"][m]

    partners = []
    for b, d in by_brand.items():
        aug_users = (d.get("users") or {}).get("2026-08", {})
        aug_funnel = (d.get("funnel") or {}).get("2026-08", {})
        aug_input = (d.get("input") or {}).get("2026-08", {})
        aug_gmv = (d.get("gmv") or {}).get("2026-08", {})
        orders = aug_gmv.get("orders") or 0
        partners.append({
            "brand": b, "mission": d.get("mission", ""),
            "orders_aug": orders,
            "users": d.get("users", {}),
            "funnel": d.get("funnel", {}),
            "input": d.get("input", {}),
            "gmv": d.get("gmv", {}),
            "summary": {
                "active_users": aug_users.get("active_users"),
                "frequency": aug_users.get("frequency"),
                "conversion": aug_funnel.get("conversion_rate"),
                "order_defect": aug_input.get("order_defect"),
                "order_replacement": aug_input.get("order_replacement"),
                "gmv": aug_gmv.get("gmv"),
                "orders": orders,
            },
        })
    partners.sort(key=lambda x: -(x["summary"].get("orders") or x["summary"].get("active_users") or 0))
    # Лише бренди з Looker CSV (Users/Funnel) або з замовленнями у серпні
    csv_brands = {r["brand"] for r in users} | {r["brand"] for r in funnel}
    partners = [p for p in partners if p["brand"] in csv_brands or (p["summary"].get("orders") or 0) >= 50]
    partners.sort(key=lambda x: -(x["summary"].get("orders") or x["summary"].get("active_users") or 0))
    return partners


def fetch_partner_db():
    try:
        from databricks import sql as dbsql
    except ImportError:
        return {}

    kwargs = {}
    if os.environ.get("DATABRICKS_TLS_NO_VERIFY", "").lower() in ("1", "true", "yes"):
        kwargs["_tls_no_verify"] = True
    conn = dbsql.connect(
        server_hostname=os.environ["DATABRICKS_HOST"],
        http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
        access_token=os.environ["DATABRICKS_TOKEN"],
        **kwargs,
    )
    cur = conn.cursor()
    vertical = "(p.delivery_vertical IN ('store_3p_ent','store_3p_mm_smb','store_unclassified') OR p.group_name IN ('ANRI-PHARM','BRSM','VAPORS','PIVASOV'))"
    cur.execute(f"""
      SELECT p.group_name AS brand, MAX(p.store_shopping_mission) AS mission,
        DATE_FORMAT(DATE_TRUNC('month', m.metric_timestamp_local), 'yyyy-MM') AS m,
        COUNT(DISTINCT CASE WHEN m.delivered_orders_count>0 THEN m.provider_id END) AS active_merchants,
        SUM(m.provider_active_rate_value*m.provider_active_rate_weight)/NULLIF(SUM(m.provider_active_rate_weight),0)*100 AS merchant_availability,
        SUM(m.provider_sku_session_availability_rate_value)/NULLIF(SUM(m.provider_sku_session_availability_rate_weight),0)*100 AS sku_availability,
        SUM(m.provider_campaign_discount_gmv_share_value*m.provider_campaign_discount_gmv_share_weight)/NULLIF(SUM(m.provider_campaign_discount_gmv_share_weight),0)*100 AS item_promo_gmv,
        SUM(m.failed_order_rate_value*m.failed_order_rate_weight)/NULLIF(SUM(m.failed_order_rate_weight),0)*100 AS not_delivered,
        SUM(m.order_total_minutes_per_order_value*m.order_total_minutes_per_order_weight)/NULLIF(SUM(m.order_total_minutes_per_order_weight),0) AS delivery_time,
        SUM(m.late_delivery_order_10min_rate_value*m.late_delivery_order_10min_rate_weight)/NULLIF(SUM(m.late_delivery_order_10min_rate_weight),0)*100 AS late_delivery_10,
        SUM(m.order_item_adjustment_rate_value*m.order_item_adjustment_rate_weight)/NULLIF(SUM(m.order_item_adjustment_rate_weight),0)*100 AS order_defect,
        SUM(m.cs_ticket_order_rate_value*m.cs_ticket_order_rate_weight)/NULLIF(SUM(m.cs_ticket_order_rate_weight),0)*100 AS cs_ticket
      FROM main.ng_delivery.fact_provider_monthly m
      JOIN main.ng_delivery.dim_provider_v2 p ON m.provider_id=p.provider_id
      WHERE p.country_code='ua' AND m.metric_timestamp_local>='{MONTHS[0]}-01' AND m.metric_timestamp_local<'2026-09-01' AND {vertical}
      GROUP BY p.group_name, DATE_TRUNC('month', m.metric_timestamp_local)
    """)
    mon = cur.fetchall()
    cur.execute(f"""
      SELECT p.group_name AS brand, DATE_FORMAT(DATE_TRUNC('month', w.metric_timestamp_local), 'yyyy-MM') AS m,
        SUM(w.order_item_replacement_rate_value*w.order_item_replacement_rate_weight)/NULLIF(SUM(w.order_item_replacement_rate_weight),0)*100 AS order_replacement
      FROM main.ng_delivery.fact_provider_weekly w
      JOIN main.ng_delivery.dim_provider_v2 p ON w.provider_id=p.provider_id
      WHERE p.country_code='ua' AND w.metric_timestamp_local>='{MONTHS[0]}-01' AND w.metric_timestamp_local<'2026-09-01' AND {vertical}
      GROUP BY p.group_name, DATE_TRUNC('month', w.metric_timestamp_local)
    """)
    repl = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    cur.execute(f"""
      SELECT p.group_name AS brand, DATE_FORMAT(DATE_TRUNC('month', f.order_created_date), 'yyyy-MM') AS m,
        COUNT(*) AS orders, ROUND(SUM(f.order_gmv_eur),0) AS gmv
      FROM main.ng_delivery.fact_order_delivery f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id=p.provider_id
      WHERE f.city_country_code='ua' AND f.order_state='delivered'
        AND f.order_created_date>='{MONTHS[0]}-01' AND f.order_created_date<'2026-09-01' AND {vertical}
      GROUP BY p.group_name, DATE_TRUNC('month', f.order_created_date)
    """)
    gmv_rows = cur.fetchall()
    cur.close()
    conn.close()

    out = {}
    for r in mon:
        brand, mission, m = r[0], r[1], r[2]
        out.setdefault(brand, {"mission": mission, "input": {}, "gmv": {}})
        out[brand]["input"][m] = {
            "active_merchants": r[3], "merchant_availability": round(r[4], 1) if r[4] else None,
            "sku_availability": round(r[5], 1) if r[5] else None,
            "item_promo_gmv": round(r[6], 2) if r[6] else None,
            "not_delivered": round(r[7], 1) if r[7] else None,
            "delivery_time": round(r[8], 1) if r[8] else None,
            "late_delivery_10": round(r[9], 1) if r[9] else None,
            "order_defect": round(r[10], 1) if r[10] else None,
            "cs_ticket": round(r[11], 1) if r[11] else None,
            "order_replacement": round(repl.get((brand, m)), 1) if repl.get((brand, m)) else None,
        }
    for brand, m, orders, gmv in gmv_rows:
        out.setdefault(brand, {"mission": "", "input": {}, "gmv": {}})
        out[brand]["gmv"][m] = {"orders": int(orders), "gmv": float(gmv)}
    return out


def fetch_country_bench():
    try:
        from databricks import sql as dbsql
    except ImportError:
        return []
    kwargs = {}
    if os.environ.get("DATABRICKS_TLS_NO_VERIFY", "").lower() in ("1", "true", "yes"):
        kwargs["_tls_no_verify"] = True
    conn = dbsql.connect(
        server_hostname=os.environ["DATABRICKS_HOST"],
        http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
        access_token=os.environ["DATABRICKS_TOKEN"],
        **kwargs,
    )
    cur = conn.cursor()
    cc = ",".join(f"'{c}'" for c in COUNTRIES)
    vertical = "p.delivery_vertical LIKE 'store_%'"
    cur.execute(f"""
      SELECT p.country_code AS cc,
        SUM(m.failed_order_rate_value*m.failed_order_rate_weight)/NULLIF(SUM(m.failed_order_rate_weight),0)*100 AS not_delivered,
        SUM(m.late_delivery_order_10min_rate_value*m.late_delivery_order_10min_rate_weight)/NULLIF(SUM(m.late_delivery_order_10min_rate_weight),0)*100 AS late_delivery_10,
        SUM(m.order_item_adjustment_rate_value*m.order_item_adjustment_rate_weight)/NULLIF(SUM(m.order_item_adjustment_rate_weight),0)*100 AS order_defect,
        SUM(m.order_total_minutes_per_order_value*m.order_total_minutes_per_order_weight)/NULLIF(SUM(m.order_total_minutes_per_order_weight),0) AS delivery_time,
        SUM(m.provider_active_rate_value*m.provider_active_rate_weight)/NULLIF(SUM(m.provider_active_rate_weight),0)*100 AS merchant_availability,
        SUM(m.provider_sku_session_availability_rate_value)/NULLIF(SUM(m.provider_sku_session_availability_rate_weight),0)*100 AS sku_availability
      FROM main.ng_delivery.fact_provider_monthly m
      JOIN main.ng_delivery.dim_provider_v2 p ON m.provider_id=p.provider_id
      WHERE p.country_code IN ({cc}) AND m.metric_timestamp_local>='{MONTHS[0]}-01' AND m.metric_timestamp_local<'2026-09-01'
        AND {vertical} AND p.is_bolt_market_provider=true
      GROUP BY p.country_code
    """)
    qual = {r[0]: dict(zip(["not_delivered", "late_delivery_10", "order_defect", "delivery_time",
                            "merchant_availability", "sku_availability"], [round(x, 1) if x else None for x in r[1:]]))
            for r in cur.fetchall()}
    cur.execute(f"""
      SELECT p.country_code, SUM(w.order_item_replacement_rate_value*w.order_item_replacement_rate_weight)
        /NULLIF(SUM(w.order_item_replacement_rate_weight),0)*100
      FROM main.ng_delivery.fact_provider_weekly w
      JOIN main.ng_delivery.dim_provider_v2 p ON w.provider_id=p.provider_id
      WHERE p.country_code IN ({cc}) AND w.metric_timestamp_local>='{MONTHS[0]}-01' AND w.metric_timestamp_local<'2026-09-01'
        AND {vertical} AND p.is_bolt_market_provider=true
      GROUP BY p.country_code
    """)
    for cc, v in cur.fetchall():
        qual.setdefault(cc, {})["order_replacement"] = round(v, 1) if v else None
    cur.close()
    conn.close()

    # UA from CSV Aug
    _, input_rows = parse_looker_csv(_DATA / "📥 CVP Input (4).csv")
    ua_csv = map_input_row(input_rows[0], "2026-08")
    qual["ua"] = {k: ua_csv.get(k) for k in ["not_delivered", "late_delivery_10", "order_defect",
                                                "delivery_time", "merchant_availability", "sku_availability",
                                                "order_replacement"]}

    rows = []
    for cc in COUNTRIES:
        rows.append({"cc": cc, "name": COUNTRY_NAMES[cc], **qual.get(cc, {})})
    return rows


def build_insights(country, bench_rows):
    insights = []
    aug = {m["key"]: m["values"][-1] for sec in [country["input"], country["gmv"], country["users"], country["funnel"]]
           for m in sec}
    aug_d = country["aug_deltas"]

    # Aug vs Jul trends from CSV values
    def trend(sec, key):
        m = next(x for x in sec if x["key"] == key)
        a, b = m["values"][-1], m["values"][-2]
        if a is None or b is None:
            return None
        return a, b, (a - b) / abs(b) * 100 if b else 0

    t = trend(country["gmv"], "orders")
    if t:
        insights.append({"level": "win" if t[2] > 0 else "watch", "kind": "Обсяг",
                         "text": f"Замовлення Лип→Сер: {int(t[1]):,}→{int(t[0]):,} ({t[2]:+.0f}%)."})

    for key, label in [("order_defect", "Order Defect Rate"), ("order_replacement", "Replacements"),
                       ("not_delivered", "Not-delivered"), ("late_delivery_10", "Late Delivery 10+")]:
        t = trend(country["input"], key)
        if t:
            worse = (key in LOWER_BETTER and t[0] > t[1]) or (key not in LOWER_BETTER and t[0] < t[1])
            insights.append({"level": "crit" if worse else "win", "kind": label,
                             "text": f"{label}: {t[1]:.1f}→{t[0]:.1f} (сер vs лип)."})

    ua = next(r for r in bench_rows if r["cc"] == "ua")
    peers = [r for r in bench_rows if r["cc"] != "ua" and r.get("order_defect")]
    if peers:
        gaps = []
        for key, label in [("order_replacement", "Replacements"), ("order_defect", "Order Defect"),
                           ("not_delivered", "Not-delivered")]:
            uv, med = ua.get(key), median([p[key] for p in peers if p.get(key)])
            if uv and med and ((key in LOWER_BETTER and uv > med) or (key not in LOWER_BETTER and uv < med)):
                gaps.append(f"{label}: UA {uv}% vs медіана {med:.1f}%")
        if gaps:
            insights.append({"level": "crit", "kind": "vs інші країни", "text": "; ".join(gaps) + "."})

    # PP from Aug CSV
    bad_pp = aug_d.get("order_defect")
    if bad_pp:
        insights.append({"level": "watch" if str(bad_pp).startswith("-") else "crit", "kind": "Серпень PP",
                         "text": f"Order Defect Rate PP vs лип: {bad_pp} п.п.; Replacements PP: {aug_d.get('order_replacement', '—')}."})
    return insights


def main():
    if not (_DATA / "📥 CVP Input (4).csv").exists():
        print("Missing CSV files in data/", file=sys.stderr)
        sys.exit(1)

    country = build_country_section()
    partners = build_partners()
    bench = fetch_country_bench()
    insights = build_insights(country, bench)

    R = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "looker_url": "https://bolt.cloud.looker.com/dashboards/32511",
        "months": MONTHS,
        "month_lbl": [MONTH_LBL[m] for m in MONTHS],
        "country": country,
        "partners": partners,
        "bench_rows": bench,
        "insights": insights,
        "input_metrics": [{"key": k, "label": l, "fmt": f} for k, l, f in INPUT_VAL],
        "gmv_metrics": [{"key": k, "label": l, "fmt": f} for k, l, f in GMV_VAL],
        "users_metrics": [{"key": k, "label": l, "fmt": f} for k, l, f in USERS_VAL],
        "funnel_metrics": [{"key": k, "label": l, "fmt": f} for k, l, f in FUNNEL_VAL],
    }

    (_ROOT / "cvp_data.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    tpl = (_ROOT / "cvp_template.html").read_text(encoding="utf-8")
    (_ROOT / "index.html").write_text(tpl.replace("/*__CVP_DATA__*/", json.dumps(R, ensure_ascii=False)), encoding="utf-8")
    print(f"OK | partners={len(partners)} | insights={len(insights)}")


if __name__ == "__main__":
    main()
