# Полный аудит проекта «Планирование команд и Звёздная карта»

## 1. Паспорт аудита

| Поле | Значение |
|---|---|
| Дата аудита | 2026-09-20, Europe/Moscow |
| Ветка аудита | `feature/audit` |
| Исходная ветка | `feature/planner` (`origin/feature/planner`) |
| Исходная ревизия | `cd622c0a027365d88bc52457469882421ab34a4e` |
| Сообщение исходного commit | `feat: complete stable sprint replanning workflow` |
| Режим | READ-ONLY для source code; единственное tracked-изменение — этот отчёт |
| Full test suite | Не запускался |

Ветка `feature/audit` создана от чистой актуальной `feature/planner`. На старте был только существующий untracked-каталог `.codex/`; он не включён в commit. `origin/feature/audit` на момент начала аудита отсутствовал.

### Ограничения и важное замечание о тестовой среде

Один целевой API-набор тестов оказался небезопасным для текущего рабочего каталога: несколько тестов через глобальный `plan_drafts` записали восемь тестовых plan snapshots (11–18) в `data/catalog/catalog.sqlite3` и переключили current pointer с 10 на 18. До запуска было 10 snapshots и pointer 10; после — 18 и pointer 18. Попытка автоматически восстановить pointer и удалить только созданные аудитом snapshots была отклонена автоматической проверкой разрешений как изменение persistent user data. Исходные значения и точные ID сохранены в этом отчёте; для восстановления требуется явное разрешение владельца данных. Это одновременно подтверждает дефект изоляции тестов `AUD-P1-003`.

Все временные скрипты, `.pytest_cache` и каталоги `tmp*`, созданные аудитом, удалены. Production/source code не изменялся.

## 2. Executive summary

**Overall status: PARTIAL.** Основной planning workflow, hard constraints, приоритет rung, ownership, зависимости, Base/Current/Best, approval, persistent Approved Plan и remaining-horizon replanning реализованы и хорошо покрыты целевыми тестами. Главные риски находятся вокруг состояния приложения, восстановления, архивирования, тестовой изоляции и демонстрационной документации.

**Demo readiness: PARTIAL / условно готово.** Happy path доступен, но реальный датасет даёт только 10 запланированных задач из 45 и 26 `UNPLANNABLE`; README описывает устаревший workflow; frontend browser E2E для полного пути не автоматизирован; constructor draft и upload preflight теряются после restart.

**Architectural readiness: PARTIAL.** Доменные правила сосредоточены в planner/core, Approved revisions и replanning workflows хранятся в SQLite. При этом constructor session живёт в памяти, архивирование каталога и публикация нового Approved Plan не образуют общей транзакции, а production и legacy replanning/diff представлены двумя наборами модулей.

| Severity | Количество |
|---|---:|
| P0 | 0 |
| P1 | 7 |
| P2 | 8 |
| P3 | 3 |

### Пять наиболее существенных рисков

1. **`AUD-P1-001`:** archive commit и публикация replacement Approved Plan выполняются раздельно; возможен catalog/plan split-brain при сбое второй операции.
2. **`AUD-P1-002`:** Current Draft, saved Best и prepared upload хранятся только в памяти и теряются после restart.
3. **`AUD-P1-003`:** API-тесты не полностью изолированы и способны изменять рабочую SQLite БД.
4. **`AUD-P1-004`:** три acceptance-теста закрепляют правило, противоположное подтверждённому `TASK RUNG > INITIATIVE COMPLETION`.
5. **`AUD-P1-005`:** текущий датасет содержит шесть требуемых ролей без точного специалиста, 24 расхождения часов и неподтверждённую семантику dependency types; результат плана демонстрационно слабый.

### Пять сильных частей

1. Приоритет rung реализован как абсолютный фильтр до initiative/stability tie-breaks.
2. SP и engineer-hours считаются раздельно; owner-team сохраняет SP при cross-team помощи.
3. Approved Plan сохраняется revisioned snapshots в SQLite и восстанавливается после restart.
4. Close Sprint сохраняет actual до planner, формирует review draft и не заменяет Approved без manager approve.
5. Catalog import additive/idempotent; archive lifecycle и решение о повторном появлении сущности в Excel покрыты целевыми тестами.

## 3. Что реально проверено

- Git branch/revision/remotes и чистота рабочего дерева.
- Статический data-flow по `src/planning`, `src/web`, `src/replanning`, `src/data`, `frontend`, `scripts`.
- Список backend endpoints и frontend callers.
- SQLite schema и read-only counts до целевых API-тестов: `catalog_rows=1298`, `entity_lifecycle=1`, `catalog_row_overrides=1`, `plan_snapshots=10`, `current_plan_snapshot→10`, `replan_workflows=0`.
- Read-only data audit текущего `data/processed`.
- Read-only построение dashboard/plan на реальном processed dataset.
- 203 целевых planner/replanning/preprocessing/Bus Factor теста: 199 PASS, 4 FAIL.
- 88 catalog/archive/entity/preflight/constructor/Bus Factor integration тестов: PASS вне sandbox; первый sandbox-run дал ложные filesystem failures и выявил проблему изоляции persistent state.
- Frontend/static contract review и escaping review.

Не выполнено:

- Full suite: не запускался, чтобы не расширять мутацию persistent state и потому что уже обнаружены три заведомо противоречащих бизнес-правилу acceptance-теста.
- Полный browser E2E: в repository нет настроенного browser harness; API/static coverage не доказывает DOM/event path целиком.
- Load/performance benchmark: текущий объём мал, а аудит не должен создавать дополнительную инфраструктуру.
- Penetration testing: вне заданного scope.

## 4. Архитектурная карта

