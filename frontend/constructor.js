'use strict';

window.planConstructor = (() => {
  const STORAGE_KEY = 'postcode.plan-constructor.v2';
  const emptyDraft = () => ({teams: [], engineers: [], tasks: []});
  let catalog = {teams: [], engineers: [], tasks: [], options: {roles: [], skills: [], grades: []}};
  let draft = emptyDraft();
  let mode = 'manual';
  let api = null;
  let previewPlan = null;
  let onPlanApproved = null;
  let onCatalogUpdated = null;
  let pendingDatasetUpload = null;
  let shownRecommendationGroups = 3;
  const shownRecommendations = new Map();
  const locallyRejectedRecommendations = new Map();
  let changedTaskEffects = [];
  let showPlanChanges = true;
  let activeRecommendationId = null;
  let recommendationUpdateInFlight = false;
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
    return `<article class="recommendation-card ${rejected ? 'recommendation-card--rejected' : ''} ${applying ? 'recommendation-card--loading' : ''}" id="recommendation-${esc(item.recommendation_id)}"><div class="recommendation-card-title"><b>${esc(item.title)}</b><span>${rejected ? `Отклонено · ${esc(label)}` : esc(label)}</span></div>${action ? `<p class="recommendation-action">${esc(action)}</p>` : ''}<div class="recommendation-effect">${effectLines}</div><p class="recommendation-description">${esc(effect.summary || 'План станет выполнимее.')}</p><details><summary>Подробнее</summary><div class="recommendation-details">${details || '<p>Изменение рассчитано в сохранённом варианте плана.</p>'}</div></details><div class="recommendation-actions">${!rejected ? `<button type="button" class="btn btn-outline" data-recommendation-reject="${esc(item.recommendation_id)}" ${recommendationUpdateInFlight ? 'disabled' : ''}>Отклонить</button>` : ''}<button type="button" class="btn btn-blue" data-recommendation-apply="${esc(item.recommendation_id)}" ${recommendationUpdateInFlight ? 'disabled' : ''}>${applying ? 'Применяем…' : 'Применить'}</button></div></article>`;
  }

  function optimizationSummaryCard(label, summary) {
    return `<div class="optimization-summary-card"><span>${label}</span><b>${number(summary.planned_tasks)} задач</b><small>${number(summary.initiatives)} инициатив завершится полностью</small></div>`;
  }

  function optimizationSummary() {
    const optimization = previewPlan?.optimization || {};
    const base = optimization.base_summary || {};
    const current = optimization.current_summary || {};
    const best = optimization.optimized_summary || {};
    return `<section class="optimization-summary">${optimizationSummaryCard('Базовый план', base)}${optimizationSummaryCard('Текущий вариант', current)}${optimizationSummaryCard('Лучший найденный вариант', best)}</section>`;
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
      ? `<div class="apply-all-recommendations ${optimization.best_selected ? 'apply-all-recommendations--selected' : ''}"><div><b>Лучший найденный совместимый вариант</b><small>Сохранённый вариант не меняется при отдельных действиях.</small></div><button class="btn btn-blue" type="button" id="applyAllRecommendations" ${recommendationUpdateInFlight || optimization.best_selected ? 'disabled' : ''}>${optimization.best_selected ? '✓ Лучший найденный вариант выбран' : 'Применить все рекомендации'}</button></div>`
      : '';
    const rejected = [...locallyRejectedRecommendations.values()];
    const rejectedSection = rejected.length ? `<section class="recommendation-group recommendation-group--rejected"><h4>Отклонённые <small>${rejected.length}</small></h4><div class="recommendation-list">${rejected.map((item) => recommendationCard(item, true)).join('')}</div></section>` : '';
    const activeContent = groups.length ? `<div class="recommendation-groups-scroll">${cards}</div>` : '<p class="muted">Дополнительных улучшений не найдено.</p>';
    return `<section class="preview-section recommendations-section"><div class="recommendations-heading"><div><h3>Как можно улучшить план</h3><p>Отдельные изменения рассчитаны в сохранённом лучшем варианте и применяются независимо.</p></div><label class="recommendations-change-toggle"><input id="showPlanChanges" type="checkbox" ${showPlanChanges ? 'checked' : ''}><span>Показывать изменения</span></label></div><div class="recommendation-comparison"><div><span>Текущий вариант</span><b>${number(current.planned_tasks)} задач · ${number(current.initiatives)} инициатив</b></div><div><span>Лучший найденный</span><b>${number(best.planned_tasks)} задач · ${number(best.initiatives)} инициатив</b></div></div>${applyAll}${activeContent}${rejectedSection}</section>`;
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

  function previewSprintCard(task, sprint) {
    const continuation = Number(task.start_sprint) < sprint;
    const helpers = (task.allocations || []).filter((item) => item.sprint === sprint && item.cross_team);
    const helperNote = helpers.length ? `<small class="preview-cross-team">+ ${new Set(helpers.map((item) => item.engineer_id)).size} специалист(а) из другой команды</small>` : '';
    const reassigned = task.team_reassigned && task.original_team_id
      ? `<small class="preview-reassigned">Исходная команда: ${esc(task.original_team_id)}</small>` : '';
    const changed = changedTaskEffects.some((item) => item.task_id === task.task_id);
    return `<article class="preview-task-card ${continuation ? 'preview-continuation' : ''} ${changed ? 'task-card--just-added' : ''}" data-preview-task="${esc(task.task_id)}" data-preview-sprint="${sprint}"><div><b>${continuation ? '↳ ' : ''}${esc(task.task_id)}</b><span class="rung-badge">R${esc(task.rung)}</span></div><strong>${continuation ? 'Продолжение' : esc(task.title)}</strong><small>${esc(task.assigned_team_id || 'Команда не назначена')}</small>${reassigned}${helperNote}</article>`;
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
    const exclusions = excludedTasks.map((task) => {
      const reasonBlocks = previewReasonBlocks(task);
      return `<article class="preview-exclusion"><div><b>${esc(task.task_id)}</b><span>${esc(task.title)}</span></div><div class="preview-reason-blocks">${reasonBlocks || '<div class="preview-reason-block"><span>Причина ограничения не раскрыта.</span></div>'}</div></article>`;
    }).join('') || '<p class="muted">Все задачи удалось разместить в квартальном плане.</p>';
    const historicalDone = (previewPlan.tasks || []).filter((task) => task.status === 'DONE');
    const historicalBlock = historicalDone.length ? `<section class="preview-section"><h3>Уже завершены до начала планирования</h3><div class="preview-exclusion-list">${historicalDone.map((task) => `<article class="preview-exclusion"><div><b>${esc(task.task_id)}</b><span>${esc(task.title)}</span></div><small>✓ Завершена до начала планирования</small></article>`).join('')}</div></section>` : '';
    target.hidden = false;
    target.innerHTML = `<div class="constructor-preview-heading"><div><h2>Предварительный план</h2><p>План построен без перераспределения задач и специалистов между командами.</p></div></div>${optimizationSummary()}<section class="preview-section" id="constructorSprintPlan"><h3>Распределение по спринтам</h3><div class="preview-board-viewport"><div class="preview-sprint-board">${sprintBoard}</div></div></section>${historicalBlock}<section class="preview-section"><h3>Помощь между командами</h3><div class="preview-help-list">${previewCrossTeamRows(previewPlan)}</div></section><section class="preview-section"><h3>Переносы и исключения</h3><div class="preview-exclusion-list">${exclusions}</div></section>${recommendationPanel()}${acceptedChangesPanel()}<div class="preview-approval"><p>Проверьте распределение перед утверждением. После утверждения этот вариант станет текущим планом.</p><div id="constructorApprovalError" class="constructor-errors" hidden></div><button class="btn btn-blue" id="approveConstructorPlan" type="button">Утвердить план</button></div>`;
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

  function renderDataPreflight(preflight) {
    pendingDatasetUpload = preflight;
    const audit = preflight.audit || {summary: {}, groups: []};
    const critical = Number(audit.summary?.critical || 0);
    const warning = Number(audit.summary?.warning || 0);
    const info = Number(audit.summary?.info || 0);
    $c('#dataPreflightSubtitle').textContent = critical
      ? 'В данных есть ошибки, из-за которых план невозможно построить.'
      : 'Перед построением плана нашли моменты, которые могут повлиять на результат.';
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
    setDatasetStatus(`${filename} · предварительный план готов · ${planned} задач вошли в квартал`, 'success');
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
      const result = await api.continueConstructorDataset(pendingDatasetUpload.upload_id);
      const filename = pendingDatasetUpload.file?.name || 'Excel';
      closeDataPreflight();
      acceptUploadedPlan(result, filename);
    } catch (error) {
      setDatasetStatus(String(error?.message || error).replaceAll('\n', ' · '), 'error');
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
      button.textContent = 'Загрузить и построить план';
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
    const teams = catalog.teams
      .filter((team) => `${team.team_id} ${team.name}`.toLowerCase().includes(teamQuery))
      .sort((left, right) => (
        Number(right.expected_velocity_sp || 0) - Number(left.expected_velocity_sp || 0)
        || String(left.name || left.team_id).localeCompare(String(right.name || right.team_id), 'ru')
      ));
    $c('#constructorTeamCatalog').innerHTML = `${!teamQuery && catalog.teams.length ? selectAllControl('teams', catalog.teams) : ''}${teams.map((team) => `<label class="constructor-option"><input type="checkbox" data-constructor-pick="teams" value="${esc(team.team_id)}" ${findDraft('teams', team.team_id) ? 'checked' : ''}><span><b>${esc(team.name || team.team_id)}</b><small>${esc(team.expected_velocity_sp)} SP</small></span></label>`).join('') || '<div class="empty-state">Команды не найдены.</div>'}`;

    const taskQuery = searchValue('#constructorTaskSearch');
    const allTasks = catalogWithDraft('tasks');
    const tasks = allTasks.filter((task) => `${task.task_id} ${task.title} ${task.initiative_id || ''}`.toLowerCase().includes(taskQuery));
    const selectableTasks = allTasks.filter((task) => !isHistoricalDoneTask(task));
    $c('#constructorTaskCatalog').innerHTML = `${!taskQuery && selectableTasks.length ? selectAllControl('tasks', selectableTasks) : ''}${tasks.map((task) => { const completed = isHistoricalDoneTask(task); return `<div class="constructor-option constructor-task-option ${completed ? 'constructor-task-completed' : ''}"><label class="constructor-task-pick"><input type="checkbox" data-constructor-pick="tasks" value="${esc(task.task_id)}" ${completed || findDraft('tasks', task.task_id) ? 'checked' : ''} ${completed ? 'disabled' : ''}><span><b>${esc(task.task_id)} · ${esc(task.title)}</b>${completed ? '<small>✓ Завершена до начала планирования</small>' : ''}</span></label><button type="button" class="constructor-task-open" data-open-task="${esc(task.task_id)}" aria-label="Открыть задачу ${esc(task.task_id)}">↗</button></div>`; }).join('') || '<div class="empty-state">Задачи не найдены.</div>'}`;
    updateSelectAllIndeterminate();
  }

  function entityRows(title, type, items, describe) {
    const key = idField(type);
    return `<div class="constructor-draft-group"><h3>${esc(title)} <span>${items.length}</span></h3>${items.length ? items.map((item) => { const historical = type === 'tasks' && isHistoricalDoneTask(item); return `<div class="constructor-draft-row"><span><b>${esc(item[key])}${type === 'tasks' ? ` · ${esc(item.title)}` : ''}</b>${historical ? '<small>Завершена до начала планирования</small>' : ''}${describe(item) ? `<small>${esc(describe(item))}</small>` : ''}</span><span>${historical ? '' : `<button type="button" data-constructor-edit="${type}" data-id="${esc(item[key])}" aria-label="Редактировать">✎</button>`}<button type="button" data-constructor-remove="${type}" data-id="${esc(item[key])}" aria-label="Удалить">×</button></span></div>`; }).join('') : '<p class="muted">Ничего не выбрано.</p>'}</div>`;
  }

  function renderDraft() {
    $c('#constructorCounters').innerHTML = [
      ['Команды', draft.teams.length], ['Задачи', draft.tasks.length],
    ].map(([label, value]) => `<div><b>${value}</b><span>${label}</span></div>`).join('');
    $c('#constructorDraftEntities').innerHTML = [
      entityRows('Команды', 'teams', draft.teams, (item) => `${item.expected_velocity_sp} SP`),
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

  function openForm(type, id = null) {
    const dialog = $c('#constructorDialog');
    const item = id ? clone(findDraft(type, id)) : null;
    const content = $c('#constructorDialogContent');
    if (type === 'teams') {
      const team = item || {team_id: '', name: '', expected_velocity_sp: 20, engineer_ids: []};
      content.innerHTML = `<h2>${item ? 'Изменить' : 'Добавить'} команду</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="teams" data-editing="${Boolean(item)}"><label>ID команды<input name="team_id" value="${esc(team.team_id)}" ${item ? 'readonly' : ''} required></label><label>Название<input name="name" value="${esc(team.name)}" required></label><label>Ожидаемая velocity, SP<input name="expected_velocity_sp" type="number" min="0" step="0.01" value="${esc(team.expected_velocity_sp)}" required></label>${freeEngineerPicker(team)}<div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
    }
    if (type === 'engineers') {
      const engineer = item || {engineer_id: '', name: '', assignments: [{team_id: '', role: '', grade: '', capacity_rate: 1}], skills: []};
      const assignment = engineer.assignments?.[0] || {team_id: '', role: '', grade: '', capacity_rate: 1};
      content.innerHTML = `<h2>${item ? 'Изменить' : 'Добавить'} инженера</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="engineers" data-editing="${Boolean(item)}"><div class="constructor-form-grid"><label>ID инженера<input name="engineer_id" value="${esc(engineer.engineer_id)}" ${item ? 'readonly' : ''} required></label><label>Имя<input name="name" value="${esc(engineer.name)}" required></label><label>Команда<select name="team_id" required><option value="">Выберите команду</option>${engineerTeamOptions(assignment.team_id)}</select></label><label>Роль<input name="role" list="constructorRoleOptions" value="${esc(assignment.role)}" required></label><label>Грейд<input name="grade" list="constructorGradeOptions" value="${esc(assignment.grade || '')}" placeholder="Junior, Middle, Senior" required></label><label>Доступность, от 0 до 1<input name="capacity_rate" type="number" min="0.01" max="1" step="0.05" value="${esc(assignment.capacity_rate ?? 1)}" required><small>Например, 1 — полная доступность, 0.5 — половина времени.</small></label><label class="constructor-wide-field">Навыки через запятую<input name="skills" value="${esc((engineer.skills || []).join(', '))}" placeholder="Python, Kafka, PostgreSQL" required></label></div>${sharedDataLists()}<div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
    }
    if (type === 'tasks') {
      const task = item || {task_id: '', title: '', initiative_id: '', team_id: null, status: 'ToDo', rung: 0, story_points: 1, role_requirements: [{role: '', required_hours: 8, skills: []}], depends_on: []};
      content.innerHTML = `<h2>${item ? 'Изменить' : 'Добавить'} задачу</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="tasks" data-editing="${Boolean(item)}"><div class="constructor-form-grid"><label>ID задачи<input name="task_id" value="${esc(task.task_id)}" ${item ? 'readonly' : ''} required></label><label>Название<input name="title" value="${esc(task.title)}" required></label><label>Инициатива<input name="initiative_id" list="constructorInitiativeOptions" value="${esc(task.initiative_id)}" placeholder="Выберите или введите PRODF"></label><label>Статус<select name="status">${optionList(['ToDo', 'InProgress', 'Done'], task.status)}</select></label><label>Приоритет Rung<input name="rung" type="number" step="0.01" value="${esc(task.rung)}" required></label><label class="constructor-sp-field">Story Points<input name="story_points" type="text" inputmode="decimal" pattern="[0-9]+([.,][0-9]+)?" value="${esc(task.story_points)}" required><small>Отдельно от человеко-часов.</small></label>${dependencyPicker(task)}</div><h3>Трудозатраты и обязательные навыки</h3><div id="constructorRequirementRows">${task.role_requirements.map(requirementRow).join('')}</div><button class="text-button" type="button" data-add-requirement>+ Добавить роль</button>${sharedDataLists()}<div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
    }
    document.body.classList.add('constructor-modal-open');
    dialog.showModal();
    updateDependencyView();
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
      entity = {task_id: id, title: String(values.get('title') || '').trim(), initiative_id: String(values.get('initiative_id') || '').trim() || null, team_id: null, status: String(values.get('status')), rung: Number(values.get('rung')), story_points: storyPoints, role_requirements: requirements, depends_on: dependsOn, source: existing?.source || 'draft'};
      dependsOn.forEach((dependencyId) => {
        if (findDraft('tasks', dependencyId)) return;
        const source = findCatalog('tasks', dependencyId);
        if (source && !isHistoricalDoneTask(source)) draft.tasks.push({...clone(source), team_id: null});
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
      const tasks = draft.tasks.map((task) => ({...task, team_id: null}));
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
      const selectAll = event.target.closest('[data-constructor-select-all]');
      if (selectAll) {
        const type = selectAll.dataset.constructorSelectAll;
        toggleAllCatalog(type, type === 'teams' ? catalog.teams : catalogWithDraft('tasks').filter((task) => !isHistoricalDoneTask(task)), selectAll.checked);
        return;
      }
      const pick = event.target.closest('[data-constructor-pick]');
      if (pick) { toggleCatalog(pick.dataset.constructorPick, pick.value, pick.checked); return; }
      if (event.target.matches('[name="dependency_id"]')) updateDependencyView();
      if (event.target.id === 'showPlanChanges') {
        showPlanChanges = event.target.checked;
        if (!showPlanChanges) changedTaskEffects = [];
        renderPreview();
        return;
      }
    });
    document.addEventListener('input', (event) => {
      if (event.target.id !== 'constructorDependencySearch') return;
      const query = event.target.value.trim().toLowerCase();
      $$c('[data-dependency-option]').forEach((option) => { option.hidden = !option.dataset.search.includes(query); });
    });
    document.addEventListener('click', (event) => {
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
        if (!dependencyMenu.hidden) $c('#constructorDependencySearch')?.focus();
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
