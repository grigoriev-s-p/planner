# Dataset Audit Report

Generated: `2026-09-18T21:54:19+03:00`  
Processed dataset: `C:/Users/nikol/OneDrive/Desktop/planirovanie-komand-i-zvyozdnaya-karta/data/processed`  
Raw source hash from manifest: `a618cb80279e9931e7706166082d8322fafd41aefefea722e44d9116a1f2f22e`  
Preprocessing version: `1.4.0`

> Detection-only audit: no processed data, planner code, preprocessing code, or business logic was changed. Canonical processed role values and exact skill strings were compared without additional implicit mapping.

## Summary

### General statistics

| Metric | Count |
|---|---:|
| Task rows | 45 |
| Source Tasks rows | 45 |
| Placeholder tasks | 0 |
| Initiatives observed in tasks | 15 |
| Teams (team_history.csv (registry proxy)) | 6 |
| Engineer profile rows | 34 |
| Unique engineers | 30 |
| Role labels in effort matrix | 21 |
| Roles with positive task demand | 20 |
| Engineer role labels | 14 |
| Unique engineer skill labels | 116 |
| Unique task-required skill labels | 0 |
| Dependencies | 19 |
| Tasks fully linkable SP ↔ positive role-hours | 45 |
| Tasks requiring SP/hours fallback | 0 |

### Issue counts by type

A problem count is the number of independently reported occurrences. For grouped role/skill issues, one occurrence is one missing role/skill; affected-task counts are shown separately in the critical/data-quality summaries.
Total occurrences: **46** (8 critical and 38 data-quality). Counts overlap when one source gap causes several downstream checks to fail.

| Issue type | Classification | Problems | Affected tasks |
|---|---|---:|---:|
| `UNRESOLVED_PLACEHOLDER_TASK` | critical | 0 | 0 |
| `DEPENDENCY_REFERENCES_UNKNOWN_TASK` | critical | 0 | 0 |
| `DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK` | critical | 0 | 0 |
| `SELF_DEPENDENCY` | critical | 0 | 0 |
| `DEPENDENCY_CYCLE` | critical | 0 | 0 |
| `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` | critical | 1 | 33 |
| `DONE_DEPENDENCY_CONFLICT` | data quality | 1 | 2 |
| `TASK_WITHOUT_EXISTING_TEAM` | critical | 0 | 0 |
| `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` | critical | 6 | 31 |
| `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` | critical | 1 | 45 |
| `SKILL_WITHOUT_AVAILABLE_ENGINEER` | critical | 0 | 0 |
| `INITIATIVE_DEPENDS_ON_MISSING_TASK` | critical | 0 | 0 |
| `DEPENDENCY_REQUIRED_FIELD_MISSING` | data quality | 0 | 0 |
| `DUPLICATE_DEPENDENCY_EDGE` | data quality | 0 | 0 |
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
| `BLANK_INITIATIVE_ID` | data quality | 0 | 0 |
| `INITIATIVE_WITHOUT_TASKS` | data quality | 0 | 0 |
| `MISSING_STORY_POINTS` | data quality | 0 | 0 |
| `INVALID_STORY_POINTS` | data quality | 0 | 0 |
| `NON_POSITIVE_STORY_POINTS` | data quality | 0 | 0 |
| `MISSING_REQUIRED_HOURS` | data quality | 0 | 0 |
| `INVALID_REQUIRED_HOURS` | data quality | 0 | 0 |
| `NEGATIVE_REQUIRED_HOURS` | data quality | 0 | 0 |
| `STORY_POINTS_WITHOUT_HOURS` | data quality | 0 | 0 |
| `HOURS_WITHOUT_STORY_POINTS` | data quality | 0 | 0 |
| `TASK_HOURS_TOTAL_MISMATCH` | data quality | 24 | 24 |
| `CAPACITY_RATE_NULL` | data quality | 0 | 0 |
| `CAPACITY_RATE_INVALID` | data quality | 0 | 0 |
| `CAPACITY_RATE_NEGATIVE` | data quality | 0 | 0 |
| `CAPACITY_RATE_ABOVE_ONE` | data quality | 0 | 0 |
| `CAPACITY_RATE_UNEXPECTED` | data quality | 0 | 0 |
| `STATUS_REFERENCE_NOT_AVAILABLE` | data quality | 1 | 0 |
| `TASK_STATUS_MISSING` | data quality | 0 | 0 |
| `UNKNOWN_TASK_STATUS` | data quality | 0 | 0 |
| `PLANNED_START_MISSING` | data quality | 0 | 0 |
| `PLANNED_END_MISSING` | data quality | 0 | 0 |
| `TASK_WITHOUT_PLANNED_DATES` | data quality | 0 | 0 |
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
| `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` | 1 | 33 | 0 |
| `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` | 6 | 31 | 6 |
| `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` | 1 | 45 | 6 |

