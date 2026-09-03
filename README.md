# Gamer Shop — ядро магазина цифровых товаров

Тестовое задание: заказ по SKU, вебхук оплаты, автовыдача кода от поставщика.

**Стек:** Python 3.13+ · Litestar · dishka · SQLAlchemy 2 (async, asyncpg) ·
PostgreSQL 17 · alembic · taskiq + Redis · structlog · pytest.

---

## Запуск

### Через docker compose (весь стек)

```bash
docker compose up --build
```

Поднимаются PostgreSQL, Redis, две заглушки-поставщика (A и B), API, воркер
фоновых задач и планировщик. Миграции и наполнение каталога прогоняются
автоматически.

* API: <http://localhost:8000/api/v1> · документация: <http://localhost:8000/docs>
* Заглушки поставщиков: <http://localhost:9101>, <http://localhost:9102>

Заглушки поднимаются со случайными сбоями (у A по умолчанию 20 % отказов и 20 %
таймаутов) — сценарии этапа 3 отыгрываются сами собой. Доли и порты
переопределяются переменными `SUPPLIER_A_FAIL_RATE`, `API_PORT` и прочими из
`.env.example`.

### Локально

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres redis      # postgres:5434, redis:6380

alembic upgrade head
python scripts/seed_catalog.py --stock 5

# две заглушки-поставщика
STUB_NAME=a STUB_KEYS_OFFSET=0 granian --interface asgi stub_supplier.app:app --port 9101 &
STUB_NAME=b STUB_KEYS_OFFSET=1 granian --interface asgi stub_supplier.app:app --port 9102 &

granian --interface asgi gamer_shop.main:app --port 8000    # API
taskiq worker gamer_shop.worker:broker                      # выдача
taskiq scheduler gamer_shop.worker:scheduler                # дожатие и сверка
```

### Тесты

```bash
pytest                       # 28 тестов, ~40 с
pytest tests/test_concurrency.py -v          # гонки
pytest tests/test_supplier_resilience.py -v  # таймауты и фолбэк
```

---

## Воспроизведение проверок

Скрипт `scripts/send_webhooks.py` эмулирует платёжную систему и одновременно
служит генератором гонок. Заглушкам можно принудительно задать режим через
`POST /_control` — `ok | fail | timeout | out_of_stock | random`.

Ниже команды для локального запуска (порт 8000). Через docker compose всё то же,
адреса заглушек — `localhost:9101` и `localhost:9102`.

### Пятьдесят параллельных «оплачено» по одному заказу

```bash
python scripts/send_webhooks.py --sku STEAM-TOPUP-500 --parallel 50
```

```
Создан заказ ord_00125, сумма 500 RUB
  HTTP-коды: {200: 50}
  Исходы:    {'applied': 1, 'already_final': 49}
  статус: delivered, код FEL3-GUXN-TCCH (поставщик a)
```

### Повтор с тем же event_id

```bash
python scripts/send_webhooks.py --sku KEY-CS2-PRIME --parallel 50 --same-event-id
#   Исходы: {'applied': 1, 'duplicate': 49}
```

### Вебхук раньше заказа

```bash
curl -X POST localhost:8000/api/v1/webhook/payment -H 'content-type: application/json' \
  -d '{"event_id":"evt_early","order_id":"ord_99999","status":"paid","amount":500,"currency":"RUB"}'
#   {"status":"accepted","outcome":"order_not_found","order_id":"ord_99999"}
```

Событие сохранено и видно в сверке; когда заказ появится, фоновая задача
`apply_orphan_events` его применит.

### Таймаут поставщика, который успел выдать код

Заглушка в режиме `timeout` сначала выдаёт и запоминает код и только потом
перестаёт отвечать.

```bash
curl -X POST localhost:9101/_control -H 'content-type: application/json' \
  -d '{"mode":"timeout","hang_seconds":30}'
python scripts/send_webhooks.py --sku KEY-GTA5 --parallel 1
#   статус: delivering — судьба кода неизвестна, деньги не тронуты

curl -s localhost:9101/_stats   # у A один код под req_00127-1
curl -s localhost:9102/_stats   # у B пусто: из таймаута фолбэка нет

curl -X POST localhost:9101/_control -H 'content-type: application/json' -d '{"mode":"ok"}'
curl -X POST localhost:8000/api/v1/admin/orders/ord_00127/retry-delivery
#   {"result":"delivered","code":"X93K-NYAQ-GEC1","supplier":"a"}
#   у A по-прежнему ровно один выданный код
```

### Отказ поставщика A и фолбэк на B

```bash
curl -X POST localhost:9101/_control -H 'content-type: application/json' -d '{"mode":"fail"}'
python scripts/send_webhooks.py --sku KEY-EFT --parallel 1
#   статус delivered, поставщик b, request_id req_00128-2
#   у A не выдано ничего, у B ровно один код
```

### Пустой остаток и восстановление

```bash
curl -X POST localhost:9101/_control -H 'content-type: application/json' -d '{"mode":"out_of_stock"}'
curl -X POST localhost:9102/_control -H 'content-type: application/json' -d '{"mode":"out_of_stock"}'
python scripts/send_webhooks.py --sku SUB-DISCORD-1M --parallel 1
#   статус out_of_stock — восстановимое состояние, падения нет

