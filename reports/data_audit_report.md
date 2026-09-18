# Dataset Audit Report

Generated: `2026-09-17T15:58:32+03:00`  
Processed dataset: `C:/Users/11211/Desktop/hackaton/data/processed`  
Raw source hash from manifest: `cb71586a7601099a209a2933020fa7d8a649cc9bf1732c815b365b7afb7ac7de`  
Preprocessing version: `1.4.0`

> Detection-only audit: no processed data, planner code, preprocessing code, or business logic was changed. Canonical processed role values and exact skill strings were compared without additional implicit mapping.

## Summary

### General statistics

| Metric | Count |
|---|---:|
| Task rows | 52 |
| Source Tasks rows | 45 |
| Placeholder tasks | 7 |
| Initiatives observed in tasks | 15 |
| Teams (team_history.csv (registry proxy)) | 6 |
| Engineer profile rows | 34 |
| Unique engineers | 30 |
| Role labels in effort matrix | 21 |
| Roles with positive task demand | 20 |
| Engineer role labels | 14 |
| Unique engineer skill labels | 116 |
| Unique task-required skill labels | 0 |
| Dependencies | 16 |
| Tasks fully linkable SP ↔ positive role-hours | 45 |
| Tasks requiring SP/hours fallback | 7 |

### Issue counts by type

A problem count is the number of independently reported occurrences. For grouped role/skill issues, one occurrence is one missing role/skill; affected-task counts are shown separately in the critical/data-quality summaries.
Total occurrences: **94** (27 critical and 67 data-quality). Counts overlap when one source gap causes several downstream checks to fail.

| Issue type | Classification | Problems | Affected tasks |
|---|---|---:|---:|
| `UNRESOLVED_PLACEHOLDER_TASK` | critical | 7 | 7 |
| `DEPENDENCY_REFERENCES_UNKNOWN_TASK` | critical | 0 | 0 |
| `DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK` | critical | 4 | 6 |
| `SELF_DEPENDENCY` | critical | 0 | 0 |
| `DEPENDENCY_CYCLE` | critical | 0 | 0 |
| `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` | critical | 1 | 22 |
| `TASK_WITHOUT_EXISTING_TEAM` | critical | 7 | 7 |
| `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` | critical | 6 | 38 |
| `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` | critical | 1 | 45 |
| `SKILL_WITHOUT_AVAILABLE_ENGINEER` | critical | 0 | 0 |
| `INITIATIVE_DEPENDS_ON_MISSING_TASK` | critical | 1 | 2 |
| `DEPENDENCY_REQUIRED_FIELD_MISSING` | data quality | 0 | 0 |
| `DUPLICATE_DEPENDENCY_EDGE` | data quality | 1 | 2 |
| `TASK_ROLE_EFFORT_UNKNOWN_TASK` | data quality | 0 | 0 |
| `ENGINEER_SKILL_UNKNOWN_ENGINEER` | data quality | 0 | 0 |
| `TASK_SKILL_UNKNOWN_TASK` | data quality | 0 | 0 |
| `TEAM_WITHOUT_ENGINEERS` | critical | 0 | 0 |
| `ENGINEER_WITHOUT_TEAM` | data quality | 0 | 0 |
| `ENGINEER_UNKNOWN_TEAM` | data quality | 0 | 0 |
| `BLANK_TASK_ID` | data quality | 0 | 0 |
| `BLANK_ENGINEER_ID` | data quality | 0 | 0 |
| `DUPLICATE_TASK_ID` | data quality | 0 | 0 |
| `DUPLICATE_ENGINEER_ID` | data quality | 4 | 0 |
| `DUPLICATE_INITIATIVE_ID` | data quality | 0 | 0 |
| `INITIATIVE_REGISTRY_NOT_AVAILABLE` | data quality | 1 | 0 |
| `BLANK_INITIATIVE_ID` | data quality | 7 | 7 |
| `INITIATIVE_WITHOUT_TASKS` | data quality | 0 | 0 |
| `MISSING_STORY_POINTS` | data quality | 7 | 7 |
| `INVALID_STORY_POINTS` | data quality | 0 | 0 |
| `NON_POSITIVE_STORY_POINTS` | data quality | 0 | 0 |
| `MISSING_REQUIRED_HOURS` | data quality | 0 | 0 |
| `INVALID_REQUIRED_HOURS` | data quality | 0 | 0 |
| `NEGATIVE_REQUIRED_HOURS` | data quality | 0 | 0 |
| `STORY_POINTS_WITHOUT_HOURS` | data quality | 0 | 0 |
| `HOURS_WITHOUT_STORY_POINTS` | data quality | 7 | 7 |
| `TASK_HOURS_TOTAL_MISMATCH` | data quality | 4 | 4 |
| `CAPACITY_RATE_NULL` | data quality | 0 | 0 |
| `CAPACITY_RATE_INVALID` | data quality | 0 | 0 |
| `CAPACITY_RATE_NEGATIVE` | data quality | 0 | 0 |
| `CAPACITY_RATE_ABOVE_ONE` | data quality | 0 | 0 |
| `CAPACITY_RATE_UNEXPECTED` | data quality | 0 | 0 |
| `STATUS_REFERENCE_NOT_AVAILABLE` | data quality | 1 | 0 |
| `TASK_STATUS_MISSING` | data quality | 7 | 7 |
| `UNKNOWN_TASK_STATUS` | data quality | 0 | 0 |
| `PLANNED_START_MISSING` | data quality | 7 | 7 |
| `PLANNED_END_MISSING` | data quality | 7 | 7 |
| `TASK_WITHOUT_PLANNED_DATES` | data quality | 7 | 7 |
| `INVALID_PLANNED_DATE` | data quality | 0 | 0 |
| `PLANNED_END_BEFORE_START` | data quality | 0 | 0 |
| `SUSPICIOUS_PLANNED_INTERVAL` | data quality | 1 | 1 |
| `IN_PROGRESS_MISSING_SPENT_TIME` | data quality | 6 | 6 |
| `INVALID_SPENT_TIME` | data quality | 0 | 0 |
| `NEGATIVE_SPENT_TIME` | data quality | 0 | 0 |
| `SPENT_TIME_EXCEEDS_ESTIMATE` | data quality | 0 | 0 |
| `SPENT_TIME_WITHOUT_ESTIMATE` | data quality | 0 | 0 |
| `POSSIBLE_ROLE_SPELLING_VARIANT` | data quality | 0 | 0 |

