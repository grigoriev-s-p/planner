# Планирование квартала команд

Проект разбирает единый Excel-файл организаторов, нормализует его в CSV,
проверяет качество данных и строит квартальный план на шесть двухнедельных
спринтов. Временный интерфейс Streamlit показывает исходные данные, проверки
выполнимости и рассчитанный план.

## Быстрый запуск

Нужен Python 3.10+ и PowerShell. Выполните из корня проекта:

```powershell
python -m pip install -r requirements.txt
python scripts/prepare_dataset.py
python scripts/data_audit.py
python -m streamlit run dev_ui/app.py
```

После запуска интерфейс обычно доступен по адресу `http://localhost:8501`.

## Структура проекта

| Путь | Назначение |
| --- | --- |
| `data/raw/` | Один исходный Excel-файл `.xlsx` или `.xlsm`. |
| `scripts/prepare_dataset.py` | Preprocessing: Excel → нормализованные CSV. |
| `scripts/data_audit.py` | Отдельная проверка качества обработанных данных. |
| `data/processed/` | Генерируемые CSV и manifest с хешами. Не коммитируется. |
| `src/data/` | Поиск файла, parser Excel, нормализация и валидация. |
| `src/planning/` | Feasibility checker, scoring, resource allocation и quarter planner. |
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

## Запуск интерфейса

```powershell
python -m streamlit run dev_ui/app.py
```

Интерфейс читает CSV из `data/processed/`. На странице «Загрузка датасета»
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
- «Справочники» — три справочника, извлечённые из конца Excel.

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

Для тестов установите `pytest`:

```powershell
python -m pip install pytest
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
python -m pytest tests/test_dev_ui.py tests/test_upload_flow.py -q -p no:cacheprovider
```

Acceptance tests создают маленькие данные программно и проверяют итоговый план,
ресурсы, причины и audit issues. Они не зависят от текущего Excel.

## Последовательность ручной проверки

1. Поместить Excel в `data/raw/`.
2. Запустить `python scripts/prepare_dataset.py`.
3. Запустить `python scripts/data_audit.py` и прочитать отчёт из `reports/`.
4. Открыть `python -m streamlit run dev_ui/app.py`.
5. На странице «Качество данных» проверить warnings.
6. На странице «Квартальный план» построить план и проверить задачи `MOVED`,
   причины, cross-team allocations и рекомендации.
7. Перед изменениями логики выполнить полный набор тестов.

## Ограничения текущей версии

- Семантика `dependency_type` пока не подтверждена организаторами. В данных и
  planner-е временно используется raw-направление
  `blocking_task_id → blocked_task_id`.
- Dataset не содержит достаточной подтверждённой информации для автоматического
  вывода требуемых skills из названия задачи.
- Интерфейс предназначен для разработки. Он не сохраняет рассчитанный план как
  production-артефакт.

```text
http://localhost:8501
```

