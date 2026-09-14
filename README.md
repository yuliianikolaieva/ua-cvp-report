# Україна — CVP Report (Looker 32511)

Звіт по **усіх метриках** [Looker dashboard 32511](https://bolt.cloud.looker.com/dashboards/32511):
- **CVP Input** (country + partner)
- **CVP Output** — GMV, Users, Funnel

**Живий звіт:** https://yuliianikolaieva.github.io/ua-cvp-report/

## Період
Останні 3 місяці (зараз **лип–вер 2026**) + порівняння з попереднім місяцем (Δ PP з Looker CSV, якщо є).

## Джерела даних
| Файл Looker | Рівень |
|-------------|--------|
| `📥 CVP Input (5).csv` | Партнери (черв–сер + PP) |
| `📥 CVP Input (4).csv` | Україна (country) |
| `📤 CVP Output - GMV/Users/Funnel` | Україна + партнери |
| Databricks | Країни (EE, LV, LT, PL, CZ, SK, RO), partner GMV |

## Оновлення

### Автоматично (щопонеділка)
GitHub Actions запускає `cvp_generate.py` **кожен понеділок о 10:00 за Києвом** (07:00 UTC, літній час EEST; у зимовому EET запуск о 09:00 Kyiv — за потреби змініть cron у `.github/workflows/weekly-update.yml` на `0 8 * * 1`).

Потрібні [repository secrets](https://github.com/yuliianikolaieva/ua-cvp-report/settings/secrets/actions):
- `DATABRICKS_HOST`
- `DATABRICKS_WAREHOUSE_ID`
- `DATABRICKS_TOKEN`

Ручний запуск: **Actions → Weekly CVP report update → Run workflow**.

### Вручну (повне оновлення з Looker)
1. Експортуйте CSV з Looker dashboard 32511 у папку `data/`
2. `pip install -r requirements.txt && python3 cvp_generate.py`
3. `git push` → GitHub Pages оновиться автоматично

> Авто-оновлення підтягує **Databricks** (країни, партнери, сегменти, commission). Метрики з **Looker CSV** змінюються лише після нового експорту в `data/`.