## Critical issues

| Issue type | Problems | Affected tasks | Affected teams |
|---|---:|---:|---:|
| `UNRESOLVED_PLACEHOLDER_TASK` | 7 | 7 | 0 |
| `DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK` | 4 | 6 | 0 |
| `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` | 1 | 22 | 0 |
| `TASK_WITHOUT_EXISTING_TEAM` | 7 | 7 | 0 |
| `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` | 6 | 38 | 6 |
| `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` | 1 | 45 | 6 |
| `INITIATIVE_DEPENDS_ON_MISSING_TASK` | 1 | 2 | 0 |

### 10 highest-priority concrete problems

| # | Issue | Task IDs | Details |
|---:|---|---|---|
| 1 | `UNRESOLVED_PLACEHOLDER_TASK` | `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019` | 7 occurrences. Highest-impact example: SRV-4013 exists only as a processed placeholder and is absent from the source Tasks block. |
| 2 | `DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK` | `SRV-4011`, `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4019` | 4 occurrences. Highest-impact example: Dependency type='depends on': SRV-4015 -> SRV-4016; source-missing placeholder endpoints: SRV-4015, SRV-4016. |
| 3 | `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` | `AI-301`, `AI-302`, `ANL-3041`, `ASUKD-5222`, `DB-201`, `DB-202`, `DB-203`, `KP-3898`, `MOB-7011`, `MOB-7012`, … (+12) | Processed data preserves blocking_task_id -> blocked_task_id, but organizer semantics are not independently confirmed for types: depends on, has to be done before, is required for. |
| 4 | `TASK_WITHOUT_EXISTING_TEAM` | `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019` | 7 occurrences. Highest-impact example: Task SRV-4013 has blank team_id: <blank>. |
| 5 | `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` | `ANL-3042`, `ASUKD-5222`, `ASUKD-5918`, `DB-201`, `KP-3898`, `ONK-2475`, `ONK-2914`, `SRV-4010`, `SRV-4011`, `SRV-4012`, … (+28) | 6 occurrences. Highest-impact example: Role Руководитель проекта requires 680 hours across 36 tasks, but has 0 exact-match engineers. |
| 6 | `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` | `AI-301`, `AI-302`, `AI-303`, `ANL-3041`, `ANL-3042`, `ASUKD-5222`, `ASUKD-5918`, `DB-201`, `DB-202`, `DB-203`, … (+35) | No task_skills.csv and no explicit task skill/technology column exist in the processed dataset. |
| 7 | `INITIATIVE_DEPENDS_ON_MISSING_TASK` | `SRV-4011`, `SRV-4019` | Initiative PRODF-7126 touches dependency has to be done before: SRV-4011 -> SRV-4019, which includes a source-missing placeholder. |
| 8 | `DUPLICATE_DEPENDENCY_EDGE` | `SRV-4015`, `SRV-4016` | Dependency edge SRV-4015 -> SRV-4016 occurs 2 times with types: depends on, is required for. |
| 9 | `BLANK_INITIATIVE_ID` | `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019` | 7 occurrences. Highest-impact example: Task SRV-4013 has blank initiative_id. |
| 10 | `MISSING_STORY_POINTS` | `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019` | 7 occurrences. Highest-impact example: Task SRV-4013 has missing story_points. |