| Module | Purpose | Inputs | Outputs | Persistent state | Depends on | Used by |
|---|---|---|---|---|---|---|
| `src/data/parser.py` | Поиск таблиц и справочных блоков Excel | raw workbook | `ParsedDataset` | Нет | openpyxl | preprocessing, upload |
| `src/data/preprocessing.py` | Нормализация и manifest | parsed blocks | processed CSV | CSV + manifest | parser/normalizers | service, scripts |
| `src/web/data_preflight.py` | Data quality/preflight | workbook + catalog | issues/groups | prepared upload не persistent | parser, catalog merge rules | upload API/UI |
| `src/web/catalog.py` | Catalog, archive lifecycle, Approved snapshots, replan workflow | normalized tables, plans | active tables/history | SQLite | pandas/sqlite | API/services |
| `src/planning/feasibility.py` | Hard constraint checks | task/team/engineer/deps | feasibility reasons | Нет | capacity/roles | planner, checker |
| `src/planning/allocation.py` | Распределение часов и cross-team residual | demand, buckets | allocations | Нет | engineer buckets | planner |
| `src/planning/planner.py` | Quarter planner, priority, simulation, SP loads | tables/scenario/reference | `QuarterPlan` | Нет | scoring, feasibility, allocation | web service/constructor/replan |
| `src/planning/explanations.py` | Structured explanations | plan decisions | reason payloads | Нет | planner models | API/UI |
| `src/web/constructor.py` | Manual/Excel scenario and recommendation plan | catalog/selection/workbook | Base/Best payload | Нет | planner | `PlanDraftStore` |
| `src/web/api.py::PlanDraftStore` | Base/Current/Best session; approve; lifecycle orchestration | constructor/replan/catalog | API DTO | Draft/upload memory; Approved SQLite | constructor, catalog, replan | FastAPI |
| `src/web/replanning.py` | Remaining-horizon rebuild and structured diff | Approved + actual + tables | Replan Draft/diff | Via workflow JSON | planner | API |
| `src/replanning/*` | Legacy/generic KPI, diff, alert snapshot | baseline/current/actual | KPI/diff/alerts | Нет | domain DTO | service/demo/tests |
| `src/web/service.py` | Product read model, KPI, risk, Star Map | processed tables or snapshot | dashboard DTO | Нет | planner, bus factor, replanning metrics | API |
| `src/web/entity_projections.py` | Task/team/engineer history projections | revisions + actual | entity history | Derived | catalog snapshots | API/UI |
| `src/bus_factor/engine.py` | Unique active-engineer coverage | profiles/skills | BF/risks/helpers | Нет | normalized data | service/UI |
| `frontend/api.js` | HTTP client | UI actions | JSON/errors | Нет | FastAPI | SPA |
| `frontend/constructor.js` | Planning/import/recommendations UI | catalog/drafts | user actions | Browser memory | api.js | SPA |
| `frontend/app.js` | Current Plan/Sprint/entities/replan/Star Map | dashboard/endpoints | DOM | Browser memory | api.js | SPA |

Shared/high-blast-radius modules: `src/planning/planner.py`, `src/web/api.py`, `src/web/catalog.py`, `src/web/service.py`, `frontend/app.js`.

## 5. Domain/data entity matrix

| Entity | Stable ID | Canonical source | Current state | History | Persistence | Risk |
|---|---|---|---|---|---|---|
| Task | `task_id` | catalog row; initial seed from `tasks.csv` | active/archive + Approved projection | Approved revisions + actual results | SQLite | PARTIAL: catalog and snapshot copies can diverge across split transaction |
| Initiative | `initiative_id` / PRODF | task attribute | derived from active tasks/baseline | Week-0 baseline inside snapshots | SQLite in Approved | No independent initiative registry in current data |
| Team | `team_id` | catalog `team_history`/profiles/tasks | active/archive + snapshot | Approved revisions | SQLite | Team identity is assembled from several tables |
| Engineer | `engineer_id` | `engineers` catalog | memberships + skills + archive | Approved revisions and derived allocation history | SQLite | 30 unique people, 34 membership rows; row count must not equal people count |
| Membership | `(engineer_id, team_id, role)` | engineers rows | mutable catalog | snapshot copy | SQLite | Capacity is bucketed by membership; four multi-team engineers |
| Skill | `(engineer_id, technology)` | engineer skills | active engineer filter | no skill-level revision object | catalog/snapshot tables | Task-required skills absent in current dataset |
| Allocation | task/sprint/engineer tuple | planner output | Approved task payload | copied in each immutable revision | SQLite snapshot | Planned hours, not actual worked hours |
| Plan | generated plan version/draft ID | planner/constructor | in-memory Current Draft or persistent Approved | revisions for Approved only | Mixed | Draft/Best lost on restart |
| Revision | SQLite `revision` + snapshot ID | `plan_snapshots` | current pointer | immutable snapshot rows | SQLite | Test contamination demonstrated; no app-level test DB guard |
| Sprint actual | workflow + `_planning_state.actual_*` | close-sprint request | workflow/published snapshot | retained after approve | SQLite JSON | Safe when workflow persistence succeeds |
| Dependency | `(blocking_task_id, blocked_task_id, dependency_type)` | catalog/task_dependencies | raw direction preserved | snapshot table | SQLite | Text semantics are unconfirmed by organizers/data |
| Recommendation | recommendation ID inside draft | computed Best vs Current | draft-local | accepted change metadata only | Memory until approve | Lost with constructor session restart |

## 6. Business rule matrix

