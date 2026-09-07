# Україна — CVP Report (Looker 32511)

Звіт по **усіх метриках** [Looker dashboard 32511](https://bolt.cloud.looker.com/dashboards/32511):
- **CVP Input** (country + partner)
- **CVP Output** — GMV, Users, Funnel

**Живий звіт:** https://yuliianikolaieva.github.io/ua-cvp-report/

## Період
Останні 3 місяці (черв–сер 2026) + порівняння з попереднім місяцем (колонки Δ PP з Looker CSV).

## Джерела даних
| Файл Looker | Рівень |
|-------------|--------|
| `📥 CVP Input (5).csv` | Партнери (черв–сер + PP) |
| `📥 CVP Input (4).csv` | Україна (country) |
| `📤 CVP Output - GMV/Users/Funnel` | Україна + партнери |
| Databricks | Країни (EE, LV, LT, PL, CZ, SK, RO), partner GMV |

## Оновлення
1. Експортуйте CSV з Looker dashboard 32511 у папку `data/`
2. `python3 cvp_generate.py`
3. `git push` → GitHub Pages оновиться автоматично