## Data quality issues

| Issue type | Problems | Affected tasks | Affected teams |
|---|---:|---:|---:|
| `DUPLICATE_DEPENDENCY_EDGE` | 1 | 2 | 0 |
| `DUPLICATE_ENGINEER_ID` | 4 | 0 | 6 |
| `INITIATIVE_REGISTRY_NOT_AVAILABLE` | 1 | 0 | 0 |
| `BLANK_INITIATIVE_ID` | 7 | 7 | 0 |
| `MISSING_STORY_POINTS` | 7 | 7 | 0 |
| `HOURS_WITHOUT_STORY_POINTS` | 7 | 7 | 0 |
| `TASK_HOURS_TOTAL_MISMATCH` | 4 | 4 | 3 |
| `STATUS_REFERENCE_NOT_AVAILABLE` | 1 | 0 | 0 |
| `TASK_STATUS_MISSING` | 7 | 7 | 0 |
| `PLANNED_START_MISSING` | 7 | 7 | 0 |
| `PLANNED_END_MISSING` | 7 | 7 | 0 |
| `TASK_WITHOUT_PLANNED_DATES` | 7 | 7 | 0 |
| `SUSPICIOUS_PLANNED_INTERVAL` | 1 | 1 | 0 |
| `IN_PROGRESS_MISSING_SPENT_TIME` | 6 | 6 | 5 |

## Planning impact