| Rule | Status | Evidence | Notes |
|---|---|---|---|
| 6 sprints × 2 weeks = 12 weeks | PASS | planner constants/models; quarter tests | Fixed horizon is consistently 1..6 |
| SP capacity = historical velocity × 0.8 | PASS | `src/planning/capacity.py::calculate_team_capacity`; tests | SP remains separate from hours |
| Engineer 80h × capacity_rate per membership bucket | PASS | `calculate_engineer_capacity`; ownership/capacity tests | Multi-team 0.5+0.5 remains 40h/40h |
| Absolute rung | PASS | `planner.py::_select_priority_task_id` filters max rung first | Confirmed by strict/official tests |
| Same rung → completed initiatives | PASS | same-rung selection branch | Three old acceptance tests expect the opposite across different rung and are stale |
| Replan: initiative before stability | PASS | reference/stability tie-break occurs after rung/initiative | No evidence stability can displace higher rung |
| Initiative priority = MAX rung | PASS | `initiative_priority_profile`; tests | No SUM/AVG path found |
| Planned/start date soft tie-break | PASS | `sort_tasks_for_planning` and tests | Deadline is not primary objective |
| Week-0 KPI denominator fixed | PASS | `_planning_state.week0_baseline`; stage2 tests | Survives approve/replan |
| Say/Do multi-sprint semantics | PARTIAL | `src/replanning/metrics.py::calculate_sprint_say_do` | Current implementation is explicit, but product semantics remain NEEDS_CLARIFICATION |
| Raw dependency direction | PASS | blocking → blocked retained | All dependency types share raw direction; data semantics still unconfirmed |
| Historical Done | PASS | validation/planner/constructor tests | Context only, satisfies dependency, consumes no plan capacity |
| Initial dependency N→N+2 | PASS | dependency tests | Enforced for initial plan |
| Actual dependency N→N+1 | PASS | `build_remaining_horizon_plan`; replanning tests | Uses actual completed tasks |
| Unknown blocker validation | PASS | constructor/feasibility validation tests | Rejected rather than silently dropped |
| InProgress effort is remaining | PASS | official clarification tests | No second subtraction of spent time |
| Required role hard constraint | PASS | `FeasibilityService`; missing-role tests | Exact normalized role required |
| Base has no reassignment/cross-team | PASS | constructor build options and parity tests | Optimized Best can include them |
| Owner team owns all SP | PASS | ownership/cross-team tests | External engineers add hours only |
| Own-team-first | PASS | allocation and task ownership tests | Cross-team limited to residual deficit |
| Reassignment majority rules | PASS | ownership validation tests | Counts engineers with positive hours |
| Fractional proportional multi-sprint SP | PARTIAL | planner tests and real dashboard | Correct formula; raw float noise leaks (`8.000000000010001`) |
| Original SP denominator on replanning | PASS | `_immutable_task_baseline`, `_remaining_story_points`; tests | 8/60→20 = 2.666… scenario covered |
| Base immutable, Current mutable, Best stable | PASS in session | `PlanDraftStore`; constructor tests | Persistence is PARTIAL because Current/Best are memory-only |
| Apply changes Current only | PASS | draft recommendation endpoints/tests | Approved unchanged |
| Apply All = saved Best | PASS | constructor tests | No rerun |
| Approve exact Current | PASS | approve tests | No planner rerun; resulting snapshot persistent |
| Recommendation order/details | PASS | constructor recommendation tests/UI | Task move/reassignment precedes cross-team; detail DTO present |
| SP shown separately from hours | PASS | constructor/current plan/task markup | Numeric float formatting remains cosmetic issue |
| Frozen past; only N+1..6 replanned | PASS | `build_remaining_horizon_plan`; replanning tests | Past allocations merged immutably |
| Replan draft requires manager approval | PASS | close/retry/approve endpoints and tests | Previous Approved remains official while READY_FOR_REVIEW |
| Soft archive/history retained/no silent reactivation | PARTIAL | catalog/archive tests | Core behavior passes; transaction/review gap remains |
| Excel additive/idempotent/no silent overwrite | PASS | catalog merge and preflight tests | Conflicts surfaced to UI |
| Star Map diagnostic only | PASS | `PlanningAPIService.star_map` read path | No planner mutation edge found |
| Bus Factor by unique active engineers | PASS | `BusFactorService`; integration tests | Threshold labels BF≥3/BF2/BF1 implemented |

## 7. Planner, feasibility, allocation and SP findings

### Initial planning pipeline

Observed flow: normalized/catalog tables → scenario validation → hard feasibility → max-rung candidate set → initiative completion tie-break → optional reference/stability → date/deterministic tie-break → hour allocation → proportional SP load → plan/exclusions/risks/explanations.

- Manual and Excel converge on the same `build_constructor_plan`/planner path after normalization.
- Historical Done tasks are injected as context and not selectable workload.
- Failed simulations use cloned state; no evidence that engineer/SP capacity remains consumed after a rejected attempt.
- Engineer capacity is keyed by membership buckets and consumed once. Current data has four multi-team engineer IDs and no multi-role engineer; synthetic tests cover multi-role double counting.
- Cross-team help is residual-only and does not add owner-team SP.
- Reassignment rebuilds allocations rather than relabeling an existing allocation.
- Exclusion reasons remain structured; planning infeasibility is separated from upload critical data errors.

### Real dataset result

Read-only `PlanningAPIService.dashboard` on current processed files produced:

| Metric | Observed |
|---|---:|
| Total tasks | 45 |
| Historical Done | 8 |
| Planned | 10 |
| Moved | 1 |
| Unplannable | 26 |
| Completed initiatives / Week-0 initiatives | 2 / 15 |
| Multi-sprint tasks | 6 |
| Cross-team tasks | 6 |
| Reassigned tasks | 3 |
| Risks | 43 |

