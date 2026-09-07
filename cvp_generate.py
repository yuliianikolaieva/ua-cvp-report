#!/usr/bin/env python3
"""Generate Ukraine CVP country report (cvp_data.json + index.html).

Джерело: Databricks main.ng_delivery (Looker CVP / Stores metrics).
Період: з червня 2026, помісячно + тижневий rolling. Порівняння з Bolt Market країнами.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from databricks import sql as dbsql

_ROOT = Path(__file__).parent
_STORES = _ROOT.parent / "Stores-internal-weekly-report"

DATA_START = "2026-06-01"
# Лише повні місяці (вересень частковий — виключаємо з monthly)
DATA_END = "2026-09-01"
COUNTRIES = ["ua", "ee", "lv", "lt", "pl", "cz", "sk", "ro"]
COUNTRY_NAMES = {
    "ua": "Україна", "ee": "Естонія", "lv": "Латвія", "lt": "Литва",
    "pl": "Польща", "cz": "Чехія", "sk": "Словаччина", "ro": "Румунія",
}
MONTH_LBL = {"06": "Чер", "07": "Лип", "08": "Сер", "09": "Вер"}
VERTICAL = "(p.delivery_vertical LIKE 'store_3p%' OR p.group_name IN ('ANRI-PHARM','BRSM','VAPORS','PIVASOV'))"


def _load_env():
    for env in (_ROOT / ".env", _STORES / ".env"):
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


METRIC_DEFS = [
    ("orders", "Замовлення", 1, "int", "Бізнес"),
    ("gmv_eur", "GMV (€)", 1, "eur", "Бізнес"),
    ("active_users", "Активні клієнти", 1, "int", "Бізнес"),
    ("new_users", "Нові клієнти", 1, "int", "Бізнес"),
    ("aov", "Середній чек (AOV)", 1, "eur", "Бізнес"),
    ("availability", "Доступність магазинів, %", 1, "pct", "Доступність"),
    ("dur", "Час доставки, хв", -1, "min", "Швидкість і якість"),
    ("late", "Запізнення 10+, %", -1, "pct", "Швидкість і якість"),
    ("lateprep", "Пізня підготовка 10+, %", -1, "pct", "Швидкість і якість"),
    ("failed", "Зірвані замовлення, %", -1, "pct", "Швидкість і якість"),
    ("bad", "Bad Order Rate, %", -1, "pct", "Швидкість і якість"),
    ("adj", "Розбіжності (adjustment), %", -1, "pct", "CVP Input"),
    ("repl", "Заміни товарів, %", -1, "pct", "CVP Input"),
    ("promo", "Промо-частка, %", -1, "pct", "CVP Input"),
    ("cs", "CS-тікети, %", -1, "pct", "Швидкість і якість"),
    ("compl", "Завершеність, %", 1, "pct", "Швидкість і якість"),
    ("rating", "Рейтинг", 1, "dec", "Швидкість і якість"),
]


def ua_biz_monthly(cur):
    return q(cur, f"""
      SELECT DATE_FORMAT(DATE_TRUNC('month', f.order_created_date), 'yyyy-MM') AS m,
        COUNT(*) AS orders, SUM(f.order_gmv_eur) AS gmv_eur,
        COUNT(DISTINCT f.user_id) AS active_users,
        COUNT(DISTINCT CASE WHEN f.is_first_delivery_order THEN f.user_id END) AS new_users,
        SUM(f.order_gmv_eur)/COUNT(*) AS aov
      FROM main.ng_delivery.fact_order_delivery f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
      WHERE f.city_country_code = 'ua' AND f.order_state = 'delivered'
        AND f.order_created_date >= '{DATA_START}' AND f.order_created_date < '{DATA_END}'
        AND {VERTICAL}
      GROUP BY 1 ORDER BY 1
    """)


def ua_qual_monthly(cur):
    return q(cur, f"""
      SELECT DATE_FORMAT(m.metric_timestamp_local, 'yyyy-MM') AS m,
        SUM(m.order_total_minutes_per_order_value*m.order_total_minutes_per_order_weight)/NULLIF(SUM(m.order_total_minutes_per_order_weight),0) AS dur,
        SUM(m.late_delivery_order_10min_rate_value*m.late_delivery_order_10min_rate_weight)/NULLIF(SUM(m.late_delivery_order_10min_rate_weight),0)*100 AS late,
        SUM(m.provider_late_preparation_10min_rate_value*m.provider_late_preparation_10min_rate_weight)/NULLIF(SUM(m.provider_late_preparation_10min_rate_weight),0)*100 AS lateprep,
        SUM(m.failed_order_rate_value*m.failed_order_rate_weight)/NULLIF(SUM(m.failed_order_rate_weight),0)*100 AS failed,
        SUM(m.bad_order_rate_value*m.bad_order_rate_weight)/NULLIF(SUM(m.bad_order_rate_weight),0)*100 AS bad,
        SUM(m.cs_ticket_order_rate_value*m.cs_ticket_order_rate_weight)/NULLIF(SUM(m.cs_ticket_order_rate_weight),0)*100 AS cs,
        SUM(m.order_delivery_completion_rate_value*m.order_delivery_completion_rate_weight)/NULLIF(SUM(m.order_delivery_completion_rate_weight),0)*100 AS compl,
        SUM(m.provider_rating_per_order_value*m.provider_rating_per_order_weight)/NULLIF(SUM(m.provider_rating_per_order_weight),0) AS rating,
        SUM(m.provider_active_rate_value*m.provider_active_rate_weight)/NULLIF(SUM(m.provider_active_rate_weight),0)*100 AS availability
      FROM main.ng_delivery.fact_provider_monthly m
      JOIN main.ng_delivery.dim_provider_v2 p ON m.provider_id = p.provider_id
      WHERE p.country_code = 'ua' AND p.is_bolt_market_provider = true
        AND m.metric_timestamp_local >= '{DATA_START}' AND m.metric_timestamp_local < '{DATA_END}'
        AND {VERTICAL}
      GROUP BY 1 ORDER BY 1
    """)


def ua_adj_monthly(cur):
    return q(cur, f"""
      SELECT DATE_FORMAT(w.metric_timestamp_local, 'yyyy-MM') AS m,
        SUM(w.order_item_adjustment_rate_value*w.order_item_adjustment_rate_weight)/NULLIF(SUM(w.order_item_adjustment_rate_weight),0)*100 AS adj,
        SUM(w.order_item_replacement_rate_value*w.order_item_replacement_rate_weight)/NULLIF(SUM(w.order_item_replacement_rate_weight),0)*100 AS repl
      FROM main.ng_delivery.fact_provider_weekly w
      JOIN main.ng_delivery.dim_provider_v2 p ON w.provider_id = p.provider_id
      WHERE p.country_code = 'ua' AND p.is_bolt_market_provider = true
        AND w.metric_timestamp_local >= '{DATA_START}' AND w.metric_timestamp_local < '{DATA_END}'
        AND {VERTICAL}
      GROUP BY 1 ORDER BY 1
    """)


def country_bench(cur):
    cc = ",".join(f"'{c}'" for c in COUNTRIES)
    qual = q(cur, f"""
      SELECT p.country_code AS cc,
        SUM(m.bad_order_rate_value*m.bad_order_rate_weight)/NULLIF(SUM(m.bad_order_rate_weight),0)*100 AS bad,
        SUM(m.late_delivery_order_10min_rate_value*m.late_delivery_order_10min_rate_weight)/NULLIF(SUM(m.late_delivery_order_10min_rate_weight),0)*100 AS late,
        SUM(m.provider_late_preparation_10min_rate_value*m.provider_late_preparation_10min_rate_weight)/NULLIF(SUM(m.provider_late_preparation_10min_rate_weight),0)*100 AS lateprep,
        SUM(m.failed_order_rate_value*m.failed_order_rate_weight)/NULLIF(SUM(m.failed_order_rate_weight),0)*100 AS failed,
        SUM(m.order_delivery_completion_rate_value*m.order_delivery_completion_rate_weight)/NULLIF(SUM(m.order_delivery_completion_rate_weight),0)*100 AS compl,
        SUM(m.provider_rating_per_order_value*m.provider_rating_per_order_weight)/NULLIF(SUM(m.provider_rating_per_order_weight),0) AS rating,
        SUM(m.provider_active_rate_value*m.provider_active_rate_weight)/NULLIF(SUM(m.provider_active_rate_weight),0)*100 AS availability,
        SUM(m.order_total_minutes_per_order_value*m.order_total_minutes_per_order_weight)/NULLIF(SUM(m.order_total_minutes_per_order_weight),0) AS dur,
        SUM(m.cs_ticket_order_rate_value*m.cs_ticket_order_rate_weight)/NULLIF(SUM(m.cs_ticket_order_rate_weight),0)*100 AS cs
      FROM main.ng_delivery.fact_provider_monthly m
      JOIN main.ng_delivery.dim_provider_v2 p ON m.provider_id = p.provider_id
      WHERE p.is_bolt_market_provider = true
        AND m.metric_timestamp_local >= '{DATA_START}' AND m.metric_timestamp_local < '{DATA_END}'
        AND p.country_code IN ({cc})
      GROUP BY 1
    """)
    adj = q(cur, f"""
      SELECT p.country_code AS cc,
        SUM(w.order_item_adjustment_rate_value*w.order_item_adjustment_rate_weight)/NULLIF(SUM(w.order_item_adjustment_rate_weight),0)*100 AS adj,
        SUM(w.order_item_replacement_rate_value*w.order_item_replacement_rate_weight)/NULLIF(SUM(w.order_item_replacement_rate_weight),0)*100 AS repl
      FROM main.ng_delivery.fact_provider_weekly w
      JOIN main.ng_delivery.dim_provider_v2 p ON w.provider_id = p.provider_id
      WHERE p.is_bolt_market_provider = true
        AND w.metric_timestamp_local >= '{DATA_START}' AND w.metric_timestamp_local < '{DATA_END}'
        AND p.country_code IN ({cc})
      GROUP BY 1
    """)
    biz = q(cur, f"""
      SELECT f.city_country_code AS cc, COUNT(*) AS orders, SUM(f.order_gmv_eur) AS gmv_eur
      FROM main.ng_delivery.fact_order_delivery f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
      WHERE f.order_state = 'delivered' AND p.is_bolt_market_provider = true
        AND f.order_created_date >= '{DATA_START}' AND f.order_created_date < '{DATA_END}'
        AND f.city_country_code IN ({cc})
      GROUP BY 1
    """)
    d = {}
    for r in qual:
        d[r["cc"]] = {k: rnd(r[k], 2 if k == "rating" else 1) for k in r if k != "cc"}
    for r in adj:
        d.setdefault(r["cc"], {})
        d[r["cc"]]["adj"] = rnd(r["adj"])
        d[r["cc"]]["repl"] = rnd(r["repl"])
    for r in biz:
        d.setdefault(r["cc"], {})
        d[r["cc"]]["orders"] = int(r["orders"])
        d[r["cc"]]["gmv_eur"] = rnd(r["gmv_eur"], 0)
    return d


def partners_monthly(cur):
    return q(cur, f"""
      WITH biz AS (
        SELECT p.group_name AS partner,
          DATE_FORMAT(DATE_TRUNC('month', f.order_created_date), 'yyyy-MM') AS m,
          COUNT(*) AS orders, SUM(f.order_gmv_eur) AS gmv_eur
        FROM main.ng_delivery.fact_order_delivery f
        JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
        WHERE f.city_country_code = 'ua' AND f.order_state = 'delivered'
          AND f.order_created_date >= '{DATA_START}' AND f.order_created_date < '{DATA_END}'
          AND {VERTICAL}
        GROUP BY 1, 2
      ),
      qual AS (
        SELECT p.group_name AS partner,
          DATE_FORMAT(m.metric_timestamp_local, 'yyyy-MM') AS m,
          SUM(m.bad_order_rate_value*m.bad_order_rate_weight)/NULLIF(SUM(m.bad_order_rate_weight),0)*100 AS bad,
          SUM(m.late_delivery_order_10min_rate_value*m.late_delivery_order_10min_rate_weight)/NULLIF(SUM(m.late_delivery_order_10min_rate_weight),0)*100 AS late,
          SUM(m.provider_late_preparation_10min_rate_value*m.provider_late_preparation_10min_rate_weight)/NULLIF(SUM(m.provider_late_preparation_10min_rate_weight),0)*100 AS lateprep,
          SUM(m.failed_order_rate_value*m.failed_order_rate_weight)/NULLIF(SUM(m.failed_order_rate_weight),0)*100 AS failed,
          SUM(m.provider_active_rate_value*m.provider_active_rate_weight)/NULLIF(SUM(m.provider_active_rate_weight),0)*100 AS availability,
          SUM(m.order_total_minutes_per_order_value*m.order_total_minutes_per_order_weight)/NULLIF(SUM(m.order_total_minutes_per_order_weight),0) AS dur,
          SUM(m.provider_rating_per_order_value*m.provider_rating_per_order_weight)/NULLIF(SUM(m.provider_rating_per_order_weight),0) AS rating
        FROM main.ng_delivery.fact_provider_monthly m
        JOIN main.ng_delivery.dim_provider_v2 p ON m.provider_id = p.provider_id
        WHERE p.country_code = 'ua' AND p.is_bolt_market_provider = true
          AND m.metric_timestamp_local >= '{DATA_START}' AND m.metric_timestamp_local < '{DATA_END}'
          AND {VERTICAL}
        GROUP BY 1, 2
      ),
      adj AS (
        SELECT p.group_name AS partner,
          DATE_FORMAT(w.metric_timestamp_local, 'yyyy-MM') AS m,
          SUM(w.order_item_adjustment_rate_value*w.order_item_adjustment_rate_weight)/NULLIF(SUM(w.order_item_adjustment_rate_weight),0)*100 AS adj,
          SUM(w.order_item_replacement_rate_value*w.order_item_replacement_rate_weight)/NULLIF(SUM(w.order_item_replacement_rate_weight),0)*100 AS repl
        FROM main.ng_delivery.fact_provider_weekly w
        JOIN main.ng_delivery.dim_provider_v2 p ON w.provider_id = p.provider_id
        WHERE p.country_code = 'ua' AND p.is_bolt_market_provider = true
          AND w.metric_timestamp_local >= '{DATA_START}' AND w.metric_timestamp_local < '{DATA_END}'
          AND {VERTICAL}
        GROUP BY 1, 2
      )
      SELECT b.partner, b.m, b.orders, b.gmv_eur,
        q.bad, q.late, q.lateprep, q.failed, q.availability, q.dur, q.rating,
        a.adj, a.repl
      FROM biz b
      LEFT JOIN qual q ON b.partner = q.partner AND b.m = q.m
      LEFT JOIN adj a ON b.partner = a.partner AND b.m = a.m
      WHERE b.orders >= 50
      ORDER BY b.partner, b.m
    """)


def ua_weekly_overview(cur):
    return q(cur, f"""
      SELECT CAST(DATE_TRUNC('week', f.order_created_date) AS STRING) AS w,
        COUNT(*) AS orders,
        SUM(f.order_gmv_eur) AS gmv_eur
      FROM main.ng_delivery.fact_order_delivery f
      JOIN main.ng_delivery.dim_provider_v2 p ON f.provider_id = p.provider_id
      WHERE f.city_country_code = 'ua' AND f.order_state = 'delivered'
        AND f.order_created_date >= '{DATA_START}' AND f.order_created_date < '{DATA_END}'
        AND {VERTICAL}
      GROUP BY 1 ORDER BY 1
    """)


def load_stores_promo():
    """Promo share from Stores weekly report (Looker-aligned)."""
    path = _STORES / "data_ops_overview_monthly.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for r in rows:
        m = str(r.get("period", ""))[:7]
        if m < "2026-06":
            continue
        # item_discount_promo_share not in overview — use demand_incentives as proxy
        out[m] = rnd(r.get("demand_incentives_gmv_share"))
    return out


def merge_monthly(biz, qual, adj, promo):
    by_m = {}
    for r in biz:
        by_m[r["m"]] = dict(r)
    for r in qual:
        by_m.setdefault(r["m"], {})["m"] = r["m"]
        by_m[r["m"]].update({k: r[k] for k in r if k != "m"})
    for r in adj:
        by_m.setdefault(r["m"], {})["m"] = r["m"]
        by_m[r["m"]].update({k: r[k] for k in r if k != "m"})
    for m, v in promo.items():
        by_m.setdefault(m, {})["promo"] = v
    months = sorted(by_m.keys())
    return months, by_m


def build_insights(months, ua_by_m, bench, partners_raw):
    insights = []
    if len(months) < 2:
        return insights
    first, last = months[0], months[-1]
    ua = bench.get("ua", {})
    peers = [v for cc, v in bench.items() if cc != "ua" and v.get("bad") is not None]
    if not peers:
        return insights

    def peer_med(key):
        vals = [p[key] for p in peers if p.get(key) is not None]
        return median(vals) if vals else None

    # 1. Country gaps
    gaps = []
    for key, label, dirn in [
        ("failed", "зірвані замовлення", -1), ("compl", "завершеність", 1),
        ("adj", "розбіжності", -1), ("repl", "заміни", -1), ("bad", "bad order rate", -1),
        ("lateprep", "пізня підготовка", -1),
    ]:
        ua_v, med_v = ua.get(key), peer_med(key)
        if ua_v is None or med_v is None:
            continue
        worse = (dirn == 1 and ua_v < med_v) or (dirn == -1 and ua_v > med_v)
        if worse:
            gaps.append((label, ua_v, med_v, dirn))
    if gaps:
        parts = []
        for lab, ua_v, med_v, _ in sorted(gaps, key=lambda x: abs(x[1] - x[2]), reverse=True)[:5]:
            suffix = "" if lab == "рейтинг" else "%"
            parts.append(f"{lab}: UA {round(ua_v, 1)}{suffix} vs медіана країн {round(med_v, 1)}{suffix}")
        txt = "; ".join(parts)
        insights.append({"level": "crit", "kind": "Відставання від регіону",
                         "text": f"Україна гірша за медіану Bolt Market (черв–сер 2026): {txt}."})

    wins = []
    for key, label, dirn in [("late", "запізнення доставки", -1), ("dur", "час доставки", -1), ("rating", "рейтинг", 1)]:
        ua_v, med_v = ua.get(key), peer_med(key)
        if ua_v is None or med_v is None:
            continue
        better = (dirn == 1 and ua_v > med_v) or (dirn == -1 and ua_v < med_v)
        if better:
            wins.append(f"{label} ({round(ua_v, 1)})")
    if wins:
        insights.append({"level": "win", "kind": "Сильні сторони",
                         "text": f"UA краща за медіану країн: {', '.join(wins)}."})

    # 2. Trend
    o1, o2 = ua_by_m.get(first, {}).get("orders"), ua_by_m.get(last, {}).get("orders")
    if o1 and o2:
        chg = (o2 - o1) / o1 * 100
        insights.append({"level": "win" if chg > 0 else "watch", "kind": "Динаміка обсягу",
                         "text": f"Замовлення {MONTH_LBL.get(first[5:], first)}→{MONTH_LBL.get(last[5:], last)}: {int(o1):,}→{int(o2):,} ({chg:+.0f}%)."})

    bad1, bad2 = ua_by_m.get(first, {}).get("bad"), ua_by_m.get(last, {}).get("bad")
    if bad1 and bad2:
        insights.append({"level": "win" if bad2 < bad1 else "watch", "kind": "Якість",
                         "text": f"Bad Order Rate: {round(bad1, 1)}%→{round(bad2, 1)}%."})

    # 3. Worst partners (avg Jun-Aug)
    by_p = {}
    for r in partners_raw:
        p = r["partner"]
        by_p.setdefault(p, {"orders": 0, "bad": [], "adj": [], "repl": [], "late": [], "failed": []})
        by_p[p]["orders"] += r.get("orders") or 0
        for k in ("bad", "adj", "repl", "late", "failed"):
            if r.get(k) is not None:
                by_p[p][k].append(r[k])
    ranked = []
    for p, d in by_p.items():
        if d["orders"] < 500:
            continue
        bad = sum(d["bad"]) / len(d["bad"]) if d["bad"] else None
        adj = sum(d["adj"]) / len(d["adj"]) if d["adj"] else None
        repl = sum(d["repl"]) / len(d["repl"]) if d["repl"] else None
        ranked.append((p, d["orders"], bad, adj, repl))
    ranked.sort(key=lambda x: (x[2] or 0, x[3] or 0), reverse=True)
    if ranked:
        top = ranked[:5]
        txt = "; ".join(f"{p} (bad {round(b, 1) if b else '—'}%, adj {round(a, 1) if a else '—'}%)" for p, _, b, a, _ in top)
        insights.append({"level": "crit", "kind": "Партнери — пріоритет",
                         "text": f"Найгірші за якістю (середнє черв–сер, >500 замовл.): {txt}."})

    # 4. Recommendations
    recs = []
    if ua.get("failed") and peer_med("failed") and ua["failed"] > peer_med("failed"):
        recs.append("Знизити failed rate: аудит причин скасувань, SLA прийому замовлень магазинами, push-нотифікації про недоступність SKU.")
    if ua.get("adj") and peer_med("adj") and ua["adj"] > peer_med("adj"):
        recs.append("Розбіжності (adjustment): інвентаризація топ-SKU, синхронізація залишків WMS↔Bolt, тренінги зборщиків.")
    if ua.get("repl") and peer_med("repl") and ua["repl"] > peer_med("repl"):
        recs.append("Заміни: покращити точність наявності, альтернативи в каталозі, зменшити out-of-stock на ходових позиціях.")
    if ua.get("compl") and peer_med("compl") and ua["compl"] < peer_med("compl"):
        recs.append("Завершеність: фокус на acceptance rate магазинів з найнижчим compl, особливо нових партнерів.")
    if bad2 and bad1 and bad2 > bad1:
        recs.append("Bad order rate зростає — щотижневий моніторинг топ-10 магазинів за bad/late prep, ескалація account-менеджерам.")
    if recs:
        insights.append({"level": "watch", "kind": "Як покращити",
                         "text": " ".join(recs[:4])})
    return insights


def main():
    conn = connect()
    cur = conn.cursor()
    print("Fetching UA monthly...")
    biz = ua_biz_monthly(cur)
    qual = ua_qual_monthly(cur)
    adj = ua_adj_monthly(cur)
    promo = load_stores_promo()
    months, ua_by_m = merge_monthly(biz, qual, adj, promo)
    month_lbl = [MONTH_LBL.get(m[5:], m[5:]) for m in months]

    print("Country benchmarks...")
    bench = country_bench(cur)
    bench_rows = []
    for cc in COUNTRIES:
        row = {"cc": cc, "name": COUNTRY_NAMES[cc], **bench.get(cc, {})}
        bench_rows.append(row)

    print("Partners...")
    partners_raw = partners_monthly(cur)
    partner_names = sorted(set(r["partner"] for r in partners_raw))
    top_partners = sorted(
        {p: sum(r.get("orders") or 0 for r in partners_raw if r["partner"] == p) for p in partner_names}.items(),
        key=lambda x: -x[1],
    )
    top15 = [p for p, _ in top_partners[:15]]

    print("Weekly...")
    weekly = ua_weekly_overview(cur)
    cur.close()
    conn.close()

    # UA metrics table
    metrics = []
    for key, label, dirn, fmt, group in METRIC_DEFS:
        series = []
        for m in months:
            v = ua_by_m.get(m, {}).get(key)
            if key in ("orders", "gmv_eur", "active_users", "new_users") and v is not None:
                v = round(v) if key != "gmv_eur" else round(v)
            elif v is not None:
                v = rnd(v, 2 if key == "rating" else 1)
            series.append(v)
        peer_vals = [bench.get(cc, {}).get(key) for cc in COUNTRIES if cc != "ua"]
        peer_vals = [v for v in peer_vals if v is not None]
        bench_v = rnd(median(peer_vals), 2 if key == "rating" else 1) if peer_vals else None
        metrics.append({"key": key, "label": label, "dir": dirn, "fmt": fmt, "group": group,
                        "bench": bench_v, "series": series})

    # Partner pivot (top 15 + Інші)
    def partner_series(partner, key):
        return [rnd(next((r[key] for r in partners_raw if r["partner"] == partner and r["m"] == m), None),
                    2 if key == "rating" else 1)
                for m in months]

    partner_metrics = []
    for key, label, dirn, fmt, group in METRIC_DEFS:
        if key in ("active_users", "new_users", "aov", "cs", "compl", "promo"):
            continue
        brands = {p: partner_series(p, key) for p in top15}
        partner_metrics.append({"key": key, "label": label, "dir": dirn, "fmt": fmt, "group": group, "brands": brands})

    insights = build_insights(months, ua_by_m, bench, partners_raw)

    partner_table = []
    for p, total_orders in top_partners:
        rows_p = [r for r in partners_raw if r["partner"] == p]
        avg = {}
        for k in ("bad", "late", "lateprep", "failed", "adj", "repl", "availability", "dur", "rating"):
            vals = [r[k] for r in rows_p if r.get(k) is not None]
            avg[k] = rnd(sum(vals) / len(vals), 2 if k == "rating" else 1) if vals else None
        partner_table.append({
            "partner": p, "orders": int(total_orders),
            **avg,
        })

    R = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "period": f"червень–{month_lbl[-1].lower()} 2026",
        "data_start": DATA_START,
        "months": months,
        "month_lbl": month_lbl,
        "metrics": metrics,
        "bench_rows": bench_rows,
        "bench_note": "орієнтир = медіана Bolt Market країн (EE, LV, LT, PL, CZ, SK, RO), черв–сер 2026",
        "insights": insights,
        "partners": partner_table,
        "top_partners": top15,
        "partner_metrics": partner_metrics,
        "weekly": [{"w": r["w"][:10], "orders": int(r["orders"]), "gmv_eur": round(r["gmv_eur"])} for r in weekly],
        "country_names": COUNTRY_NAMES,
    }

    (_ROOT / "cvp_data.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved cvp_data.json | months={months} | partners={len(partner_table)}")

    tpl = (_ROOT / "cvp_template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*__CVP_DATA__*/", json.dumps(R, ensure_ascii=False))
    (_ROOT / "index.html").write_text(html, encoding="utf-8")
    print("Saved index.html")


if __name__ == "__main__":
    main()