- `UNRESOLVED_PLACEHOLDER_TASK` (7 problems, 7 affected tasks): The planner has effort or dependency information but no business attributes, SP, team, status, or dates for the task.
- `DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK` (4 problems, 6 affected tasks): The dependency is formally connected to a processed placeholder, but the source task is missing, so readiness and completion cannot be established.
- `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` (1 problem, 22 affected tasks): A wrong interpretation of blocking_task_id and blocked_task_id reverses prerequisite order and invalidates the plan.
- `TASK_WITHOUT_EXISTING_TEAM` (7 problems, 7 affected tasks): The task has no team capacity, velocity, or eligible engineer pool and therefore cannot be scheduled reliably.
- `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` (6 problems, 38 affected tasks): Tasks require positive hours for a role with zero exact-match engineers, making their role-hours infeasible.
- `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` (1 problem, 45 affected tasks): Technology fit cannot be checked, so a plan may assign engineers who do not have required competencies.
- `INITIATIVE_DEPENDS_ON_MISSING_TASK` (1 problem, 2 affected tasks): The initiative touches a dependency whose other endpoint is a source-missing placeholder, so completion cannot be certified.
- `DUPLICATE_DEPENDENCY_EDGE` (1 problem, 2 affected tasks): Repeated edges can double-count constraints or encode contradictory relationship types.
- `DUPLICATE_ENGINEER_ID` (4 problems): Repeated engineer IDs may be intentional multi-team assignments, but naïve aggregation can duplicate personal capacity.
- `INITIATIVE_REGISTRY_NOT_AVAILABLE` (1 problem): Initiatives without tasks and duplicate initiative entities cannot be detected from task foreign keys alone.
- `BLANK_INITIATIVE_ID` (7 problems, 7 affected tasks): The task cannot contribute to initiative completion or PI Predictability.
- `MISSING_STORY_POINTS` (7 problems, 7 affected tasks): Team SP capacity and prioritization cannot be evaluated for the task.
- `HOURS_WITHOUT_STORY_POINTS` (7 problems, 7 affected tasks): Role-hours exist, but the task cannot be constrained by team velocity or included reliably in SP s.
- `TASK_HOURS_TOTAL_MISMATCH` (4 problems, 4 affected tasks): Task estimated_hh and the sum of role-hours imply different workload totals.
- `STATUS_REFERENCE_NOT_AVAILABLE` (1 problem): Unknown statuses cannot be distinguished from valid source statuses without an independent catalog.
- `TASK_STATUS_MISSING` (7 problems, 7 affected tasks): The planner cannot decide whether the task is eligible, completed, or already in progress.
- `PLANNED_START_MISSING` (7 problems, 7 affected tasks): Baseline schedule comparisons and date-quality checks are incomplete.
- `PLANNED_END_MISSING` (7 problems, 7 affected tasks): Deadline and interval analysis are incomplete.
- `TASK_WITHOUT_PLANNED_DATES` (7 problems, 7 affected tasks): The task has no usable baseline interval for schedule-risk reporting.
- `SUSPICIOUS_PLANNED_INTERVAL` (1 problem, 1 affected task): The baseline interval exceeds the 12-week planning horizon and may distort deadline-risk interpretation.
- `IN_PROGRESS_MISSING_SPENT_TIME` (6 problems, 6 affected tasks): Remaining effort for active work cannot be calculated from actual consumption.

## Recommendations

- `UNRESOLVED_PLACEHOLDER_TASK`: Ask the organizers for the missing Tasks rows; until clarified, keep these tasks unplannable placeholders.
- `DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK`: Ask the organizers for the missing task record and treat the dependency as unresolved until then.
- `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED`: Ask the organizers to confirm the direction of every dependency_type before using dates or sprint order as hard constraints.
- `TASK_WITHOUT_EXISTING_TEAM`: Ask for the correct team; until then, treat the task as unplannable rather than assigning a default team.
- `NO_AVAILABLE_SPECIALISTS_FOR_ROLE`: Confirm role naming or staffing; if no mapping or specialist exists, mark affected tasks infeasible.
- `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE`: Ask for task-level skill requirements or a documented role/task-to-skill mapping; do not infer skills from summaries.
- `INITIATIVE_DEPENDS_ON_MISSING_TASK`: Ask which initiative owns the placeholder and restore the missing task record before assessing initiative completion.
- `DUPLICATE_DEPENDENCY_EDGE`: Confirm whether repeated edges are intentional and choose one documented semantic representation.
- `DUPLICATE_ENGINEER_ID`: Treat repeated IDs as one person and confirm that capacity_rate is total personal capacity, not capacity per row.
- `INITIATIVE_REGISTRY_NOT_AVAILABLE`: Ask for an authoritative initiative registry; do not treat repeated task initiative_id values as duplicate initiatives.
- `BLANK_INITIATIVE_ID`: Ask for the owning PRODF initiative or explicitly classify the task as initiative-independent.
- `MISSING_STORY_POINTS`: Request an SP estimate or route the task through an explicit, documented fallback.
- `HOURS_WITHOUT_STORY_POINTS`: Request SP; until then, keep the task out of normal SP-based planning or use a visible fallback.
- `TASK_HOURS_TOTAL_MISMATCH`: Ask which estimate is authoritative; do not scale role-hours automatically.
- `STATUS_REFERENCE_NOT_AVAILABLE`: Ask for the official task-status catalog and add it to processed data.
- `TASK_STATUS_MISSING`: Request a status; do not assume ToDo.
- `PLANNED_START_MISSING`: Request the planned start or keep it explicitly unknown; do not use dates as hard constraints.
- `PLANNED_END_MISSING`: Request the planned end or keep it explicitly unknown; do not use dates as hard constraints.
- `TASK_WITHOUT_PLANNED_DATES`: Ask for both dates if baseline comparison is required.
- `SUSPICIOUS_PLANNED_INTERVAL`: Confirm whether the interval intentionally spans quarters; keep it informational rather than a hard constraint.
- `IN_PROGRESS_MISSING_SPENT_TIME`: Request spent_time or define an explicit remaining-effort fallback.