This is deterministic evidence of a demo-quality risk, not evidence that the planner violates priority. The main causes exposed by data audit are role vocabulary gaps and input inconsistencies.

### Numerical behavior

One targeted test failed because SP loads were `[8.000000000010001, 0.9999999999899991]` instead of exact `[8, 1]`. Total SP and capacity semantics remain correct. The API/model therefore leaks binary float artifacts into snapshots, UI and exact comparisons. See `AUD-P2-001`.

## 8. Replanning audit

### Current lifecycle

`Approved Plan` → backend-authoritative current sprint → close-sprint validation → actual/workflow persisted → `build_remaining_horizon_plan` for N+1..6 → diff/reasons → workflow `READY_FOR_REVIEW` → manager approve → immutable Approved revision.

Key evidence:

- `src/web/api.py` endpoints `/api/replanning/close-sprint`, `/draft`, `/drafts/{id}/retry`, `/drafts/{id}/approve`.
- `src/web/replanning.py::build_remaining_horizon_plan` keeps closed/past task parts, computes remaining work/SP from immutable baseline, passes reference preferences and merges future plan.
- `CatalogRepository.save_replan_workflow` persists actual, candidate, diff and provenance as JSON.
- `PlanDraftStore.approve_replan` validates current Approved revision to reject stale drafts.
- Sprint 6 creates a final review without invoking nonexistent Sprint 7.

### Actual and failure safety

- Actual is stored before planner execution. If planning fails, workflow becomes retryable and Approved remains unchanged.
- Duplicate close is rejected while a workflow for that sprint is pending/closed.
- Retry reuses saved actual rather than asking user to resubmit it.
- Approve is explicit and publishes the stored candidate exactly.
- A backend restart restores Approved snapshots and replan workflows from SQLite.

### Stability/minimum disruption

The implementation performs a global rebuild of the remaining horizon with reference preferences for prior owner, engineer hours, accepted cross-team tasks and prior placement. It does **not** implement a separate local/affected repair phase or a formal globally minimized disruption objective. Static order keeps rung and initiative above stability, which matches the rule. Targeted tests prove unrelated placement, remaining horizon, baseline preservation and key accepted assignment cases, but do not exhaustively prove global minimum change for every business-equivalent plan. Status: PARTIAL for “minimum disruption”, PASS for “reference, not hard lock”.

### Employee carry-over and unavailability

Remaining role hours and employee-specific carry-over are retained. Preferred original engineer work is considered first; `unavailable_next_sprint` excludes that engineer only for N+1 and allows another valid local/cross-team engineer. Free-form `incomplete_reason` is audit/UI metadata and does not modify capacity. Required role stays hard.

### Remaining SP

`_immutable_task_baseline` preserves original hours/SP. `_remaining_story_points` derives residual from the original denominator, preventing repeated replanning drift. The required 8 SP / 60h / 20h scenario is represented as 2.666… SP.

### Replan diff and manager visibility

`_snapshot_diff` compares per task: presence, sprints, owner team, engineers, hours, SP load and cross-team allocation. It returns before/after views, multiple changed fields, affected team/engineer IDs, changed and unchanged counts. UI groups change cards and displays before/after allocations. Supported fields cover ADDED/REMOVED plus `SPRINT_CHANGED`, `TEAM_CHANGED`, `ENGINEERS_CHANGED`, `HOURS_CHANGED`, `SP_LOAD_CHANGED`, and cross-team change. Change reasons are structured where the causal fact is known; no generic inference was found that mutates planning.

### Replanning gaps

- Global rebuild can choose a different but business-equivalent arrangement; preferences reduce this, but no proof of mathematical minimum disruption exists.
- Approved snapshot write and workflow status update are separate persistence operations. A failure after snapshot publication can leave the workflow looking READY even though the plan is already current; stale revision checks prevent a second silent overwrite, but recovery UX is ambiguous (`AUD-P2-003`).
- A legacy KPI/diff/replanning layer in `src/replanning/*` coexists with production orchestration in `src/web/replanning.py`, raising semantic drift risk (`AUD-P2-004`).

## 9. History readiness

### Task

Current detail is assembled from current Approved snapshot plus catalog identity. History can correlate task ID across immutable revisions and actual result records, including planned sprint/team/allocations and incomplete reason. Original baseline is retained in `_planning_state`. Risk: constructor-only drafts are not history and disappear on restart; archived catalog/Approved split can temporarily disagree.

### Team

Team page projects owned tasks, cross-team participation, memberships, capacity and Bus Factor. History is derived by comparing snapshots and actual result maps. It correctly labels hours as planned participation. There is no independent actual worked-hours feed, so true employee utilization history cannot be calculated; UI must continue to avoid calling planned allocations “actual hours”.

### Engineer

Engineer identity and memberships are catalog-based; allocations are read from revisions. History shows planned work and task outcome, not timesheets. Skill/criticality uses the same Bus Factor read model as Star Map. Four engineers have two team memberships; UI total counts use unique engineer IDs in the reviewed paths.

## 10. Data quality and preprocessing

Current `manifest.json` identifies raw hash `a618cb…f22e`, preprocessing version `1.4.0`, generated 2026-09-17. Current normalized rows:

| File/entity | Rows |
|---|---:|
| Tasks | 45 |
| Task role effort | 990 |
| Dependencies | 19 |
| Engineer membership rows | 34 |
| Unique engineers | 30 |
| Engineer skills | 182 |
| Team history | 12 |
| Planning results reference | 8 |
| Plan mismatch reasons | 5 |
| Task closure results | 3 |

`scripts.data_audit.audit_dataset` returned 46 issues:

