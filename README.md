# Планирование квартала команд

Проект разбирает единый Excel-файл организаторов, нормализует его в CSV,
проверяет качество данных и строит квартальный план на шесть двухнедельных
спринтов. Product frontend работает через FastAPI и показывает реальный результат
planner-а. Streamlit остаётся техническим интерфейсом для диагностики.

## Быстрый запуск

Нужен Python 3.10+ и PowerShell. Выполните из корня проекта:

```powershell
python -m pip install -r requirements.txt
python scripts/prepare_dataset.py
python scripts/data_audit.py
python -m uvicorn src.web.api:app --reload
```

Product frontend доступен по адресу `http://127.0.0.1:8000`.

## Структура проекта

| Путь | Назначение |
| --- | --- |
| `data/raw/` | Один исходный Excel-файл `.xlsx` или `.xlsm`. |
| `scripts/prepare_dataset.py` | Preprocessing: Excel → нормализованные CSV. |
| `scripts/data_audit.py` | Отдельная проверка качества обработанных данных. |
| `data/processed/` | Генерируемые CSV и manifest с хешами. Не коммитируется. |
| `src/data/` | Поиск файла, parser Excel, нормализация и валидация. |
| `src/planning/` | Feasibility checker, scoring, resource allocation и quarter planner. |
| `src/replanning/` | Baseline/fact/forecast KPI, diff версий плана и объяснимые алерты. |
| `src/web/` | FastAPI, загрузка processed snapshot и стабильные API DTO. |
| `frontend/` | Product SPA из HTML, CSS и JavaScript без build pipeline. |
| `dev_ui/` | Временный русскоязычный интерфейс Streamlit. |
| `tests/` | Unit, integration и synthetic acceptance tests. |
| `reports/` | Локальные отчёты data audit. |

## Подготовка исходных данных

Поместите в `data/raw/` ровно один `.xlsx` или `.xlsm` файл. Скрипт не выбирает
файл произвольно: при нуле или нескольких Excel-файлах завершится с ошибкой.

```powershell
python scripts/prepare_dataset.py
```

Скрипт находит блоки таблиц в исходном листе, нормализует задачи, трудозатраты
по ролям, зависимости, профили инженеров и историю команд. Он создаёт
placeholder-задачи для ID, которые есть в смете или зависимостях, но отсутствуют
в `Tasks`, и сохраняет три справочника из конца Excel.

Перед записью выполняется валидация ссылок и структуры. Затем в
`data/processed/` создаются:

```text
tasks.csv
task_role_effort.csv
task_dependencies.csv
engineers.csv
engineer_skills.csv
team_history.csv
planning_results.csv
plan_mismatch_reasons.csv
task_closure_results.csv
_dataset_manifest.json
```

Manifest содержит хеш исходного Excel и хеши CSV. Повторный запуск с тем же
файлом ничего не перезаписывает. Для принудительной пересборки:

```powershell
python scripts/prepare_dataset.py --force
```

## Проверка качества данных

Preprocessing проверяет структуру выходных таблиц. Для диагностики бизнес- и
data-quality проблем используйте отдельный audit:

```powershell
python scripts/data_audit.py
```

По умолчанию он читает `data/processed/`, выводит сводку в терминал и создаёт
Markdown-отчёт `reports/data_audit_report.md`. Audit ничего не исправляет и не
запускает planner.

```powershell
# Проверить другой каталог с CSV
python scripts/data_audit.py --input-dir C:\path\to\processed

# Сохранить отчёт в другом месте
python scripts/data_audit.py --output reports\audit.md

# Считать подозрительными плановые интервалы длиннее 90 дней
python scripts/data_audit.py --suspicious-date-days 90
```

Audit отделяет проблемы входных данных от обычной невозможности спланировать
работу. Например, несовпадение `estimated_hh` и суммы role-hours — warning
качества данных; дефицит часов инженера — причина решения planner-а.

## Запуск product frontend и API

После preprocessing запустите одну команду:

```powershell
python -m uvicorn src.web.api:app --reload
```

Откройте `http://127.0.0.1:8000`. FastAPI раздаёт статические файлы из
`frontend/`, а browser получает данные через `/api/*`. Source of truth для
статусов, SP, allocations, причин, рисков и рекомендаций — backend/planner.

Основные endpoints:

- `/api/dashboard`, `/api/plan`, `/api/summary`;
- `/api/tasks`, `/api/tasks/{task_id}`, `/api/initiatives`;
- `/api/risks`;
- `/api/teams`, `/api/teams/{team_id}`;
- `/api/engineers`, `/api/engineers/{engineer_id}`, `/api/star-map`.

«Главная», «Планирование», «Задачи», «Риски» и «Звёздная карта» используют
реальный processed dataset. «Пересчёт плана» остаётся интерфейсной заготовкой:
выбранный файл не обрабатывается, потому что production replanning backend ещё
не реализован. Bus Factor также явно помечен как не рассчитанный.

## Запуск технического Streamlit UI

```powershell
python -m streamlit run dev_ui/app.py
```

Streamlit читает CSV из `data/processed/`. На странице «Загрузка датасета»
можно проверить загруженный Excel изолированно: файл обрабатывается во временном
каталоге и не заменяет `data/raw/` или `data/processed/`.