### 10 highest-priority concrete problems

| # | Issue | Task IDs | Details |
|---:|---|---|---|
| 1 | `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` | `AI-301`, `AI-302`, `ANL-3041`, `ASUKD-5222`, `DB-201`, `DB-203`, `KP-3898`, `MOB-7011`, `MOB-7012`, `MP-101`, … (+23) | Processed data preserves blocking_task_id -> blocked_task_id, but organizer semantics are not independently confirmed for types: depends on, has to be done before, is required for. |
| 2 | `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` | `AI-303`, `ANL-3042`, `ASUKD-5222`, `ASUKD-5918`, `DB-201`, `KP-3898`, `MOB-7013`, `ONK-2475`, `ONK-2914`, `SRV-4010`, … (+21) | 6 occurrences. Highest-impact example: Role Руководитель проекта requires 575 hours across 29 tasks, but has 0 exact-match engineers. |
| 3 | `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` | `AI-301`, `AI-302`, `AI-303`, `ANL-3041`, `ANL-3042`, `ASUKD-5222`, `ASUKD-5918`, `DB-201`, `DB-202`, `DB-203`, … (+35) | No task_skills.csv and no explicit task skill/technology column exist in the processed dataset. |
| 4 | `DONE_DEPENDENCY_CONFLICT` | `ASUKD-5222`, `KP-3898` | Done task ASUKD-5222 is recorded as blocked by KP-3898, whose status is InProgress. |
| 5 | `TASK_HOURS_TOTAL_MISMATCH` | `AI-303`, `ANL-3042`, `DB-203`, `MOB-7011`, `MOB-7013`, `ONK-2475`, `SRV-4021`, `SRV-4022`, `SRV-4023`, `SRV-4031`, … (+14) | 24 occurrences. Highest-impact example: Task AI-303: estimated_hh=100, sum of positive role-hours=155, delta(role-total minus task)=55. |
| 6 | `SUSPICIOUS_PLANNED_INTERVAL` | `KP-0000` | Task KP-0000: planned interval is 94.0 days, above the 84-day threshold. |
| 7 | `IN_PROGRESS_MISSING_SPENT_TIME` | `DB-202`, `KP-3898`, `MOB-7011`, `MP-102`, `SRV-4011`, `SRV-4041` | 6 occurrences. Highest-impact example: InProgress task DB-202 has missing spent_time; estimated_hh=324. |

## Data quality issues

| Issue type | Problems | Affected tasks | Affected teams |
|---|---:|---:|---:|
| `DONE_DEPENDENCY_CONFLICT` | 1 | 2 | 0 |
| `DUPLICATE_ENGINEER_ID` | 4 | 0 | 6 |
| `INITIATIVE_REGISTRY_NOT_AVAILABLE` | 1 | 0 | 0 |
| `TASK_HOURS_TOTAL_MISMATCH` | 24 | 24 | 6 |
| `STATUS_REFERENCE_NOT_AVAILABLE` | 1 | 0 | 0 |
| `SUSPICIOUS_PLANNED_INTERVAL` | 1 | 1 | 0 |
| `IN_PROGRESS_MISSING_SPENT_TIME` | 6 | 6 | 5 |

## Planning impact

- `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` (1 problem, 33 affected tasks): A wrong interpretation of blocking_task_id and blocked_task_id reverses prerequisite order and invalidates the plan.
- `DONE_DEPENDENCY_CONFLICT` (1 problem, 2 affected tasks): A source task is already Done although its recorded blocker is not Done; the factual status and dependency graph disagree.
- `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` (6 problems, 31 affected tasks): Tasks require positive hours for a role with zero exact-match engineers, making their role-hours infeasible.
- `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` (1 problem, 45 affected tasks): Technology fit cannot be checked, so a plan may assign engineers who do not have required competencies.
- `DUPLICATE_ENGINEER_ID` (4 problems): Repeated engineer IDs identify one person with separate team-specific capacity buckets.
- `INITIATIVE_REGISTRY_NOT_AVAILABLE` (1 problem): Initiatives without tasks and duplicate initiative entities cannot be detected from task foreign keys alone.
- `TASK_HOURS_TOTAL_MISMATCH` (24 problems, 24 affected tasks): Task estimated_hh and the sum of role-hours imply different workload totals.
- `STATUS_REFERENCE_NOT_AVAILABLE` (1 problem): Unknown statuses cannot be distinguished from valid source statuses without an independent catalog.
- `SUSPICIOUS_PLANNED_INTERVAL` (1 problem, 1 affected task): The baseline interval exceeds the 12-week planning horizon and may distort deadline-risk interpretation.
- `IN_PROGRESS_MISSING_SPENT_TIME` (6 problems, 6 affected tasks): Remaining effort for active work cannot be calculated from actual consumption.