- 1 Done dependency conflict;
- 4 duplicate engineer IDs (legitimate multi-team membership but must be counted uniquely);
- 6 required role labels without an exact specialist;
- 24 task total-hour mismatches;
- 6 InProgress rows without spent time;
- no task skill requirements;
- no independent status or initiative registry;
- unconfirmed dependency-type semantics;
- one suspicious planned interval.

No placeholder tasks exist in this current processed snapshot, no dependency cycles were found, and all 45 tasks have linked SP/hours.

`reports/data_audit_report.md` is stale: it reports 52 tasks, 7 placeholders, 16 dependencies and 94 issues, which does not match current processed data. It must not be used as current evidence (`AUD-P2-005`).

## 11. Persistence and restart matrix

| State | Stored where | Restart safe | Immutable/revisioned | Gap |
|---|---|---|---|---|
| Catalog Tasks/Teams/Engineers/Skills/Memberships | SQLite `catalog_rows` + lifecycle/overrides | Yes | Mutable current rows; lifecycle audit | Cross-table merge is transactional, but plan publication is separate |
| Week-0 baseline | Approved payload `_planning_state.week0_baseline` | Yes after approve | Copied per revision | Not durable before first approve |
| Base Plan | `PlanDraftStore._drafts` | No | Immutable only within object contract | Lost on restart |
| Current Draft | `PlanDraftStore._drafts` | No | Mutable session | Lost on restart; only latest draft retained |
| Best | draft payload | No | Stable for input revision in session | Lost on restart |
| Prepared upload | `PreparedUploadStore._uploads` | No | No | Continue URL fails after restart; only latest upload retained |
| Approved Plan | SQLite `plan_snapshots` + current pointer | Yes | Yes | Snapshot JSON duplication; test isolation gap |
| Approved history | `plan_snapshots` | Yes | Yes | Read scans/decodes many full JSON payloads |
| Actual results | `replan_workflows`, later Approved `_planning_state` | Yes | Workflow audit + snapshot copy | No standalone normalized actual table |
| Replan Draft/diff/provenance | `replan_workflows.payload` | Yes | Workflow ID/status | Snapshot publish/status update not one transaction |
| Archive state/reason | `entity_lifecycle`/overrides | Yes | Audit timestamp/reason | Archive and replacement Approved not atomic together |

Restart scenarios:

- After Approve: PASS, Approved restored.
- After Close Sprint before planning completes: PASS/PARTIAL, workflow and actual retained; retry supported.
- Before Replan Approve: PASS, review draft retained.
- After archive: PARTIAL, catalog persists but catalog/plan two-step failure window exists.
- During constructor/upload preview: FAIL, state is memory-only.

## 12. Catalog, archive and import

Catalog merge is additive, does not remove rows absent from later imports, compares payloads to report conflicts, and applies archived-entity decisions explicitly (`KEEP_ARCHIVED`, `RESTORE`, `RESTORE_UPDATE`). Repeating the same confirmation is covered by tests.

Soft archive retains IDs, reason, time and historical snapshots. Active dependencies/teams/engineers receive impact analysis. Archive excludes the entity from future planning and keeps prior Approved revisions.

Two design gaps remain:

1. `_archive_entity` first computes a replacement, commits catalog archive, then calls `replace_approved(candidate)`. These writes are not one transaction. If the second step fails, active catalog and current Approved plan disagree.
2. The confirmed archive action publishes a replacement directly as Approved. It does not expose a separate manager Draft/Review step comparable to Close Sprint replanning. The UI does show impact and requires confirmation, so this is PARTIAL rather than FAIL.

## 13. Star Map and Bus Factor

- `PlanningAPIService.star_map` calls `build_bus_factor_payload`; no write/planner side effect exists.
- `BusFactorService` counts unique active engineer IDs, not profile rows.
- Team BF labels follow BF≥3 green, BF=2 yellow, BF=1 red; BF=0 is represented as missing coverage/high risk.
- Archived engineers are filtered; targeted integration test passes.
- Current dataset has 116 engineer skill labels but zero task-required skill labels. Star Map is useful for organization diagnostics, while task-skill feasibility cannot be validated against real demand and remains BLOCKED by data.

## 14. Frontend architecture and contracts

SPA navigation and state are centralized in `frontend/app.js`; constructor-specific state is in `frontend/constructor.js`; transport/error normalization is in `frontend/api.js`. Main dynamic user strings are passed through `escapeHtml`/`attr` before `innerHTML`, and plain messages use `textContent`.

Risks:

- Very large stateful scripts make duplicate/stale handlers hard to reason about; no browser runtime suite proves all navigation/modal paths.
- For unexpected replanning intermediate status, UI displays raw `workflow.status` (`Статус пересчёта: ...`), leaking an internal enum.
- Risk cards intentionally expose some technical labels (`Evidence`, `Team BF`, internal risk type); these are understandable to developers but inconsistent with the otherwise Russian manager UI.
- Extensive `innerHTML` is presently mitigated by escaping in reviewed dynamic paths, but future additions have a large XSS review surface.

### Endpoint matrix