Основные страницы:

- «Обзор», «Задачи», «Трудозатраты по ролям», «Зависимости» — просмотр
  нормализованных данных;
- «Инженеры и компетенции», «История производительности команд» — ресурсы;
- «Качество данных» — предупреждения, включая placeholder и конфликт `Done` с
  зависимостью;
- «Проверка выполнимости задачи» — проверка одной задачи при заданном состоянии
  спринта;
- «Квартальный план» — расчёт плана, allocations, причины решений и
  рекомендации;
- «KPI и пересчёт» — воспроизводимый сценарий факта Sprint 1, FACT/FORECAST,
  diff, многоспринтовые часы и RED/ORANGE/YELLOW-алерты;
- «Справочники» — три справочника, извлечённые из конца Excel.

## KPI, риски и replanning-модель

`src/replanning/` хранит раздельные неизменяемые снимки:

- `original_plan` — baseline на неделе 0 и знаменатель KPI;
- `previous_plan` — версия до пересчёта;
- `current_plan` — новый forecast, включая спринты за пределами кварта;
- `actual_results` — факты только закрытых спринтов.

PI Predictability и Sprint Say/Do всегда используют baseline из `original_plan`.
`build_replanning_snapshot(...)` возвращает FACT, FORECAST, task-level diff и алерты
с числовым evidence. Текущая Streamlit-страница использует детерминированный
fixture; адаптер к `src/planning/` ещё нужно подключить.

## Как работает планировщик

Планировщик получает нормализованные записи через Python API
`build_quarter_plan(FeasibilityContext)`. Отдельной CLI-команды для сохранения
плана пока нет: для интерактивного запуска используйте страницу «Квартальный
план» в интерфейсе.

Ключевые правила:

- горизонт — шесть спринтов;
- главная цель — закрыть максимум инициатив полностью; для пяти или меньшего
  числа надёжных инициатив planner перебирает варианты их порядка, затем
  применяет rung и детерминированные tie-breakers;
- высокий `rung` повышает приоритет, но не блокирует выполнимые задачи с меньшим
  rung;
- команда получает SP capacity как `mean(velocity_achieved) × 0.8`;
- capacity engineer-team bucket равна `80 × capacity_rate` часов на спринт;
- инженер из другой команды может помочь задаче, расходуя свой source bucket;
- задача может быть переназначена только в существующую команду;
- multi-sprint `capacity_sp_load` распределяется пропорционально фактически
  выделенным часам; `completed_sp` для прогнозной задачи остаётся нулём;
- `Done` считается фактом, не получает новую нагрузку и может разблокировать
  зависимые задачи; противоречие зависимости фиксируется в data audit;
- неуспешное размещение атомарно откатывает часы, SP и промежуточные allocations.

Подробности реализации: [planning core](src/planning/README.md) и
[scoring v1](src/planning/SCORING.md).

## Запуск тестов

Для тестов установите `pytest` и HTTP-клиент Starlette:

```powershell
python -m pip install pytest httpx2
```

Запуск всех тестов:

```powershell
python -m pytest tests -q -p no:cacheprovider
```

`-p no:cacheprovider` отключает только локальный pytest cache; на сами тесты не
влияет.

```powershell
# Synthetic acceptance scenarios planner-а без Excel
python -m pytest tests/acceptance -q -p no:cacheprovider

# Planner, resource allocation и cross-team scenarios
python -m pytest tests/test_quarter_planner.py tests/test_cross_team.py -q -p no:cacheprovider

# Preprocessing, placeholders, idempotency и справочники
python -m pytest tests/test_preprocessing.py -q -p no:cacheprovider

# Streamlit UI и upload/audit flow
python -m pytest tests/test_dev_ui.py tests/test_upload_flow.py tests/test_replanning_demo.py -q -p no:cacheprovider
```

Acceptance tests создают маленькие данные программно и проверяют итоговый план,
ресурсы, причины и audit issues. Они не зависят от текущего Excel.

## Последовательность ручной проверки

1. Поместить Excel в `data/raw/`.
2. Запустить `python scripts/prepare_dataset.py`.
3. Запустить `python scripts/data_audit.py` и прочитать отчёт из `reports/`.
4. Запустить `python -m uvicorn src.web.api:app --reload` и открыть product UI.
5. При необходимости открыть `python -m streamlit run dev_ui/app.py`.
6. На странице «Качество данных» проверить warnings.
7. На странице «Квартальный план» построить план и проверить задачи `MOVED`,
   причины, cross-team allocations и рекомендации.
8. Перед изменениями логики выполнить полный набор тестов.

## Ограничения текущей версии

- Семантика `dependency_type` пока не подтверждена организаторами. В данных и
  planner-е временно используется raw-направление
  `blocking_task_id → blocked_task_id`.
- Dataset не содержит достаточной подтверждённой информации для автоматического
  вывода требуемых skills из названия задачи.
- Текущий спринт в product API равен Sprint 1, пока нет факта replanning.
- Production replanning и Bus Factor пока не подключены к backend/frontend;
  Streamlit показывает только явно помеченный demo-fixture.
- План рассчитывается и кэшируется при первом API-запросе; после пересборки CSV
  перезапустите FastAPI.

```text
http://127.0.0.1:8000
```
