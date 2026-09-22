'use strict';

window.planConstructor = (() => {
  const STORAGE_KEY = 'postcode.plan-constructor.v2';
  const emptyDraft = () => ({teams: [], engineers: [], tasks: []});
  let catalog = {teams: [], engineers: [], tasks: [], options: {roles: [], skills: [], grades: []}};
  let draft = emptyDraft();
  let mode = 'upload';
  let api = null;
  let previewPlan = null;
  let onPlanApproved = null;
  let onCatalogUpdated = null;
  let pendingDatasetUpload = null;
  let shownRecommendationGroups = 3;
  let exclusionReasonPriority = 'all';
  const shownRecommendations = new Map();
  const locallyRejectedRecommendations = new Map();
  let changedTaskEffects = [];
  let showPlanChanges = true;
  let exclusionReasonPriority = 'all';
  let activeRecommendationId = null;
  let recommendationUpdateInFlight = false;
  let editingSkillRequirements = [];
  let skillExtractionTimer = null;
  let bound = false;

  const $c = (selector, root = document) => root.querySelector(selector);
  const $$c = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const idField = (type) => `${type.slice(0, -1)}_id`;
  const findDraft = (type, id) => draft[type].find((item) => item[idField(type)] === id);
  const findCatalog = (type, id) => catalog[type].find((item) => item[idField(type)] === id);
  const csvValues = (value) => [...new Set(String(value || '').split(',').map((item) => item.trim()).filter(Boolean))];
  const optionList = (items, selected = '') => items.map((item) => `<option value="${esc(item)}" ${item === selected ? 'selected' : ''}>${esc(item)}</option>`).join('');
  const isCompletedTask = (task) => ['done', 'completed'].includes(String(task?.status || '').trim().toLowerCase());
  const isHistoricalDoneTask = (task) => isCompletedTask(task)
    || isCompletedTask(findCatalog('tasks', task?.task_id));
  const number = (value) => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(Number(value || 0));

  function percent(value) {
    if (value == null) return 'KPI недоступен';
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) return 'KPI недоступен';
    return `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(parsed)}%`;
  }

  function recommendationGroups() {
    const recommendations = (previewPlan?.optimization?.recommendations || [])
      .filter((item) => !locallyRejectedRecommendations.has(item.recommendation_id));
    const groups = [
      {key: 'task-allocation', title: 'Перераспределение задач', types: new Set(['TASK_SWAP', 'TASK_REASSIGNMENT', 'ATOMIC_PACKAGE'])},
      {key: 'cross-team-help', title: 'Помощь между командами', types: new Set(['CROSS_TEAM_HELP'])},
    ];
    return groups.map((group) => ({
      ...group,
      items: recommendations.filter((item) => group.types.has(item.type)).sort((left, right) => Number(right.rung || 0) - Number(left.rung || 0) || String(left.task_id || '').localeCompare(String(right.task_id || ''), 'ru')),
    })).filter((group) => group.items.length);
  }

  function ownershipRows(ownership) {
    if (!ownership || ownership.total_hours == null) return '';
    return `<div class="recommendation-evidence"><b>Кто выполняет работу</b><p>Основная команда: ${number(ownership.owner_hours)} ч · ${number(ownership.owner_engineer_count)} инженер(а)</p><p>Внешняя помощь: ${number(ownership.external_hours)} ч · ${number(ownership.external_engineer_count)} специалист(а)</p><p>Команда выполняет ${number(ownership.owner_share_percent)}% трудозатрат</p></div>`;
  }

  function recommendationCard(item, rejected = false) {
    const effect = item.effect || {};
    const currentSummary = previewPlan?.optimization?.current_summary || {};
    const resultingSummary = item.plan_delta?.resulting_summary || {};
    const transfer = (item.task_reassignment || [])[0];
    const help = (item.engineer_help || [])[0];
    const taskIds = (item.tasks || [item.task_id]).filter(Boolean);
    const label = item.is_atomic_package ? `${taskIds.length} связанные задачи` : taskIds.join(' ↔ ');
    const action = transfer ? `${transfer.from_team} → ${transfer.to_team}` : help ? `${help.engineer_name || help.engineer_id} · ${help.source_team} → ${help.target_team}` : '';
    const taskDelta = Array.isArray(effect.tasks_entering_quarter)
      ? effect.tasks_entering_quarter.length
      : Number(resultingSummary.planned_tasks || 0) - Number(currentSummary.planned_tasks || 0);
    const initiativeDelta = Number(resultingSummary.initiatives || 0) - Number(currentSummary.initiatives || 0);
    const effectLines = `<span>${taskDelta >= 0 ? '+' : ''}${number(taskDelta)} задач войдёт в квартал</span><span>${initiativeDelta >= 0 ? '+' : ''}${number(initiativeDelta)} инициатив завершится полностью</span>`;
    const details = [
      ...(item.task_reassignment || []).map((change) => `<p>${esc(change.task_id)} · ${esc(change.from_team)} → ${esc(change.to_team)} · Sprint ${esc(change.after_sprint || '—')} · SP: ${number(change.task_sp)}</p>${ownershipRows(change.ownership)}`),
      ...(item.engineer_help || []).map((change) => `<p>${esc(change.target_task)} · ${esc(change.engineer_name || change.engineer_id)} · ${esc(change.source_team)} → ${esc(change.target_team)} · ${esc(change.role)} · Sprint ${esc(change.sprint)} · ${number(change.help_hours)} ч</p><p>После подключения: ${number(change.engineer_capacity?.resulting_total_hours)} / ${number(change.engineer_capacity?.capacity_hours)} ч</p>${ownershipRows(change.ownership)}`),
    ].join('');
    const applying = activeRecommendationId === item.recommendation_id;
    return `<article class="recommendation-card ${rejected ? 'recommendation-card--rejected' : ''} ${applying ? 'recommendation-card--loading' : ''}" id="recommendation-${esc(item.recommendation_id)}"><div class="recommendation-card-title"><b>${esc(item.title)}</b><span>${rejected ? `Отклонено · ${esc(label)}` : esc(label)}</span></div>${action ? `<p class="recommendation-action">${esc(action)}</p>` : ''}<div class="recommendation-effect">${effectLines}</div><p class="recommendation-description">${esc(effect.summary || 'План станет выполнимее.')}</p><details><summary>Подробнее</summary><div class="recommendation-details">${details || '<p>Изменение рассчитано в сохранённом варианте плана.</p>'}</div></details><div class="recommendation-actions">${!rejected ? `<button type="button" class="btn recommendation-reject-button" data-recommendation-reject="${esc(item.recommendation_id)}" ${recommendationUpdateInFlight ? 'disabled' : ''}>Отклонить</button>` : ''}<button type="button" class="btn recommendation-apply-button" data-recommendation-apply="${esc(item.recommendation_id)}" ${recommendationUpdateInFlight ? 'disabled' : ''}>${applying ? 'Применяем…' : 'Применить'}</button></div></article>`;
  }

  function optimizationSummaryCard(label, summary, className = '') {
    return `<div class="optimization-summary-card ${className}"><span>${label}</span><b>${number(summary.planned_tasks)} задач</b><small>${number(summary.initiatives)} инициатив завершится полностью</small></div>`;
  }

  function optimizationSummary() {
    const optimization = previewPlan?.optimization || {};
    const base = optimization.base_summary || {};
    const current = optimization.current_summary || {};
    const best = optimization.optimized_summary || {};
    return `<section class="optimization-summary">${optimizationSummaryCard('Базовый план', base)}${optimizationSummaryCard('Текущий вариант', current)}${optimizationSummaryCard('Лучший найденный вариант', best, 'optimization-summary-card--best')}</section>`;
  }

  function acceptedChangesPanel() {
    const accepted = previewPlan?.optimization?.accepted_changes || [];
    if (!accepted.length) return '';
    const rows = accepted.map((item) => {
      const action = item.action || {};
      const changes = action.type === 'TASK_SWAP' ? (action.changes || []) : [action];
      const title = action.type === 'TASK_SWAP'
        ? 'Обменять задачи между командами'
        : action.type === 'CROSS_TEAM_HELP'
          ? 'Подключить специалиста другой команды'
          : action.type === 'TASK_REASSIGNMENT' && (item.tasks || []).length > 1
            ? 'Совместное перераспределение задач'
            : 'Передать задачу другой команде';
      const taskLabel = (item.tasks?.length ? item.tasks : [item.task_id]).filter(Boolean).join(', ');
      const routing = changes.filter((change) => change.type === 'TASK_REASSIGNMENT' && (change.source_team_id || change.team_id))
        .map((change) => `${change.source_team_id || 'Команда'} → ${change.team_id || 'команда'}`).join(' · ');
      const detail = [taskLabel, routing || item.title].filter(Boolean).join(' · ');
      return `<article class="accepted-change-row"><span class="accepted-change-mark" aria-hidden="true">✓</span><div class="accepted-change-copy"><b>${esc(title)}</b><small>${esc(detail)}</small><em>${esc(item.summary || 'Изменение применено.')}</em></div><button class="btn btn-outline" type="button" data-change-undo="${esc(item.change_id)}">Отменить</button></article>`;
    }).join('');
    return `<section class="preview-section accepted-changes-section"><div class="accepted-changes-heading"><h3>Применённые изменения</h3><span>${accepted.length}</span></div><div class="accepted-changes">${rows}</div><button class="text-button" type="button" id="resetConstructorDraft">Вернуться к базовому плану</button></section>`;
  }

  function recommendationPanel() {
    const optimization = previewPlan?.optimization || {};
    const groups = recommendationGroups();
    const current = optimization.current_summary || {};
    const best = optimization.optimized_summary || {};
    const cards = groups.slice(0, shownRecommendationGroups).map((group) => {
      const visible = shownRecommendations.get(group.key) || 3;
      return `<section class="recommendation-group"><h4>${group.title}<small>${group.items.length} доступных улучшений</small></h4><div class="recommendation-list">${group.items.slice(0, visible).map(recommendationCard).join('')}</div>${group.items.length > visible ? `<button class="text-button" type="button" data-more-recommendations="${esc(group.key)}">Показать ещё ${group.items.length - visible}</button>` : ''}</section>`;
    }).join('');
    const applyAll = optimization.status === 'ready' && (optimization.optimized_actions || []).length
      ? `<div class="apply-all-recommendations ${optimization.best_selected ? 'apply-all-recommendations--selected' : ''}"><div><b>Лучший найденный совместимый вариант</b><small>Сохранённый вариант не меняется при отдельных действиях.</small></div><button class="btn recommendation-apply-all-button" type="button" id="applyAllRecommendations" ${recommendationUpdateInFlight || optimization.best_selected ? 'disabled' : ''}>${optimization.best_selected ? '✓ Лучший найденный вариант выбран' : 'Применить все рекомендации'}</button></div>`
      : '';
    const rejected = [...locallyRejectedRecommendations.values()];
    const rejectedSection = rejected.length ? `<section class="recommendation-group recommendation-group--rejected"><h4>Отклонённые <small>${rejected.length}</small></h4><div class="recommendation-list">${rejected.map((item) => recommendationCard(item, true)).join('')}</div></section>` : '';
    const activeContent = groups.length ? `<div class="recommendation-groups-scroll">${cards}</div>` : '<p class="muted">Дополнительных улучшений не найдено.</p>';
    return `<section class="preview-section recommendations-section"><div class="recommendations-heading"><div><h3>Как можно улучшить план</h3></div><label class="recommendations-change-toggle"><input id="showPlanChanges" type="checkbox" ${showPlanChanges ? 'checked' : ''}><span>Показывать изменения</span></label></div><div class="recommendation-comparison"><div><span>Текущий вариант</span><b>${number(current.planned_tasks)} задач · ${number(current.initiatives)} инициатив</b></div><div><span>Лучший найденный</span><b>${number(best.planned_tasks)} задач · ${number(best.initiatives)} инициатив</b></div></div>${applyAll}${activeContent}${rejectedSection}</section>`;
  }

  function invalidatePreview() {
    if (!previewPlan) return;
    previewPlan = null;
    locallyRejectedRecommendations.clear();
    renderPreview();
  }

  function previewReasons(task) {
    const missingRoleCodes = new Set(['MISSING_SPECIALIST', 'NO_AVAILABLE_ROLE_SPECIALIST']);
    const missingRoles = [...new Set((task.reasons || [])
      .filter((reason) => missingRoleCodes.has(reason.code) && reason.details?.role)
      .map((reason) => reason.details.role))];
    const messages = (task.reasons || [])
      .filter((reason) => !missingRoleCodes.has(reason.code) || !reason.details?.role)
      .map((reason) => reason.message)
      .filter(Boolean);
    if (missingRoles.length) messages.unshift(`Нет специалистов ролей: ${missingRoles.join(', ')}.`);
    return [...new Set(messages)];
  }

  function previewReasonBlocks(task) {
    const blocks = Array.isArray(task.reason_blocks) ? task.reason_blocks : [];
    if (blocks.length) {
      return blocks.map((block) => `<div class="preview-reason-block"><b>${esc(block.title || 'Причина ограничения')}</b>${(block.details || []).map((detail) => `<span>${esc(detail)}</span>`).join('')}</div>`).join('');
    }
    return previewReasons(task).map((message) => `<div class="preview-reason-block"><span>${esc(message)}</span></div>`).join('');
  }

  const exclusionReasonGroups = [
    {key: 'specialists', label: 'Не хватает специалистов', tone: 'critical', rank: 0, codes: new Set(['MISSING_SPECIALIST', 'NO_AVAILABLE_ROLE_SPECIALIST', 'INSUFFICIENT_SKILL_CAPACITY'])},
    {key: 'dependency', label: 'Задача заблокирована зависимостью', tone: 'dependency', rank: 1, codes: new Set(['DEPENDENCY_NOT_COMPLETED', 'UNKNOWN_DEPENDENCY_DATE', 'MISSING_DEPENDENCY_REFERENCE', 'DEPENDENCY_DELAY'])},
    {key: 'engineer_hours', label: 'Не хватает доступного времени специалистов', tone: 'capacity', rank: 2, codes: new Set(['INSUFFICIENT_ROLE_CAPACITY', 'INSUFFICIENT_ENGINEER_CAPACITY'])},
    {key: 'team_capacity', label: 'Недостаточно ресурсов команды', tone: 'capacity', rank: 3, codes: new Set(['INSUFFICIENT_TEAM_CAPACITY', 'INSUFFICIENT_SP_CAPACITY'])},
    {key: 'quarter', label: 'Не помещается в текущем квартале', tone: 'capacity', rank: 4, codes: new Set(['MULTI_SPRINT_REQUIRED', 'OUTSIDE_QUARTER', 'QUARTER_OVERFLOW'])},
    {key: 'allocation', label: 'Не удалось разместить задачу', tone: 'capacity', rank: 5, codes: new Set(['ALLOCATION_ROLLED_BACK'])},
    {key: 'data', label: 'Требуется уточнение данных', tone: 'data', rank: 6, codes: new Set(['UNKNOWN_TASK_DATA', 'STORY_POINTS_WITHOUT_HOURS', 'INVALID_TASK_OWNERSHIP', 'UNSUPPORTED_TASK_STATUS'])},
  ];

  function exclusionReasonMetas(task) {
    const codes = new Set((task.reasons || []).map((reason) => String(reason.code || '')));
    const matches = exclusionReasonGroups.filter((group) => [...group.codes].some((code) => codes.has(code)));
    return matches.length ? matches : [{key: 'other', label: 'Другая причина', tone: 'data', rank: 7}];
  }

  function exclusionReasonMeta(task) {
    const matches = exclusionReasonMetas(task);
    return matches.find((group) => group.key === exclusionReasonPriority) || matches[0];
  }

  function filteredExcludedTasks(tasks) {
    const visible = exclusionReasonPriority === 'all' ? [...tasks] : tasks.filter((task) => exclusionReasonMetas(task).some((group) => group.key === exclusionReasonPriority));
    return visible.sort((left, right) => {
      const leftReason = exclusionReasonMeta(left);
      const rightReason = exclusionReasonMeta(right);
      return leftReason.rank - rightReason.rank || Number(right.rung || 0) - Number(left.rung || 0) || String(left.task_id || '').localeCompare(String(right.task_id || ''), 'ru');
    });
  }

  function emptyExclusionsMessage() {
    if (exclusionReasonPriority === 'all') return 'Все задачи удалось разместить в квартальном плане.';
    const reason = exclusionReasonGroups.find((group) => group.key === exclusionReasonPriority);
    return reason ? `Задач с причиной «${reason.label}» нет.` : 'Задач с выбранной причиной нет.';
  }

  function previewSprintCard(task, sprint) {
    const continuation = Number(task.start_sprint) < sprint;
    const sprintSp = Number(task.sp_load_by_sprint?.[String(sprint)] || 0);
    const sprintHours = Number(task.hours_by_sprint?.[String(sprint)] || 0);
    const helpers = (task.allocations || []).filter((item) => item.sprint === sprint && item.cross_team);
    const helperNote = helpers.length ? `<small class="preview-cross-team">+ ${new Set(helpers.map((item) => item.engineer_id)).size} специалист(а) из другой команды</small>` : '';
    const reassigned = task.team_reassigned && task.original_team_id
      ? `<small class="preview-reassigned">Исходная команда: ${esc(task.original_team_id)}</small>` : '';
    const changed = changedTaskEffects.some((item) => item.task_id === task.task_id);
    const recommended = appliedRecommendationTaskIds().has(String(task.task_id));
    return `<article class="preview-task-card ${continuation ? 'preview-continuation' : ''} ${recommended ? 'preview-task-card--recommended' : ''} ${changed ? 'task-card--just-added' : ''}" data-preview-task="${esc(task.task_id)}" data-preview-sprint="${sprint}"><div><b>${continuation ? '↳ ' : ''}${esc(task.task_id)}</b><span class="rung-badge">R${esc(task.rung)}</span></div><strong>${continuation ? 'Продолжение' : esc(task.title)}</strong><small>Всего задачи: ${number(task.story_points)} SP</small><small>В Sprint ${sprint}: ${number(sprintSp)} SP</small><small>Часы в спринте: ${number(sprintHours)} ч</small><small>${esc(task.assigned_team_id || 'Команда не назначена')}</small>${reassigned}${helperNote}</article>`;
  }

  function previewCrossTeamRows(plan) {
    const engineers = new Map((plan.engineers || []).map((engineer) => [engineer.engineer_id, engineer]));
    const rows = new Map();
    (plan.tasks || []).forEach((task) => (task.allocations || []).filter((item) => item.cross_team).forEach((item) => {
      const key = [item.engineer_id, item.source_team_id, item.assigned_task_team_id, task.task_id, item.sprint].join('|');
      const row = rows.get(key) || {...item, task_id: task.task_id, title: task.title, hours: 0};
      row.hours += Number(item.allocated_hours || 0);
      rows.set(key, row);
    }));
    return [...rows.values()].sort((left, right) => left.sprint - right.sprint || left.task_id.localeCompare(right.task_id, 'ru')).map((row) => {
      const engineer = engineers.get(row.engineer_id);
      return `<article class="preview-help-row"><div><b>${esc(engineer?.name || row.engineer_id)}</b><span>${esc(row.source_team_id)} → ${esc(row.assigned_task_team_id)}</span></div><div><span>Sprint ${esc(row.sprint)} · ${esc(row.task_id)}</span><strong>${number(row.hours)} ч</strong></div></article>`;
    }).join('') || '<p class="muted">Внешняя помощь между командами не потребовалась.</p>';
  }

  function renderPreview() {
    const target = $c('#constructorPreview');
    if (!target) return;
    if (!previewPlan) {
      target.hidden = true;
      target.innerHTML = '';
      return;
    }
    const plannedTasks = (previewPlan.tasks || []).filter((task) => task.status === 'PLANNED');
    const excludedTasks = (previewPlan.tasks || []).filter((task) => ['MOVED', 'UNPLANNABLE'].includes(task.status));
    const sprintBoard = Array.from({length: 6}, (_, index) => {
      const sprint = index + 1;
      const cards = plannedTasks.filter((task) => (task.planned_sprints || []).includes(sprint))
        .sort((left, right) => Number(right.rung || 0) - Number(left.rung || 0) || left.task_id.localeCompare(right.task_id, 'ru'))
        .map((task) => previewSprintCard(task, sprint)).join('') || '<p class="preview-sprint-empty">Нет задач</p>';
      return `<section class="preview-sprint"><h3>Sprint ${sprint}</h3><div>${cards}</div></section>`;
    }).join('');
    const exclusions = filteredExcludedTasks(excludedTasks).map((task) => {
      const reasonBlocks = previewReasonBlocks(task);
      const reason = exclusionReasonMeta(task);
      return `<article class="preview-exclusion preview-exclusion--${esc(reason.tone)}"><div><span class="preview-exclusion-category">${esc(reason.label)}</span><b>${esc(task.task_id)}</b><span>${esc(task.title)}</span></div><div class="preview-reason-blocks">${reasonBlocks || '<div class="preview-reason-block"><span>Причина ограничения не раскрыта.</span></div>'}</div></article>`;
    }).join('') || `<div class="preview-exclusion-empty"><b>${esc(emptyExclusionsMessage())}</b><span>Выберите другую причину, чтобы посмотреть остальные переносы и исключения.</span></div>`;
    const historicalDone = (previewPlan.tasks || []).filter((task) => task.status === 'DONE');
    const historicalBlock = historicalDone.length ? `<section class="preview-section"><h3>Уже завершены до начала планирования</h3><div class="preview-exclusion-list">${historicalDone.map((task) => `<article class="preview-exclusion"><div><b>${esc(task.task_id)}</b><span>${esc(task.title)}</span></div><small>✓ Завершена до начала планирования</small></article>`).join('')}</div></section>` : '';
    target.hidden = false;
    const reasonOptions = exclusionReasonGroups.map((reason) => `<option value="${esc(reason.key)}" ${exclusionReasonPriority === reason.key ? 'selected' : ''}>${esc(reason.label)}</option>`).join('');
    target.innerHTML = `<div class="constructor-preview-heading"><div><h2>Предварительный план</h2><p>План построен без перераспределения задач и специалистов между командами.</p></div></div>${optimizationSummary()}<section class="preview-section" id="constructorSprintPlan"><h3>Распределение по спринтам</h3><div class="preview-board-viewport"><div class="preview-sprint-board">${sprintBoard}</div></div></section>${recommendationPanel()}${acceptedChangesPanel()}${historicalBlock}<section class="preview-section"><h3>Помощь между командами</h3><div class="preview-help-list">${previewCrossTeamRows(previewPlan)}</div></section><section class="preview-section preview-exclusions-section"><div class="preview-section-heading"><div><h3>Переносы и исключения</h3></div><label class="preview-sort-control"><span>Причина переноса или исключения</span><select id="previewExclusionSort"><option value="all" ${exclusionReasonPriority === 'all' ? 'selected' : ''}>Все причины</option>${reasonOptions}</select></label></div><div class="preview-exclusion-list">${exclusions}</div></section><div class="preview-approval"><p>Проверьте распределение перед утверждением. После утверждения этот вариант станет текущим планом.</p><div id="constructorApprovalError" class="constructor-errors" hidden></div><button class="btn btn-blue" id="approveConstructorPlan" type="button">Утвердить план</button></div>`;
  }

  function loadDraft() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (stored && Array.isArray(stored.teams) && Array.isArray(stored.tasks)) {
        draft = {
          teams: stored.teams,
          engineers: Array.isArray(stored.engineers) ? stored.engineers : [],
          tasks: stored.tasks.map((task) => ({...task, team_id: null})),
        };
      }
    } catch (_) {
      draft = emptyDraft();
    }
  }

  function saveDraft() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  }

  function setDatasetStatus(message, kind = '') {
    const status = $c('#constructorDatasetStatus');
    if (!status) return;
    status.textContent = message;
    status.classList.remove('constructor-dataset-status-success', 'constructor-dataset-status-error');
    if (kind) status.classList.add(`constructor-dataset-status-${kind}`);
  }

  function setMode(nextMode) {
    mode = nextMode;
    $$c('[data-constructor-mode]').forEach((button) => {
      const selected = button.dataset.constructorMode === mode;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
    });
    $c('#constructorManualMode').hidden = mode !== 'manual';
    $c('#constructorUploadMode').hidden = mode !== 'upload';
  }

  function issueCountLabel(value, one, few, many) {
    const numberValue = Number(value || 0);
    const mod10 = numberValue % 10;
    const mod100 = numberValue % 100;
    const label = mod10 === 1 && mod100 !== 11 ? one : ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100) ? few : many);
    return `${numberValue} ${label}`;
  }

  function closeDataPreflight() {
    const dialog = $c('#dataPreflightDialog');
    if (dialog?.open) dialog.close();
  }

  function archiveDisplayValue(value) {
    if (value == null || value === '') return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function archiveComparison(item) {
    const differences = item.differences || [];
    if (!differences.length) return '<p class="archive-compare-empty">Данные в Excel совпадают с архивной записью.</p>';
    return `<table class="archive-compare-table"><thead><tr><th>Поле</th><th>В каталоге</th><th>В Excel</th></tr></thead><tbody>${differences.map((difference) => `<tr><th>${esc(difference.label)}</th><td>${esc(archiveDisplayValue(difference.current))}</td><td>${esc(archiveDisplayValue(difference.incoming))}</td></tr>`).join('')}</tbody></table>`;
  }

  function archiveEntityRows(items) {
    pendingDatasetUpload.archiveDecisions ||= Object.fromEntries(items.map((item) => [item.decision_key, 'KEEP_ARCHIVED']));
    return items.map((item) => {
      const decision = pendingDatasetUpload.archiveDecisions[item.decision_key] || 'KEEP_ARCHIVED';
      const reason = [item.archived_at ? `Архивирована: ${item.archived_at}` : '', item.archive_reason_label, item.archive_reason_text].filter(Boolean).join(' · ');
      return `<article class="archive-entity-row" data-import-entity-row data-archive-type="${esc(item.entity_type)}" data-archive-search="${esc(`${item.entity_id} ${item.display_name}`.toLowerCase())}"><label class="archive-entity-select"><input type="checkbox" data-archive-select="${esc(item.decision_key)}"><span></span></label><div class="archive-entity-copy"><div class="archive-entity-heading"><b>${esc(item.entity_label)} · ${esc(item.entity_id)}</b><strong>${esc(item.display_name)}</strong></div>${reason ? `<small>${esc(reason)}</small>` : ''}<details class="archive-compare"><summary>Сравнить данные</summary>${archiveComparison(item)}</details></div><select data-archive-decision="${esc(item.decision_key)}"><option value="KEEP_ARCHIVED" ${decision === 'KEEP_ARCHIVED' ? 'selected' : ''}>Оставить в архиве</option><option value="RESTORE" ${decision === 'RESTORE' ? 'selected' : ''}>Восстановить</option><option value="RESTORE_UPDATE" ${decision === 'RESTORE_UPDATE' ? 'selected' : ''}>Восстановить и обновить</option></select></article>`;
    }).join('');
  }

  function existingEntityRows(items) {
    pendingDatasetUpload.existingDecisions ||= Object.fromEntries(items.map((item) => [item.decision_key, 'KEEP_CURRENT']));
    return items.map((item) => {
      const decision = pendingDatasetUpload.existingDecisions[item.decision_key] || 'KEEP_CURRENT';
      return `<article class="archive-entity-row" data-import-entity-row data-archive-type="${esc(item.entity_type)}" data-archive-search="${esc(`${item.entity_id} ${item.display_name}`.toLowerCase())}"><label class="archive-entity-select"><input type="checkbox" data-existing-select="${esc(item.decision_key)}"><span></span></label><div class="archive-entity-copy"><div class="archive-entity-heading"><b>${esc(item.entity_label)} · ${esc(item.entity_id)}</b><strong>${esc(item.display_name)}</strong></div><small>В Excel найдена запись с тем же ID, но с отличающимися данными.</small><details class="archive-compare"><summary>Сравнить данные</summary>${archiveComparison(item)}</details></div><select data-existing-decision="${esc(item.decision_key)}"><option value="KEEP_CURRENT" ${decision === 'KEEP_CURRENT' ? 'selected' : ''}>Оставить текущее</option><option value="UPDATE" ${decision === 'UPDATE' ? 'selected' : ''}>Обновить из Excel</option></select></article>`;
    }).join('');
  }

  function updateArchiveImportSummary() {
    const items = pendingDatasetUpload?.audit?.archived_entities || [];
    const existingItems = pendingDatasetUpload?.audit?.existing_entities || [];
    const decisions = pendingDatasetUpload?.archiveDecisions || {};
    const existingDecisions = pendingDatasetUpload?.existingDecisions || {};
    const counts = {KEEP_ARCHIVED: 0, RESTORE: 0, RESTORE_UPDATE: 0};
    const existingCounts = {KEEP_CURRENT: 0, UPDATE: 0};
    items.forEach((item) => { counts[decisions[item.decision_key] || 'KEEP_ARCHIVED'] += 1; });
    existingItems.forEach((item) => { existingCounts[existingDecisions[item.decision_key] || 'KEEP_CURRENT'] += 1; });
    const selected = $$c('[data-archive-select]:checked, [data-existing-select]:checked').length;
    const summary = pendingDatasetUpload?.audit?.import_summary || {};
    const target = $c('#archiveImportSummary');
    if (target) target.innerHTML = `<span>Новые записи: <b>${number(summary.new)}</b></span><span>Совпадения ID: <b>${number(existingItems.length)}</b></span><span>Из существующих: <b>оставить ${existingCounts.KEEP_CURRENT}</b> · <b>обновить ${existingCounts.UPDATE}</b></span>${items.length ? `<span>Архивные записи: <b>${number(items.length)}</b></span><span>Из архивных: <b>оставить ${counts.KEEP_ARCHIVED}</b> · <b>восстановить ${counts.RESTORE}</b> · <b>обновить ${counts.RESTORE_UPDATE}</b></span>` : ''}`;
    const selectedCount = $c('#archiveSelectedCount');
    if (selectedCount) selectedCount.textContent = `Выбрано: ${selected}`;
  }

  function filterArchiveRows() {
    const query = ($c('#archiveEntitySearch')?.value || '').trim().toLowerCase();
    const type = $c('#archiveEntityTypeFilter')?.value || '';
    $$c('[data-import-entity-row]').forEach((row) => {
      row.hidden = Boolean((query && !row.dataset.archiveSearch.includes(query)) || (type && row.dataset.archiveType !== type));
    });
  }

  function applyArchiveBulk(decision) {
    const selected = $$c('[data-archive-select]:checked').map((input) => input.dataset.archiveSelect);
    if (decision === 'RESTORE_UPDATE' && selected.length > 1
      && !confirm('Данные выбранных архивных записей будут обновлены значениями из Excel. Продолжить?')) return;
    selected.forEach((key) => { pendingDatasetUpload.archiveDecisions[key] = decision; });
    $$c('[data-archive-decision]').forEach((select) => {
      if (selected.includes(select.dataset.archiveDecision)) select.value = decision;
    });
    updateArchiveImportSummary();
  }

  function applyExistingBulk(decision) {
    const selected = $$c('[data-existing-select]:checked').map((input) => input.dataset.existingSelect);
    selected.forEach((key) => { pendingDatasetUpload.existingDecisions[key] = decision; });
    $$c('[data-existing-decision]').forEach((select) => {
      if (selected.includes(select.dataset.existingDecision)) select.value = decision;
    });
    updateArchiveImportSummary();
  }

  function renderArchivePreflight(preflight) {
    const audit = preflight.audit || {};
    const items = audit.archived_entities || [];
    const existingItems = audit.existing_entities || [];
    pendingDatasetUpload.archiveDecisions = Object.fromEntries(items.map((item) => [item.decision_key, 'KEEP_ARCHIVED']));
    pendingDatasetUpload.existingDecisions = Object.fromEntries(existingItems.map((item) => [item.decision_key, 'KEEP_CURRENT']));
    $c('#dataPreflightTitle').textContent = 'Проверка совпадений по ID';
    $c('#dataPreflightSubtitle').textContent = 'Для записей, которые уже есть в базе, выберите: оставить текущую версию или обновить её данными из Excel.';
    const critical = Number(audit.summary?.critical || 0);
    const counters = [];
    if (critical) counters.push(`<span class="preflight-counter critical">${issueCountLabel(critical, 'критическая ошибка', 'критические ошибки', 'критических ошибок')}</span>`);
    $c('#dataPreflightSummary').innerHTML = counters.join('');
    const types = [...new Set([...items, ...existingItems].map((item) => item.entity_type))];
    const labels = {task: 'Задачи', team: 'Команды', engineer: 'Инженеры'};
    const otherIssues = (audit.groups || []).flatMap((group) => group.items || []).filter((item) => !['ARCHIVED_ENTITY_IN_UPLOAD', 'CATALOG_ENTITY_CONFLICT'].includes(item.code));
    const issueBlock = otherIssues.length ? `<details class="archive-other-issues"><summary>Другие результаты проверки (${otherIssues.length})</summary><div>${otherIssues.map((item) => `<p><b>${esc(item.title)}</b> — ${esc(item.message)}</p>`).join('')}</div></details>` : '';
    const existingSection = existingItems.length ? `<section class="import-decision-section"><h3>Уже есть в активной базе</h3><div class="archive-bulk-actions"><button class="btn btn-outline" type="button" data-existing-bulk="KEEP_CURRENT">Оставить выбранные текущими</button><button class="btn btn-outline" type="button" data-existing-bulk="UPDATE">Обновить выбранные из Excel</button></div><div class="archive-entity-list">${existingEntityRows(existingItems)}</div></section>` : '';
    const archiveSection = items.length ? `<section class="import-decision-section"><h3>Найдены в архиве</h3><div class="archive-bulk-actions"><button class="btn btn-outline" type="button" data-archive-bulk="KEEP_ARCHIVED">Оставить в архиве</button><button class="btn btn-outline" type="button" data-archive-bulk="RESTORE">Восстановить</button><button class="btn btn-outline" type="button" data-archive-bulk="RESTORE_UPDATE">Восстановить и обновить</button></div><div class="archive-entity-list">${archiveEntityRows(items)}</div></section>` : '';
    $c('#dataPreflightGroups').innerHTML = `<div class="archive-import-toolbar"><input id="archiveEntitySearch" type="search" placeholder="Поиск по ID или названию"><select id="archiveEntityTypeFilter"><option value="">Все типы</option>${types.map((type) => `<option value="${esc(type)}">${labels[type] || esc(type)}</option>`).join('')}</select><span id="archiveSelectedCount">Выбрано: 0</span></div>${existingSection}${archiveSection}<div class="archive-import-summary" id="archiveImportSummary"></div>${issueBlock}<div class="archive-import-error" id="archiveImportError" hidden></div>`;
    $c('#dataPreflightActions').innerHTML = critical
      ? '<button class="btn btn-outline" type="button" data-close-data-preflight>Закрыть</button><button class="btn btn-blue" type="button" data-replace-data-file>Загрузить исправленный файл</button>'
      : '<button class="btn btn-outline" type="button" data-close-data-preflight>Отмена</button><button class="btn btn-blue" type="button" data-continue-data-preflight>Продолжить импорт</button>';
    updateArchiveImportSummary();
    const dialog = $c('#dataPreflightDialog');
    document.body.classList.add('constructor-modal-open');
    if (!dialog.open) dialog.showModal();
  }

  function renderDataPreflight(preflight) {
    pendingDatasetUpload = preflight;
    const audit = preflight.audit || {summary: {}, groups: []};
    if ((audit.archived_entities || []).length || (audit.existing_entities || []).length) {
      renderArchivePreflight(preflight);
      return;
    }
    const critical = Number(audit.summary?.critical || 0);
    const warning = Number(audit.summary?.warning || 0);
    const info = Number(audit.summary?.info || 0);
    $c('#dataPreflightSubtitle').textContent = critical
      ? 'В данных есть ошибки, из-за которых план невозможно построить.'
      : 'Нашли моменты, которые могут повлиять на результат.';
    const counters = [];
    if (critical) counters.push(`<span class="preflight-counter critical">${issueCountLabel(critical, 'критическая ошибка', 'критические ошибки', 'критических ошибок')}</span>`);
    if (warning) counters.push(`<span class="preflight-counter warning">${issueCountLabel(warning, 'предупреждение', 'предупреждения', 'предупреждений')}</span>`);
    if (info) counters.push(`<span class="preflight-counter info">${issueCountLabel(info, 'замечание', 'замечания', 'замечаний')}</span>`);
    $c('#dataPreflightSummary').innerHTML = counters.join('');
    $c('#dataPreflightGroups').innerHTML = (audit.groups || []).map((group, index) => `<details class="preflight-group" ${index === 0 && group.count <= 8 ? 'open' : ''}><summary><span>${esc(group.title)}</span><b>${esc(group.count)}</b></summary><div>${(group.items || []).map((item) => {
      const location = [item.sheet ? `Раздел: ${item.sheet}` : '', item.row ? `Строка: ${item.row}` : ''].filter(Boolean);
      return `<article class="preflight-issue ${esc(item.severity)}"><div class="preflight-issue-mark" aria-hidden="true">${item.severity === 'info' ? 'i' : '!'}</div><div><span class="preflight-severity">${item.severity === 'critical' ? 'Критично' : item.severity === 'warning' ? 'Предупреждение' : 'Информация'}</span>${item.entity_id ? `<b>${esc(item.entity_id)}</b>` : ''}<h3>${esc(item.title)}</h3><p>${esc(item.message)}</p>${(location.length || item.details?.length) ? `<details class="preflight-details"><summary>Подробнее</summary>${location.map((value) => `<span>${esc(value)}</span>`).join('')}${(item.details || []).map((value) => `<span>${esc(value)}</span>`).join('')}</details>` : ''}</div></article>`;
    }).join('')}</div></details>`).join('');
    $c('#dataPreflightActions').innerHTML = critical
      ? '<button class="btn btn-outline" type="button" data-close-data-preflight>Закрыть</button><button class="btn btn-blue" type="button" data-replace-data-file>Загрузить исправленный файл</button>'
      : '<button class="btn btn-outline" type="button" data-close-data-preflight>Вернуться к файлу</button><button class="btn btn-blue" type="button" data-continue-data-preflight>Продолжить с этими данными</button>';
    const dialog = $c('#dataPreflightDialog');
    document.body.classList.add('constructor-modal-open');
    if (!dialog.open) dialog.showModal();
  }

  function acceptUploadedPlan(planResult, filename) {
    if (planResult.catalog) {
      catalog = planResult.catalog;
      onCatalogUpdated?.(catalog);
    }
    const planned = (planResult.tasks || []).filter((task) => task.status === 'PLANNED').length;
    const merge = planResult.uploaded_dataset?.catalog_merge || {};
    const total = (values) => Object.values(values || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    const removedDuplicates = Number(planResult.uploaded_dataset?.data_preflight?.deduplication?.removed_total || 0);
    const importResult = merge.archived_entities?.length || Number(merge.summary?.active_existing || 0)
      ? `Импорт завершён · добавлено ${Number(merge.summary?.new ?? total(merge.added))}, обновлено ${total(merge.updated) + total(merge.restored_updated)}, оставлено текущее ${Math.max(Number(merge.summary?.active_existing || 0) - total(merge.updated), 0)}, восстановлено ${total(merge.restored)}, оставлено в архиве ${total(merge.kept_archived)}${removedDuplicates ? `, удалено дублей ${removedDuplicates}` : ''}`
      : `Предварительный план готов · ${planned} задач вошли в квартал${removedDuplicates ? ` · удалено дублей ${removedDuplicates}` : ''}`;
    setDatasetStatus(`${filename} · ${importResult}`, 'success');
    previewPlan = planResult;
    pendingDatasetUpload = null;
    shownRecommendationGroups = 3;
    shownRecommendations.clear();
    locallyRejectedRecommendations.clear();
    render();
    $c('#constructorPreview')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  async function continueDatasetUpload() {
    if (!pendingDatasetUpload?.upload_id) return;
    const button = $c('[data-continue-data-preflight]');
    if (button) { button.disabled = true; button.textContent = 'Строим план…'; }
    setDatasetStatus('Проверка принята. Строим предварительный план…');
    try {
      const decisions = pendingDatasetUpload.archiveDecisions || {};
      const existingDecisions = pendingDatasetUpload.existingDecisions || {};
      const result = await api.continueConstructorDataset(pendingDatasetUpload.upload_id, decisions, existingDecisions);
      const filename = pendingDatasetUpload.file?.name || 'Excel';
      closeDataPreflight();
      acceptUploadedPlan(result, filename);
    } catch (error) {
      const message = String(error?.message || error).replaceAll('\n', ' · ');
      const errorBox = $c('#archiveImportError');
      if (errorBox) { errorBox.textContent = message; errorBox.hidden = false; }
      setDatasetStatus(message, 'error');
      if (button) { button.disabled = false; button.textContent = 'Продолжить с этими данными'; }
    }
  }

  async function uploadDataset(file) {
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!['xlsx', 'xlsm'].includes(extension)) {
      setDatasetStatus('Выберите Excel-файл в формате .xlsx или .xlsm.', 'error');
      return;
    }
    if (!file.size) {
      setDatasetStatus('Выбранный Excel-файл пуст.', 'error');
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      setDatasetStatus('Размер Excel-файла не должен превышать 25 МБ.', 'error');
      return;
    }
    invalidatePreview();
    pendingDatasetUpload = null;
    closeDataPreflight();
    const button = $c('#uploadConstructorDataset');
    button.disabled = true;
    button.textContent = 'Проверяем данные…';
    setDatasetStatus(`Проверяем данные из ${file.name}…`);
    try {
      const result = await api.preflightConstructorDataset(file);
      const preflight = result.data_preflight;
      if (preflight?.status === 'blocked' || preflight?.status === 'review_required') {
        setDatasetStatus(
          preflight.status === 'blocked'
            ? `${file.name} · исправьте критические ошибки в данных`
            : `${file.name} · требуется подтверждение найденных замечаний`,
          preflight.status === 'blocked' ? 'error' : '',
        );
        renderDataPreflight(preflight);
      } else {
        acceptUploadedPlan(result, file.name);
      }
    } catch (error) {
      setDatasetStatus(String(error?.message || error).replaceAll('\n', ' · '), 'error');
    } finally {
      button.disabled = false;
      button.textContent = 'Загрузить файл';
      $c('#constructorDatasetInput').value = '';
    }
  }

  function toggleCatalog(type, id, selected) {
    if (selected) {
      if (findDraft(type, id)) return;
      const source = findCatalog(type, id);
      if (!source) return;
      const copy = clone(source);
      if (type === 'tasks') copy.team_id = null;
      draft[type].push(copy);
    } else {
      draft[type] = draft[type].filter((item) => item[idField(type)] !== id);
    }
    invalidatePreview();
    saveDraft();
    render();
  }

  function toggleAllCatalog(type, items, selected) {
    const key = idField(type);
    const ids = new Set(items.map((item) => item[key]));
    if (selected) {
      items.forEach((item) => {
        if (findDraft(type, item[key])) return;
        const copy = clone(findCatalog(type, item[key]) || item);
        if (type === 'tasks') copy.team_id = null;
        draft[type].push(copy);
      });
    } else {
      draft[type] = draft[type].filter((item) => !ids.has(item[key]));
    }
    invalidatePreview();
    saveDraft();
    render();
  }

  function searchValue(id) {
    return ($c(id)?.value || '').trim().toLowerCase();
  }

  function catalogWithDraft(type) {
    const key = idField(type);
    const catalogIds = new Set(catalog[type].map((item) => item[key]));
    const draftById = new Map(draft[type].map((item) => [item[key], item]));
    const scenarioOnly = draft[type].filter((item) => !catalogIds.has(item[key]));
    const sourceCatalog = catalog[type].map((item) => {
      const draftItem = draftById.get(item[key]);
      if (type === 'tasks' && isCompletedTask(item) && draftItem) {
        return {...item, ...draftItem, status: item.status};
      }
      return draftItem || item;
    });
    return [...scenarioOnly, ...sourceCatalog];
  }

  function selectAllControl(type, items) {
    const key = idField(type);
    const selected = items.filter((item) => findDraft(type, item[key])).length;
    const all = items.length > 0 && selected === items.length;
    const partial = selected > 0 && !all;
    return `<label class="constructor-select-all"><input type="checkbox" data-constructor-select-all="${type}" ${all ? 'checked' : ''} data-partial="${partial}"><span>${all ? 'Выбраны все' : 'Выбрать все'}</span></label>`;
  }

  function updateSelectAllIndeterminate() {
    $$c('[data-constructor-select-all]').forEach((input) => {
      input.indeterminate = input.dataset.partial === 'true';
    });
  }

  function renderCatalogs() {
    const teamQuery = searchValue('#constructorTeamSearch');
    // Include newly created scenario-only teams in the same picker as the
    // persistent catalog. They remain selected until the user removes them
    // from the draft and disappear only after that explicit action.
    const allTeams = catalogWithDraft('teams');
    const teams = allTeams
      .filter((team) => `${team.team_id} ${team.name}`.toLowerCase().includes(teamQuery))
      .sort((left, right) => (
        Number(right.expected_velocity_sp || 0) - Number(left.expected_velocity_sp || 0)
        || String(left.name || left.team_id).localeCompare(String(right.name || right.team_id), 'ru')
      ));
      $c('#constructorTeamCatalog').innerHTML = `${!teamQuery && allTeams.length ? selectAllControl('teams', allTeams) : ''}${teams.map((team) => {
        const capacityLabel = team.expected_velocity_sp != null
          ? `Исторический velocity: ${number(team.expected_velocity_sp)} SP`
          : (team.capacity_sp != null ? `Ручная capacity: ${number(team.capacity_sp)} SP` : 'Capacity не задана');
        return `<label class="constructor-option"><input type="checkbox" data-constructor-pick="teams" value="${esc(team.team_id)}" ${findDraft('teams', team.team_id) ? 'checked' : ''}><span><b>${esc(team.name || team.team_id)}</b><small>${capacityLabel}</small></span></label>`;
      }).join('') || '<div class="empty-state">Команды не найдены.</div>'}`;

    const taskQuery = searchValue('#constructorTaskSearch');
    const allTasks = catalogWithDraft('tasks');
    const tasks = allTasks.filter((task) => `${task.task_id} ${task.title} ${task.initiative_id || ''}`.toLowerCase().includes(taskQuery));
    const selectableTasks = allTasks.filter((task) => !isHistoricalDoneTask(task));
    $c('#constructorTaskCatalog').innerHTML = `${!taskQuery && selectableTasks.length ? selectAllControl('tasks', selectableTasks) : ''}${tasks.map((task) => { const completed = isHistoricalDoneTask(task); return `<div class="constructor-option constructor-task-option ${completed ? 'constructor-task-completed' : ''}"><label class="constructor-task-pick"><input type="checkbox" data-constructor-pick="tasks" value="${esc(task.task_id)}" ${completed || findDraft('tasks', task.task_id) ? 'checked' : ''} ${completed ? 'disabled' : ''}><span><b>${esc(task.task_id)} · ${esc(task.title)}</b><small>R${esc(task.rung)} · ${number(task.story_points)} SP</small>${completed ? '<small>✓ Завершена до начала планирования</small>' : ''}</span></label><button type="button" class="constructor-task-open" data-open-task="${esc(task.task_id)}" aria-label="Открыть задачу ${esc(task.task_id)}">↗</button></div>`; }).join('') || '<div class="empty-state">Задачи не найдены.</div>'}`;
    updateSelectAllIndeterminate();
  }

  function entityRows(title, type, items, describe) {
    const key = idField(type);
    return `<div class="constructor-draft-group"><h3>${esc(title)} <span>${items.length}</span></h3>${items.length ? items.map((item) => { const historical = type === 'tasks' && isHistoricalDoneTask(item); return `<div class="constructor-draft-row"><span><b>${esc(item[key])}${type === 'tasks' ? ` · ${esc(item.title)}` : ''}</b>${historical ? '<small>Завершена до начала планирования</small>' : ''}${describe(item) ? `<small>${esc(describe(item))}</small>` : ''}</span><span>${historical ? '' : `<button type="button" data-constructor-edit="${type}" data-id="${esc(item[key])}" aria-label="Редактировать">✎</button>`}<button type="button" data-constructor-remove="${type}" data-id="${esc(item[key])}" aria-label="Удалить">×</button></span></div>`; }).join('') : '<p class="muted">Ничего не выбрано.</p>'}</div>`;
  }

  function renderDraft() {
    $c('#constructorCounters').innerHTML = [
      ['Команды', draft.teams.length], ['Инженеры', draft.engineers.length], ['Задачи', draft.tasks.length],
    ].map(([label, value]) => `<div><b>${value}</b><span>${label}</span></div>`).join('');
    $c('#constructorDraftEntities').innerHTML = [
      entityRows('Команды', 'teams', draft.teams, (item) => item.expected_velocity_sp != null ? `Исторический velocity: ${number(item.expected_velocity_sp)} SP` : `Ручная capacity: ${item.capacity_sp == null ? 'не задана' : number(item.capacity_sp)} SP`),
      entityRows('Задачи', 'tasks', draft.tasks, () => ''),
    ].join('');
  }

  function automaticEngineers() {
    const selectedTeams = new Set(draft.teams.map((team) => team.team_id));
    const engineers = catalog.engineers.map((engineer) => ({
      ...clone(engineer),
      assignments: engineer.assignments.filter((assignment) => selectedTeams.has(assignment.team_id)),
    })).filter((engineer) => engineer.assignments.length > 0);
    const usedIds = new Set(engineers.map((engineer) => engineer.engineer_id));
    draft.engineers.forEach((engineer) => {
      const scenarioAssignments = (engineer.assignments || []).filter((assignment) => selectedTeams.has(assignment.team_id));
      if (!scenarioAssignments.length) return;
      const scenarioEngineer = {...clone(engineer), assignments: scenarioAssignments};
      const existingIndex = engineers.findIndex((item) => item.engineer_id === engineer.engineer_id);
      if (existingIndex >= 0) engineers[existingIndex] = scenarioEngineer;
      else engineers.push(scenarioEngineer);
      usedIds.add(engineer.engineer_id);
    });
    draft.teams.forEach((team) => (team.engineer_ids || []).forEach((engineerId) => {
      if (usedIds.has(engineerId)) return;
      const source = findCatalog('engineers', engineerId);
      const availability = source?.free_assignment;
      if (!source || source.assignments.length || !availability?.role) return;
      engineers.push({
        ...clone(source),
        assignments: [{
          team_id: team.team_id,
          role: availability.role,
          grade: availability.grade || null,
          capacity_rate: Number(availability.capacity_rate || 1),
        }],
      });
      usedIds.add(engineerId);
    }));
    return engineers;
  }

  function errorsForDraft() {
    const errors = [];
    const taskIds = new Set(draft.tasks.map((item) => item.task_id));
    if (!draft.teams.length) errors.push('Выберите или добавьте хотя бы одну команду.');
    if (!draft.tasks.length) errors.push('Выберите или добавьте хотя бы одну задачу.');
    const engineers = automaticEngineers();
    const engineerTeams = new Set(engineers.flatMap((engineer) => engineer.assignments.map((assignment) => assignment.team_id)));
    draft.teams.forEach((team) => {
      if (!engineerTeams.has(team.team_id)) errors.push(`Для команды ${team.name || team.team_id} не выбран ни один доступный инженер.`);
    });
    if (draft.teams.length && !engineers.length) errors.push('Для выбранных команд в исходном датасете не найден доступный ресурс.');
    draft.tasks.forEach((task) => {
      if (isHistoricalDoneTask(task)) return;
      (task.depends_on || []).forEach((dependency) => {
        if (taskIds.has(dependency) || isHistoricalDoneTask(findDraft('tasks', dependency) || findCatalog('tasks', dependency))) return;
        if (findCatalog('tasks', dependency)) {
          errors.push(`Активная блокирующая задача ${dependency} не включена в сценарий планирования.`);
        }
        // Unknown or archived historical blockers are validated by the server,
        // which has access to the complete dependency catalog.
      });
    });
    return [...new Set(errors)];
  }

  function showErrors(errors) {
    const box = $c('#constructorErrors');
    box.hidden = !errors.length;
    box.innerHTML = errors.length ? `<b>Нужно исправить:</b><ul>${errors.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>` : '';
  }

  function requirementRow(requirement = {}) {
    return `<div class="constructor-repeat-row constructor-requirement-row"><label>Роль<input name="requirement_role" list="constructorRoleOptions" value="${esc(requirement.role)}" required></label><label>Часы<input name="requirement_hours" type="number" min="0.01" step="0.01" value="${esc(requirement.required_hours ?? 8)}" required></label><label class="wide">Навыки через запятую<input name="requirement_skills" value="${esc((requirement.skills || []).join(', '))}" placeholder="Python, Kafka"></label><button type="button" class="repeat-remove" data-remove-repeat aria-label="Удалить">×</button></div>`;
  }

  function skillSourceLabel(item) {
    if (item.rejected) return 'Отклонено пользователем';
    if (item.source === 'both') return 'Указано вручную и найдено в названии';
    if (item.source === 'title') return item.confirmed ? 'Найдено в названии' : 'Требует подтверждения';
    return 'Указано вручную';
  }

  function skillRoleOptions(selected = '') {
    const roles = $$c('.constructor-requirement-row [name="requirement_role"]')
      .map((input) => input.value.trim()).filter(Boolean);
    return `<option value="">Для всей задачи</option>${[...new Set(roles)].map((role) => `<option value="${esc(role)}" ${role === selected ? 'selected' : ''}>${esc(role)}</option>`).join('')}`;
  }

  function renderSkillRequirements() {
    const target = $c('#constructorSkillRequirementList');
    if (!target) return;
    target.innerHTML = editingSkillRequirements.length ? editingSkillRequirements.map((item, index) => `<div class="constructor-skill-row ${item.rejected ? 'rejected' : ''}"><div><b>${esc(item.technology)}</b><small>${esc(skillSourceLabel(item))}${item.matched_text ? ` · «${esc(item.matched_text)}»` : ''}</small></div><select data-skill-role="${index}" aria-label="Роль для ${esc(item.technology)}" ${item.rejected ? 'disabled' : ''}>${skillRoleOptions(item.role || '')}</select><div>${item.source === 'title' && !item.confirmed && !item.rejected ? `<button type="button" class="text-button" data-confirm-skill="${index}">Подтвердить</button>` : ''}<button type="button" class="text-button" data-reject-skill="${index}">${item.rejected ? 'Вернуть' : 'Убрать'}</button></div></div>`).join('') : '';
  }

  async function refreshSkillExtraction(extraExplicit = null) {
    const form = $c('#constructorEntityForm');
    if (!form || form.dataset.formType !== 'tasks' || !api?.extractTaskSkills) return;
    const explicit = editingSkillRequirements.filter((item) => !item.rejected && item.source !== 'title');
    if (extraExplicit) explicit.push({technology: extraExplicit, source: 'explicit', confirmed: true});
    const rejected = editingSkillRequirements.filter((item) => item.rejected).map((item) => item.technology);
    const roles = $$c('.constructor-requirement-row [name="requirement_role"]', form).map((input) => input.value.trim()).filter(Boolean);
    try {
      const result = await api.extractTaskSkills({
        task_id: String(new FormData(form).get('task_id') || 'preview'),
        title: String(new FormData(form).get('title') || ''), roles, explicit, rejected,
      });
      const rejectedRows = editingSkillRequirements.filter((item) => item.rejected);
      editingSkillRequirements = [...(result.requirements || []), ...rejectedRows];
      renderSkillRequirements();
      const input = $c('#constructorSkillSearch');
      if (input) input.value = '';
    } catch (_) {
      // Planner validation will still canonicalize the saved scenario.
    }
  }

  function scheduleSkillExtraction() {
    window.clearTimeout(skillExtractionTimer);
    skillExtractionTimer = window.setTimeout(() => refreshSkillExtraction(), 220);
  }

  function dependencyCandidates(currentTaskId) {
    const combined = [...catalog.tasks, ...draft.tasks];
    const seen = new Set();
    return combined.filter((task) => {
      if (task.task_id === currentTaskId || seen.has(task.task_id)) return false;
      seen.add(task.task_id);
      return true;
    }).sort((left, right) => left.task_id.localeCompare(right.task_id));
  }

  function dependencyPicker(task) {
    const selected = new Set(task.depends_on || []);
    const options = dependencyCandidates(task.task_id);
    return `<div class="constructor-dependency-field"><span class="constructor-field-label">Зависит от</span><div class="constructor-dependency-picker"><button type="button" id="constructorDependencyToggle" class="constructor-dropdown-button" aria-haspopup="listbox" aria-expanded="false"><span id="constructorDependencyLabel">${selected.size ? `Выбрано: ${selected.size}` : 'Выбрать задачи'}</span><span>⌄</span></button><div id="constructorDependencyMenu" class="constructor-dropdown-menu" role="listbox" hidden><input id="constructorDependencySearch" type="search" placeholder="Поиск задачи"><div id="constructorDependencyOptions">${options.map((candidate) => `<label data-dependency-option data-search="${esc(`${candidate.task_id} ${candidate.title}`.toLowerCase())}"><input type="checkbox" name="dependency_id" value="${esc(candidate.task_id)}" ${selected.has(candidate.task_id) ? 'checked' : ''}><span><b>${esc(candidate.task_id)}</b> · ${esc(candidate.title)}</span></label>`).join('') || '<p class="muted">Других задач пока нет.</p>'}</div></div></div><div id="constructorDependencyChips" class="constructor-dependency-chips"></div></div>`;
  }

  function sharedDataLists() {
    const initiatives = [...new Set([...catalog.tasks, ...draft.tasks].map((task) => task.initiative_id).filter(Boolean))].sort();
    return `<datalist id="constructorRoleOptions">${(catalog.options.roles || []).map((item) => `<option value="${esc(item)}"></option>`).join('')}</datalist><datalist id="constructorGradeOptions">${(catalog.options.grades || []).map((item) => `<option value="${esc(item)}"></option>`).join('')}</datalist><datalist id="constructorInitiativeOptions">${initiatives.map((item) => `<option value="${esc(item)}"></option>`).join('')}</datalist>`;
  }

  function engineerTeamOptions(selectedTeamId = '') {
    const teams = catalogWithDraft('teams').sort((left, right) => String(left.name || left.team_id).localeCompare(String(right.name || right.team_id), 'ru'));
    return teams.map((team) => `<option value="${esc(team.team_id)}" ${team.team_id === selectedTeamId ? 'selected' : ''}>${esc(team.name || team.team_id)}</option>`).join('');
  }

  function freeEngineerCandidates(currentTeamId = null) {
    const reservedByOtherTeams = new Set(draft.teams
      .filter((team) => team.team_id !== currentTeamId)
      .flatMap((team) => team.engineer_ids || []));
    return catalog.engineers.filter((engineer) => (
      engineer.assignments.length === 0
      && engineer.free_assignment?.role
      && !reservedByOtherTeams.has(engineer.engineer_id)
    ));
  }

  function freeEngineerPicker(team) {
    const selected = new Set(team.engineer_ids || []);
    const engineers = freeEngineerCandidates(team.team_id);
    const options = engineers.map((engineer) => {
      const assignment = engineer.free_assignment;
      const details = [assignment.role, assignment.grade, `${number(Number(assignment.capacity_rate || 1) * 100)}%`].filter(Boolean).join(' · ');
      return `<label class="constructor-option"><input type="checkbox" name="team_engineer_id" value="${esc(engineer.engineer_id)}" ${selected.has(engineer.engineer_id) ? 'checked' : ''}><span><b>${esc(engineer.name || engineer.engineer_id)}</b><small>${esc(details)}</small></span></label>`;
    }).join('');
    return `<fieldset class="constructor-free-engineers"><legend>Свободные инженеры</legend><p class="muted">Можно выбрать только специалистов, которые не закреплены за другой командой.</p><div class="constructor-pick-list">${options || '<div class="empty-state">Свободных инженеров сейчас нет.</div>'}</div></fieldset>`;
  }

  function updateDependencyView() {
    const form = $c('#constructorEntityForm');
    if (!form || form.dataset.formType !== 'tasks') return;
    const selected = $$c('[name="dependency_id"]:checked', form);
    $c('#constructorDependencyLabel').textContent = selected.length ? `Выбрано: ${selected.length}` : 'Выбрать задачи';
    $c('#constructorDependencyChips').innerHTML = selected.map((input) => `<span>${esc(input.value)}</span>`).join('');
  }

  function updateDependencyMenuPlacement() {
    const toggle = $c('#constructorDependencyToggle');
    const menu = $c('#constructorDependencyMenu');
    if (!toggle || !menu || menu.hidden) return;
    const toggleRect = toggle.getBoundingClientRect();
    const menuHeight = menu.getBoundingClientRect().height;
    const spaceBelow = window.innerHeight - toggleRect.bottom;
    const spaceAbove = toggleRect.top;
    menu.classList.toggle('opens-up', spaceBelow < menuHeight && spaceAbove > spaceBelow);
  }

  function openForm(type, id = null) {
    const dialog = $c('#constructorDialog');
    const item = id ? clone(findDraft(type, id)) : null;
    const content = $c('#constructorDialogContent');
    if (type === 'teams') {
      const team = item || {team_id: '', name: '', expected_velocity_sp: null, capacity_sp: '', engineer_ids: []};
      const historicalVelocity = team.expected_velocity_sp != null;
      const scenarioCapacity = historicalVelocity
        ? `<p class="muted">Исторический velocity: <b>${number(team.expected_velocity_sp)} SP</b>. Capacity команды будет рассчитана по историческим данным.</p>`
        : `<label>Ручная capacity за спринт, SP<input name="capacity_sp" type="number" inputmode="numeric" min="0" step="1" value="${esc(team.capacity_sp ?? '')}" required><small>Для новой команды укажите capacity целым числом SP за спринт.</small></label>`;
      content.innerHTML = `<h2>${item ? 'Изменить настройки сценария для' : 'Добавить'} команду</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="teams" data-editing="${Boolean(item)}"><label>ID команды<input name="team_id" value="${esc(team.team_id)}" ${item ? 'readonly' : ''} required></label><label>Название<input name="name" value="${esc(team.name)}" ${item ? 'readonly' : ''} required></label>${scenarioCapacity}${!item ? freeEngineerPicker(team) : ''}<div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
    }
    if (type === 'engineers') {
      const engineer = item || {engineer_id: '', name: '', assignments: [{team_id: '', role: '', grade: '', capacity_rate: 1}], skills: []};
      const assignment = engineer.assignments?.[0] || {team_id: '', role: '', grade: '', capacity_rate: 1};
      const capacityRate = Number(assignment.capacity_rate) === 0.5 ? 0.5 : 1;
      content.innerHTML = `<h2>${item ? 'Изменить' : 'Добавить'} инженера</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="engineers" data-editing="${Boolean(item)}"><div class="constructor-form-grid"><label>ID инженера<input name="engineer_id" value="${esc(engineer.engineer_id)}" ${item ? 'readonly' : ''} required></label><label>Имя<input name="name" value="${esc(engineer.name)}" required></label><label>Команда<select name="team_id" required><option value="">Выберите команду</option>${engineerTeamOptions(assignment.team_id)}</select></label><label>Роль<input name="role" list="constructorRoleOptions" value="${esc(assignment.role)}" required></label><label>Грейд<input name="grade" list="constructorGradeOptions" value="${esc(assignment.grade || '')}" placeholder="Junior, Middle, Senior" required></label><label>Доступность<select name="capacity_rate" required><option value="1" ${capacityRate === 1 ? 'selected' : ''}>1 — полная доступность</option><option value="0.5" ${capacityRate === 0.5 ? 'selected' : ''}>0.5 — половина времени</option></select></label><label class="constructor-wide-field">Навыки через запятую<input name="skills" value="${esc((engineer.skills || []).join(', '))}" placeholder="Python, Kafka, PostgreSQL" required></label></div>${sharedDataLists()}<div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
    }
    if (type === 'tasks') {
      const task = item || {task_id: '', title: '', initiative_id: '', team_id: null, status: 'ToDo', rung: 0, story_points: 1, role_requirements: [{role: '', required_hours: 8, skills: []}], required_skills: [], depends_on: []};
      editingSkillRequirements = clone(task.required_skills || []);
      const skillOptions = (catalog.options.skills || []).map((skill) => `<option value="${esc(skill)}"></option>`).join('');
      content.innerHTML = `<h2>${item ? 'Изменить' : 'Добавить'} задачу</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="tasks" data-editing="${Boolean(item)}"><div class="constructor-form-grid"><label>ID задачи<input name="task_id" value="${esc(task.task_id)}" ${item ? 'readonly' : ''} required></label><label>Название<input name="title" value="${esc(task.title)}" required></label><label>Инициатива<input name="initiative_id" list="constructorInitiativeOptions" value="${esc(task.initiative_id)}" placeholder="Выберите или введите PRODF"></label><label>Статус<select name="status">${optionList(['ToDo', 'InProgress', 'Done'], task.status)}</select></label><label>Приоритет Rung<input name="rung" type="number" step="0.01" value="${esc(task.rung)}" required></label><label class="constructor-sp-field">Story Points<input name="story_points" type="text" inputmode="decimal" pattern="[0-9]+([.,][0-9]+)?" value="${esc(task.story_points)}" required><small>Отдельно от человеко-часов.</small></label>${dependencyPicker(task)}</div><h3>Трудозатраты</h3><div id="constructorRequirementRows">${task.role_requirements.map(requirementRow).join('')}</div><button class="text-button" type="button" data-add-requirement>+ Добавить роль</button><section class="constructor-skills"><h3>Требуемые компетенции</h3><p class="muted">Точные технологии из названия добавляются автоматически. Предложения можно подтвердить или отклонить.</p><div class="constructor-skill-add"><input id="constructorSkillSearch" list="constructorSkillOptions" placeholder="Найти навык или алиас"><datalist id="constructorSkillOptions">${skillOptions}</datalist><button type="button" class="btn btn-outline" data-add-task-skill>Добавить</button></div><div id="constructorSkillRequirementList"></div></section>${sharedDataLists()}<div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
    }
    document.body.classList.add('constructor-modal-open');
    dialog.showModal();
    updateDependencyView();
    if (type === 'tasks') {
      renderSkillRequirements();
      if (!item) scheduleSkillExtraction();
    }
  }

  function saveEntity(form) {
    const type = form.dataset.formType;
    const values = new FormData(form);
    const key = idField(type);
    const id = String(values.get(key) || '').trim();
    const editing = form.dataset.editing === 'true';
    const existing = editing ? findDraft(type, id) : null;
    if (!editing && (findCatalog(type, id) || draft[type].some((item) => item[key] === id))) {
      const input = form.elements[key];
      input.setCustomValidity(`ID ${id} уже есть. Выберите его из списка.`);
      input.reportValidity();
      input.addEventListener('input', () => input.setCustomValidity(''), {once: true});
      return;
    }
    let entity;
    if (type === 'teams') {
      entity = {team_id: id, name: String(values.get('name') || '').trim(), expected_velocity_sp: Number(values.get('expected_velocity_sp')), engineer_ids: values.getAll('team_engineer_id').map(String), source: existing?.source || 'draft'};
    }
    if (type === 'engineers') {
      const teamId = String(values.get('team_id') || '').trim();
      entity = {
        engineer_id: id,
        name: String(values.get('name') || '').trim(),
        assignments: [{
          team_id: teamId,
          role: String(values.get('role') || '').trim(),
          grade: String(values.get('grade') || '').trim(),
          capacity_rate: Number(values.get('capacity_rate')),
        }],
        skills: csvValues(values.get('skills')),
        source: existing?.source || 'draft',
      };
      if (!findDraft('teams', teamId)) {
        const sourceTeam = findCatalog('teams', teamId);
        if (sourceTeam) draft.teams.push(clone(sourceTeam));
      }
    }
    if (type === 'tasks') {
      const storyPoints = Number(String(values.get('story_points')).replace(',', '.'));
      const requirements = $$c('.constructor-requirement-row', form).map((row) => ({
        role: $c('[name="requirement_role"]', row).value.trim(),
        required_hours: Number($c('[name="requirement_hours"]', row).value),
        skills: csvValues($c('[name="requirement_skills"]', row).value),
      }));
      const dependsOn = $$c('[name="dependency_id"]:checked', form).map((input) => input.value);
      entity = {task_id: id, title: String(values.get('title') || '').trim(), initiative_id: String(values.get('initiative_id') || '').trim() || null, team_id: existing?.team_id || null, status: String(values.get('status')), rung: Number(values.get('rung')), story_points: storyPoints, role_requirements: requirements, required_skills: clone(editingSkillRequirements), depends_on: dependsOn, planned_start: existing?.planned_start || null, deadline: existing?.deadline || null, source: existing?.source || 'draft'};
      dependsOn.forEach((dependencyId) => {
        if (findDraft('tasks', dependencyId)) return;
        const source = findCatalog('tasks', dependencyId);
        if (source && !isHistoricalDoneTask(source)) draft.tasks.push(clone(source));
      });
    }
    if (existing) draft[type] = draft[type].map((item) => item[key] === id ? entity : item);
    else draft[type].push(entity);
    if (type === 'tasks' && !editing) $c('#constructorTaskSearch').value = '';
    invalidatePreview();
    saveDraft();
    $c('#constructorDialog').close();
    render();
    if (type === 'tasks' && !editing) {
      window.dispatchEvent(new CustomEvent('postcode:constructor-task-added', {detail: {taskId: id}}));
    }
    if (type === 'engineers' && !editing) {
      window.dispatchEvent(new CustomEvent('postcode:constructor-engineer-added', {detail: {engineerId: id}}));
    }
    if (type === 'teams' && !editing) {
      window.dispatchEvent(new CustomEvent('postcode:constructor-team-added', {detail: {teamId: id}}));
    }
  }

  async function buildPlan() {
    const errors = errorsForDraft();
    showErrors(errors);
    if (errors.length) return;
    const button = $c('#buildConstructorPlan');
    button.disabled = true;
    button.textContent = 'Строим план…';
    try {
      const tasks = draft.tasks.map((task) => ({...task}));
      const teams = draft.teams.map(({engineer_ids: _engineerIds, focus_factor: _legacyFocusFactor, ...team}) => team);
      const planResult = await api.buildConstructorPlan({teams, engineers: automaticEngineers(), tasks});
      showErrors([]);
      previewPlan = planResult;
      shownRecommendationGroups = 3;
      shownRecommendations.clear();
      locallyRejectedRecommendations.clear();
      renderPreview();
      $c('#constructorPreview')?.scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (error) {
      showErrors(String(error?.message || error).split('\n').filter(Boolean));
    } finally {
      button.disabled = false;
      button.textContent = 'Построить план';
    }
  }

  function focusChangedTasks(effects) {
    if (!showPlanChanges) {
      changedTaskEffects = [];
      return;
    }
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const cards = (effects || []).flatMap((effect) => $$c(`[data-preview-task="${CSS.escape(effect.task_id)}"]`)
      .filter((card) => effect.to_sprint == null || Number(card.dataset.previewSprint) === Number(effect.to_sprint)));
    const uniqueCards = [...new Set(cards)];
    (uniqueCards[0] || $c('#constructorSprintPlan'))?.scrollIntoView({behavior: reducedMotion ? 'auto' : 'smooth', block: 'center'});
    if (!reducedMotion) uniqueCards.forEach((card) => {
      card.classList.add('task-card--just-added');
      window.setTimeout(() => card.classList.remove('task-card--just-added'), 900);
    });
    changedTaskEffects = [];
  }

  function applyOptimisticDelta(recommendationId) {
    const optimization = previewPlan?.optimization || {};
    const recommendation = (optimization.recommendations || []).find((item) => item.recommendation_id === recommendationId);
    const delta = recommendation?.plan_delta;
    if (!delta || (optimization.accepted_changes || []).length || delta.base_fingerprint !== optimization.input_plan_fingerprint || delta.recommendation_signature !== recommendationId) return false;
    const states = new Map((delta.task_states || []).map((task) => [task.task_id, task]));
    previewPlan.tasks = (previewPlan.tasks || []).map((task) => states.has(task.task_id) ? clone(states.get(task.task_id)) : task);
    previewPlan.optimization.current_summary = clone(delta.resulting_summary || optimization.current_summary);
    previewPlan.optimization.changed_tasks = clone(delta.changed_tasks || []);
    changedTaskEffects = clone(delta.changed_tasks || []);
    renderPreview();
    focusChangedTasks(changedTaskEffects);
    return true;
  }

  async function updateRecommendation(action, id) {
    if (!previewPlan?.draft?.id || recommendationUpdateInFlight) return;
    const draftId = previewPlan.draft.id;
    const previous = clone(previewPlan);
    recommendationUpdateInFlight = true;
    activeRecommendationId = action === 'applyAll' ? 'apply-all' : id;
    renderPreview();
    if (action === 'reject') {
      const recommendation = (previewPlan.optimization?.recommendations || []).find((item) => item.recommendation_id === id);
      if (recommendation) locallyRejectedRecommendations.set(id, clone(recommendation));
      recommendationUpdateInFlight = false;
      activeRecommendationId = null;
      renderPreview();
      return;
    }
    try {
      if (action === 'apply') applyOptimisticDelta(id);
      if (action === 'apply') previewPlan = await api.applyConstructorRecommendation(draftId, id);
      if (action === 'undo') previewPlan = await api.undoConstructorChange(draftId, id);
      if (action === 'reset') previewPlan = await api.resetConstructorDraft(draftId);
      if (action === 'applyAll') previewPlan = await api.applyAllConstructorRecommendations(draftId);
      if (action === 'apply') locallyRejectedRecommendations.delete(id);
      if (action === 'reset' || action === 'applyAll') locallyRejectedRecommendations.clear();
      changedTaskEffects = previewPlan.optimization?.changed_tasks || [];
      shownRecommendationGroups = 3;
      shownRecommendations.clear();
      recommendationUpdateInFlight = false;
      activeRecommendationId = null;
      renderPreview();
      if (changedTaskEffects.length || action === 'applyAll') focusChangedTasks(changedTaskEffects);
    } catch (error) {
      previewPlan = previous;
      recommendationUpdateInFlight = false;
      activeRecommendationId = null;
      renderPreview();
      const errorBox = $c('#constructorApprovalError');
      if (errorBox) { errorBox.textContent = String(error?.message || error); errorBox.hidden = false; }
    }
  }

  function bindEvents() {
    if (bound) return;
    bound = true;
    ['#constructorTeamSearch', '#constructorTaskSearch'].forEach((selector) => $c(selector).addEventListener('input', renderCatalogs));
    $c('#addConstructorTeam').addEventListener('click', () => openForm('teams'));
    $c('#addConstructorTask').addEventListener('click', () => openForm('tasks'));
    $c('#uploadConstructorDataset').addEventListener('click', () => $c('#constructorDatasetInput').click());
    $c('#constructorDatasetInput').addEventListener('change', (event) => {
      const file = event.target.files?.[0];
      if (file) uploadDataset(file);
    });
    $$c('[data-constructor-mode]').forEach((button) => button.addEventListener('click', () => {
      if (button.dataset.constructorMode !== mode) invalidatePreview();
      setMode(button.dataset.constructorMode);
    }));
    $c('#buildConstructorPlan').addEventListener('click', buildPlan);
    $c('#clearConstructorDraft').addEventListener('click', () => {
      if (!confirm('Очистить весь локальный черновик? Исходный датасет не изменится.')) return;
      draft = emptyDraft(); invalidatePreview(); saveDraft(); showErrors([]); render();
    });
    $c('#constructorDialog').addEventListener('close', () => document.body.classList.remove('constructor-modal-open'));
    $c('#dataPreflightDialog').addEventListener('close', () => document.body.classList.remove('constructor-modal-open'));
    document.addEventListener('change', (event) => {
      const archiveDecision = event.target.closest('[data-archive-decision]');
      if (archiveDecision && pendingDatasetUpload) {
        pendingDatasetUpload.archiveDecisions[archiveDecision.dataset.archiveDecision] = archiveDecision.value;
        updateArchiveImportSummary();
        return;
      }
      const existingDecision = event.target.closest('[data-existing-decision]');
      if (existingDecision && pendingDatasetUpload) {
        pendingDatasetUpload.existingDecisions[existingDecision.dataset.existingDecision] = existingDecision.value;
        updateArchiveImportSummary();
        return;
      }
      if (event.target.id === 'archiveEntityTypeFilter') { filterArchiveRows(); return; }
      const selectAll = event.target.closest('[data-constructor-select-all]');
      if (selectAll) {
        const type = selectAll.dataset.constructorSelectAll;
        toggleAllCatalog(type, type === 'teams' ? catalogWithDraft('teams') : catalogWithDraft('tasks').filter((task) => !isHistoricalDoneTask(task)), selectAll.checked);
        return;
      }
      const pick = event.target.closest('[data-constructor-pick]');
      if (pick) { toggleCatalog(pick.dataset.constructorPick, pick.value, pick.checked); return; }
      if (event.target.matches('[name="dependency_id"]')) updateDependencyView();
      if (event.target.matches('[name="requirement_role"]')) {
        renderSkillRequirements();
        scheduleSkillExtraction();
      }
      if (event.target.matches('[data-skill-role]')) {
        const item = editingSkillRequirements[Number(event.target.dataset.skillRole)];
        if (item) item.role = event.target.value || null;
      }
      if (event.target.id === 'showPlanChanges') {
        showPlanChanges = event.target.checked;
        if (!showPlanChanges) changedTaskEffects = [];
        renderPreview();
        return;
      }
      if (event.target.id === 'previewExclusionSort') {
        exclusionReasonPriority = event.target.value;
        renderPreview();
        return;
      }
    });
    document.addEventListener('input', (event) => {
      if (event.target.id === 'archiveEntitySearch') { filterArchiveRows(); return; }
      if (event.target.matches('#constructorEntityForm [name="title"]')) { scheduleSkillExtraction(); return; }
      if (event.target.id !== 'constructorDependencySearch') return;
      const query = event.target.value.trim().toLowerCase();
      $$c('[data-dependency-option]').forEach((option) => { option.hidden = !option.dataset.search.includes(query); });
      updateDependencyMenuPlacement();
    });
    document.addEventListener('click', (event) => {
      const archiveBulk = event.target.closest('[data-archive-bulk]');
      if (archiveBulk && pendingDatasetUpload) { applyArchiveBulk(archiveBulk.dataset.archiveBulk); return; }
      const existingBulk = event.target.closest('[data-existing-bulk]');
      if (existingBulk && pendingDatasetUpload) { applyExistingBulk(existingBulk.dataset.existingBulk); return; }
      if (event.target.closest('[data-close-data-preflight]')) { closeDataPreflight(); return; }
      if (event.target.closest('[data-replace-data-file]')) {
        closeDataPreflight();
        $c('#constructorDatasetInput').click();
        return;
      }
      if (event.target.closest('[data-continue-data-preflight]')) { continueDatasetUpload(); return; }
      if (event.target.closest('#approveConstructorPlan')) { approvePlan(); return; }
      const apply = event.target.closest('[data-recommendation-apply]');
      if (apply) { updateRecommendation('apply', apply.dataset.recommendationApply); return; }
      const reject = event.target.closest('[data-recommendation-reject]');
      if (reject) { updateRecommendation('reject', reject.dataset.recommendationReject); return; }
      const undo = event.target.closest('[data-change-undo]');
      if (undo) { updateRecommendation('undo', undo.dataset.changeUndo); return; }
      if (event.target.closest('#resetConstructorDraft')) { updateRecommendation('reset'); return; }
      if (event.target.closest('#applyAllRecommendations')) { updateRecommendation('applyAll'); return; }
      if (event.target.closest('#showMoreRecommendationGroups')) { shownRecommendationGroups += 3; renderPreview(); return; }
      const more = event.target.closest('[data-more-recommendations]');
      if (more) { shownRecommendations.set(more.dataset.moreRecommendations, (shownRecommendations.get(more.dataset.moreRecommendations) || 3) + 3); renderPreview(); return; }
      const edit = event.target.closest('[data-constructor-edit]');
      if (edit) { openForm(edit.dataset.constructorEdit, edit.dataset.id); return; }
      const remove = event.target.closest('[data-constructor-remove]');
      if (remove) { toggleCatalog(remove.dataset.constructorRemove, remove.dataset.id, false); return; }
      if (event.target.closest('[data-close-constructor-form]')) { $c('#constructorDialog').close(); return; }
      if (event.target.closest('[data-add-requirement]')) { $c('#constructorRequirementRows').insertAdjacentHTML('beforeend', requirementRow()); return; }
      if (event.target.closest('[data-add-task-skill]')) {
        const value = $c('#constructorSkillSearch')?.value.trim();
        if (value) refreshSkillExtraction(value);
        return;
      }
      const confirmSkill = event.target.closest('[data-confirm-skill]');
      if (confirmSkill) {
        const item = editingSkillRequirements[Number(confirmSkill.dataset.confirmSkill)];
        if (item) { item.confirmed = true; renderSkillRequirements(); }
        return;
      }
      const rejectSkill = event.target.closest('[data-reject-skill]');
      if (rejectSkill) {
        const item = editingSkillRequirements[Number(rejectSkill.dataset.rejectSkill)];
        if (item) {
          if (item.rejected) { item.rejected = false; item.confirmed = true; }
          else { item.rejected = true; item.confirmed = false; }
          renderSkillRequirements();
        }
        return;
      }
      const repeatRemove = event.target.closest('[data-remove-repeat]');
      if (repeatRemove) {
        const row = repeatRemove.closest('.constructor-repeat-row');
        if (row.parentElement.children.length > 1) row.remove();
        return;
      }
      const dependencyToggle = event.target.closest('#constructorDependencyToggle');
      const dependencyMenu = $c('#constructorDependencyMenu');
      if (dependencyToggle && dependencyMenu) {
        dependencyMenu.hidden = !dependencyMenu.hidden;
        dependencyToggle.setAttribute('aria-expanded', String(!dependencyMenu.hidden));
        if (!dependencyMenu.hidden) {
          updateDependencyMenuPlacement();
          $c('#constructorDependencySearch')?.focus();
        } else {
          dependencyMenu.classList.remove('opens-up');
        }
        return;
      }
      if (dependencyMenu && !event.target.closest('.constructor-dependency-picker')) dependencyMenu.hidden = true;
    });
    document.addEventListener('submit', (event) => {
      if (event.target.id !== 'constructorEntityForm') return;
      event.preventDefault();
      if (event.target.checkValidity()) saveEntity(event.target);
    });
  }

  function render() {
    if (!$c('#page-planning')) return;
    setMode(mode);
    renderCatalogs();
    renderDraft();
    renderPreview();
  }

  function initialize(sourceCatalog, apiClient, handlePlanApproved, handleCatalogUpdated) {
    catalog = sourceCatalog;
    if (catalog?.data_state === 'UNINITIALIZED') {
      mode = 'upload';
      setDatasetStatus('Каталог пуст. Загрузите Excel, чтобы автоматически создать каталог и построить квартальный план.');
    }
    api = apiClient;
    onPlanApproved = typeof handlePlanApproved === 'function' ? handlePlanApproved : () => {};
    onCatalogUpdated = typeof handleCatalogUpdated === 'function' ? handleCatalogUpdated : () => {};
    loadDraft();
    bindEvents();
    render();
  }

  async function approvePlan() {
    if (!previewPlan?.draft?.id) return;
    const button = $c('#approveConstructorPlan');
    const errorBox = $c('#constructorApprovalError');
    button.disabled = true;
    button.textContent = 'Утверждаем план…';
    errorBox.hidden = true;
    try {
      const approvedPlan = await api.approveConstructorPlan(previewPlan.draft.id);
      const refreshedCatalog = approvedPlan.catalog || await api.getConstructorCatalog().catch(() => null);
      if (refreshedCatalog) {
        catalog = refreshedCatalog;
        onCatalogUpdated(refreshedCatalog);
      }
      previewPlan = null;
      onPlanApproved(approvedPlan);
    } catch (error) {
      errorBox.textContent = String(error?.message || error);
      errorBox.hidden = false;
      button.disabled = false;
      button.textContent = 'Утвердить план';
    }
  }

  function updateCatalog(sourceCatalog) {
    catalog = sourceCatalog;
    render();
  }

  function openTaskForm() {
    openForm('tasks');
  }

  function openEngineerForm() {
    openForm('engineers');
  }

  function openTeamForm() {
    openForm('teams');
  }

  return Object.freeze({initialize, updateCatalog, render, openTaskForm, openEngineerForm, openTeamForm});
})();
