'use strict';

const state = {
  page: 'home', taskId: null, taskSprint: null,
  teamId: null, engineerId: null, starTab: 'teams', riskId: null,
  riskFilter: 'Все', riskSearch: '', riskModalOpen: false, skillTeam: 'all', skillRole: 'all', skillTech: 'all',
  competency: null, criticalOnly: false,
  taskReturnPage: 'current-plan', loading: true, error: null, archive: null,
  taskSortKey: 'task_id', taskSortDirection: 'asc',
};

const data = {
  plan: null, summary: {}, tasks: [], initiatives: [], risks: [], teams: [],
  engineers: [], starMap: {teams: [], engineers: []}, kpis: null, features: {}, archiveReasons: {}, ready: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? '—').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const attr = escapeHtml;
const number = (value) => value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toLocaleString('ru-RU', {maximumFractionDigits: 2});
const percent = (value) => value == null || Number.isNaN(Number(value)) ? '—' : `${Number(value).toLocaleString('ru-RU', {maximumFractionDigits: 1})}%`;
const teamBy = (id) => data.teams.find((team) => team.team_id === id) || {team_id: id, name: id || 'Команда не указана', sp_by_sprint: {}};
const engineerBy = (id) => data.engineers.find((engineer) => engineer.engineer_id === id);
const starTeamBy = (id) => data.starMap?.teams?.find((team) => team.team_id === id);
const starEngineerBy = (id) => data.starMap?.engineers?.find((engineer) => engineer.engineer_id === id);
const taskBy = (id) => data.tasks.find((task) => task.task_id === id);
const currentSprint = () => Number(data.plan?.quarter?.current_sprint || 1);
const sprintBy = (sprint) => data.plan?.sprints?.find((item) => item.sprint_number === sprint);
const taskSprints = (task) => task?.planned_sprints || [];
const inQuarter = (task) => task?.status === 'PLANNED' && taskSprints(task).length > 0;
const portion = (task, sprint) => Number(task?.sp_load_by_sprint?.[String(sprint)] || 0);
const sprintHours = (task, sprint) => task?.role_hours_by_sprint?.[String(sprint)] || null;
const sprintLabel = (task) => {
  const sprints = taskSprints(task);
  if (!sprints.length) return task.status === 'DONE' ? 'Завершена до квартала' : 'Не включена в квартальный план';
  return sprints.length === 1 ? `Sprint ${sprints[0]}` : `Sprint ${sprints[0]}–${sprints.at(-1)}`;
};
const sprintStatus = (sprint) => ({CURRENT: 'Текущий', PLANNED: 'Запланирован', DONE: 'Выполнен'}[sprintBy(sprint)?.status] || 'Запланирован');
const statusClass = (status) => ({DONE: 'done', PLANNED: 'planned', MOVED: 'moved', UNPLANNABLE: 'cancelled'}[status] || 'planned');
const badge = (task) => `<span class="status ${statusClass(task.status)}">${escapeHtml(task.status_label)}</span>`;
const taskDisplayStatus = (task) => {
  const sourceStatus = String(task?.source_status || '').replaceAll(' ', '').toUpperCase();
  if (sourceStatus === 'CANCELLED' || sourceStatus === 'CANCELED') return 'Отменена';
  if (task?.status === 'PLANNED' && sourceStatus === 'INPROGRESS') return 'В процессе';
  return ({PLANNED: 'Запланирована', DONE: 'Выполнена', COMPLETED: 'Выполнена', IN_PROGRESS: 'В процессе', CANCELLED: 'Отменена', MOVED: 'Перенесена', UNPLANNABLE: 'Невыполнима'}[task?.status] || task?.status_label || 'Статус не указан');
};
const taskStatusClass = (task) => {
  const label = taskDisplayStatus(task);
  return ({'Запланирована': 'planned', 'Выполнена': 'done', 'В процессе': 'work', 'Отменена': 'cancelled', 'Перенесена': 'moved', 'Невыполнима': 'cancelled'}[label] || 'planned');
};
const taskStatusTooltip = (task) => ({
  'Запланирована': 'Задача выполнима и включена в квартальный план.',
  'Выполнена': 'Задача выполнена.',
  'В процессе': 'Задача находится в процессе выполнения.',
  'Отменена': 'Задача отменена.',
  'Перенесена': 'Задача перенесена за пределы текущего плана.',
  'Невыполнима': 'Задача невыполнима при текущих ограничениях.',
}[taskDisplayStatus(task)] || `Статус задачи: ${taskDisplayStatus(task)}.`);
const taskStatusIndicator = (task) => `<span class="task-status-indicator ${taskStatusClass(task)}" tabindex="0" role="img" aria-label="${attr(taskStatusTooltip(task))}" data-tooltip="${attr(taskStatusTooltip(task))}"></span>`;
const riskClass = (type) => ({'Сроки': 'deadline', 'Зависимости': 'dependency', 'Ресурсы': 'resource', 'Компетенции': 'competence'}[type] || 'resource');
const severityClass = (severity) => ({RED: 'deadline', YELLOW: 'dependency', ORANGE: 'resource'}[severity] || 'resource');
const teamEngineers = (teamId) => data.engineers.filter((engineer) => engineer.team_ids.includes(teamId));
const teamSprintResource = (teamId, sprint) => teamBy(teamId).sp_by_sprint?.[String(sprint)] || {capacity: 0, used: 0, remaining: 0};
const tButton = (task, label = task.task_id) => `<button type="button" class="table-button" data-open-task="${attr(task.task_id)}">${escapeHtml(label)}</button>`;
const rungValues = () => [...new Set(data.tasks.filter(inQuarter).map((task) => Number(task.rung)).filter(Number.isFinite))].sort((left, right) => left - right);
const rungLevel = (task) => {
  const values = rungValues();
  if (values.length < 2) return 'medium';
  const value = Number(task.rung);
  const index = values.indexOf(value);
  const resolvedIndex = index >= 0 ? index : values.filter((item) => item < value).length;
  if (values.length === 2) return resolvedIndex >= 1 ? 'high' : 'low';
  const third = Math.floor(values.length / 3);
  if (resolvedIndex >= values.length - third) return 'high';
  return resolvedIndex < third ? 'low' : 'medium';
};
const rungBadge = (task) => Number.isFinite(Number(task.rung))
  ? `<span class="rung-badge rung-${rungLevel(task)}">R${escapeHtml(task.rung)}</span>`
  : '<span class="muted">—</span>';
const REASON_GROUPS = Object.freeze({
  MISSING_SPECIALIST: 'Нет специалиста требуемой роли',
  NO_AVAILABLE_ROLE_SPECIALIST: 'Нет специалиста требуемой роли',
  INSUFFICIENT_ROLE_CAPACITY: 'Нет специалиста / недостаточно часов требуемой роли',
  INSUFFICIENT_ENGINEER_CAPACITY: 'Недостаточно доступного времени инженеров',
  INSUFFICIENT_SKILL_CAPACITY: 'Недостаточно специалистов с нужными компетенциями',
  INSUFFICIENT_SP_CAPACITY: 'Недостаточно SP команды',
  INSUFFICIENT_TEAM_CAPACITY: 'Недостаточно SP команды',
  QUARTER_OVERFLOW: 'Не помещается в квартал',
  OUTSIDE_QUARTER: 'Не помещается в квартал',
  DEPENDENCY_DELAY: 'Ограничено зависимостью',
  DEPENDENCY_NOT_COMPLETED: 'Ограничено зависимостью',
  UNKNOWN_DEPENDENCY_DATE: 'Ограничено зависимостью',
  MISSING_DEPENDENCY_REFERENCE: 'Ограничено зависимостью',
  UNKNOWN_TASK_DATA: 'Недостаточно данных о задаче',
});

function reasonGroup(task) {
  for (const reason of task.reasons || []) {
    if (REASON_GROUPS[reason.code]) return REASON_GROUPS[reason.code];
  }
  return 'Другая причина';
}

function renderDevelopmentNotice(target, text) {
  const element = $(target);
  if (!element) return;
  element.innerHTML = `<aside class="development-notice" role="note"><span class="development-icon" aria-hidden="true">◷</span><div><b>Раздел в разработке</b><p>${escapeHtml(text)}</p></div></aside>`;
}