## Recommendations

- `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED`: Ask the organizers to confirm the direction of every dependency_type before using dates or sprint order as hard constraints.
- `DONE_DEPENDENCY_CONFLICT`: Keep the task completed for planning and ask the organizers to correct or explain the dependency.
- `NO_AVAILABLE_SPECIALISTS_FOR_ROLE`: Confirm role naming or staffing; if no mapping or specialist exists, mark affected tasks infeasible.
- `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE`: Ask for task-level skill requirements or a documented role/task-to-skill mapping; do not infer skills from summaries.
- `DUPLICATE_ENGINEER_ID`: Keep each unique engineer-team bucket and deduplicate only repeated rows of the same pair.
- `INITIATIVE_REGISTRY_NOT_AVAILABLE`: Ask for an authoritative initiative registry; do not treat repeated task initiative_id values as duplicate initiatives.
- `TASK_HOURS_TOTAL_MISMATCH`: Ask which estimate is authoritative; do not scale role-hours automatically.
- `STATUS_REFERENCE_NOT_AVAILABLE`: Ask for the official task-status catalog and add it to processed data.
- `SUSPICIOUS_PLANNED_INTERVAL`: Confirm whether the interval intentionally spans quarters; keep it informational rather than a hard constraint.
- `IN_PROGRESS_MISSING_SPENT_TIME`: Request spent_time or define an explicit remaining-effort fallback.

### Questions requiring organizer clarification

- `DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED` — Ask the organizers to confirm the direction of every dependency_type before using dates or sprint order as hard constraints.
- `DONE_DEPENDENCY_CONFLICT` — Keep the task completed for planning and ask the organizers to correct or explain the dependency.
- `NO_AVAILABLE_SPECIALISTS_FOR_ROLE` — Confirm role naming or staffing; if no mapping or specialist exists, mark affected tasks infeasible.
- `TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE` — Ask for task-level skill requirements or a documented role/task-to-skill mapping; do not infer skills from summaries.
- `DUPLICATE_ENGINEER_ID` — Keep each unique engineer-team bucket and deduplicate only repeated rows of the same pair.
- `INITIATIVE_REGISTRY_NOT_AVAILABLE` — Ask for an authoritative initiative registry; do not treat repeated task initiative_id values as duplicate initiatives.
- `TASK_HOURS_TOTAL_MISMATCH` — Ask which estimate is authoritative; do not scale role-hours automatically.
- `STATUS_REFERENCE_NOT_AVAILABLE` — Ask for the official task-status catalog and add it to processed data.
- `IN_PROGRESS_MISSING_SPENT_TIME` — Request spent_time or define an explicit remaining-effort fallback.

## Detailed checks

### 1. Roles and specialists

Exact string matching found the following required roles with zero engineers:

| Role | Required hours | Tasks | Task IDs | Teams | Available engineers |
|---|---:|---:|---|---|---:|
| Аналитик 1С | 10 | 1 | `SRV-4043` | `Team-Delta` | 0 |
| Архитектор 1С | 35 | 2 | `DB-201`, `SRV-4091` | `Team-Alpha`, `Team-Platform` | 0 |
| Разработчик 1С | 107 | 2 | `SRV-4043`, `SRV-4091` | `Team-Alpha`, `Team-Delta` | 0 |
| Руководитель проекта | 575 | 29 | `AI-303`, `ANL-3042`, `ASUKD-5222`, `ASUKD-5918`, `KP-3898`, `MOB-7013`, `ONK-2475`, `ONK-2914`, `SRV-4010`, `SRV-4011`, `SRV-4012`, `SRV-4021`, `SRV-4022`, `SRV-4023`, `SRV-4031`, `SRV-4032`, `SRV-4033`, `SRV-4041`, `SRV-4052`, `SRV-4061`, `SRV-4062`, `SRV-4063`, `SRV-4071`, `SRV-4072`, `SRV-4081`, `SRV-4082`, `SRV-4091`, `SRV-4092`, `SRV-4093` | `Team-Alpha`, `Team-Beta`, `Team-Delta`, `Team-K`, `Team-L`, `Team-Platform` | 0 |
| Специалист поддержки | 107 | 13 | `ASUKD-5222`, `ASUKD-5918`, `SRV-4010`, `SRV-4021`, `SRV-4022`, `SRV-4023`, `SRV-4032`, `SRV-4061`, `SRV-4071`, `SRV-4072`, `SRV-4082`, `SRV-4091`, `SRV-4092` | `Team-Alpha`, `Team-Beta`, `Team-K`, `Team-Platform` | 0 |
| Специалист поддержки 1С | 12 | 2 | `MOB-7013`, `SRV-4043` | `Team-Delta`, `Team-L` | 0 |