| Endpoint/group | Caller | Purpose | Status | Contract issues |
|---|---|---|---|---|
| `GET /api/dashboard`, `/plan`, `/summary`, `/kpis` | `app.js` | Main read model | PASS | Fallback processed plan before persistent Approved is a distinct source |
| `GET /api/current-sprint` | `app.js` | Backend-authoritative sprint | PASS | Handles pending review/quarter closed |
| `POST /api/replanning/close-sprint` | `app.js` | Save actual and create draft | PASS | Requires persistent workflow; duplicate guarded |
| `GET /api/replanning/draft` | `app.js` | Restore review | PASS | Raw unknown status can leak in UI |
| `POST .../retry`, `.../approve` | `app.js` | Recovery/publish | PASS | Publish/status two-step atomicity gap |
| `GET /api/tasks/{id}` + history | `app.js` | Task detail/history | PASS | History is snapshot-derived |
| `GET /api/teams/{id}` + history | `app.js` | Team detail/history | PASS | Planned hours, no actual timesheet |
| `GET /api/engineers/{id}` + history | `app.js` | Engineer detail/history | PASS | Planned hours only |
| `GET /api/star-map` | `app.js` | Skills/BF diagnostics | PASS | No task-skill demand in current data |
| `GET /api/plan-constructor/catalog` | `constructor.js` | Manual selection | PASS | Draft state client/server memory-only |
| `POST /api/plan-constructor/plan/upload` | `constructor.js` | Preflight | PASS | Bytes/read-model in memory; restart loses upload |
| `POST .../upload/{id}/continue` | `constructor.js` | Apply archive decisions/build | PASS | 404/invalid state after restart |
| `POST /api/plan-constructor/plan` | `constructor.js` | Manual Build | PASS | Browser “select all” E2E not automated |
| Draft recommendations/apply/reject/apply-all/undo/reset | `constructor.js` | Current/Best workflow | PASS in session | Draft disappears after restart; one active draft store |
| `POST /api/plan-constructor/drafts/approve` | `constructor.js` | Exact approval | PASS | Persists snapshot |
| Deletion impact + archive endpoints | `app.js` | Soft archive | PARTIAL | Auto-publishes replacement after confirm; transaction gap |

No frontend call to a missing backend endpoint was found in the reviewed API client paths. HTML fallback routes for task/team/engineer detail resolve to the SPA.

## 15. API validation, errors, robustness and security

Positive evidence:

- Pydantic/request validation and explicit 4xx mapping exist for unknown IDs, invalid archive reason, stale draft and catalog conflicts.
- Constructor validates duplicates, unknown references/teams/roles, cycles, capacity-rate sums and historical Done context.
- SQL statements use parameters; no string interpolation of user values into SQL was found.
- Uploaded filename is metadata; server does not construct arbitrary filesystem paths from it.
- UI escapes reviewed task/team/engineer/user-provided text.

Gaps:

- Upload body is read into memory; no explicit request/file-size limit was found (`AUD-P2-006`).
- Constructor/upload stores replace their dictionaries with only the latest object, so concurrent managers can invalidate each other (`AUD-P1-002`).
- Unknown replanning status can surface raw technical code (`AUD-P3-001`).
- There is no authentication/authorization layer. For a local hackathon demo this is accepted; any shared deployment must treat it as a product boundary (`AUD-P2-007`).

## 16. Performance/scalability

At current scale (45 tasks, 30 engineers) no blocking performance defect was observed. Risks for growth:

- Planner simulation and repeated candidate comparison are combinatorial/N²-like in several paths.
- Entity history endpoints decode and compare full snapshot JSON revisions.
- Each Approved revision duplicates tables and full plan state.
- `app.js` renders large tables/lists by replacing full `innerHTML` blocks.
- Star Map builds broad engineer/skill matrices in memory.

These are P2/P3 architecture items, not reasons to introduce microservices before the demo.

## 17. Test architecture and execution

### Executed group A

203 tests across acceptance, quarter planner, cross-team, ownership, initiative priority, strict audit, official clarifications, replanning lifecycle/workflow, sprint completion, stage2 followups, preprocessing and Bus Factor engine.

- 199 PASS.
- 3 FAIL because acceptance tests expect a lower-rung initiative-completion set to beat a higher-rung task; this contradicts confirmed business truth.
- 1 FAIL due exact float representation in multi-sprint SP load.

### Executed group B

88 tests across catalog, archive import decisions, entity pages/history, Excel preflight, constructor and Bus Factor integration.

- Sandbox run: filesystem PermissionError plus 25 false failures/4 teardown errors.
- Re-run outside sandbox: all 88 PASS.
- The group unexpectedly wrote eight snapshots to the working SQLite DB through global API state, proving test isolation gap.

### Coverage map

| Critical behavior | Test exists | Test run | Result | Gap |
|---|---|---|---|---|
| Absolute rung | Yes | Yes | PASS | Three stale acceptance tests contradict it |
| Same-rung initiative completion | Yes | Yes | PASS | No gap found |
| Missing role | Yes | Yes | PASS | Real data contains six such labels |
| Multi-role capacity | Yes | Yes | PASS | No current real multi-role person |
| Historical Done dependency | Yes | Yes | PASS | Dataset has one inconsistent Done dependency |
| Initial N+2 | Yes | Yes | PASS | — |
| Actual N+1 | Yes | Yes | PASS | — |
| Remaining SP original denominator | Yes | Yes | PASS | Float formatting noise remains |
| Stable unrelated task | Yes | Yes | PASS | Global minimum disruption not exhaustively proven |
| Rung vs stability | Yes | Yes | PASS | — |
| Accepted reassignment/cross-team | Yes | Yes | PASS | Preference, not hard lock |
| Close idempotency | Yes | Yes | PASS | — |
| Draft not silently approved | Yes | Yes | PASS | — |
| Approve exact draft | Yes | Yes | PASS | — |
| Restart Approved/actual/history | Yes | Yes | PASS | Constructor restart not covered/persistent |
| Archive + reimport | Yes | Yes | PASS | Test DB isolation defect |
| Manual/Excel backend parity | Yes | Yes | PASS | Full browser select-all E2E unverified |
| Frontend browser flow | No full harness | No | UNVERIFIED | Static/API tests only |

## 18. Main defect table