function activateSprintScrollbars() {
  $$('.sprint-task-shell').forEach((shell) => {
    const column = shell.closest('.sprint-column');
    const scrollArea = $('.sprint-task-scroll', shell);
    const thumb = $('.sprint-scroll-thumb', shell);
    let pointerInside = false;

    const updateThumb = () => {
      const maxScroll = scrollArea.scrollHeight - scrollArea.clientHeight;
      const scrollable = maxScroll > 1;
      shell.classList.toggle('is-scrollable', scrollable);
      if (!scrollable) {
        shell.classList.remove('is-scroll-active');
        return;
      }
      const thumbHeight = Math.max(28, Math.round(shell.clientHeight * scrollArea.clientHeight / scrollArea.scrollHeight));
      const thumbTop = Math.round((shell.clientHeight - thumbHeight) * scrollArea.scrollTop / maxScroll);
      thumb.style.height = `${thumbHeight}px`;
      thumb.style.transform = `translateY(${thumbTop}px)`;
    };

    column.addEventListener('pointerenter', () => {
      pointerInside = true;
      updateThumb();
      if (shell.classList.contains('is-scrollable')) shell.classList.add('is-scroll-active');
    });
    column.addEventListener('pointerleave', () => {
      pointerInside = false;
      shell.classList.remove('is-scroll-active');
    });
    scrollArea.addEventListener('scroll', () => {
      updateThumb();
      if (pointerInside || document.activeElement === scrollArea) shell.classList.add('is-scroll-active');
    }, {passive: true});
    scrollArea.addEventListener('focus', () => {
      updateThumb();
      if (shell.classList.contains('is-scrollable')) shell.classList.add('is-scroll-active');
    });
    scrollArea.addEventListener('blur', () => {
      if (!pointerInside) shell.classList.remove('is-scroll-active');
    });
    updateThumb();
  });
}

function meter(value, total) {
  const percentage = total > 0 ? Math.min(100, Math.round(Number(value || 0) / total * 100)) : 0;
  return `<div class="meter" role="meter" aria-valuenow="${number(value)}" aria-valuemin="0" aria-valuemax="${number(total)}" style="--meter:${percentage}%"><i></i></div>`;
}

function table(headers, rows, extraClass = '') {
  return `<div class="table-scroll"><table class="data-table ${attr(extraClass)}"><thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

function empty(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function archiveState(entity) {
  if (!entity?.archived) return '';
  const reason = entity.archive_reason_text || entity.archive_reason_label || entity.archive_reason_code || 'Причина не указана';
  return `<div class="archive-state"><b>Архивировано</b><span>Причина: ${escapeHtml(reason)}</span></div>`;
}

function showToast(message) {
  const toast = $('#appToast');
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 5000);
}

function archiveEntityLabel(entityType) {
  return ({task: 'задачу', engineer: 'инженера', team: 'команду'})[entityType] || 'объект';
}

function openArchiveModal(entityType, entityId, hasCurrentPlanReferences = false) {
  const dialog = $('#archiveDialog');
  const labels = data.archiveReasons[entityType] || {};
  state.archive = {entityType, entityId, hasCurrentPlanReferences};
  $('#archiveDialogTitle').textContent = `Удалить ${archiveEntityLabel(entityType)}`;
  $('#archiveDialogDescription').textContent = 'Объект будет исключён из новых планов. Исторические данные сохранятся.';
  $('#archiveCurrentPlanWarning').hidden = !hasCurrentPlanReferences;
  $('#archiveCurrentPlanWarning').textContent = 'Объект участвует в текущем плане. После удаления текущий план останется без изменений и не будет пересчитан автоматически.';
  $('#archiveReason').innerHTML = `<option value="">Выберите причину</option>${Object.entries(labels).map(([code, label]) => `<option value="${attr(code)}">${escapeHtml(label)}</option>`).join('')}`;
  $('#archiveReasonText').value = '';
  $('#archiveOtherField').hidden = true;
  $('#archiveError').hidden = true;
  $('#archiveError').textContent = '';
  updateArchiveConfirmation();
  dialog.showModal();
}

function closeArchiveModal() {
  const dialog = $('#archiveDialog');
  state.archive = null;
  if (dialog.open) dialog.close();
}

function updateArchiveConfirmation() {
  const reasonCode = $('#archiveReason').value;
  const isOther = reasonCode === 'OTHER';
  $('#archiveOtherField').hidden = !isOther;
  $('#confirmArchive').disabled = !reasonCode || (isOther && !$('#archiveReasonText').value.trim());
}

async function submitArchive() {
  if (!state.archive) return;
  const reasonCode = $('#archiveReason').value;
  const reasonText = $('#archiveReasonText').value.trim();
  updateArchiveConfirmation();
  if ($('#confirmArchive').disabled) return;
  const button = $('#confirmArchive');
  const errorBox = $('#archiveError');
  button.disabled = true;
  button.textContent = 'Удаляем…';
  errorBox.hidden = true;
  const {entityType, entityId} = state.archive;
  try {
    const result = await window.postcodeApi.archiveEntity(entityType, entityId, {
      reason_code: reasonCode,
      reason_text: reasonCode === 'OTHER' ? reasonText : null,
    });
    const collection = {task: data.tasks, engineer: data.engineers, team: data.teams}[entityType];
    const idField = `${entityType}_id`;
    const entity = collection.find((item) => item[idField] === entityId);
    if (entity) Object.assign(entity, result.entity);
    let refreshWarning = null;
    try {
      data.starMap = await window.postcodeApi.getStarMap();
    } catch (_) {
      refreshWarning = 'Обновите страницу, чтобы перечитать Bus Factor.';
    }
    closeArchiveModal();
    if (entityType === 'task') { renderTasks(); if (state.page === 'task-detail') renderTaskDetail(); }
    if (entityType === 'engineer') openEmployee(entityId);
    if (entityType === 'team') renderStarTeams();
    showToast([result.message, result.warning, refreshWarning].filter(Boolean).join(' '));
    try {
      const catalog = await window.postcodeApi.getConstructorCatalog();
      window.planConstructor.updateCatalog(catalog);
    } catch (_) {
      showToast(`${result.message} Обновите страницу, чтобы перечитать активный каталог.`);
    }
  } catch (error) {
    errorBox.textContent = error?.message || 'Не удалось удалить объект.';
    errorBox.hidden = false;
  } finally {
    button.textContent = 'Удалить';
    if (state.archive) updateArchiveConfirmation();
  }
}

function pageLocation(page) {
  if (page === 'task-detail' && state.taskId) return `/tasks/${encodeURIComponent(state.taskId)}`;
  if (page === 'tasks') return '/tasks';
  return page === 'home' ? '/' : `/#${page}`;
}