### 2. Skills / competencies

Task skill source: `not available`.
Engineer profiles declare 116 unique exact skill labels.
Task-level skill requirements are absent, so missing-skill and team skill-deficit checks are not computable. Roles and task summaries were not treated as skills.

### 3. Dependencies

Dependencies checked: 19.
Dependency types observed: `depends on`, `has to be done before`, `is required for`.
Cycles found: 0; self-dependencies: 0; duplicate directed edges: 0.

No missing endpoints, placeholders, blank required fields, or self-dependencies were found.

### 4. Teams

Team registry source: `team_history.csv (registry proxy)`; known teams: `Team-Alpha`, `Team-Beta`, `Team-Delta`, `Team-K`, `Team-L`, `Team-Platform`.
All tasks reference a known team.
Teams without engineers: 0; engineer rows without a team: 0.

### 5. Task IDs and duplicates

Duplicate task_id values: 0.
Duplicate engineer_id values: 4: `ENG-405`, `ENG-406`, `ENG-419`, `ENG-426`.
Initiative registry: `not available`. Duplicate initiative entities are not inferred from repeated task foreign keys.
Blank task_id problems: 0; blank engineer_id problems: 0.

### 6. Story points and hours

Tasks fully linkable SP ↔ positive role-hours: **45**.
Tasks requiring fallback: **0**: —.
Tasks with positive SP but without positive role-hours: 0: —.
Tasks with positive role-hours but without positive SP: 0: —.
Missing required_hours cells: 0; negative required_hours: 0.

Task-level estimated_hh versus role-hour totals:

| Task ID | estimated_hh | Sum role-hours | Delta (role − task) |
|---|---:|---:|---:|
| `ONK-2475` | 276 | 256 | -20 |
| `MOB-7011` | 180 | 165 | -15 |
| `MOB-7013` | 120 | 110 | -10 |
| `ANL-3042` | 90 | 135 | 45 |
| `DB-203` | 80 | 72 | -8 |
| `AI-303` | 100 | 155 | 55 |
| `SRV-4021` | 110 | 380 | 270 |
| `SRV-4022` | 130 | 220 | 90 |
| `SRV-4023` | 70 | 165 | 95 |
| `SRV-4031` | 340 | 90 | -250 |
| `SRV-4033` | 120 | 140 | 20 |
| `SRV-4042` | 150 | 120 | -30 |
| `SRV-4051` | 130 | 100 | -30 |
| `SRV-4052` | 160 | 110 | -50 |
| `SRV-4061` | 90 | 110 | 20 |
| `SRV-4062` | 140 | 130 | -10 |
| `SRV-4063` | 100 | 70 | -30 |
| `SRV-4071` | 180 | 340 | 160 |
| `SRV-4072` | 210 | 180 | -30 |
| `SRV-4081` | 140 | 120 | -20 |
| `SRV-4082` | 190 | 150 | -40 |
| `SRV-4091` | 120 | 170 | 50 |
| `SRV-4092` | 240 | 235 | -5 |
| `SRV-4093` | 160 | 140 | -20 |

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
Tasks without initiative: 0: —.
Initiatives without tasks cannot be detected without a separate registry. Repeated PRODF values in tasks are expected one-to-many links, not duplicate initiative records.

### 10. Planned dates

A suspicious interval is defined for this audit as more than 84 calendar days (the documented 12-week quarter). Dates remain informational and are not used as planning constraints.
Missing planned_start: 0; missing planned_end: 0; tasks missing both: 0.
End before start: 0; suspiciously long intervals: 1.

| Task ID | planned_start | planned_end | Issues |
|---|---|---|---|
| `KP-0000` | 2026-03-16 00:00:00 | 2026-06-18 00:00:00 | SUSPICIOUS_PLANNED_INTERVAL |

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