### Questions requiring organizer clarification

- `UNRESOLVED_PLACEHOLDER_TASK` — Ask the organizers for the missing Tasks rows; until clarified, keep these tasks unplannable placeholders.
- `DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK` — Ask the organizers for the missing task record and treat the dependency as unresolved until then.
- `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` — Ask the organizers to confirm the direction of every dependency_type before using dates or sprint order as hard constraints.
- `TASK_WITHOUT_EXISTING_TEAM` — Ask for the correct team; until then, treat the task as unplannable rather than assigning a default team.
- `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` — Confirm role naming or staffing; if no mapping or specialist exists, mark affected tasks infeasible.
- `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` — Ask for task-level skill requirements or a documented role/task-to-skill mapping; do not infer skills from summaries.
- `INITIATIVE_DEPENDS_ON_MISSING_TASK` — Ask which initiative owns the placeholder and restore the missing task record before assessing initiative completion.
- `DUPLICATE_DEPENDENCY_EDGE` — Confirm whether repeated edges are intentional and choose one documented semantic representation.
- `DUPLICATE_ENGINEER_ID` — Treat repeated IDs as one person and confirm that capacity_rate is total personal capacity, not capacity per row.
- `INITIATIVE_REGISTRY_NOT_AVAILABLE` — Ask for an authoritative initiative registry; do not treat repeated task initiative_id values as duplicate initiatives.
- `BLANK_INITIATIVE_ID` — Ask for the owning PRODF initiative or explicitly classify the task as initiative-independent.
- `MISSING_STORY_POINTS` — Request an SP estimate or route the task through an explicit, documented fallback.
- `HOURS_WITHOUT_STORY_POINTS` — Request SP; until then, keep the task out of normal SP-based planning or use a visible fallback.
- `TASK_HOURS_TOTAL_MISMATCH` — Ask which estimate is authoritative; do not scale role-hours automatically.
- `STATUS_REFERENCE_NOT_AVAILABLE` — Ask for the official task-status catalog and add it to processed data.
- `TASK_STATUS_MISSING` — Request a status; do not assume ToDo.
- `IN_PROGRESS_MISSING_SPENT_TIME` — Request spent_time or define an explicit remaining-effort fallback.

## Detailed checks

### 1. Roles and specialists

Exact string matching found the following required roles with zero engineers:

| Role | Required hours | Tasks | Task IDs | Teams | Available engineers |
|---|---:|---:|---|---|---:|
| Аналитик 1С | 10 | 1 | `ANL-3042` | `Team-L` | 0 |
| Архитектор 1С | 35 | 2 | `DB-201`, `SRV-4082` | `Team-Alpha`, `Team-Platform` | 0 |
| Разработчик 1С | 107 | 2 | `ANL-3042`, `SRV-4082` | `Team-Alpha`, `Team-L` | 0 |
| Руководитель проекта | 680 | 36 | `ASUKD-5222`, `ASUKD-5918`, `KP-3898`, `ONK-2475`, `ONK-2914`, `SRV-4010`, `SRV-4011`, `SRV-4012`, `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019`, `SRV-4021`, `SRV-4022`, `SRV-4023`, `SRV-4031`, `SRV-4032`, `SRV-4033`, `SRV-4041`, `SRV-4042`, `SRV-4043`, `SRV-4051`, `SRV-4052`, `SRV-4061`, `SRV-4062`, `SRV-4063`, `SRV-4071`, `SRV-4072`, `SRV-4081`, `SRV-4082`, `SRV-4091`, `SRV-4092`, `SRV-4093` | `Team-Alpha`, `Team-Beta`, `Team-Delta`, `Team-K`, `Team-L`, `Team-Platform` | 0 |
| Специалист поддержки | 127 | 15 | `ASUKD-5222`, `ASUKD-5918`, `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4018`, `SRV-4021`, `SRV-4031`, `SRV-4032`, `SRV-4042`, `SRV-4052`, `SRV-4072`, `SRV-4091`, `SRV-4092` | `Team-Alpha`, `Team-Beta`, `Team-Delta`, `Team-K` | 0 |
| Специалист поддержки 1С | 12 | 2 | `ANL-3042`, `SRV-4082` | `Team-Alpha`, `Team-L` | 0 |