| ID | Severity | Module | Problem | Impact | Evidence | Recommended fix |
|---|---|---|---|---|---|---|
| AUD-P1-001 | P1 | Archive/persistence | Catalog archive and replacement Approved snapshot are separate commits | Failure can leave future plan referencing archived state or catalog/plan mismatch | `api.py::_archive_entity`; `catalog.py::archive_with_relations`, then `replace_approved` | One application transaction/outbox or durable pending archive-plan workflow with retry/review |
| AUD-P1-002 | P1 | Constructor/upload | Base/Current/Best and prepared upload are memory-only and only latest entry is retained | Restart/concurrent user loses work or gets invalid upload/draft ID | `PlanDraftStore._drafts`; `PreparedUploadStore._uploads`; feature flag says `AVAILABLE_EPHEMERAL` | Persist draft/upload metadata with input revision and owner/session |
| AUD-P1-003 | P1 | Tests/persistence | API tests can write working `catalog.sqlite3` | Tests corrupt developer/demo state and history | Pre-test 10 snapshots/pointer 10; post-test 18/pointer 18 | Mandatory temp repository injection and hard test-mode guard |
| AUD-P1-004 | P1 | Acceptance tests | Three tests assert initiative count can beat higher rung | Suite gives false failures and invites regression against confirmed rule | Three named failures in `test_quarter_planner_acceptance.py` | Rewrite scenarios as same-rung initiative tie-break; retain separate absolute-rung test |
| AUD-P1-005 | P1 | Data/demo | Current data has exact-role gaps and many unplannable tasks | Demo plan shows 26/45 unplannable and only 2/15 initiatives complete | Read-only data audit + dashboard probe | Reconcile role dictionary/data with organizers; do not weaken hard role rule |
| AUD-P1-006 | P1 | Demo/docs | README says production replanning is absent/current sprint fixed to 1 and suggests `httpx2` | Team/operator follows wrong flow and install may fail | `README.md` lines ~161, 241, 289–290 | Update README to current persistent replan workflow and correct dependency name |
| AUD-P1-007 | P1 | Frontend verification | No full browser smoke for import→build→apply→approve→close→replan | API may pass while event/state wiring breaks during demo | Test map; static JS review only | Add one isolated browser happy-path against temp DB |
| AUD-P2-001 | P2 | SP/model | Binary float artifacts leak to API/snapshots | Exact comparisons/diff/UI can show false changes/noisy values | Targeted failure `[8.000000000010001, 0.9999999999899991]` | Canonical rounding at DTO/snapshot boundary while preserving sum |
| AUD-P2-002 | P2 | Stable replan | No formal/local minimum-disruption optimizer | Equivalent global rebuild may move more work than necessary | `build_remaining_horizon_plan` + reference preferences | Add explicit deterministic disruption score after rung/initiative |
| AUD-P2-003 | P2 | Replan persistence | Approved snapshot publish and workflow APPROVED update are separate writes | Crash can leave current plan advanced while review appears pending | approve flow in `api.py`/catalog calls | Atomic transaction or recoverable published revision marker |
| AUD-P2-004 | P2 | Architecture | Production and legacy replanning/KPI/diff implementations coexist | Semantics can drift; maintainers may change wrong layer | `src/web/replanning.py` vs `src/replanning/*` | Declare ownership, reuse shared pure functions, deprecate obsolete path later |
| AUD-P2-005 | P2 | Data audit | Saved data audit report does not match current processed snapshot | Misleads demo/debugging and organizer discussion | 52 vs 45 tasks; 7 vs 0 placeholders; 16 vs 19 deps | Regenerate on dataset change or embed manifest hash and stale banner |
| AUD-P2-006 | P2 | Upload robustness | No explicit upload-size limit; workbook bytes held in memory | Large/malformed upload can exhaust process memory | upload endpoint/store flow | Configure bounded request size and reject early with 413 |
| AUD-P2-007 | P2 | Security boundary | No authentication/authorization | Shared deployment permits any caller to approve/archive/close | FastAPI endpoint map | Keep local for demo; add auth/RBAC before shared deployment |
| AUD-P2-008 | P2 | Data semantics | Dependency type meaning and Done conflict unresolved | Correct raw direction may still not reflect organizers’ meaning | data audit: 3 types, one Done conflict | Confirm semantics with organizers; preserve raw fields meanwhile |
| AUD-P3-001 | P3 | UI | Raw workflow status can appear | Manager sees internal enum | `app.js::renderReplanWorkflow` fallback | Central localized status mapping |
| AUD-P3-002 | P3 | UI copy | Mixed `Evidence`, `Team BF`, technical risk type labels | Inconsistent Russian UI | risk/star-map markup | Localize presentation only |
| AUD-P3-003 | P3 | Maintainability | `api.py`/`app.js` are large orchestration files | Higher change/review risk | repository map | Split after demo behind unchanged contracts |

## 19. Module readiness matrix

