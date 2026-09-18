# Bus Factor / Competency Risk module

Независимый модуль анализа Bus Factor для проекта квартального планирования.

Модуль не изменяет quarter planner и preprocessing. Он читает нормализованные CSV
из `data/processed/` и формирует отдельные отчёты в `reports/bus_factor/`.

## Что реализовано

- Team Bus Factor и Organization Bus Factor по ролям и skills.
- Квартальная актуальность ролей через `tasks.csv` + `task_role_effort.csv`.
- Для skills квартальная актуальность остаётся `unknown`, пока нет явной связи
  `task -> required_skill`.
- Потенциальные cross-team helpers.
- Один helper = один уникальный инженер, даже если он распределён между несколькими командами.
- Поиск кандидатов на cross-training.
- Анализ влияния отсутствия сотрудника.
- Полный coverage-отчёт по ролям и skills.

## Важные правила

### Helpers

Для ролей helper-рекомендации строятся только если:

- роль требуется активными задачами квартала;
- Team BF <= 2.

Для skills без `task_skill_requirements.csv` рассматриваются только skills,
которые уже существуют в команде и имеют Team BF 1 или 2. Skill с Team BF = 0
не считается дефицитом автоматически, потому что команда может вообще не
использовать эту технологию.

Если инженер распределён между несколькими командами, он возвращается одной
строкой. Например:

```text
ENG-419 | Team-K, Team-Platform | capacity_rate=1.0
```

Bus Factor не определяет фактическую свободную capacity специалиста по спринтам.
Это ответственность planner-а.

### Training candidates

Без явной связи `task -> skill` обучение предлагается только для skills с Team BF
1 или 2.

Кандидат должен ещё не владеть target skill и быть достаточно близок к текущему
holder-у:

- та же роль: минимум 1 общий skill;
- смежная роль: минимум 2 общих skills.

Это отсеивает случайные рекомендации по одному общему Git/SQL и похожие слабые
совпадения.

## Входные данные

Обязательные:

```text
data/processed/engineers.csv
data/processed/engineer_skills.csv
```

Используются при наличии:

```text
data/processed/tasks.csv
data/processed/task_role_effort.csv
data/processed/task_skill_requirements.csv
```

`task_role_effort.csv` поддерживает в том числе колонку `required_hours`.

## Файлы модуля

```text
src/bus_factor/__init__.py
src/bus_factor/engine.py
scripts/run_bus_factor.py
tests/test_bus_factor_engine.py
README_BUS_FACTOR.md
```

## Запуск

Из корня проекта:

```powershell
py scripts/prepare_dataset.py
py scripts/run_bus_factor.py
```

Если `data/processed/` уже актуален, повторно `prepare_dataset.py` запускать не
обязательно.

Для одной команды:

```powershell
py scripts/run_bus_factor.py --team Team-L
```

## Выходные отчёты

```text
reports/bus_factor/coverage.csv
reports/bus_factor/potential_helpers.csv
reports/bus_factor/training_candidates.csv
reports/bus_factor/absence_impact.csv
```

Эти CSV являются генерируемыми отчётами и обычно не должны коммититься в Git.

## Python API

```python
from src.bus_factor import BusFactorService

service = BusFactorService.from_processed_dir("data/processed")
```

Product API использует тот же `BusFactorService` через адаптер
`src.web.service.build_bus_factor_payload`. Endpoint `/api/star-map` возвращает
готовые Team + competency DTO с уникальными инженерами, risk presentation и
отдельно обозначенным cross-team резервом. Перед расчётом current view архивные
сущности исключаются существующим `CatalogRepository.active_planning_tables()`.

Coverage:

```python
coverage = service.coverage("Team-L")
```

Potential helpers для одной компетенции:

```python
helpers = service.get_potential_helpers(
    target_team_id="Team-L",
    competency_type="skill",
    competency="Kafka",
)
```

Все helpers для уязвимых компетенций:

```python
helpers = service.get_all_potential_helpers(max_team_bf=2)
```

Training candidates:

```python
training = service.training_candidates(
    team_id="Team-L",
    skill="Kafka",
    top_n=3,
)
```

Absence impact:

```python
impact = service.employee_absence_impact("ENG-401")
```

## Тесты

```powershell
py -m pytest tests/test_bus_factor_engine.py -q
```

На текущей версии модуля проходят 18 тестов. Возможен `FutureWarning` от pandas;
он не влияет на результаты расчётов.

## Интеграция с planner

Bus Factor выдаёт потенциально подходящих специалистов, но не решает, свободны
ли они в конкретном спринте. Planner может использовать результат
`get_potential_helpers(...)` и затем применять собственные ограничения capacity.