curl -X POST localhost:9101/_control -H 'content-type: application/json' -d '{"mode":"ok"}'
curl -X POST localhost:8000/api/v1/admin/orders/<id>/retry-delivery
#   статус delivered
```

### Сверка

```bash
curl -s "localhost:8000/api/v1/admin/reconciliation?older_than_seconds=0"
```


---

## Как это устроено

### Идемпотентность вебхука

`INSERT INTO payment_events ... ON CONFLICT (event_id) DO NOTHING RETURNING`.
Проверка «а был ли уже такой event_id» с последующей вставкой оставляет окно
гонки между SELECT и INSERT; у `ON CONFLICT` его нет. Ноль вставленных строк —
дубль, отвечаем 200 и выходим.

### Однократная выдача: три рубежа

Каждый самодостаточен, любой в одиночку не даёт задвоиться.

1. `SELECT ... FOR UPDATE` по заказу сериализует вебхуки с разными `event_id` —
   это превращает 50 одновременных «оплачено» в один переход.
2. Условный `UPDATE ... WHERE status IN (...)` вместо чтения-изменения-записи:
   переход выполняет ровно один вызов, остальные получают `rowcount = 0`.
   Он же мьютекс на выдачу — переход `paid → delivering` выигрывает один воркер.
3. `UNIQUE(deliveries.order_id)` и `UNIQUE(deliveries.code)` — последний рубеж
   на уровне БД. Даже при полном отказе логики вторая выдача по заказу и
   повторное использование ключа физически не запишутся.

### Таймаут ≠ отказ

Ответ поставщика классифицируется в три исхода, а не в «успех/ошибка»:

| Исход | Когда | Фолбэк на B |
|---|---|---|
| `OK` | 200 с кодом | — |
| `REFUSED` | 4xx, `out_of_stock`, отказ соединения, устойчивый 5xx | разрешён |
| `UNKNOWN` | таймаут чтения, обрыв | запрещён |

5xx — завершённый ответ: запрос отработан, кода нет, к резервному идти
безопасно. Таймаут — незавершённый запрос: поставщик мог выдать код и может
всё ещё его обрабатывать, обращение к B дало бы вторую выдачу.

Из `UNKNOWN` выход один — повтор тем же `request_id` к тому же поставщику. По
контракту он вернёт тот же код, так что повтор работает как запрос состояния.
`request_id` выводится из заказа и поставщика, а не из номера попытки: иначе
повтор попал бы в новый идентификатор и поставщик выдал бы второй код.

Пока исход неизвестен, заказ остаётся в `delivering`, резерв держится, выручка
не признаётся. Дожимает фоновая задача — тем же `request_id`.

### Выдача идёт тремя фазами

Заявка (короткая транзакция) → поход к поставщику (без транзакции) → фиксация
(короткая транзакция). Держать блокировку строки на время HTTP-запроса, который
может зависнуть на десятки секунд, нельзя — это заблокировало бы и вебхуки, и
сверку по этому заказу. Крах воркера между фазами не теряет заказ: он остаётся
в `delivering`, и фоновая задача перехватывает его атомарным CAS по давности.

### Журнал денег

Двойная запись, обе строки вставляются одним запросом:

| Событие | Дебет | Кредит |
|---|---|---|
| оплата подтверждена | `cash_in` | `customer_liability` |
| товар выдан | `customer_liability` | `revenue` |

Отсюда `SUM(debit) − SUM(credit) ≡ 0`, а ненулевой остаток по
`customer_liability` и есть «оплачен, но не выдан» — журнал сам является
отчётом сверки. Повтор гасится уникальным `idempotency_key`. Деньги — целые
числа в рублях, как в контракте вебхука.

### Витрина под нагрузкой

Остаток лежит счётчиком в отдельной узкой таблице `product_stock`, а не
считается `COUNT(*)` по ключам: O(1) вместо скана, обновляется в той же
транзакции, что и резерв, поэтому не расходится.

Запрос идёт по покрывающему частичному индексу
`products(type, sku) INCLUDE (name, price, currency) WHERE is_active` —
Index Only Scan без обращений к таблице, снятые с продажи товары в индексе не
хранятся вовсе. Пагинация keyset по `sku`, а не `OFFSET`: время не зависит от
глубины страницы.

Страница сначала отбирается подзапросом с `LIMIT`, и только потом к готовым 50
строкам подтягивается остаток по первичному ключу. Если писать плоский `JOIN`,
планировщик на больших объёмах уходит в merge join и вычитывает десятки тысяч
строк остатков ради полусотни нужных. На 205 тыс. SKU: 0.9 мс против 245 мс у
плоского join и 662 мс у варианта с `OFFSET`.

---

## Масштабирование

* **Вебхуки.** `FOR UPDATE` держится микросекунды и блокирует один заказ, так
  что API масштабируется репликами. `payment_events` и `ledger_entries` растут
  безгранично — партиционировать по месяцам, старые партиции в архив.
* **Выдача.** Воркеры независимы: право на заказ берётся условным UPDATE, а не
  координатором. Дальше — отдельные очереди на поставщика и circuit breaker,
  чтобы медленный поставщик не выедал весь пул.
* **Постановка задач.** Сейчас `kiq` вызывается после коммита, потерю страхует
  периодическая сверка. Под нагрузкой надёжнее transactional outbox: запись в ту
  же транзакцию плюс отдельный публикатор.
* **Витрина.** Выносится на реплику для чтения, остаток кэшируется с коротким
  TTL, карточки — надолго. При росте до миллионов SKU — партиционирование
  `products` по типу.

---

## Время

Около 11 часов: 3 на схему и разбор ловушки таймаута, 4 на реализацию, 2 на
тесты и замеры, 2 на разбор дефектов, найденных прогонами.