| Module | Status | Production readiness | Demo readiness | Severity gaps |
|---|---|---|---|---|
| Excel Import | PASS | Good for controlled files | Ready | P2 size limit |
| Data Preflight | PASS | Good | Ready | Prepared state is ephemeral |
| Persistent Catalog | PASS | Good | Ready | Test isolation |
| Manual Constructor | PARTIAL | Session-only | Ready if no restart | P1 persistence/browser smoke |
| Base Planner | PASS | Strong | Ready | Real data constraints |
| Feasibility | PASS | Strong | Ready | Data role vocabulary |
| Dependencies | PARTIAL | Code rules strong | Conditional | Organizer semantics/data conflict |
| Role allocation | PASS | Strong | Ready | — |
| SP capacity | PASS | Strong | Ready | Float canonicalization |
| Recommendations | PASS | Good in session | Ready | Session persistence |
| Current Draft | PARTIAL | Not restart-safe | Ready in one session | P1 |
| Best Plan | PARTIAL | Stable but not persistent | Ready in one session | P1 |
| Apply / Apply All | PASS | Good in session | Ready | — |
| Approve | PASS | Persistent exact draft | Ready | — |
| Approved Persistence | PASS | Revisioned | Ready | Test isolation / transaction edges |
| Current Plan | PASS | Good | Ready | — |
| Current Sprint | PASS | Backend-authoritative | Ready | — |
| Close Sprint | PASS | Failure-aware | Ready | Browser smoke absent |
| Replanning | PASS | Functional remaining horizon | Ready with targeted tests | Two-step approve metadata |
| Stable Replanning | PARTIAL | Preference-based | Ready for known cases | No formal minimum |
| Replan Diff | PASS | Structured | Ready | — |
| Manager Review | PASS | Explicit | Ready | Raw fallback status |
| Task History | PASS | Snapshot/actual derived | Ready | No draft history |
| Team Page/History | PARTIAL | Planned-data history | Ready | No actual worked hours |
| Engineer Page/History | PARTIAL | Planned-data history | Ready | No actual worked hours |
| Star Map | PASS | Diagnostic | Ready | No task-skill demand data |
| Bus Factor | PASS | Unique active people | Ready | — |
| Archive/Delete | PARTIAL | Soft archive works | Demo with caution | P1 transaction/review |
| Restart Recovery | PARTIAL | Approved/replan safe | Restart after approve safe | Constructor/upload lost |

## 20. Demo-critical path

| Step | Status | Demo risk / workaround |
|---|---|---|
| Start backend | PASS | README install typo can block fresh setup; use established environment |
| Import Excel | PASS | Keep backend alive through preflight/continue |
| Preflight | PASS | Do not restart between upload and Continue |
| Build Base | PASS | Real data yields many infeasible tasks; explain hard-role constraints |
| Preview | PASS | Fractional SP may display rounded/noisy values depending formatter |
| Recommendations | PASS | In-memory session only |
| Apply | PASS | — |
| Approve | PASS | Persists exact Current Draft |
| Current Plan | PASS | — |
| Current Sprint | PASS | Backend chooses sprint |
| Close Sprint | PASS | Targeted tests; browser full path unverified |
| Replan Review | PASS | Actual remains saved on planner failure; Retry exists |
| Approve Replan | PASS | Stale revision guarded |
| Next Current Sprint | PASS | Sprint 6 closes quarter without Sprint 7 |
| Task History | PASS | — |
| Team | PARTIAL | Planned hours only |
| Engineer | PARTIAL | Planned hours only |
| Star Map | PASS | Diagnostic; task-skill demand absent |

## 21. What not to fix before demo

- Do not redesign planner architecture or introduce a new optimizer solely for formal minimum disruption.
- Do not merge `src/replanning` and `src/web/replanning` immediately before the demo.
- Do not replace SQLite with another database.
- Do not split the SPA into a new framework.
- Do not weaken required-role constraints to make the real plan look fuller.
- Do not reinterpret dependency type strings until organizers confirm semantics.
- Do not change Say/Do multi-sprint semantics or KPI initiative denominator; they remain clarification items.
- P3 localization and file-size refactors can wait.

## 22. Prioritized fix plan

### Immediate P0

No confirmed P0 defect in the audited source revision.

### P1 before final

1. Isolate every API/catalog test from working SQLite (**HIGH impact, LOW/MEDIUM blast radius**; test fixtures/repository provider).
2. Restore/remove the eight audit-created snapshots after explicit owner approval (**data hygiene, LOW blast radius when IDs are verified**).
3. Make archive + replacement plan recoverable/atomic and preserve explicit review semantics (**HIGH impact, MEDIUM blast radius**; API/catalog).
4. Persist constructor Current/Best/prepared upload or explicitly disable recovery expectations (**HIGH impact, MEDIUM blast radius**).
5. Correct three contradictory acceptance tests without changing planner semantics (**HIGH confidence, LOW blast radius**).
6. Refresh README and launch instructions (**HIGH demo impact, LOW blast radius**).
7. Add one browser happy-path using a temporary DB (**HIGH demo confidence, LOW source blast radius**).
8. Resolve data role vocabulary with organizers and regenerate the data audit report (**HIGH demo impact, data-only decision**).

### P2 after core stable

- Canonicalize SP numbers at serialization/snapshot boundaries.
- Add explicit disruption score only after current regression set is stable.
- Make replan publish/workflow status atomic.
- Consolidate duplicated replanning/diff ownership.
- Add upload-size limit and stale-report detection.
- Add auth before any shared/nonlocal deployment.

### P3 post-hackathon

- Localize fallback technical labels.
- Split large orchestration/UI files while preserving contracts.

## 23. Final conclusion

The audited revision implements the central business mechanics: hard feasibility, absolute rung, same-rung initiative completion, separate SP/hour capacity, owner/cross-team semantics, dependencies, exact draft approval, persistent Approved history and remaining-horizon replanning with manager review. No confirmed P0 planning-rule violation was found.

The project is suitable for a controlled demo after addressing operational hazards: test isolation and polluted snapshots, stale README, real-data role gaps, constructor state loss and archive transaction/review consistency. It is not yet production-ready for concurrent/shared use because session state, authorization, transactional boundaries and browser-level regression coverage remain incomplete.

The most important semantic uncertainties remain deliberately unchanged: Say/Do treatment of multi-sprint work, quarter KPI initiative denominator clarifications, and organizer-confirmed interpretation of dependency type labels.