### 2. Skills / competencies

Task skill source: `not available`.
Engineer profiles declare 116 unique exact skill labels.
Task-level skill requirements are absent, so missing-skill and team skill-deficit checks are not computable. Roles and task summaries were not treated as skills.

### 3. Dependencies

Dependencies checked: 16.
Dependency types observed: `depends on`, `has to be done before`, `is required for`.
Cycles found: 0; self-dependencies: 0; duplicate directed edges: 1.

| dependency_type | blocking_task_id | blocked_task_id | Issue |
|---|---|---|---|
| is required for | `SRV-4013` | `SRV-4014` | DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK |
| depends on | `SRV-4015` | `SRV-4016` | DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK |
| has to be done before | `SRV-4011` | `SRV-4019` | DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK |
| is required for | `SRV-4015` | `SRV-4016` | DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK |

Repeated directed edges:

- `SRV-4015` → `SRV-4016` occurs 2 times; types: depends on, is required for.

### 4. Teams

Team registry source: `team_history.csv (registry proxy)`; known teams: `Team-Alpha`, `Team-Beta`, `Team-Delta`, `Team-K`, `Team-L`, `Team-Platform`.

| Task ID | team_id | Problem |
|---|---|---|
| `SRV-4013` | `<blank>` | blank team_id |
| `SRV-4014` | `<blank>` | blank team_id |
| `SRV-4015` | `<blank>` | blank team_id |
| `SRV-4016` | `<blank>` | blank team_id |
| `SRV-4017` | `<blank>` | blank team_id |
| `SRV-4018` | `<blank>` | blank team_id |
| `SRV-4019` | `<blank>` | blank team_id |
Teams without engineers: 0; engineer rows without a team: 0.

### 5. Task IDs and duplicates

Duplicate task_id values: 0.
Duplicate engineer_id values: 4: `ENG-405`, `ENG-406`, `ENG-419`, `ENG-426`.
Initiative registry: `not available`. Duplicate initiative entities are not inferred from repeated task foreign keys.
Blank task_id problems: 0; blank engineer_id problems: 0.

### 6. Story points and hours

Tasks fully linkable SP ↔ positive role-hours: **45**.
Tasks requiring fallback: **7**: `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019`.
Tasks with positive SP but without positive role-hours: 0: —.
Tasks with positive role-hours but without positive SP: 7: `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019`.
Missing required_hours cells: 0; negative required_hours: 0.

Task-level estimated_hh versus role-hour totals:

| Task ID | estimated_hh | Sum role-hours | Delta (role − task) |
|---|---:|---:|---:|
| `ONK-2475` | 276 | 256 | -20 |
| `MOB-7011` | 180 | 165 | -15 |
| `DB-203` | 80 | 72 | -8 |
| `SRV-4012` | 240 | 270 | 30 |

### 7. Capacity rate

Allowed values: `0`, `0.5`, `1`.

| Value | Engineer rows |
|---|---:|
| `0.5` | 8 |
| `1` | 26 |
NULL: 0; negative: 0; above 1: 0; unexpected allowed-range values: 0.

### 8. Task statuses

Official status reference source: `not available`.

| Status | Tasks |
|---|---:|
| `<blank>` | 7 |
| `Done` | 8 |
| `InProgress` | 6 |
| `ToDo` | 31 |
Unknown-status validation is not claimable because no independent status catalog exists in processed data.

### 9. Initiative / PRODF

