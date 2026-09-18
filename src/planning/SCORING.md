# Scoring v1

Scoring v1 — объяснимая формула вторичных бизнес-предпочтений. Production
planner сначала применяет hard constraints и абсолютный `rung`, затем мягкий
`planned_start`, и только после этого использует компоненты score. Feasibility
checker независимо решает, можно ли разместить задачу с учётом Story Points,
ролей, инженеров и зависимостей.

```text
base_score = rung_score + status_score
dynamic_score = initiative_completion_score + dependency_unlock_score
total_score = base_score + dynamic_score
```

Веса по умолчанию заданы в `ScoringConfig`:

- `rung_weight = 40`;
- `in_progress_bonus = 25`;
- `deadline_weight = 0` (поле совместимости, в scoring не участвует);
- `initiative_completion_weight = 20`;
- `dependency_unlock_weight = 10`;
- `dependency_unlock_saturation_count = 4`.

Организаторы подтвердили: **чем выше `rung`, тем выше приоритет задачи**.
`rung` нормализуется среди планируемых задач. `ScoringConfig.rung_direction`
принимает только подтверждённое значение `"higher_is_better"`; прежний режим
`"lower_is_better"` отклоняется как противоречащий бизнес-правилу.
Численный `rung_score` сохранён в объяснении, но production order сравнивает
исходный `rung` отдельным первым ключом. Поэтому сумма остальных компонентов не
может поднять задачу с меньшим `rung` выше. При равном `rung` более ранний
`planned_start` используется как soft preference, если даты присутствуют у всех
сравниваемых задач; иначе применяется следующий tie-break.

При разных известных rung: `rung_score = 40 * (rung - min_rung) / (max_rung - min_rung)`.
При одинаковых известных значениях вклад равен 40, при пропуске — 0.
Итоговая формула по умолчанию:

```text
total_score = rung_score                         # 0..40
            + 25 * is_in_progress
            + 20 / (unfinished_after_task + 1)   # известная инициатива, иначе 0
            + 10 * min(downstream_count / 4, 1)
```

`planned_start` не является hard constraint и не ограничивает спринт размещения.
`planned_end` сохраняется только как baseline metadata: оно не является hard
constraint, objective, score component или tie-break. Бонус завершения
инициативы равен максимальному весу, делённому на
количество незавершённых после этой задачи работ плюс один. Он не применяется
при неизвестном составе инициативы. Бонус зависимостей линейно растёт по числу
уникальных достижимых незавершённых задач и достигает максимума на четырёх.
Циклы не приводят к повторному учёту, задачи-заглушки не учитываются.

Бонусы инициатив и зависимостей, нормализация и численные веса являются
проектными эвристиками. Свободные SP, часы, роли, инженеры (включая внешние команды)
и фактическая выполнимость в score не входят. Веса меняются конфигурацией.
