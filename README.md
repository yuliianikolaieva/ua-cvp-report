# Україна — CVP Country Report

Звіт по **усіх CVP метриках** Bolt Food Stores в Україні: помісячно з червня 2026, тижневий rolling, порівняння з іншими країнами Bolt Market та деталізація по **всіх партнерах**.

**Живий звіт:** https://yuliianikolaieva.github.io/ua-cvp-report/

## Зміст
1. **Огляд UA** — ключові висновки, проблеми, рекомендації + помісячні метрики
2. **Порівняння країн** — UA vs EE, LV, LT, PL, CZ, SK, RO
3. **Тижневий rolling** — динаміка замовлень
4. **Партнери** — рейтинг якості + деталізація по місяцях (топ-15 + повний список)

## Метрики (Looker / Databricks)
- **Бізнес:** GMV, замовлення, активні/нові клієнти, AOV
- **Доступність:** provider active rate
- **Швидкість і якість:** час доставки, запізнення 10+, пізня підготовка, failed, bad order, CS, завершеність, рейтинг
- **CVP Input:** adjustment rate, replacement rate, promo share

## Оновлення
```bash
python3 cvp_generate.py
```

Джерело: `main.ng_delivery` (Databricks). Потрібен `.env` з Databricks credentials.