function routeFromLocation() {
  const match = window.location.pathname.match(/^\/tasks\/([^/]+)\/?$/);
  if (match) return {page: 'task-detail', taskId: decodeURIComponent(match[1])};
  if (window.location.pathname === '/tasks' || window.location.pathname === '/tasks/') return {page: 'tasks', taskId: null};
  const hashPage = window.location.hash.replace(/^#/, '');
  const knownPages = new Set(['home', 'planning', 'current-plan', 'tasks', 'risks', 'recalc', 'star']);
  return {page: knownPages.has(hashPage) ? hashPage : 'home', taskId: null};
}

function show(page, historyMode = 'push') {
  state.page = page;
  $$('.page').forEach((element) => element.classList.toggle('active', element.id === `page-${page}`));
  $$('.nav-link').forEach((element) => element.classList.toggle('active', element.dataset.page === (page === 'task-detail' ? 'tasks' : page)));
  closeEmployee();
  renderPage(page);
  if (historyMode) window.history[historyMode === 'replace' ? 'replaceState' : 'pushState']({page, taskId: state.taskId}, '', pageLocation(page));
  window.scrollTo({top: 0, behavior: 'instant'});
}

function renderPage(page) {
  if (!data.ready) {
    renderUnavailable();
    return;
  }
  ({home: renderHome, planning: window.planConstructor.render, 'current-plan': renderPlanning, tasks: renderTasks, 'task-detail': renderTaskDetail, risks: renderRisks, recalc: renderRecalc, star: renderStar})[page]?.();
}

function renderUnavailable() {
  const message = state.error ? `Backend недоступен: ${state.error}` : 'Загружаем данные planner…';
  if ($('#homeSprints')) $('#homeSprints').innerHTML = empty(message);
  if ($('#homeTasks')) $('#homeTasks').innerHTML = empty(message);
  if ($('#homeRisks')) $('#homeRisks').innerHTML = empty(message);
  if ($('#tasksListState')) $('#tasksListState').innerHTML = empty(message);
  if ($('#tasksTableWrap')) $('#tasksTableWrap').hidden = true;
  if ($('#taskDetailState')) $('#taskDetailState').innerHTML = empty(message);
  if ($('#taskDetailContent')) $('#taskDetailContent').hidden = true;
}

function gotoTask(taskId) {
  if (!taskBy(taskId)) return;
  if (state.page !== 'task-detail') state.taskReturnPage = state.page;
  state.taskId = taskId;
  state.taskSprint = null;
  show('task-detail');
}

function gotoTeam(teamId) {
  if (!teamBy(teamId).team_id) return;
  state.teamId = teamId;
  state.competency = null;
  state.starTab = 'teams';
  show('star');
}

function gotoEmployee(engineerId) {
  if (!engineerBy(engineerId)) return;
  state.engineerId = engineerId;
  state.starTab = 'teams';
  show('star');
  openEmployee(engineerId);
}

function renderHome() {
  $('#homeSprints').innerHTML = Array.from({length: 6}, (_, index) => {
    const sprint = index + 1;
    return `<button class="home-sprint ${sprint === currentSprint() ? 'current' : ''}" type="button" data-home-sprint="${sprint}"><b>Sprint ${sprint}</b><span>${escapeHtml(sprintStatus(sprint))}</span></button>`;
  }).join('');
  const active = data.tasks.filter((task) => inQuarter(task) && taskSprints(task).includes(currentSprint()));
  $('#homeTasks').innerHTML = active.length ? active.map((task) => `<button class="list-button current-task" data-open-task="${attr(task.task_id)}" type="button"><span><strong>${escapeHtml(task.task_id)} · ${escapeHtml(task.title)}</strong><small>${escapeHtml(task.assigned_team_id)}</small></span><span class="end-label">Открыть ↗</span></button>`).join('') : empty('В текущем спринте нет активных задач.');
  $('#homeRisks').innerHTML = data.risks.slice(0, 3).map((risk) => `<button class="list-button" type="button" data-open-risk="${attr(risk.risk_id)}"><span><strong>${escapeHtml(risk.title)}</strong><small>${escapeHtml(risk.cause)}</small></span><span class="end-label">${escapeHtml(risk.type)} ↗</span></button>`).join('') || empty('Рассчитанных рисков нет.');
}

function initiativeTaskList(initiative) {
  return `<div class="initiative-task-list"><h3>Задачи инициативы</h3>${initiative.task_ids.map((taskId) => {
    const task = taskBy(taskId);
    const complete = ['PLANNED', 'DONE'].includes(task?.status);
    return `<button type="button" class="initiative-task-link" data-open-task="${attr(taskId)}"><span class="initiative-task-state ${complete ? 'complete' : 'incomplete'}">${complete ? '✓' : '✕'}</span><span><b>${escapeHtml(taskId)}</b>${task?.title ? `<small>${escapeHtml(task.title)}</small>` : ''}</span><span aria-hidden="true">↗</span></button>`;
  }).join('')}</div>`;
}

function unfinishedInitiativeReasons(initiative) {
  const blockedTasks = initiative.task_ids.map(taskBy).filter((task) => task && !['PLANNED', 'DONE'].includes(task.status));
  if (!blockedTasks.length) return '<p class="hint">Backend не передал отдельную причину незавершения.</p>';
  const missingRoleCodes = new Set(['MISSING_SPECIALIST', 'NO_AVAILABLE_ROLE_SPECIALIST']);
  const reasonLines = (task) => {
    const roles = new Set();
    const remaining = [];
    (task.reasons || []).forEach((reason) => {
      if (reason.code === 'ALLOCATION_ROLLED_BACK') return;
      const role = String(reason.details?.role || '').trim();
      if (missingRoleCodes.has(reason.code) && role) roles.add(role);
      else remaining.push(reason.message || reason.title);
    });
    const lines = [...remaining.filter(Boolean)];
    if (roles.size) lines.unshift(`Нет специалистов ролей: ${[...roles].sort((left, right) => left.localeCompare(right, 'ru')).join(', ')}.`);
    return lines.length ? lines : [task.summary || 'Причина не предоставлена backend.'];
  };
  return `<div class="initiative-reasons"><h3>Почему инициатива не будет завершена</h3>${blockedTasks.map((task) => reasonLines(task).map((text, index) => `<p>${index ? '' : `<b>${escapeHtml(task.task_id)}</b> — `}${escapeHtml(text)}</p>`).join('')).join('')}</div>`;
}

function renderInitiatives() {
  const completed = data.initiatives.filter((initiative) => initiative.completed_in_quarter);
  const incomplete = data.initiatives.filter((initiative) => !initiative.completed_in_quarter);
  $('#completedInitiatives').innerHTML = completed.length
    ? completed.map((initiative) => `<details class="initiative-item"><summary>${escapeHtml(initiative.initiative_id)}</summary><div class="initiative-content">${initiativeTaskList(initiative)}</div></details>`).join('')
    : empty('В текущем плане пока нет полностью завершаемых инициатив.');
  $('#incompleteInitiatives').innerHTML = incomplete.length
    ? incomplete.map((initiative) => `<details class="initiative-item incomplete"><summary>${escapeHtml(initiative.initiative_id)}</summary><div class="initiative-content">${unfinishedInitiativeReasons(initiative)}${initiativeTaskList(initiative)}</div></details>`).join('')
    : empty('Все инициативы текущего плана завершаются в квартале.');
}

function renderExcludedTasks() {
  const outside = data.tasks.filter((task) => ['MOVED', 'UNPLANNABLE'].includes(task.status));
  if (!outside.length) {
    $('#outsideTasks').innerHTML = empty('Все выбранные задачи помещаются в квартальный план.');
    return;
  }
  const groups = new Map();
  outside.forEach((task) => {
    const label = reasonGroup(task);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(task);
  });
  $('#outsideTasks').innerHTML = [...groups.entries()].map(([label, tasks]) => `<section class="excluded-group"><div class="excluded-group-title"><span>${escapeHtml(label)}</span><b>${tasks.length}</b></div><div class="excluded-task-list">${tasks.map((task) => `<details class="excluded-task"><summary>${escapeHtml(task.task_id)}</summary><div class="excluded-task-content"><span>${escapeHtml(task.title)}</span><button type="button" class="text-button" data-open-task="${attr(task.task_id)}">Открыть задачу ↗</button></div></details>`).join('')}</div></section>`).join('');
}

function renderKpis() {
  const target = $('#planKpis');
  const expected = data.kpis?.expected_kpi;
  if (!target || !expected) {
    if (target) target.innerHTML = empty('KPI пока недоступны.');
    return;
  }

  if (expected.completion_percent == null) {
    target.innerHTML = `<article class="expected-kpi-card unavailable"><div><span>Ожидаемое выполнение квартального плана</span><strong>—</strong></div><p>Нет данных для расчёта.</p></article>`;
    return;
  }

  target.innerHTML = `<article class="expected-kpi-card"><div><span>Ожидаемое выполнение квартального плана</span><strong>${percent(expected.completion_percent)}</strong></div><div class="expected-kpi-copy"><p>Будут завершены ${number(expected.completed_initiatives)} из ${number(expected.baseline_initiatives)} инициатив исходного плана</p><small>Не будут завершены: ${number(expected.incomplete_initiatives)}</small></div></article>`;
}

function renderPlanning() {
  $('#sprintBoard').innerHTML = Array.from({length: 6}, (_, index) => {
    const sprint = index + 1;
    const tasks = data.tasks
      .filter((task) => inQuarter(task) && taskSprints(task).includes(sprint))
      .sort((left, right) => Number(right.rung || 0) - Number(left.rung || 0) || String(left.task_id).localeCompare(String(right.task_id), 'ru'))
      .map((task) => taskSprints(task)[0] < sprint
        ? `<button class="sprint-continuation" type="button" data-plan-task="${attr(task.task_id)}"><span class="sprint-card-top"><b>↳ ${escapeHtml(task.task_id)}</b>${rungBadge(task)}</span><span>Продолжение</span><small>${escapeHtml(task.assigned_team_id)}</small></button>`
        : `<button class="sprint-card" type="button" data-plan-task="${attr(task.task_id)}"><span class="sprint-card-top"><b class="card-id">${escapeHtml(task.task_id)}</b>${rungBadge(task)}</span><span class="card-title">${escapeHtml(task.title)}</span><span class="card-team">${escapeHtml(task.assigned_team_id)}</span></button>`)
      .join('') || '<div class="sprint-empty">Нет задач</div>';
    return `<section class="sprint-column ${sprint === currentSprint() ? 'selected' : ''}" data-sprint-column="${sprint}"><div class="sprint-column-header"><b>Sprint ${sprint}</b><span>${escapeHtml(sprintStatus(sprint))}</span></div><div class="sprint-task-shell"><div class="sprint-task-scroll" tabindex="0" aria-label="Задачи Sprint ${sprint}">${tasks}</div><span class="sprint-scroll-thumb" aria-hidden="true"></span></div></section>`;
  }).join('');
  activateSprintScrollbars();
  renderKpis();
  renderInitiatives();
  renderExcludedTasks();
}

function renderTasks() {
  const body = $('#tasksTableBody');
  const wrap = $('#tasksTableWrap');
  const stateBox = $('#tasksListState');
  $('#tasksListCount').textContent = `${data.tasks.length}`;
  stateBox.innerHTML = '';
  wrap.hidden = false;
  const numericKeys = new Set(['story_points', 'rung', 'start_sprint']);
  const sorted = [...data.tasks].sort((left, right) => {
    const leftValue = state.taskSortKey === 'status' ? taskDisplayStatus(left) : left[state.taskSortKey];
    const rightValue = state.taskSortKey === 'status' ? taskDisplayStatus(right) : right[state.taskSortKey];
    const leftMissing = leftValue == null || leftValue === '';
    const rightMissing = rightValue == null || rightValue === '';
    if (leftMissing || rightMissing) return leftMissing === rightMissing ? 0 : (leftMissing ? 1 : -1);
    const comparison = numericKeys.has(state.taskSortKey)
      ? Number(leftValue) - Number(rightValue)
      : String(leftValue).localeCompare(String(rightValue), 'ru', {numeric: true, sensitivity: 'base'});
    return state.taskSortDirection === 'asc' ? comparison : -comparison;
  });
  $$('[data-task-sort]').forEach((button) => {
    const active = button.dataset.taskSort === state.taskSortKey;
    button.classList.toggle('active', active);
    button.parentElement.setAttribute('aria-sort', active ? (state.taskSortDirection === 'asc' ? 'ascending' : 'descending') : 'none');
    $('span', button).textContent = active ? (state.taskSortDirection === 'asc' ? '↑' : '↓') : '↕';
  });
  if (!sorted.length) {
    body.innerHTML = '<tr><td colspan="8"><div class="empty-state">Задач пока нет.</div></td></tr>';
    return;
  }
  body.innerHTML = sorted.map((task) => `<tr data-open-task="${attr(task.task_id)}" data-task-row tabindex="0"><td>${tButton(task)}</td><td>${escapeHtml(task.title)}</td><td>${number(task.story_points)}</td><td>${rungBadge(task)}</td><td>${escapeHtml(task.assigned_team_id || '—')}</td><td>${escapeHtml(task.initiative_id || '—')}</td><td>${task.start_sprint ? `Sprint ${number(task.start_sprint)}` : '—'}</td><td class="task-status-cell">${taskStatusIndicator(task)}</td></tr>`).join('');
}

function renderTaskDetail() {
  const task = taskBy(state.taskId);
  const content = $('#taskDetailContent');
  const stateBox = $('#taskDetailState');
  if (!task) {
    $('#taskDetailTitle').textContent = 'Задача не найдена';
    $('#taskHeroId').textContent = state.taskId || '—';
    $('#taskHeroStatus').innerHTML = '';
    $('#taskTopActions').innerHTML = '';
    stateBox.innerHTML = empty(state.taskId ? `Задача ${state.taskId} отсутствует в текущем наборе.` : 'Задача не выбрана.');
    content.hidden = true;
    return;
  }
  content.hidden = false;
  stateBox.innerHTML = '';
  $('#taskDetailTitle').textContent = task.title || 'Название не указано';
  $('#taskHeroId').textContent = task.task_id;
  $('#taskHeroStatus').innerHTML = `<span class="hero-status-dot ${taskStatusClass(task)}" aria-hidden="true"></span>${escapeHtml(taskDisplayStatus(task))}`;
  $('#taskTopActions').innerHTML = task.archived ? archiveState(task) : `<button class="btn btn-danger" type="button" data-archive-entity="task" data-archive-id="${attr(task.task_id)}" data-current-plan-reference="${inQuarter(task)}">Удалить</button>`;
  const sprints = taskSprints(task);
  const selectedSprint = state.taskSprint && sprints.includes(state.taskSprint) ? state.taskSprint : (sprints[0] || null);
  state.taskSprint = selectedSprint;
  const allocations = (task.allocations || []).filter((item) => selectedSprint == null || item.sprint === selectedSprint);
  const teamValue = task.assigned_team_id
    ? `<button type="button" class="fact-link" data-open-team="${attr(task.assigned_team_id)}">${escapeHtml(task.assigned_team_id)} ↗</button>`
    : '<b>—</b>';
  $('#taskFacts').innerHTML = `<div class="task-fact"><span>Команда</span>${teamValue}</div><div class="task-fact"><span>Спринт</span><b>${escapeHtml(sprintLabel(task))}</b></div><div class="task-fact"><span>Rung</span>${rungBadge(task)}</div><div class="task-fact"><span>Инициатива</span><b>${escapeHtml(task.initiative_id || '—')}</b></div>${task.team_reassigned ? `<p class="task-team-note">Исходная команда: <b>${escapeHtml(task.original_team_id || '—')}</b></p>` : ''}`;
  const incoming = task.dependencies?.incoming || [];
  const outgoing = task.dependencies?.outgoing || [];
  const dependencyLink = (taskId, title) => {
    const linkedTask = taskBy(taskId);
    const label = `${taskId || 'Неизвестная задача'}${title ? ` · ${title}` : ''}`;
    return linkedTask ? tButton(linkedTask, label) : `<b>${escapeHtml(label)}</b>`;
  };
  const incomingRows = incoming.map((item) => {
    const cssClass = item.state === 'HISTORICAL_DONE' ? 'dependency-satisfied' : (item.state === 'MISSING' ? 'dependency-missing' : '');
    const mark = item.state === 'HISTORICAL_DONE' ? '✓ ' : '';
    return `<p class="${cssClass}">${mark}${dependencyLink(item.blocking_task_id, item.blocking_task_title)}<small>${escapeHtml(item.status_message)} · ${escapeHtml(item.dependency_type)}</small></p>`;
  });
  const outgoingRows = outgoing.map((item) => `<p>${dependencyLink(item.blocked_task_id, taskBy(item.blocked_task_id)?.title)}<small>${escapeHtml(item.dependency_type)}</small></p>`);
  $('#taskDependencies').innerHTML = `<h2>Зависимости</h2><p class="hint">Положение в квартальном плане</p><div class="dependency-grid"><div class="dependency-box"><strong>Блокирующие задачи</strong>${incomingRows.length ? incomingRows.join('') : '<p>Нет</p>'}</div><div class="dependency-box"><strong>Зависимые задачи</strong>${outgoingRows.length ? outgoingRows.join('') : '<p>Нет</p>'}</div></div>`;
  const allocationRows = allocations.map((item) => {
    const crossTeam = Boolean(item.cross_team) || Boolean(item.source_team_id && task.assigned_team_id && item.source_team_id !== task.assigned_team_id);
    return `<tr class="${crossTeam ? 'cross-team-row' : ''}"><td><button class="table-button" type="button" data-open-employee="${attr(item.engineer_id)}">${escapeHtml(item.engineer_id)} ↗</button></td><td>${escapeHtml(item.role || '—')}</td><td>${escapeHtml(item.source_team_id || '—')}${crossTeam ? '<span class="cross-team-badge" title="Сотрудник привлечён из другой команды">⚠ Другая команда</span>' : ''}</td><td>${number(item.allocated_hours)} ч</td></tr>`;
  });
  const sprintPicker = task.multi_sprint && selectedSprint ? `<label class="task-sprint-picker">Показать спринт<select id="taskSprintSelect">${sprints.map((sprint) => `<option value="${sprint}" ${sprint === selectedSprint ? 'selected' : ''}>Sprint ${sprint}</option>`).join('')}</select></label>` : '';
  $('#taskHours').innerHTML = `<div class="panel-heading"><div><h2>Распределение ресурсов</h2><p class="hint">Фактические назначения planner-а${selectedSprint ? ` для Sprint ${selectedSprint}` : ''}.</p></div>${sprintPicker}</div>${allocationRows.length ? table(['Инженер', 'Роль', 'Исходная команда', 'Выделено'], allocationRows) : empty('Распределения ресурсов отсутствуют.')}`;
}

function riskEvidenceValue(item) {
  const raw = item?.value;
  const value = typeof raw === 'number' ? number(raw) : escapeHtml(raw);
  if (!item?.unit) return value;
  if (item.unit === 'спринт' && typeof raw === 'number') return `Sprint ${number(raw)}`;
  return `${value} ${escapeHtml(item.unit)}`;
}

function renderRiskEvidence(risk) {
  const evidence = risk.evidence || [];
  if (!evidence.length) return '';
  return `<div class="detail-row"><strong>Evidence</strong><div class="risk-evidence-grid">${evidence.map((item) => `<div class="risk-evidence-card"><span>${escapeHtml(item.label)}</span><b>${riskEvidenceValue(item)}</b></div>`).join('')}</div></div>`;
}

function renderRiskAffectedTasks(risk) {
  const affected = [...new Set(risk.affected_tasks || [])];
  if (!affected.length || (affected.length === 1 && affected[0] === risk.task_id)) return '';
  return `<div class="detail-row"><strong>Затронутые задачи</strong><div class="risk-task-links">${affected.map((taskId) => `<button class="table-button" type="button" data-open-task="${attr(taskId)}">${escapeHtml(taskId)} ↗</button>`).join('')}</div></div>`;
}

function matchesRiskSearch(risk) {
  const query = (state.riskSearch || '').trim().toLowerCase();
  if (!query) return true;
  const haystack = [
    risk.risk_id,
    risk.title,
    risk.type,
    risk.object,
    risk.task_id,
    risk.team_id,
    risk.initiative_id,
    risk.cause,
    risk.effect,
    risk.recommendation,
    ...(risk.affected_tasks || []),
    ...((risk.evidence || []).map((item) => `${item.label} ${item.value ?? ''} ${item.unit ?? ''}`)),
  ].filter(Boolean).join(' ').toLowerCase();
  return haystack.includes(query);
}

function riskDetailMarkup(risk) {
  const accent = severityClass(risk.severity);
  return `
    <div class="eyebrow">ДЕТАЛИЗАЦИЯ РИСКА</div>
    <div class="risk-dialog-heading">
      <div>
        <h2 id="riskDialogTitle">${escapeHtml(risk.title)}</h2>
        <div class="risk-detail-pills">
          <span class="alert-pill ${accent}">${escapeHtml(risk.severity_label)}</span>
          <span class="risk-pill ${riskClass(risk.type)}">${escapeHtml(risk.type)}</span>
        </div>
      </div>
      <div class="risk-detail-meta">
        <span class="risk-dialog-object">${escapeHtml(risk.object)}</span>
        ${risk.sprint ? `<span>Sprint ${risk.sprint}</span>` : ''}
        ${risk.initiative_id ? `<span>${escapeHtml(risk.initiative_id)}</span>` : ''}
      </div>
    </div>
    <div class="risk-dialog-grid">
      <section class="detail-row"><strong>Причина</strong><span class="risk-detail-copy">${escapeHtml(risk.cause)}</span></section>
      ${renderRiskEvidence(risk)}
      <section class="detail-row risk-consequence"><strong>Последствие</strong><span class="risk-detail-copy">${escapeHtml(risk.effect)}</span></section>
      ${renderRiskAffectedTasks(risk)}
      ${renderRiskBusFactor(risk)}
      <section class="detail-row risk-recommendation"><strong>Рекомендация</strong><span class="risk-detail-copy">${escapeHtml(risk.recommendation)}</span></section>
    </div>
    <div class="risk-actions risk-actions-modal">
      <button class="btn btn-blue" type="button" data-open-task="${attr(risk.task_id)}">Открыть ${escapeHtml(risk.task_id)} ↗</button>
      ${risk.team_id ? `<button class="btn btn-outline" type="button" data-open-team="${attr(risk.team_id)}">Команда ${escapeHtml(risk.team_id)} ↗</button>` : ''}
    </div>
  `;
}

function openRiskModal(riskId = state.riskId) {
  const dialog = $('#riskDialog');
  const content = $('#riskDialogContent');
  const risk = data.risks.find((item) => item.risk_id === riskId);
  if (!dialog || !content || !risk) return;
  state.riskId = risk.risk_id;
  state.riskModalOpen = true;
  content.innerHTML = riskDetailMarkup(risk);
  if (!dialog.open) dialog.showModal();
}

function closeRiskModal() {
  state.riskModalOpen = false;
  const dialog = $('#riskDialog');
  if (dialog?.open) dialog.close();
}

function renderRiskBusFactor(risk) {
  const context = risk.bus_factor_context;
  if (!context) return '';
  const helpers = context.potential_helpers || [];
  const helperRows = helpers.length
    ? `<div class="risk-helper-list">${helpers.map((helper) => `<button class="risk-helper" type="button" data-open-employee="${attr(helper.engineer_id)}"><b>${escapeHtml(helper.engineer_id)}</b><span>${escapeHtml(helper.role || context.role)} · ${escapeHtml(helper.grade || 'grade не указан')}</span><small>${escapeHtml(helper.source_team_id || 'внешняя команда')} ↗</small></button>`).join('')}</div>`
    : '<p class="hint">Подходящий cross-team резерв по этой роли не найден.</p>';
  return `<div class="detail-row"><strong>Кадровый контекст · Bus Factor</strong><div class="risk-bf-metrics"><div><span>Роль</span><b>${escapeHtml(context.role)}</b></div><div><span>Team BF</span><b>${number(context.team_bus_factor)}</b></div><div><span>Organization BF</span><b>${number(context.organization_bus_factor)}</b></div></div>${helperRows}<p class="hint risk-bf-note">${escapeHtml(context.availability_note || '')}</p></div>`;
}

function renderRisks() {
  $$('#riskFilters button').forEach((button) => button.classList.toggle('selected', button.dataset.riskFilter === state.riskFilter));
  const search = $('#riskSearch');
  if (search && search.value !== state.riskSearch) search.value = state.riskSearch;
  const rows = data.risks.filter((risk) => (state.riskFilter === 'Все' || risk.type === state.riskFilter) && matchesRiskSearch(risk));
  if (!rows.some((risk) => risk.risk_id === state.riskId)) {
    state.riskId = rows[0]?.risk_id || null;
    if (!state.riskId) state.riskModalOpen = false;
  }
  $('#riskCount').textContent = `${rows.length}`;
  $('#riskRows').innerHTML = rows.map((risk) => {
    const accent = severityClass(risk.severity);
    const selected = state.riskModalOpen && state.riskId === risk.risk_id;
    return `<tr data-risk-id="${attr(risk.risk_id)}" class="risk-row-${accent} ${selected ? 'selected' : ''}"><td><span class="alert-pill ${accent}">${escapeHtml(risk.severity_label)}</span></td><td><b>${escapeHtml(risk.title)}</b></td><td><span class="risk-pill ${riskClass(risk.type)}">${escapeHtml(risk.type)}</span></td><td>${escapeHtml(risk.object)}</td><td>${risk.sprint ? `Sprint ${risk.sprint}` : '—'}</td><td><span class="risk-table-copy">${escapeHtml(risk.cause)}</span></td><td><span class="risk-table-copy">${escapeHtml(risk.effect)}</span></td></tr>`;
  }).join('');
  if (!rows.length) {
    $('#riskRows').innerHTML = '<tr><td colspan="7">Рисков этого типа нет.</td></tr>';
    closeRiskModal();
    return;
  }
  if (state.riskModalOpen && state.riskId) openRiskModal(state.riskId);
}

function renderRecalc() {
  renderDevelopmentNotice('#recalcDevelopmentNotice', 'Страница пока не завершена. Отображаемые данные и структура могут измениться.');
  $('#recalcPlaceholder').hidden = false;
  $('#recalcResult').hidden = true;
  $('#runDemo').disabled = true;
  $('#runDemo').textContent = 'Backend пересчёта не подключён';
}

function renderStar() {
  $$('.subtab').forEach((button) => {
    const active = button.dataset.starTab === state.starTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  $$('.star-view').forEach((element) => element.classList.toggle('active', element.id === ({teams: 'starTeams', skills: 'starSkills', distribution: 'starDistribution'}[state.starTab])));
  if (state.starTab === 'teams') renderStarTeams();
  if (state.starTab === 'skills') renderStarSkills();
  if (state.starTab === 'distribution') renderDistribution();
}

function busRiskTag(competency) {
  return `<span class="bf-risk-tag ${attr(competency.risk_level)}">${escapeHtml(competency.risk_label)}</span>`;
}

function renderCompetencyMap(team, selected) {
  if (!team.bus_factor_available) {
    $('#competencyMap').innerHTML = empty(team.message || 'Для этой команды пока нет данных о компетенциях.');
    $('#competencyDetail').innerHTML = '';
    $('#busRiskList').innerHTML = empty(team.message || 'Недостаточно данных для расчёта Bus Factor.');
    return;
  }
  const nodes = team.competencies.map((competency) => `<button type="button" class="competency-node ${attr(competency.risk_level)} ${competency.skill === selected?.skill ? 'selected' : ''}" data-star-competency="${attr(competency.skill)}" aria-pressed="${competency.skill === selected?.skill}"><span>${escapeHtml(competency.skill)}</span><b>BF ${number(competency.bus_factor)}</b></button>`).join('');
  $('#competencyMap').innerHTML = `<div class="map-team-core"><span>Команда</span><b>${escapeHtml(team.team_name)}</b><small>${team.engineer_count} инженеров · ${team.competency_count} компетенций</small></div><div class="competency-node-grid">${nodes}</div>`;
  $('#busRiskList').innerHTML = team.competencies.map((competency, index) => `<button type="button" class="bus-risk-item ${competency.skill === selected?.skill ? 'selected' : ''}" data-star-competency="${attr(competency.skill)}"><span class="risk-position">${index + 1}</span><span class="risk-copy"><b>${escapeHtml(competency.skill)}</b><small>${escapeHtml(competency.reason)}</small></span><span class="risk-value">BF ${number(competency.bus_factor)}${busRiskTag(competency)}</span></button>`).join('');
  if (!selected) {
    $('#competencyDetail').innerHTML = empty('Выберите компетенцию на карте.');
    return;
  }
  const holders = selected.engineers.length
    ? selected.engineers.map((engineer) => `<button type="button" class="engineer-chip" data-open-employee="${attr(engineer.engineer_id)}"><span>${escapeHtml(engineer.name)}</span><small>${escapeHtml(engineer.roles.join(', ') || engineer.engineer_id)}</small></button>`).join('')
    : empty('В команде нет инженеров с этой компетенцией.');
  const external = selected.external_engineers?.length
    ? `<div class="external-backup"><h3>Резерв в других командах</h3><p>Эти инженеры не считаются локальными участниками команды.</p><div class="engineer-chip-row">${selected.external_engineers.map((engineer) => `<button type="button" class="engineer-chip secondary" data-open-employee="${attr(engineer.engineer_id)}"><span>${escapeHtml(engineer.name)}</span><small>${escapeHtml(engineer.source_team_ids.join(', '))}</small></button>`).join('')}</div></div>`
    : '';
  $('#competencyDetail').innerHTML = `<div class="competency-detail-heading"><div><span class="eyebrow">ВЫБРАННАЯ КОМПЕТЕНЦИЯ</span><h2>${escapeHtml(selected.skill)}</h2></div><div class="bf-number"><span>Bus Factor</span><b>${number(selected.bus_factor)}</b>${busRiskTag(selected)}</div></div><p class="competency-reason">${escapeHtml(selected.reason)}</p>${selected.recommendation ? `<p class="competency-recommendation">${escapeHtml(selected.recommendation)}</p>` : ''}<h3>Кто покрывает компетенцию</h3><div class="engineer-chip-row">${holders}</div>${external}`;
}

function renderStarTeams() {
  const availableTeams = data.starMap?.teams || [];
  if (!availableTeams.length) {
    $('#starTeamSelect').innerHTML = '';
    $('#teamMetrics').innerHTML = '';
    $('#teamLifecycleActions').innerHTML = '';
    $('#competencyMap').innerHTML = empty(data.starMap?.message || 'Недостаточно данных для расчёта Bus Factor.');
    $('#competencyDetail').innerHTML = '';
    $('#busRiskList').innerHTML = empty('Критичных рисков Bus Factor не обнаружено.');
    $('#teamMembers').innerHTML = empty('В команде нет инженеров.');
    $('#teamWorkload').innerHTML = empty('Нет данных о доступности.');
    $('#teamTasks').innerHTML = empty('Текущих задач нет.');
    return;
  }
  if (!starTeamBy(state.teamId)) state.teamId = availableTeams[0].team_id;
  const busTeam = starTeamBy(state.teamId);
  const team = teamBy(busTeam.team_id);
  const activeMemberIds = new Set(busTeam.member_ids || []);
  const engineers = data.engineers.filter((engineer) => activeMemberIds.has(engineer.engineer_id));
  const sprint = currentSprint();
  const sourceUsed = Number(team.source_used_hours_by_sprint?.[String(sprint)] || 0);
  const memberCapacity = Number(team.member_capacity_hours || 0);
  const resource = teamSprintResource(team.team_id, sprint);
  const active = data.tasks.filter((task) => task.assigned_team_id === team.team_id && inQuarter(task));
  $('#starTeamSelect').innerHTML = availableTeams.map((item) => `<option value="${attr(item.team_id)}" ${item.team_id === busTeam.team_id ? 'selected' : ''}>${escapeHtml(item.team_name)}</option>`).join('');
  const summary = busTeam.summary;
  const metrics = busTeam.bus_factor_available
    ? [['Инженеров', busTeam.engineer_count, 'neutral'], ['Компетенций', busTeam.competency_count, 'neutral'], ['BF = 1', summary.single_owner_count, 'critical'], ['Требуют внимания', summary.warning_count, 'warning'], ['Устойчивое покрытие', summary.healthy_count, 'healthy']]
    : [['Инженеров', busTeam.engineer_count, 'neutral'], ['Компетенции', 'Нет данных', 'neutral']];
  $('#teamMetrics').innerHTML = metrics.map(([label, value, tone]) => `<div class="bf-summary-item ${tone}"><span>${escapeHtml(label)}</span><b>${typeof value === 'number' ? number(value) : escapeHtml(value)}</b></div>`).join('');
  $('#teamLifecycleActions').innerHTML = `${archiveState(team)}${team.archived ? '' : `<div class="entity-actions"><button class="btn btn-danger" type="button" data-archive-entity="team" data-archive-id="${attr(team.team_id)}" data-current-plan-reference="${active.length > 0 || engineers.length > 0}">Удалить</button></div>`}`;
  $('#teamMembers').innerHTML = engineers.length ? table(['Сотрудник', 'Роли', 'Grade', 'Доступно', 'Занято', 'Свободно'], engineers.map((engineer) => `<tr><td><button type="button" class="table-button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td>${escapeHtml(engineer.roles.join(', '))}</td><td>${escapeHtml(engineer.grade)}</td><td>${number(engineer.capacity_hours)} ч</td><td>${number(engineer.used_hours_by_sprint[String(sprint)])} ч</td><td>${number(engineer.free_hours_by_sprint[String(sprint)])} ч</td></tr>`)) : empty('В команде нет инженеров.');
  $('#teamWorkload').innerHTML = `<div class="forecast-number" style="font-size:52px">${memberCapacity ? Math.round(sourceUsed / memberCapacity * 100) : 0}%</div>${meter(sourceUsed, memberCapacity)}<div class="resource-line"><span>Доступно участникам</span><b>${number(memberCapacity)} ч</b></div><div class="resource-line"><span>Использовано участниками</span><b>${number(sourceUsed)} ч</b></div><div class="resource-line"><span>Работа задач выбранной команды</span><b>${number(team.task_work_hours_by_sprint?.[String(sprint)])} ч</b></div><div class="resource-line"><span>SP команды</span><b>${number(resource.used)} / ${number(resource.capacity)} SP</b></div>`;
  $('#teamTasks').innerHTML = active.length ? table(['Задача', 'Инициатива', 'Период', 'Статус'], active.map((task) => `<tr><td>${tButton(task)}</td><td>${escapeHtml(task.initiative_id || '—')}</td><td>${escapeHtml(sprintLabel(task))}</td><td>${badge(task)}</td></tr>`)) : empty('Текущих задач нет.');
  let selected = busTeam.competencies.find((item) => item.skill === state.competency);
  if (!selected) selected = busTeam.competencies[0] || null;
  state.competency = selected?.skill || null;
  renderCompetencyMap(busTeam, selected);
}

function renderStarSkills() {
  const activeEngineers = data.starMap?.engineers || [];
  const activeIds = new Set(activeEngineers.map((engineer) => engineer.engineer_id));
  const allSkills = [...new Set(activeEngineers.flatMap((engineer) => engineer.skills))].sort();
  const allRoles = [...new Set(activeEngineers.flatMap((engineer) => engineer.roles))].sort();
  const bfTeams = data.starMap?.teams || [];
  $('#skillTeam').innerHTML = `<option value="all">Все команды</option>${bfTeams.map((team) => `<option value="${attr(team.team_id)}" ${state.skillTeam === team.team_id ? 'selected' : ''}>${escapeHtml(team.team_name)}</option>`).join('')}`;
  $('#skillRole').innerHTML = `<option value="all">Все роли</option>${allRoles.map((role) => `<option value="${attr(role)}" ${state.skillRole === role ? 'selected' : ''}>${escapeHtml(role)}</option>`).join('')}`;
  $('#skillTech').innerHTML = `<option value="all">Все технологии</option>${allSkills.map((skill) => `<option value="${attr(skill)}" ${state.skillTech === skill ? 'selected' : ''}>${escapeHtml(skill)}</option>`).join('')}`;
  $('#criticalOnly').checked = state.criticalOnly;
  const teamScope = state.skillTeam === 'all' ? null : starTeamBy(state.skillTeam);
  const scopeIds = teamScope ? new Set(teamScope.member_ids) : activeIds;
  const scope = activeEngineers.filter((engineer) => scopeIds.has(engineer.engineer_id));
  const criticalSkills = new Set((teamScope ? teamScope.competencies : bfTeams.flatMap((team) => team.competencies)).filter((item) => item.bus_factor === 1).map((item) => item.skill));
  const columns = allSkills.filter((skill) => (state.skillTech === 'all' || skill === state.skillTech) && (!state.criticalOnly || criticalSkills.has(skill)));
  $('#heatScope').textContent = state.criticalOnly ? 'Показаны только single-person dependencies (BF = 1).' : 'Показано наличие компетенции; Bus Factor рассчитан backend-сервисом.';
  const subset = scope.filter((engineer) => state.skillRole === 'all' || engineer.roles.includes(state.skillRole));
  $('#heatmap').innerHTML = subset.length && columns.length ? table(['Инженер', ...columns], subset.map((engineer) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button><div class="muted">${escapeHtml(engineer.roles.join(', '))}</div></td>${columns.map((skill) => `<td>${engineer.skills.includes(skill) ? '<span class="heat-yes">Есть</span>' : '<span class="heat-no">—</span>'}</td>`).join('')}</tr>`), 'heatmap-table') : empty('Для выбранных фильтров сотрудников или технологий нет.');
}

function renderDistribution() {
  const sprint = currentSprint();
  const activeIds = new Set((data.starMap?.engineers || []).map((engineer) => engineer.engineer_id));
  const activeEngineers = data.engineers.filter((engineer) => activeIds.has(engineer.engineer_id));
  $('#distributionTable').innerHTML = activeEngineers.length ? table(['Сотрудник', 'Участие в командах', 'Роли', 'Доступно', 'Занято', 'Свободно'], activeEngineers.map((engineer) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td>${engineer.capacity_buckets.map((bucket) => `${escapeHtml(bucket.team_id)}: ${number(bucket.capacity_hours)} ч`).join('<br>')}</td><td>${escapeHtml(engineer.roles.join(', '))}</td><td>${number(engineer.capacity_hours)} ч</td><td>${number(engineer.used_hours_by_sprint[String(sprint)])} ч</td><td>${number(engineer.free_hours_by_sprint[String(sprint)])} ч</td></tr>`)) : empty('Инженеры не найдены.');
}

function openEmployee(engineerId) {
  const engineer = engineerBy(engineerId);
  if (!engineer) return;
  const busProfile = starEngineerBy(engineerId);
  const sprint = currentSprint();
  const allocations = engineer.allocations.filter((item) => item.sprint === sprint);
  const grade = engineer.grade ? ` · Grade: ${escapeHtml(engineer.grade)}` : '';
  const criticality = busProfile?.team_criticality || [];
  const displayedSkills = busProfile?.skills || engineer.skills;
  const impact = criticality.length ? criticality.map((item) => `<button type="button" class="engineer-impact ${attr(item.risk_level)}" data-employee-team="${attr(item.team_id)}"><span><b>${escapeHtml(item.team_name)} · ${escapeHtml(item.skill)}</b><small>${item.single_person_dependency ? 'Единственный специалист команды' : escapeHtml(item.risk_label)}</small></span><strong>BF ${number(item.bus_factor)}</strong></button>`).join('') : empty(engineer.archived ? 'Архивный инженер не входит в текущий Bus Factor.' : 'Нет данных о вкладе в компетенции команд.');
  $('#employeeContent').innerHTML = `<div class="eyebrow">КАРТОЧКА СОТРУДНИКА</div><h2>${escapeHtml(engineer.name || engineer.engineer_id)}</h2><p>${escapeHtml(engineer.roles.join(', ') || engineer.engineer_id)}${grade}</p><div class="metric-grid employee-metrics"><div class="metric"><span>Команд</span><b>${engineer.team_ids.length}</b></div><div class="metric"><span>Доступно за спринт</span><b>${number(engineer.capacity_hours)} ч</b></div><div class="metric"><span>Занято в Sprint ${sprint}</span><b>${number(engineer.used_hours_by_sprint[String(sprint)])} ч</b></div><div class="metric"><span>Свободно</span><b>${number(engineer.free_hours_by_sprint[String(sprint)])} ч</b></div></div><div class="drawer-section"><h3>Команды и участие</h3>${engineer.capacity_buckets.map((bucket) => `<button class="profile-link" data-employee-team="${attr(bucket.team_id)}" type="button">${escapeHtml(bucket.team_id)} · доступность ${percent(Number(bucket.capacity_rate) * 100)} · ${number(bucket.capacity_hours)} ч ↗</button>`).join('')}</div><div class="drawer-section"><h3>Технологии</h3><div class="skill-tags">${displayedSkills.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></div><div class="drawer-section"><h3>Критичность для команд</h3><div class="engineer-impact-list">${impact}</div></div><div class="drawer-section"><h3>Назначения Sprint ${sprint}</h3>${allocations.length ? allocations.map((item) => `<p>${escapeHtml(item.task_id)} · ${escapeHtml(item.role)} · ${number(item.allocated_hours)} ч${item.cross_team ? ` · помогает ${escapeHtml(item.assigned_task_team_id)} из ${escapeHtml(item.source_team_id)}` : ''}</p>`).join('') : '<p>Назначений нет.</p>'}</div>${archiveState(engineer)}${engineer.archived ? '' : `<div class="entity-actions"><button class="btn btn-danger" type="button" data-archive-entity="engineer" data-archive-id="${attr(engineer.engineer_id)}" data-current-plan-reference="${engineer.allocations.length > 0}">Удалить</button></div>`}`;
  $('#employeeDrawer').classList.add('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'false');
}

function closeEmployee() {
  $('#employeeDrawer').classList.remove('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'true');
}

function bindEvents() {
  $('#addTaskFromTasks')?.addEventListener('click', () => window.planConstructor.openTaskForm());
  window.addEventListener('postcode:constructor-task-added', (event) => {
    show('planning');
    showToast(`Задача ${event.detail?.taskId || ''} добавлена в черновик сценария и выбрана.`);
  });
  $('#starTeamSelect').addEventListener('change', (event) => { state.teamId = event.target.value; state.competency = null; renderStarTeams(); });
  $('#skillTeam').addEventListener('change', (event) => { state.skillTeam = event.target.value; renderStarSkills(); });
  $('#skillRole').addEventListener('change', (event) => { state.skillRole = event.target.value; renderStarSkills(); });
  $('#skillTech').addEventListener('change', (event) => { state.skillTech = event.target.value; renderStarSkills(); });
  $('#criticalOnly').addEventListener('change', (event) => { state.criticalOnly = event.target.checked; renderStarSkills(); });
  $('#chooseFact').addEventListener('click', () => $('#factInput').click());
  $('#factInput').addEventListener('change', (event) => { const file = event.target.files?.[0]; $('#uploadFileStatus').textContent = file ? `Выбран файл: ${file.name}. Backend пересчёта не подключён, файл не обработан.` : 'Файл не выбран'; });
  $('#archiveForm').addEventListener('submit', (event) => { event.preventDefault(); submitArchive(); });
  $('#archiveReason').addEventListener('change', updateArchiveConfirmation);
  $('#archiveReasonText').addEventListener('input', updateArchiveConfirmation);
  $('#archiveDialog').addEventListener('click', (event) => { if (event.target === $('#archiveDialog')) closeArchiveModal(); });
  $('#riskDialog')?.addEventListener('click', (event) => { if (event.target === $('#riskDialog')) closeRiskModal(); });
  $('#riskDialog')?.addEventListener('close', () => { state.riskModalOpen = false; renderRisks(); });
  $('#riskSearch')?.addEventListener('input', (event) => { state.riskSearch = event.target.value; renderRisks(); });
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-close-archive]')) { closeArchiveModal(); return; }
    const archive = event.target.closest('[data-archive-entity]');
    if (archive) { openArchiveModal(archive.dataset.archiveEntity, archive.dataset.archiveId, archive.dataset.currentPlanReference === 'true'); return; }
    if (event.target.closest('[data-close-drawer]')) { closeEmployee(); return; }
    if (event.target.closest('[data-task-back]')) { show(state.taskReturnPage || 'tasks'); return; }
    const nav = event.target.closest('button[data-page]'); if (nav) { closeRiskModal(); show(nav.dataset.page); return; }
    const taskSort = event.target.closest('[data-task-sort]');
    if (taskSort) {
      const key = taskSort.dataset.taskSort;
      if (state.taskSortKey === key) state.taskSortDirection = state.taskSortDirection === 'asc' ? 'desc' : 'asc';
      else { state.taskSortKey = key; state.taskSortDirection = 'asc'; }
      renderTasks();
      return;
    }
    const homeSprint = event.target.closest('[data-home-sprint]'); if (homeSprint) { const sprint = Number(homeSprint.dataset.homeSprint); show('current-plan'); $(`[data-sprint-column="${sprint}"]`)?.scrollIntoView({behavior: 'smooth', inline: 'center', block: 'nearest'}); return; }
    const planTask = event.target.closest('[data-plan-task]'); if (planTask) { closeRiskModal(); gotoTask(planTask.dataset.planTask); return; }
    const task = event.target.closest('[data-open-task]'); if (task) { closeRiskModal(); gotoTask(task.dataset.openTask); return; }
    const risk = event.target.closest('[data-open-risk]'); if (risk) { state.riskId = risk.dataset.openRisk; state.riskFilter = 'Все'; state.riskSearch = ''; state.riskModalOpen = true; show('risks'); return; }
    const filter = event.target.closest('[data-risk-filter]'); if (filter) { state.riskFilter = filter.dataset.riskFilter; renderRisks(); return; }
    const riskRow = event.target.closest('[data-risk-id]'); if (riskRow) { state.riskId = riskRow.dataset.riskId; openRiskModal(riskRow.dataset.riskId); renderRisks(); return; }
    const competency = event.target.closest('[data-star-competency]'); if (competency) { state.competency = competency.dataset.starCompetency; renderStarTeams(); $('#competencyDetail')?.scrollIntoView({behavior: 'smooth', block: 'nearest'}); return; }
    const team = event.target.closest('[data-open-team]'); if (team) { closeRiskModal(); gotoTeam(team.dataset.openTeam); return; }
    const employee = event.target.closest('[data-open-employee]'); if (employee) { gotoEmployee(employee.dataset.openEmployee); return; }
    const backToTeam = event.target.closest('[data-employee-team]'); if (backToTeam) { closeEmployee(); gotoTeam(backToTeam.dataset.employeeTeam); return; }
    const starTab = event.target.closest('[data-star-tab]'); if (starTab) { state.starTab = starTab.dataset.starTab; renderStar(); return; }
  });
  document.addEventListener('change', (event) => { if (event.target.id === 'taskSprintSelect') { state.taskSprint = Number(event.target.value); renderTaskDetail(); } });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { closeArchiveModal(); closeRiskModal(); closeEmployee(); }
    const taskRow = event.target.closest?.('[data-task-row]');
    if (taskRow && event.target === taskRow && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); gotoTask(taskRow.dataset.openTask); }
  });
  window.addEventListener('popstate', () => {
    const route = routeFromLocation();
    state.taskId = route.taskId || state.taskId;
    state.taskSprint = null;
    show(route.page, false);
  });
}

