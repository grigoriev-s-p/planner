'use strict';

window.planConstructor = (() => {
  const STORAGE_KEY = 'postcode.plan-constructor.v2';
  const emptyDraft = () => ({teams: [], tasks: []});
  let catalog = {teams: [], engineers: [], tasks: [], options: {roles: [], skills: [], grades: []}};
  let draft = emptyDraft();
  let mode = 'manual';
  let api = null;
  let previewPlan = null;
  let onPlanApproved = null;
  let bound = false;
  let shownRecommendationGroups = 3;
  const shownRecommendations = new Map();
  let changedTaskEffects = [];
  let activeRecommendationId = null;
  let recommendationUpdateInFlight = false;
  let changedTaskAnimationUntil = 0;

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
  const number = (value) => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(Number(value || 0));

  function invalidatePreview() {
    if (!previewPlan) return;
    previewPlan = null;
    renderPreview();
  }

  function previewReasonBlocks(task) {
    return (task.reason_blocks || []).map((reason) => `<section class="preview-reason"><b>${esc(reason.title)}</b><ul>${(reason.details || []).map((detail) => `<li>${esc(detail)}</li>`).join('')}</ul></section>`).join('') || '<section class="preview-reason"><b>Причина переноса</b><p>Не удалось разместить задачу в текущем квартале.</p></section>';
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

  function percent(value) {
    if (value == null) return 'KPI недоступен';
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) {
      console.warn('Некорректное значение KPI:', value);
      return 'KPI недоступен';
    }
    return `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(parsed)}%`;
  }

  function recommendationGroups() {
    const recommendations = previewPlan?.optimization?.recommendations || [];
    const definitions = [
      {key: 'task-allocation', type: 'TASK_ALLOCATION', title: 'Перераспределение задач'},
      {key: 'cross-team-help', type: 'CROSS_TEAM_HELP', title: 'Помощь между командами'},
    ];
    return definitions.map((group) => ({
      ...group,
      items: recommendations.filter((item) => group.key === 'cross-team-help'
        ? item.type === 'CROSS_TEAM_HELP' : item.type === 'TASK_SWAP' || item.type === 'TASK_REASSIGNMENT')
        .sort((left, right) => Number(right.rung || 0) - Number(left.rung || 0)
          || Number(left.effect?.before_sprint || 99) - Number(right.effect?.before_sprint || 99)
          || String(left.task_id).localeCompare(String(right.task_id), 'ru')),
    })).filter((group) => group.items.length);
  }

  function capacityRows(help) {
    const capacity = help.engineer_capacity || {};
    return `<div class="recommendation-evidence"><b>Ресурс специалиста</b><dl><div><dt>Доступно</dt><dd>${number(capacity.capacity_hours)} ч</dd></div><div><dt>Свои задачи</dt><dd>${number(capacity.already_allocated_hours)} ч</dd></div><div><dt>Помощь</dt><dd>+${number(capacity.recommended_hours)} ч</dd></div><div><dt>После подключения</dt><dd>${number(capacity.resulting_total_hours)} / ${number(capacity.capacity_hours)} ч</dd></div><div><dt>Останется</dt><dd>${number(capacity.remaining_hours)} ч</dd></div></dl><p class="recommendation-check">✓ Специалист помещается по часам</p><p class="recommendation-check">${capacity.source_team_plan_stays_feasible ? '✓' : '⚠'} Задачи основной команды остаются выполнимыми</p></div>`;
  }

  function reassignmentRows(rows) {
    return rows.map((row) => {
      const target = row.target_capacity || {};
      const roles = Object.entries(target.role_hours_required || {}).map(([role, hours]) => `<li>${esc(role)}: требуется ${number(hours)} ч, доступно ${number(target.role_hours_available?.[role])} ч</li>`).join('');
      const bottlenecks = (row.source_bottlenecks || []).map((reason) => `<li>${esc(reason)}</li>`).join('');
      return `<div class="recommendation-evidence"><b>${esc(row.task_id)}: ${esc(row.from_team)} → ${esc(row.to_team)}</b><div class="recommendation-before-after"><section><span>Сейчас</span><strong>${esc(row.from_team)}</strong><ul>${bottlenecks}</ul></section><section><span>Предлагается</span><strong>${esc(row.to_team)}</strong><p>Sprint ${esc(row.after_sprint || '—')}</p><p>SP: требуется ${number(row.task_sp)}, доступно ${number(target.available_sp)}</p><ul>${roles}</ul></section></div><p class="recommendation-check">✓ ${esc(row.effect)}</p><p class="recommendation-check">✓ Команда помещает задачу без превышения ресурсов</p></div>`;
    }).join('');
  }

  function recommendationCard(item) {
    const effect = item.effect || {};
    const help = (item.engineer_help || [])[0];
    const transfers = item.task_reassignment || [];
    const taskLabel = (item.tasks || [item.task_id]).filter(Boolean).map((taskId) => `${esc(taskId)}${Number.isFinite(Number(item.rung)) ? ` · R${esc(item.rung)}` : ''}`).join(' + ');
    const actor = help
      ? `${help.engineer_name || help.engineer_id} · ${help.source_team} → ${help.target_team}`
      : transfers.map((row) => `${row.from_team} → ${row.to_team}`).join(' · ');
    const timing = help ? `Sprint ${help.sprint} · ${number(help.help_hours)} ч` : transfers[0]?.after_sprint ? `Sprint ${transfers[0].after_sprint}` : '';
    const detail = help
      ? `<p><b>Кто:</b> ${esc(help.engineer_name || help.engineer_id)} · ${esc(help.role)}</p><p><b>Откуда:</b> ${esc(help.source_team)} · <b>Куда:</b> ${esc(help.target_team)}</p><p><b>Задача:</b> ${esc(help.target_task)} · <b>Sprint:</b> ${esc(help.sprint)} · <b>Помощь:</b> ${number(help.help_hours)} ч</p>${(item.engineer_help || []).map(capacityRows).join('')}`
      : reassignmentRows(transfers);
    const applying = activeRecommendationId === item.recommendation_id;
    return `<article class="recommendation-card ${applying ? 'recommendation-card--loading' : ''}" id="recommendation-${esc(item.recommendation_id)}"><div class="recommendation-card-title"><b>${esc(item.title)}</b><span>${taskLabel}</span></div><p>${esc(actor)}${timing ? ` · ${esc(timing)}` : ''}</p><strong>${esc(effect.summary || 'План станет выполнимее.')}</strong><p class="recommendation-check">✓ Изменение проверено планировщиком</p><details><summary>Подробнее</summary>${detail}</details><div class="recommendation-actions"><button type="button" class="btn btn-outline" data-recommendation-reject="${esc(item.recommendation_id)}" ${recommendationUpdateInFlight ? 'disabled' : ''}>Отклонить</button><button type="button" class="btn btn-blue" data-recommendation-apply="${esc(item.recommendation_id)}" ${recommendationUpdateInFlight ? 'disabled' : ''}>${applying ? 'Применяем…' : 'Применить'}</button></div></article>`;
  }

  function optimizationSummary() {
    const optimization = previewPlan?.optimization || {};
    const base = optimization.base_summary || {};
    const current = optimization.current_summary || {};
    const best = optimization.optimized_summary || {};
    const rung = (best.rung_coverage || []).slice(0, 3).map((row) => `R${number(row.rung)}: ${number(row.planned)}/${number(row.total)}`).join(' · ');
    return `<section class="optimization-summary"><div><span>Базовый план</span><b>${number(base.planned_tasks)} задач</b><small>${number(base.initiatives)} завершённых инициатив</small></div><div><span>Текущий вариант</span><b>${number(current.planned_tasks)} задач</b><small>${number(current.initiatives)} завершённых инициатив</small></div><div><span>Лучший найденный вариант</span><b>${number(best.planned_tasks)} задач</b><small>${number(best.initiatives)} завершённых инициатив${rung ? ` · ${esc(rung)}` : ''}</small></div></section>`;
  }

  function acceptedChangesPanel() {
    const accepted = previewPlan?.optimization?.accepted_changes || [];
    return accepted.length ? `<section class="preview-section"><h3>Применённые изменения</h3><div class="accepted-changes">${accepted.map((item) => `<article><span><b>✓ ${esc(item.title)}</b><small>${esc(item.task_id)} · ${esc(item.summary || '')}</small></span><button class="btn btn-outline" type="button" data-change-undo="${esc(item.change_id)}">Отменить</button></article>`).join('')}</div><button class="text-button" type="button" id="resetConstructorDraft">Вернуться к базовому плану</button></section>` : '';
  }

  function recommendationPanel() {
    const optimization = previewPlan?.optimization || {};
    const groups = recommendationGroups();
    const current = optimization.current_summary || {};
    const shadow = optimization.optimized_summary || {};
    const visibleGroups = groups.slice(0, shownRecommendationGroups);
    const cards = visibleGroups.map((group) => {
      const visible = shownRecommendations.get(group.key) || 3;
      return `<section class="recommendation-group"><h4>${group.title}<small>${group.items.length} доступных улучшений</small></h4><div class="recommendation-list">${group.items.slice(0, visible).map(recommendationCard).join('')}</div>${group.items.length > visible ? `<button class="text-button" type="button" data-more-recommendations="${esc(group.key)}">Показать ещё ${group.items.length - visible}</button>` : ''}</section>`;
    }).join('');
    const moreGroups = groups.length > shownRecommendationGroups ? `<button class="text-button" type="button" id="showMoreRecommendationGroups">Показать ещё ${groups.length - shownRecommendationGroups} групп</button>` : '';
    const taskGap = Math.max(Number(shadow.planned_tasks || 0) - Number(current.planned_tasks || 0), 0);
    const initiativeGap = Math.max(Number(shadow.initiatives || 0) - Number(current.initiatives || 0), 0);
    const comparison = `<div class="recommendation-comparison"><div><span>Текущий вариант</span><b>${number(current.planned_tasks)} задач · ${number(current.initiatives)} инициатив</b></div><div><span>Лучший найденный</span><b>${number(shadow.planned_tasks)} задач · ${number(shadow.initiatives)} инициатив</b><small>До лучшего: +${number(taskGap)} задач · +${number(initiativeGap)} инициатив</small></div></div>`;
    const applyAll = optimization.status === 'ready' && (optimization.optimized_actions || []).length
      ? `<div class="apply-all-recommendations ${optimization.best_selected ? 'apply-all-recommendations--selected' : ''}"><div><b>Лучший найденный совместимый вариант</b><small>Сохранённый вариант не меняется при применении или отклонении отдельных рекомендаций.</small></div><button class="btn btn-blue" type="button" id="applyAllRecommendations" ${recommendationUpdateInFlight || optimization.best_selected ? 'disabled' : ''}>${optimization.best_selected ? '✓ Лучший найденный вариант выбран' : activeRecommendationId === 'apply-all' ? 'Применяем…' : 'Применить все рекомендации'}</button></div>` : '';
    const recommendationContent = optimization.status === 'pending' ? '' : optimization.recommendation_error
      ? `<p class="muted">Не удалось обновить рекомендации: ${esc(optimization.recommendation_error)}. Предварительный план доступен для утверждения.</p>`
      : groups.length ? `${cards}${moreGroups}` : '<p class="muted">Дополнительных улучшений с помощью перераспределения доступных ресурсов не найдено.</p>';
    return `<section class="preview-section recommendations-section"><h3>Как можно улучшить план</h3><p class="muted">Можно перераспределить доступные ресурсы и сразу увидеть, как изменится квартальный план.</p>${comparison}${applyAll}${recommendationContent}</section>`;
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
    const currentSummary = previewPlan.optimization?.current_summary || {};
    const sprintBoard = Array.from({length: 6}, (_, index) => {
      const sprint = index + 1;
      const cards = plannedTasks.filter((task) => (task.planned_sprints || []).includes(sprint))
        .sort((left, right) => Number(right.rung || 0) - Number(left.rung || 0) || left.task_id.localeCompare(right.task_id, 'ru'))
        .map((task) => previewSprintCard(task, sprint)).join('') || '<p class="preview-sprint-empty">Нет задач</p>';
      return `<section class="preview-sprint" data-preview-sprint-column="${sprint}"><h3>Sprint ${sprint}</h3><div>${cards}</div></section>`;
    }).join('');
    const exclusions = excludedTasks.map((task) => {
      return `<article class="preview-exclusion"><div><b>${esc(task.task_id)} <span class="rung-badge">R${esc(task.rung)}</span></b><span>${esc(task.title)}</span></div><div class="preview-reasons">${previewReasonBlocks(task)}</div></article>`;
    }).join('') || '<p class="muted">Все задачи удалось разместить в квартальном плане.</p>';
    target.hidden = false;
    target.innerHTML = `<div class="constructor-preview-heading"><div><h2>Предварительный план</h2><p>План построен без перераспределения задач и специалистов между командами.</p></div></div><section class="preview-kpi"><span>Текущий вариант</span><b>${number(currentSummary.planned_tasks)} задач · ${number(currentSummary.initiatives)} завершённых инициатив</b><small>KPI здесь не используется для выбора лучшего варианта.</small></section>${optimizationSummary()}<section class="preview-section" id="constructorSprintPlan"><h3>Распределение по спринтам</h3><div class="preview-board-viewport"><div class="preview-sprint-board">${sprintBoard}</div></div></section><section class="preview-section"><h3>Помощь между командами</h3><div class="preview-help-list">${previewCrossTeamRows(previewPlan)}</div></section><section class="preview-section"><h3>Переносы и исключения</h3><div class="preview-exclusion-list">${exclusions}</div></section>${recommendationPanel()}${acceptedChangesPanel()}<div class="preview-approval"><p>Проверьте распределение перед утверждением. После утверждения этот вариант станет текущим планом.</p><div id="constructorApprovalError" class="constructor-errors" hidden></div><button class="btn btn-blue" id="approveConstructorPlan" type="button">Утвердить план</button></div>`;
  }

  function loadDraft() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (stored && Array.isArray(stored.teams) && Array.isArray(stored.tasks)) {
        draft = {
          teams: stored.teams,
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
    const button = $c('#uploadConstructorDataset');
    button.disabled = true;
    button.textContent = 'Строим план…';
    setDatasetStatus(`Проверяем ${file.name} и рассчитываем шесть спринтов…`);
    try {
      const planResult = await api.buildConstructorPlanFromDataset(file);
      if (planResult.catalog) catalog = planResult.catalog;
      const planned = (planResult.tasks || []).filter((task) => task.status === 'PLANNED').length;
      setDatasetStatus(`${file.name} · предварительный план готов · ${planned} задач вошли в квартал`, 'success');
      previewPlan = planResult;
      shownRecommendationGroups = 3;
      shownRecommendations.clear();
      render();
      $c('#constructorPreview')?.scrollIntoView({behavior: 'smooth', block: 'start'});
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
    const sourceCatalog = catalog[type].map((item) => draftById.get(item[key]) || item);
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
    const selectableTasks = allTasks.filter((task) => !isCompletedTask(task));
    $c('#constructorTaskCatalog').innerHTML = `${!taskQuery && selectableTasks.length ? selectAllControl('tasks', selectableTasks) : ''}${tasks.map((task) => { const completed = isCompletedTask(task); return `<div class="constructor-option constructor-task-option ${completed ? 'constructor-task-completed' : ''}"><label class="constructor-task-pick"><input type="checkbox" data-constructor-pick="tasks" value="${esc(task.task_id)}" ${completed || findDraft('tasks', task.task_id) ? 'checked' : ''} ${completed ? 'disabled' : ''}><span><b>${esc(task.task_id)} · ${esc(task.title)}</b>${completed ? '<small>Задача завершена</small>' : ''}</span></label><button type="button" class="constructor-task-open" data-open-task="${esc(task.task_id)}" aria-label="Открыть задачу ${esc(task.task_id)}">↗</button></div>`; }).join('') || '<div class="empty-state">Задачи не найдены.</div>'}`;
    updateSelectAllIndeterminate();
  }

  function entityRows(title, type, items, describe) {
    const key = idField(type);
    return `<div class="constructor-draft-group"><h3>${esc(title)} <span>${items.length}</span></h3>${items.length ? items.map((item) => `<div class="constructor-draft-row"><span><b>${esc(item[key])}${type === 'tasks' ? ` · ${esc(item.title)}` : ''}</b>${describe(item) ? `<small>${esc(describe(item))}</small>` : ''}</span><span><button type="button" data-constructor-edit="${type}" data-id="${esc(item[key])}" aria-label="Редактировать">✎</button><button type="button" data-constructor-remove="${type}" data-id="${esc(item[key])}" aria-label="Удалить">×</button></span></div>`).join('') : '<p class="muted">Ничего не выбрано.</p>'}</div>`;
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
    return catalog.engineers.map((engineer) => ({
      ...clone(engineer),
      assignments: engineer.assignments.filter((assignment) => selectedTeams.has(assignment.team_id)),
    })).filter((engineer) => engineer.assignments.length > 0);
  }

  function errorsForDraft() {
    const errors = [];
    const taskIds = new Set(draft.tasks.map((item) => item.task_id));
    if (!draft.teams.length) errors.push('Выберите или добавьте хотя бы одну команду.');
    if (!draft.tasks.length) errors.push('Выберите или добавьте хотя бы одну задачу.');
    if (draft.teams.length && !automaticEngineers().length) errors.push('Для выбранных команд в исходном датасете не найден доступный ресурс.');
    draft.tasks.forEach((task) => (task.depends_on || []).forEach((dependency) => {
      if (!taskIds.has(dependency)) errors.push(`Добавьте блокирующую задачу ${dependency} для ${task.task_id} или уберите зависимость.`);
    }));
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
    return `<datalist id="constructorRoleOptions">${catalog.options.roles.map((item) => `<option value="${esc(item)}"></option>`).join('')}</datalist><datalist id="constructorInitiativeOptions">${initiatives.map((item) => `<option value="${esc(item)}"></option>`).join('')}</datalist>`;
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
      const team = item || {team_id: '', name: '', expected_velocity_sp: 20, focus_factor: 0.8};
      content.innerHTML = `<h2>${item ? 'Изменить' : 'Добавить'} команду</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="teams" data-editing="${Boolean(item)}"><label>ID команды<input name="team_id" value="${esc(team.team_id)}" ${item ? 'readonly' : ''} required></label><label>Название<input name="name" value="${esc(team.name)}" required></label><label>Ожидаемая velocity, SP<input name="expected_velocity_sp" type="number" min="0" step="0.01" value="${esc(team.expected_velocity_sp)}" required></label><label>Focus Factor<input name="focus_factor" value="0.8" readonly><small>Зафиксирован правилами кейса.</small></label><div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
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
      entity = {team_id: id, name: String(values.get('name') || '').trim(), expected_velocity_sp: Number(values.get('expected_velocity_sp')), focus_factor: 0.8, source: existing?.source || 'draft'};
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
        if (source) draft.tasks.push({...clone(source), team_id: null});
      });
    }
    if (existing) draft[type] = draft[type].map((item) => item[key] === id ? entity : item);
    else draft[type].push(entity);
    if (type === 'tasks' && !editing) $c('#constructorTaskSearch').value = '';
    invalidatePreview();
    saveDraft();
    $c('#constructorDialog').close();
    render();
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
      const planResult = await api.buildConstructorPlan({teams: draft.teams, engineers: automaticEngineers(), tasks});
      showErrors([]);
      previewPlan = planResult;
      shownRecommendationGroups = 3;
      shownRecommendations.clear();
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
    const items = effects || [];
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const cards = items.flatMap((effect) => $$c(`[data-preview-task="${CSS.escape(effect.task_id)}"]`)
      .filter((card) => effect.to_sprint == null || Number(card.dataset.previewSprint) === Number(effect.to_sprint)));
    const uniqueCards = [...new Set(cards)];
    const primary = uniqueCards[0] || $c('#constructorSprintPlan');
    primary?.scrollIntoView({behavior: reducedMotion ? 'auto' : 'smooth', block: 'center', inline: 'nearest'});
    if (!reducedMotion) {
      if (uniqueCards.length) changedTaskAnimationUntil = Date.now() + 850;
      uniqueCards.forEach((card) => {
        card.classList.add('task-card--just-added');
        window.setTimeout(() => card.classList.remove('task-card--just-added'), 900);
      });
    }
    changedTaskEffects = [];
  }

  function applyOptimisticDelta(recommendationId) {
    const optimization = previewPlan?.optimization || {};
    const recommendation = (optimization.recommendations || []).find((item) => item.recommendation_id === recommendationId);
    const delta = recommendation?.plan_delta;
    if (!delta || (optimization.accepted_changes || []).length
      || delta.base_fingerprint !== optimization.input_plan_fingerprint
      || delta.recommendation_signature !== recommendationId) return false;
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
    const canonicalBefore = clone(previewPlan);
    recommendationUpdateInFlight = true;
    activeRecommendationId = action === 'applyAll' ? 'apply-all' : id;
    renderPreview();
    try {
      if (action === 'apply') applyOptimisticDelta(id);
      if (action === 'apply') previewPlan = await api.applyConstructorRecommendation(draftId, id);
      if (action === 'reject') previewPlan = await api.rejectConstructorRecommendation(draftId, id);
      if (action === 'undo') previewPlan = await api.undoConstructorChange(draftId, id);
      if (action === 'reset') previewPlan = await api.resetConstructorDraft(draftId);
      if (action === 'applyAll') previewPlan = await api.applyAllConstructorRecommendations(draftId);
      changedTaskEffects = previewPlan.optimization?.changed_tasks || [];
      shownRecommendationGroups = 3;
      shownRecommendations.clear();
      recommendationUpdateInFlight = false;
      activeRecommendationId = null;
      renderPreview();
      if (changedTaskEffects.length || action === 'applyAll') focusChangedTasks(changedTaskEffects);
    } catch (error) {
      previewPlan = canonicalBefore;
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
    document.addEventListener('change', (event) => {
      const selectAll = event.target.closest('[data-constructor-select-all]');
      if (selectAll) {
        const type = selectAll.dataset.constructorSelectAll;
        toggleAllCatalog(type, type === 'teams' ? catalog.teams : catalogWithDraft('tasks').filter((task) => !isCompletedTask(task)), selectAll.checked);
        return;
      }
      const pick = event.target.closest('[data-constructor-pick]');
      if (pick) { toggleCatalog(pick.dataset.constructorPick, pick.value, pick.checked); return; }
      if (event.target.matches('[name="dependency_id"]')) updateDependencyView();
    });
    document.addEventListener('input', (event) => {
      if (event.target.id !== 'constructorDependencySearch') return;
      const query = event.target.value.trim().toLowerCase();
      $$c('[data-dependency-option]').forEach((option) => { option.hidden = !option.dataset.search.includes(query); });
    });
    document.addEventListener('click', (event) => {
      if (event.target.closest('#approveConstructorPlan')) { approvePlan(); return; }
      const apply = event.target.closest('[data-recommendation-apply]');
      if (apply) { updateRecommendation('apply', apply.dataset.recommendationApply); return; }
      const reject = event.target.closest('[data-recommendation-reject]');
      if (reject) { updateRecommendation('reject', reject.dataset.recommendationReject); return; }
      const undo = event.target.closest('[data-change-undo]');
      if (undo) { updateRecommendation('undo', undo.dataset.changeUndo); return; }
      if (event.target.closest('#resetConstructorDraft')) {
        if (confirm('Вернуть черновик к базовому плану? Принятые изменения будут отменены.')) updateRecommendation('reset');
        return;
      }
      if (event.target.closest('#applyAllRecommendations')) {
        const rejected = previewPlan?.optimization?.rejected_recommendation_ids || [];
        if (rejected.length && !confirm(`Вы ранее отклонили ${rejected.length} рекомендаций. Чтобы выбрать лучший найденный вариант, они также будут применены.`)) return;
        updateRecommendation('applyAll'); return;
      }
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

  function initialize(sourceCatalog, apiClient, handlePlanApproved) {
    catalog = sourceCatalog;
    api = apiClient;
    onPlanApproved = typeof handlePlanApproved === 'function' ? handlePlanApproved : () => {};
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

  return Object.freeze({initialize, updateCatalog, render});
})();