Initiative registry source: `not available`.

| Initiative | Tasks |
|---|---:|
| `PRODF-7120` | 6 |
| `PRODF-7121` | 3 |
| `PRODF-7122` | 3 |
| `PRODF-7123` | 3 |
| `PRODF-7124` | 3 |
| `PRODF-7125` | 3 |
| `PRODF-7126` | 3 |
| `PRODF-7127` | 3 |
| `PRODF-7128` | 3 |
| `PRODF-7129` | 3 |
| `PRODF-7130` | 2 |
| `PRODF-7131` | 3 |
| `PRODF-7132` | 2 |
| `PRODF-7133` | 2 |
| `PRODF-7134` | 3 |
Tasks without initiative: 7: `SRV-4013`, `SRV-4014`, `SRV-4015`, `SRV-4016`, `SRV-4017`, `SRV-4018`, `SRV-4019`.
Initiatives without tasks cannot be detected without a separate registry. Repeated PRODF values in tasks are expected one-to-many links, not duplicate initiative records.

Initiatives whose completion cannot be certified because a dependency includes a source-missing task:

- `PRODF-7126`: `SRV-4011` → `SRV-4019` (has to be done before).

### 10. Planned dates

A suspicious interval is defined for this audit as more than 84 calendar days (the documented 12-week quarter). Dates remain informational and are not used as planning constraints.
Missing planned_start: 7; missing planned_end: 7; tasks missing both: 7.
End before start: 0; suspiciously long intervals: 1.

| Task ID | planned_start | planned_end | Issues |
|---|---|---|---|
| `KP-0000` | 2026-03-16 17:49:00 | 2026-06-18 17:23:00 | SUSPICIOUS_PLANNED_INTERVAL |
| `SRV-4013` | <blank> | <blank> | PLANNED_START_MISSING, PLANNED_END_MISSING, TASK_WITHOUT_PLANNED_DATES |
| `SRV-4014` | <blank> | <blank> | PLANNED_START_MISSING, PLANNED_END_MISSING, TASK_WITHOUT_PLANNED_DATES |
| `SRV-4015` | <blank> | <blank> | PLANNED_START_MISSING, PLANNED_END_MISSING, TASK_WITHOUT_PLANNED_DATES |
| `SRV-4016` | <blank> | <blank> | PLANNED_START_MISSING, PLANNED_END_MISSING, TASK_WITHOUT_PLANNED_DATES |
| `SRV-4017` | <blank> | <blank> | PLANNED_START_MISSING, PLANNED_END_MISSING, TASK_WITHOUT_PLANNED_DATES |
| `SRV-4018` | <blank> | <blank> | PLANNED_START_MISSING, PLANNED_END_MISSING, TASK_WITHOUT_PLANNED_DATES |
| `SRV-4019` | <blank> | <blank> | PLANNED_START_MISSING, PLANNED_END_MISSING, TASK_WITHOUT_PLANNED_DATES |

### 11. InProgress and spent time

InProgress tasks: 6.

| Task ID | Team | spent_time | estimated_hh | Issues |
|---|---|---:|---:|---|
| `KP-3898` | `Team-Platform` | <blank> | 194 | IN_PROGRESS_MISSING_SPENT_TIME |
| `MOB-7011` | `Team-L` | <blank> | 180 | IN_PROGRESS_MISSING_SPENT_TIME |
| `MP-102` | `Team-K` | <blank> | 186 | IN_PROGRESS_MISSING_SPENT_TIME |
| `DB-202` | `Team-Platform` | <blank> | 324 | IN_PROGRESS_MISSING_SPENT_TIME |
| `SRV-4011` | `Team-Alpha` | <blank> | 160 | IN_PROGRESS_MISSING_SPENT_TIME |
| `SRV-4041` | `Team-Delta` | <blank> | 220 | IN_PROGRESS_MISSING_SPENT_TIME |
spent_time > estimated_hh: 0; spent_time without estimated_hh: 0.

### 12. Overall statistics

Counts are reported in the Summary. Unique engineers are counted by engineer_id; engineer profile rows are also shown because multi-team assignments repeat IDs. Role and skill counts use exact source labels.