function hydrate(payload) {
  data.plan = payload.plan;
  data.summary = payload.summary;
  data.tasks = payload.tasks || [];
  data.initiatives = payload.initiatives || [];
  data.risks = payload.risks || [];
  data.teams = payload.teams || [];
  data.engineers = payload.engineers || [];
  data.starMap = payload.star_map || {teams: [], engineers: []};
  data.kpis = payload.kpis || null;
  data.features = payload.features || {};
  data.ready = true;
  state.loading = false;
  state.error = null;
  if (!state.taskId) state.taskId = data.tasks.find((task) => task.status === 'PLANNED')?.task_id || data.tasks[0]?.task_id || null;
  state.teamId = data.starMap.teams?.[0]?.team_id || data.teams[0]?.team_id || null;
  state.competency = null;
  state.riskSearch = '';
  state.riskModalOpen = false;
  state.riskId = data.risks[0]?.risk_id || null;
}

async function initialize() {
  bindEvents();
  const initialRoute = routeFromLocation();
  state.taskId = initialRoute.taskId;
  show(initialRoute.page, 'replace');
  try {
    const [dashboard, constructorCatalog, archiveReasons] = await Promise.all([
      window.postcodeApi.getDashboard(),
      window.postcodeApi.getConstructorCatalog(),
      window.postcodeApi.getArchiveReasons(),
    ]);
    hydrate(dashboard);
    data.archiveReasons = archiveReasons;
    window.planConstructor.initialize(constructorCatalog, window.postcodeApi, (plannerResult) => {
      hydrate(plannerResult);
      show('current-plan');
    });
    renderPage(state.page);
  } catch (error) {
    state.loading = false;
    state.error = error?.message || 'неизвестная ошибка';
    renderUnavailable();
  }
}

initialize();
