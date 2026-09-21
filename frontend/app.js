'use strict';

const state = {
  page: 'home', taskId: null, taskSprint: null,
  teamId: null, engineerId: null, riskHubTab: 'risks', riskId: null,
  teamDetailTab: 'overview', teamReturnPage: 'teams',
  engineerDetailTab: 'overview', engineerReturnPage: 'engineers',
  riskFilter: 'Все', riskSearch: '', riskModalOpen: false, skillTeam: 'all', skillRole: 'all', skillTech: 'all',
  competency: null, criticalOnly: false, starGraphMode: 'technology', starQuarterOnly: false,
  starCompetencyOpen: false,
  starEngineerSearch: '', starRoleFilters: [],
  workspaceMode: 'manager', engineerWorkspaceId: null, engineerTaskSearch: '', engineerWorkspaceLoading: false,
  taskReturnPage: 'home', loading: true, error: null, archive: null,
  currentSprintExpandedTaskId: null,
  kpiDetailsExpanded: false,
  taskSortKey: 'task_id', taskSortDirection: 'asc',
  engineerSortKey: 'engineer_id', engineerSortDirection: 'asc',
  teamSortKey: 'name', teamSortDirection: 'asc',
  developmentTeam: 'all', developmentRole: 'all', developmentSkill: 'all',
  developmentLoading: false, developmentError: null,
};

const data = {
  plan: null, summary: {}, tasks: [], historicalTasks: [], initiatives: [], risks: [], teams: [],
  engineers: [], starMap: {teams: [], engineers: []}, kpis: null, features: {}, archiveReasons: {}, ready: false,
  status: 'LOADING', dataState: 'LOADING', message: null,
  teamDetails: {}, teamHistories: {}, engineerDetails: {}, engineerHistories: {},
  replanWorkflow: null,
  engineerWorkspace: null, engineerCatalog: [],
  development: null,
};

const starViewport = {
  minZoom: 0.65, maxZoom: 2.4, scale: 1, x: 0, y: 0,
  pointerId: null, startX: 0, startY: 0, originX: 0, originY: 0,
  dragged: false,
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
const starTeamBy = (id) => data.starMap?.company?.team_id === id
  ? data.starMap.company
  : data.starMap?.teams?.find((team) => team.team_id === id);
const starEngineerBy = (id) => data.starMap?.engineers?.find((engineer) => engineer.engineer_id === id);
const taskBy = (id) => data.tasks.find((task) => task.task_id === id)
  || data.historicalTasks.find((task) => task.task_id === id);
const historicalTaskFromCatalog = (task) => ({
  task_id: task.task_id, title: task.title, description: task.title,
  initiative_id: task.initiative_id, status: 'DONE',
  status_label: 'Завершена до начала планирования', source_status: task.status,
  rung: task.rung,
  priority_label: Number.isFinite(Number(task.rung)) ? `Rung ${task.rung}` : 'Rung не указан',
  story_points: task.story_points, original_team_id: task.team_id, assigned_team_id: task.team_id,
  team_reassigned: false, planned_sprints: [], completion_sprint: 0, multi_sprint: false,
  sp_load_by_sprint: {}, hours_by_sprint: {}, role_hours_by_sprint: {},
  role_requirements: Object.fromEntries((task.role_requirements || []).map((item) => [item.role, item.required_hours])),
  required_skills: task.required_skills || [],
  dependencies: {incoming: [], outgoing: []}, allocations: [], ownership: null,
  reasons: [], reason_blocks: [], risks: [], recommendations: [],
  summary: 'Завершена до начала планирования.', outside_quarter: false, is_placeholder: false,
});
const currentSprint = () => Number(data.plan?.quarter?.current_sprint || 1);
const hasPendingReplan = () => Boolean(data.replanWorkflow
  && ['ACTUAL_SAVED', 'READY_FOR_REVIEW', 'REPLANNING_FAILED'].includes(data.replanWorkflow.status));
const sprintBy = (sprint) => data.plan?.sprints?.find((item) => item.sprint_number === sprint);
const taskSprints = (task) => task?.planned_sprints || [];
const inQuarter = (task) => task?.status === 'PLANNED' && taskSprints(task).length > 0;
const portion = (task, sprint) => Number(task?.sp_load_by_sprint?.[String(sprint)] || 0);
const sprintHours = (task, sprint) => task?.role_hours_by_sprint?.[String(sprint)] || null;
const sprintLabel = (task) => {
  const sprints = taskSprints(task);
  if (!sprints.length) return task.status === 'DONE' ? 'Завершена до начала планирования' : 'Не включена в квартальный план';
  return sprints.length === 1 ? `Sprint ${sprints[0]}` : `Sprint ${sprints[0]}–${sprints.at(-1)}`;
};
const sprintStatus = (sprint) => ({CURRENT: 'Текущий', PLANNED: 'Запланирован', DONE: 'Завершён', COMPLETED: 'Завершён'}[sprintBy(sprint)?.status] || 'Запланирован');
const statusClass = (status) => ({DONE: 'done', PLANNED: 'planned', MOVED: 'moved', UNPLANNABLE: 'cancelled'}[status] || 'planned');
const badge = (task) => `<span class="status ${taskStatusClass(task)}">${escapeHtml(taskDisplayStatus(task))}</span>`;
const taskDisplayStatus = (task) => {
  const lifecycleStatus = String(task?.current_status || '').toUpperCase();
  if (lifecycleStatus) return ({DONE: 'Завершена', COMPLETED: 'Завершена', IN_PROGRESS: 'В процессе', PLANNED: 'Запланирована', MOVED: 'Перенесена', UNPLANNABLE: 'Невыполнима', CANCELLED: 'Отменена'}[lifecycleStatus] || task?.current_status_label || lifecycleStatus);
  const sourceStatus = String(task?.source_status || '').replaceAll(' ', '').toUpperCase();
  if (sourceStatus === 'CANCELLED' || sourceStatus === 'CANCELED') return 'Отменена';
  if (task?.status === 'PLANNED' && sourceStatus === 'INPROGRESS') return 'В процессе';
  return ({PLANNED: 'Запланирована', DONE: 'Завершена', COMPLETED: 'Завершена', IN_PROGRESS: 'В процессе', CANCELLED: 'Отменена', MOVED: 'Перенесена', UNPLANNABLE: 'Невыполнима'}[task?.status] || task?.status_label || 'Статус не указан');
};
const taskStatusClass = (task) => {
  const label = taskDisplayStatus(task);
  return ({'Запланирована': 'planned', 'Завершена': 'done', 'В процессе': 'work', 'Отменена': 'cancelled', 'Перенесена': 'moved', 'Невыполнима': 'cancelled'}[label] || 'planned');
};
const taskStatusTooltip = (task) => ({
  'Запланирована': 'Задача выполнима и включена в квартальный план.',
  'Завершена': 'Задача завершена по фактическому результату спринта.',
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

function openResetDataDialog() {
  const dialog = $('#resetDataDialog');
  const checkbox = $('#resetDataConfirm');
  const errorBox = $('#resetDataError');
  checkbox.checked = false;
  $('#confirmResetData').disabled = true;
  errorBox.hidden = true;
  errorBox.textContent = '';
  dialog.showModal();
}

function closeResetDataDialog() {
  $('#resetDataDialog').close();
}

async function submitResetData(event) {
  event.preventDefault();
  if (!$('#resetDataConfirm').checked) return;
  const button = $('#confirmResetData');
  const errorBox = $('#resetDataError');
  button.disabled = true;
  button.textContent = 'Удаляем…';
  errorBox.hidden = true;
  try {
    await window.postcodeApi.resetAllData();
    window.localStorage.removeItem('postcode.plan-constructor.v2');
    window.location.replace('/?reset=1#planning');
  } catch (error) {
    errorBox.textContent = String(error?.message || error);
    errorBox.hidden = false;
    button.disabled = false;
    button.textContent = 'Удалить все данные';
  }
}

function archiveEntityLabel(entityType) {
  return ({task: 'задачу', engineer: 'инженера', team: 'команду'})[entityType] || 'объект';
}

function renderArchiveImpact(impact) {
  if (!state.archive) return;
  const engineerCount = (impact.engineer_ids || []).length;
  const taskCount = (impact.task_ids || []).length;
  const allocationCount = Number(impact.allocation_count || 0);
  const decisions = [];
  if (state.archive.entityType === 'team' && engineerCount) {
    decisions.push(`<fieldset class="archive-decision"><legend>Сотрудники команды</legend><label><input type="radio" name="teamEngineerAction" value="UNASSIGN"><span>Оставить активными без команды</span></label><label><input type="radio" name="teamEngineerAction" value="DEACTIVATE"><span>Сделать неактивными</span></label></fieldset>`);
  }
  if (state.archive.entityType === 'team' && taskCount) {
    decisions.push(`<fieldset class="archive-decision"><legend>Задачи команды</legend><label><input type="radio" name="teamTaskAction" value="REMOVE"><span>Убрать задачи из плана</span></label><label><input type="radio" name="teamTaskAction" value="REDISTRIBUTE"><span>Попытаться перераспределить между оставшимися командами</span></label></fieldset>`);
  }
  const replacementOptions = impact.replacement_options || [];
  const replacementRows = replacementOptions.map((item) => `<div class="archive-impact-note ${item.possible ? '' : 'danger'}"><b>${escapeHtml(item.task_id)}</b>: ${item.possible ? `планировщик может назначить ${escapeHtml(item.engineer_ids.join(', '))}` : 'подходящая замена не найдена'}</div>`).join('');
  if (state.archive.entityType === 'engineer' && replacementOptions.length) {
    const allReplaceable = replacementOptions.every((item) => item.possible);
    decisions.push(`<fieldset class="archive-decision"><legend>Текущие allocations инженера</legend><label><input type="radio" name="engineerAllocationAction" value="${allReplaceable ? 'APPLY_PLANNER' : 'REPLAN_WITHOUT_REPLACEMENT'}"><span>${allReplaceable ? 'Применить предложенные planner замены' : 'Деактивировать инженера и пересчитать план без замены'}</span></label></fieldset>`);
  }
  const blocked = (impact.blocked_task_ids || []).length ? `<div class="archive-impact-note danger">Задача блокирует: ${escapeHtml(impact.blocked_task_ids.join(', '))}. Сначала измените зависимости.</div>` : '';
  const teamBlocked = (impact.external_blocked_task_ids || []).length ? `<div class="archive-impact-note danger">При варианте «Убрать задачи» останутся зависимые задачи: ${escapeHtml(impact.external_blocked_task_ids.join(', '))}. Выберите перераспределение или измените зависимости.</div>` : '';
  const unplannable = (impact.unplannable_task_ids || []).length ? `<div class="archive-impact-note danger">После пересчёта могут не войти в план: ${escapeHtml(impact.unplannable_task_ids.join(', '))}.</div>` : '';
  const optimization = impact.optimization_available ? `<div class="archive-impact-note">После удаления план можно оптимизировать: раньше поставить ${escapeHtml((impact.improved_task_ids || []).join(', '))}.</div>` : '';
  const simulationError = impact.simulation_error ? `<div class="archive-impact-note danger">Не удалось проверить новый план: ${escapeHtml(impact.simulation_error)}</div>` : '';
  $('#archiveImpact').innerHTML = `<div class="archive-impact-summary"><div><b>${engineerCount}</b><span>сотрудников</span></div><div><b>${taskCount}</b><span>задач</span></div><div><b>${allocationCount}</b><span>allocations</span></div></div>${decisions.join('')}${replacementRows}${blocked}${teamBlocked}${unplannable}${optimization}${simulationError}`;
  const requiresReplan = state.archive.entityType !== 'task' || impact.requires_replan || impact.optimization_available;
  state.archive = {...state.archive, impact, impactLoaded: true, requiresReplan};
  $('#archiveReplanField').hidden = !requiresReplan;
  $('#archiveCurrentPlanWarning').hidden = !requiresReplan;
  $('#archiveCurrentPlanWarning').textContent = requiresReplan
    ? 'Изменение затрагивает текущий план. Новый вариант будет рассчитан существующим planner и применён только после вашего подтверждения.'
    : 'Исторические данные сохранятся; физического удаления из БД не будет.';
  updateArchiveConfirmation();
}

async function openArchiveModal(entityType, entityId, hasCurrentPlanReferences = false) {
  const dialog = $('#archiveDialog');
  const labels = data.archiveReasons[entityType] || {};
  state.archive = {entityType, entityId, hasCurrentPlanReferences, impactLoaded: false, requiresReplan: false};
  $('#archiveDialogTitle').textContent = `Удалить ${archiveEntityLabel(entityType)}`;
  $('#archiveDialogDescription').textContent = 'Сначала проверяем сотрудников, задачи, allocations, зависимости и последствия для capacity.';
  $('#archiveImpact').innerHTML = '<div class="archive-impact-note">Проверяем последствия удаления…</div>';
  $('#archiveCurrentPlanWarning').hidden = true;
  $('#archiveReplanField').hidden = true;
  $('#archiveReplanConfirm').checked = false;
  $('#archiveReason').innerHTML = `<option value="">Выберите причину</option>${Object.entries(labels).map(([code, label]) => `<option value="${attr(code)}">${escapeHtml(label)}</option>`).join('')}`;
  $('#archiveReasonText').value = '';
  $('#archiveOtherField').hidden = true;
  $('#archiveError').hidden = true;
  $('#archiveError').textContent = '';
  updateArchiveConfirmation();
  dialog.showModal();
  try {
    const impact = await window.postcodeApi.getDeletionImpact(entityType, entityId);
    if (state.archive?.entityType === entityType && state.archive?.entityId === entityId) renderArchiveImpact(impact);
  } catch (error) {
    if (!state.archive) return;
    $('#archiveImpact').innerHTML = `<div class="archive-impact-note danger">${escapeHtml(error?.message || 'Не удалось проверить последствия удаления.')}</div>`;
    $('#archiveError').textContent = error?.message || 'Не удалось проверить последствия удаления.';
    $('#archiveError').hidden = false;
  }
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
  const archive = state.archive;
  const teamEngineerRequired = archive?.entityType === 'team' && (archive.impact?.engineer_ids || []).length > 0;
  const teamTaskRequired = archive?.entityType === 'team' && (archive.impact?.task_ids || []).length > 0;
  const engineerChoice = document.querySelector('[name="teamEngineerAction"]:checked');
  const taskChoice = document.querySelector('[name="teamTaskAction"]:checked');
  const replacementRequired = archive?.entityType === 'engineer' && (archive.impact?.replacement_options || []).length > 0;
  const replacementChoice = document.querySelector('[name="engineerAllocationAction"]:checked');
  const blockedByDependency = (archive?.impact?.blocked_task_ids || []).length > 0
    || (taskChoice?.value === 'REMOVE' && (archive?.impact?.external_blocked_task_ids || []).length > 0);
  const choicesReady = (!teamEngineerRequired || engineerChoice) && (!teamTaskRequired || taskChoice) && (!replacementRequired || replacementChoice) && !blockedByDependency;
  const replanReady = !archive?.requiresReplan || $('#archiveReplanConfirm').checked;
  $('#confirmArchive').disabled = !archive?.impactLoaded || !reasonCode || (isOther && !$('#archiveReasonText').value.trim()) || !choicesReady || !replanReady;
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
      confirm_replan: $('#archiveReplanConfirm').checked,
      team_engineer_action: document.querySelector('[name="teamEngineerAction"]:checked')?.value || null,
      team_task_action: document.querySelector('[name="teamTaskAction"]:checked')?.value || null,
      engineer_allocation_action: document.querySelector('[name="engineerAllocationAction"]:checked')?.value || null,
    });
    hydrate(result.dashboard);
    closeArchiveModal();
    closeEmployee();
    const destination = {task: 'tasks', engineer: 'engineers', team: 'teams'}[entityType];
    show(destination);
    showToast([result.message, result.warning].filter(Boolean).join(' '));
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
  if (page === 'team-detail' && state.teamId) return `/teams/${encodeURIComponent(state.teamId)}`;
  if (page === 'engineer-detail' && state.engineerId) return `/engineers/${encodeURIComponent(state.engineerId)}`;
  if (page === 'tasks') return '/tasks';
  if (page === 'teams') return '/teams';
  if (page === 'engineers') return '/engineers';
  return page === 'home' ? '/' : `/#${page}`;
}

function routeFromLocation() {
  const taskMatch = window.location.pathname.match(/^\/tasks\/([^/]+)\/?$/);
  if (taskMatch) return {page: 'task-detail', taskId: decodeURIComponent(taskMatch[1])};
  const teamMatch = window.location.pathname.match(/^\/teams\/([^/]+)\/?$/);
  if (teamMatch) return {page: 'team-detail', teamId: decodeURIComponent(teamMatch[1])};
  const engineerMatch = window.location.pathname.match(/^\/engineers\/([^/]+)\/?$/);
  if (engineerMatch) return {page: 'engineer-detail', engineerId: decodeURIComponent(engineerMatch[1])};
  if (window.location.pathname === '/tasks' || window.location.pathname === '/tasks/') return {page: 'tasks', taskId: null};
  if (window.location.pathname === '/teams' || window.location.pathname === '/teams/') return {page: 'teams', teamId: null};
  if (window.location.pathname === '/engineers' || window.location.pathname === '/engineers/') return {page: 'engineers', engineerId: null};
  const hashPage = window.location.hash.replace(/^#/, '');
  const knownPages = new Set(['home', 'planning', 'current-plan', 'current-sprint', 'tasks', 'engineers', 'teams', 'risks', 'development', 'recalc', 'star', 'engineer-sprint', 'engineer-quarter', 'engineer-tasks']);
  const page = knownPages.has(hashPage) ? hashPage : 'home';
  return {page, taskId: null, teamId: null, workspaceMode: page.startsWith('engineer-') ? 'engineer' : (hashPage ? 'manager' : null)};
}

function updateWorkspaceChrome() {
  const engineerMode = state.workspaceMode === 'engineer';
  $$('[data-manager-nav]').forEach((item) => { item.hidden = engineerMode; });
  $$('[data-engineer-nav]').forEach((item) => { item.hidden = !engineerMode; });
  $$('[data-workspace-mode]').forEach((item) => item.classList.toggle('active', item.dataset.workspaceMode === state.workspaceMode));
  $('#engineerContext').hidden = !engineerMode;
  $('.brand').dataset.page = engineerMode ? 'engineer-sprint' : 'home';
  document.body.classList.toggle('engineer-mode', engineerMode);
}

function canonicalPage(page) {
  if (page === 'star') return 'risks';
  return page === 'current-plan' ? 'home' : page;
}

function show(page, historyMode = 'push') {
  if (page === 'recalc' && data.ready && !hasPendingReplan()) page = 'current-sprint';
  if (page === 'star') state.riskHubTab = 'teams';
  page = canonicalPage(page);
  if (page === 'home') state.kpiDetailsExpanded = false;
  state.page = page;
  updateWorkspaceChrome();
  const viewPage = page === 'home' ? 'current-plan' : page;
  $$('.page').forEach((element) => element.classList.toggle('active', element.id === `page-${viewPage}`));
  $$('.nav-link').forEach((element) => element.classList.toggle('active', element.dataset.page === ({'task-detail': 'tasks', 'team-detail': 'teams', 'engineer-detail': 'engineers'}[page] || page)));
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
  ({home: renderPlanning, planning: window.planConstructor.render, 'current-sprint': renderCurrentSprint, tasks: renderTasks, engineers: renderEngineers, teams: renderTeams, 'team-detail': renderTeamDetail, 'engineer-detail': renderEngineerDetail, 'task-detail': renderTaskDetail, risks: renderRisks, development: renderDevelopment, recalc: renderRecalc, 'engineer-sprint': renderEngineerSprint, 'engineer-quarter': renderEngineerQuarter, 'engineer-tasks': renderEngineerTasks})[canonicalPage(page)]?.();
}

function renderUnavailable() {
  const message = state.error ? `Backend недоступен: ${state.error}` : 'Загружаем данные planner…';
  if ($('#tasksListState')) $('#tasksListState').innerHTML = empty(message);
  if ($('#tasksTableWrap')) $('#tasksTableWrap').hidden = true;
  if ($('#engineersListState')) $('#engineersListState').innerHTML = empty(message);
  if ($('#engineersTableWrap')) $('#engineersTableWrap').hidden = true;
  if ($('#teamsListState')) $('#teamsListState').innerHTML = empty(message);
  if ($('#teamsTableWrap')) $('#teamsTableWrap').hidden = true;
  if ($('#taskDetailState')) $('#taskDetailState').innerHTML = empty(message);
  if ($('#taskDetailContent')) $('#taskDetailContent').hidden = true;
  if ($('#teamDetailState')) $('#teamDetailState').innerHTML = empty(message);
  if ($('#teamDetailContent')) $('#teamDetailContent').hidden = true;
  if ($('#engineerDetailState')) $('#engineerDetailState').innerHTML = empty(message);
  if ($('#engineerDetailContent')) $('#engineerDetailContent').hidden = true;
  if ($('#developmentState')) $('#developmentState').innerHTML = empty(message);
  if ($('#developmentContent')) $('#developmentContent').hidden = true;
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
  if (state.page !== 'team-detail') state.teamReturnPage = state.page;
  state.teamId = teamId;
  state.teamDetailTab = 'overview';
  show('team-detail');
  loadTeamDetail(teamId);
}

function gotoEmployee(engineerId) {
  if (!engineerBy(engineerId)) {
    showToast(`Инженер ${engineerId || ''} не найден в текущем каталоге.`);
    return;
  }
  if (state.page !== 'engineer-detail') state.engineerReturnPage = state.page;
  state.engineerId = engineerId;
  state.engineerDetailTab = 'overview';
  show('engineer-detail');
  loadEngineerDetail(engineerId);
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
  const quarter = data.kpis?.quarter_kpi;
  if (!target || !expected || !quarter) {
    if (target) target.innerHTML = empty('KPI пока недоступны.');
    return;
  }
  const quarterStatus = quarter.status === 'ON_TARGET' ? 'В норме' : quarter.status === 'BELOW_TARGET' ? 'Ниже нормы' : quarter.status === 'ABOVE_TARGET' ? 'Выше нормы' : 'Факта пока нет';
  target.innerHTML = `<article class="management-kpi-card pi-kpi-card ${attr(quarter.status || 'UNAVAILABLE')}">
    <div class="management-kpi-head"><span>PI Predictability</span><small>Норма 80–100%</small></div>
    <div class="pi-kpi-comparison">
      <section class="pi-kpi-side forecast"><span>Прогноз</span><strong>${expected.completion_percent == null ? '—' : percent(expected.completion_percent)}</strong><p>${number(expected.completed_initiatives)} из ${number(expected.baseline_initiatives)} инициатив по текущему плану</p></section>
      <section class="pi-kpi-side actual"><span>На текущий момент</span><strong>${quarter.completion_percent == null ? '—' : percent(quarter.completion_percent)}</strong><p>${quarter.has_actual ? `${number(quarter.completed_initiatives || 0)} из ${number(quarter.baseline_initiatives)} · ${escapeHtml(quarterStatus)}` : 'Появится после завершения первого спринта'}</p></section>
    </div>
  </article>`;
}

function updateKpiDetailsToggle() {
  const details = $('#kpiDetails');
  const button = $('#toggleKpiDetails');
  if (!details || !button) return;
  details.hidden = !state.kpiDetailsExpanded;
  button.setAttribute('aria-expanded', String(state.kpiDetailsExpanded));
  button.innerHTML = state.kpiDetailsExpanded
    ? 'Скрыть подробности <span aria-hidden="true">⌃</span>'
    : 'Подробнее <span aria-hidden="true">⌄</span>';
}

function toggleKpiDetails() {
  state.kpiDetailsExpanded = !state.kpiDetailsExpanded;
  updateKpiDetailsToggle();
}

function renderPlanRiskSummary() {
  const target = $('#planRiskSummary');
  if (!target) return;
  const counts = data.risks.reduce((result, risk) => {
    if (Object.hasOwn(result, risk.severity)) result[risk.severity] += 1;
    return result;
  }, {RED: 0, ORANGE: 0, YELLOW: 0});
  const total = counts.RED + counts.ORANGE + counts.YELLOW;
  const clear = total === 0;
  target.classList.toggle('clear', clear);
  target.innerHTML = `<div class="plan-risk-summary-copy"><span class="plan-risk-summary-icon" aria-hidden="true">${clear ? '✓' : '!'}</span><div><h2>Внимание, риски по плану</h2><p>${clear ? 'Активных алертов сейчас нет' : `${number(total)} активных алертов требуют контроля`}</p></div></div><div class="plan-risk-counts" aria-label="Количество алертов по уровню"><span class="red"><b>${number(counts.RED)}</b> красных</span><span class="orange"><b>${number(counts.ORANGE)}</b> оранжевых</span><span class="yellow"><b>${number(counts.YELLOW)}</b> жёлтых</span></div><button class="plan-risk-link" type="button" data-page="risks">Открыть риски →</button>`;
}

function renderPlanning() {
  const uninitialized = data.dataState === 'UNINITIALIZED';
  const emptyState = $('#planUninitialized');
  const planSections = [
    $('#planRiskSummary'),
    $('#page-current-plan .planning-board-panel'),
    $('#page-current-plan .kpi-panel'),
    $('#kpiDetails'),
  ].filter(Boolean);
  if (emptyState) emptyState.hidden = !uninitialized;
  planSections.forEach((element) => { element.hidden = uninitialized; });
  if (uninitialized) return;
  $('#sprintBoard').innerHTML = Array.from({length: 6}, (_, index) => {
    const sprint = index + 1;
    const sprintData = sprintBy(sprint) || {tasks: [], presentation_state: sprint === currentSprint() ? 'CURRENT' : 'FUTURE'};
    const closed = sprintData.presentation_state === 'CLOSED';
    const sprintKpi = (data.kpis?.sprint_kpis || []).find((item) => Number(item.sprint) === sprint && item.has_actual);
    const sprintTasks = closed
      ? (sprintData.tasks || []).map((row) => ({task: row, row}))
      : data.tasks.filter((task) => inQuarter(task) && taskSprints(task).includes(sprint)).map((task) => ({task, row: task}));
    const tasks = sprintTasks
      .sort((left, right) => Number(right.task.rung || 0) - Number(left.task.rung || 0) || String(left.task.task_id).localeCompare(String(right.task.task_id), 'ru'))
      .map(({task, row}) => {
        const sprintSp = Number(row.sprint_capacity_sp_load ?? task.sp_load_by_sprint?.[String(sprint)] ?? 0);
        const sprintHours = Number(row.sprint_allocated_hours ?? task.hours_by_sprint?.[String(sprint)] ?? 0);
        const load = `<small>Всего: ${number(task.story_points)} SP</small><small>Sprint ${sprint}: ${number(sprintSp)} SP</small><small>${number(sprintHours)} ч</small>`;
        if (closed) {
          const outcome = row.outcome || {status: 'UNFINISHED', label: 'Не завершена'};
          return `<button class="sprint-card historical ${attr(String(outcome.status || '').toLowerCase())}" type="button" data-plan-task="${attr(task.task_id)}"><span class="sprint-card-top"><b class="card-id">${escapeHtml(task.task_id)}</b>${rungBadge(task)}</span><span class="card-title">${escapeHtml(task.title)}</span><span class="status ${outcome.completed ? 'done' : 'work'}">${escapeHtml(outcome.label)}</span>${load}<span class="card-team">${escapeHtml(task.assigned_team_id)}</span></button>`;
        }
        return taskSprints(task)[0] < sprint
          ? `<button class="sprint-continuation" type="button" data-plan-task="${attr(task.task_id)}"><span class="sprint-card-top"><b>↳ ${escapeHtml(task.task_id)}</b>${rungBadge(task)}</span><span>Продолжение</span>${load}<small>${escapeHtml(task.assigned_team_id)}</small></button>`
          : `<button class="sprint-card" type="button" data-plan-task="${attr(task.task_id)}"><span class="sprint-card-top"><b class="card-id">${escapeHtml(task.task_id)}</b>${rungBadge(task)}</span><span class="card-title">${escapeHtml(task.title)}</span>${load}<span class="card-team">${escapeHtml(task.assigned_team_id)}</span></button>`;
      })
      .join('') || '<div class="sprint-empty">Нет задач</div>';
    const kpiBadge = sprintKpi ? `<span class="sprint-column-kpi ${attr(sprintKpi.status || 'UNAVAILABLE')}" title="Sprint Say/Do Ratio">KPI ${sprintKpi.completion_percent == null ? '—' : percent(sprintKpi.completion_percent)}</span>` : '';
    return `<section class="sprint-column ${sprint === currentSprint() ? 'selected' : ''}" data-sprint-column="${sprint}"><div class="sprint-column-header"><div class="sprint-column-title"><b>Sprint ${sprint}</b>${kpiBadge}</div><span>${escapeHtml(sprintStatus(sprint))}</span></div><div class="sprint-task-shell"><div class="sprint-task-scroll" tabindex="0" aria-label="Задачи Sprint ${sprint}">${tasks}</div><span class="sprint-scroll-thumb" aria-hidden="true"></span></div></section>`;
  }).join('');
  activateSprintScrollbars();
  renderPlanRiskSummary();
  renderKpis();
  renderInitiatives();
  renderExcludedTasks();
  updateKpiDetailsToggle();
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

function catalogSort(items, key, direction, valueFor, numericKeys = new Set()) {
  return [...items].sort((left, right) => {
    const leftValue = valueFor(left, key);
    const rightValue = valueFor(right, key);
    const leftMissing = leftValue == null || leftValue === '';
    const rightMissing = rightValue == null || rightValue === '';
    if (leftMissing || rightMissing) return leftMissing === rightMissing ? 0 : (leftMissing ? 1 : -1);
    const comparison = numericKeys.has(key)
      ? Number(leftValue) - Number(rightValue)
      : String(leftValue).localeCompare(String(rightValue), 'ru', {numeric: true, sensitivity: 'base'});
    return direction === 'asc' ? comparison : -comparison;
  });
}

function updateCatalogSortButtons(selector, activeKey, direction) {
  $$(selector).forEach((button) => {
    const key = button.dataset.engineerSort || button.dataset.teamSort;
    const active = key === activeKey;
    button.classList.toggle('active', active);
    button.parentElement.setAttribute('aria-sort', active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none');
    $('span', button).textContent = active ? (direction === 'asc' ? '↑' : '↓') : '↕';
  });
}

function renderEngineers() {
  const sprint = currentSprint();
  const body = $('#engineersTableBody');
  const wrap = $('#engineersTableWrap');
  $('#engineersListCount').textContent = `${data.engineers.length}`;
  $('#engineersListState').innerHTML = '';
  wrap.hidden = false;
  const currentAllocations = (engineer) => (engineer.allocations || []).filter((item) => Number(item.sprint) === sprint);
  const valueFor = (engineer, key) => ({
    roles: (engineer.roles || []).join(', '),
    team_ids: (engineer.team_ids || []).join(', '),
    used_hours: Number(engineer.used_hours_by_sprint?.[String(sprint)] || 0),
    current_task: currentAllocations(engineer).map((item) => item.task_id).sort().join(', '),
    skills: (engineer.skills || []).join(', '),
  }[key] ?? engineer[key]);
  const sorted = catalogSort(
    data.engineers,
    state.engineerSortKey,
    state.engineerSortDirection,
    valueFor,
    new Set(['capacity_hours', 'used_hours']),
  );
  updateCatalogSortButtons('[data-engineer-sort]', state.engineerSortKey, state.engineerSortDirection);
  if (!sorted.length) {
    body.innerHTML = '<tr><td colspan="8"><div class="empty-state">Инженеров пока нет.</div></td></tr>';
    return;
  }
  body.innerHTML = sorted.map((engineer) => {
    const used = Number(engineer.used_hours_by_sprint?.[String(sprint)] || 0);
    const activeTasks = [...new Set(currentAllocations(engineer).map((item) => item.task_id))];
    const skillPreview = (engineer.skills || []).slice(0, 3).join(', ');
    const remainingSkills = (engineer.skills || []).slice(3);
    const moreSkills = remainingSkills.length;
    const taskLinks = activeTasks.length
      ? activeTasks.map((taskId) => {
        const title = taskBy(taskId)?.title;
        return `<button class="table-button current-task-link" type="button" data-open-task="${attr(taskId)}">${escapeHtml(title ? `${taskId} · ${title}` : taskId)} ↗</button>`;
      }).join('')
      : '<span class="engineer-idle">Нет активной задачи</span>';
    const skillMore = moreSkills ? ` <button type="button" class="catalog-skill-more" tabindex="0" aria-label="Остальные навыки: ${attr(remainingSkills.join(', '))}" data-tooltip="${attr(remainingSkills.join(', '))}">+${moreSkills}</button>` : '';
    return `<tr data-open-employee="${attr(engineer.engineer_id)}" data-engineer-row tabindex="0"><td><button class="table-button" type="button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.name || engineer.engineer_id)} ↗</button></td><td>${escapeHtml((engineer.roles || []).join(', ') || '—')}</td><td>${escapeHtml(engineer.grade || '—')}</td><td>${escapeHtml((engineer.team_ids || []).join(', ') || 'Без команды')}</td><td>${number(engineer.capacity_hours)} ч</td><td>${number(used)} ч</td><td><div class="engineer-current-tasks">${taskLinks}</div></td><td><span class="catalog-skills">${escapeHtml(skillPreview || '—')}${skillMore}</span></td></tr>`;
  }).join('');
}

function renderTeams() {
  const sprint = currentSprint();
  const body = $('#teamsTableBody');
  const wrap = $('#teamsTableWrap');
  $('#teamsListCount').textContent = `${data.teams.length}`;
  $('#teamsListState').innerHTML = '';
  wrap.hidden = false;
  const resource = (team) => team.sp_by_sprint?.[String(sprint)] || {used: 0, remaining: 0};
  const valueFor = (team, key) => ({
    members: (team.member_ids || []).length,
    roles: (team.roles || []).join(', '),
    used_sp: Number(resource(team).used || 0),
    remaining_sp: Number(resource(team).remaining || 0),
    bus_factor: Number(team.bus_factor?.critical_count || 0),
  }[key] ?? team[key]);
  const sorted = catalogSort(
    data.teams,
    state.teamSortKey,
    state.teamSortDirection,
    valueFor,
    new Set(['members', 'average_velocity_sp', 'capacity_sp', 'used_sp', 'remaining_sp', 'bus_factor']),
  );
  updateCatalogSortButtons('[data-team-sort]', state.teamSortKey, state.teamSortDirection);
  if (!sorted.length) {
    body.innerHTML = '<tr><td colspan="8"><div class="empty-state">Команд пока нет.</div></td></tr>';
    return;
  }
  body.innerHTML = sorted.map((team) => {
    const sprintResource = resource(team);
    const critical = Number(team.bus_factor?.critical_count || 0);
    const riskLabel = team.bus_factor_status === 'AVAILABLE' ? `${critical} крит.` : 'Нет данных';
    const roleBadges = (team.roles || []).map((role) => `<span class="team-role-badge">${escapeHtml(role)}</span>`).join('') || '<span class="muted">Нет данных</span>';
    return `<tr data-open-team="${attr(team.team_id)}" data-team-row tabindex="0"><td><button class="table-button" type="button" data-open-team="${attr(team.team_id)}">${escapeHtml(team.name || team.team_id)} ↗</button></td><td>${number((team.member_ids || []).length)}</td><td><div class="team-role-badges">${roleBadges}</div></td><td>${number(team.average_velocity_sp)} SP</td><td>${number(team.capacity_sp)} SP</td><td>${number(sprintResource.used)} SP</td><td>${number(sprintResource.remaining)} SP</td><td><span class="bf-catalog-status ${critical ? 'critical' : 'healthy'}">${escapeHtml(riskLabel)}</span></td></tr>`;
  }).join('');
}

async function loadDevelopmentRecommendations(force = false) {
  if (state.developmentLoading || (data.development && !force)) return;
  state.developmentLoading = true;
  state.developmentError = null;
  if (state.page === 'development') renderDevelopment();
  try {
    data.development = await window.postcodeApi.getDevelopmentRecommendations();
  } catch (error) {
    state.developmentError = error?.message || 'Не удалось рассчитать рекомендации.';
  } finally {
    state.developmentLoading = false;
    if (state.page === 'development') renderDevelopment();
  }
}

function developmentSelectOptions(values, selected, allLabel) {
  return `<option value="all">${escapeHtml(allLabel)}</option>${(values || []).map((value) => `<option value="${attr(value)}" ${value === selected ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}`;
}

function developmentDeficitTitle(item) {
  if (item.deficit?.type === 'SKILL') return item.deficit?.skill || 'Навык';
  const role = item.deficit?.role;
  const skills = item.deficit?.skills || [];
  if (role && skills.length) return `${role} · ${skills.join(', ')}`;
  return role || skills.join(', ') || 'Дефицит команды';
}

function developmentActionLabel(action) {
  if (action === 'UPSKILL_OR_HIRE') return 'ДООБУЧИТЬ ИЛИ НАНЯТЬ';
  return 'РЕКОМЕНДАЦИЯ';
}

function developmentTaskCountLabel(value) {
  const count = Number(value || 0);
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return `${number(count)} ЗАДАЧ`;
  if (count % 10 === 1) return `${number(count)} ЗАДАЧА`;
  if (count % 10 >= 2 && count % 10 <= 4) return `${number(count)} ЗАДАЧИ`;
  return `${number(count)} ЗАДАЧ`;
}

function developmentCandidate(candidate, primary = false) {
  return `<article class="development-candidate ${primary ? 'primary' : ''} ${candidate.quarter_unassigned ? 'unassigned' : ''}"><div><div class="development-candidate-name"><button type="button" class="table-button" data-open-employee="${attr(candidate.engineer_id)}">${escapeHtml(candidate.name || candidate.engineer_id)} ↗</button>${candidate.quarter_unassigned ? '<em>Не задействован в квартале</em>' : ''}</div><span>${escapeHtml((candidate.roles || []).join(', ') || 'Роль не указана')} · ${escapeHtml(candidate.grade || 'Grade не указан')}</span></div><b>${number(candidate.score_percent)}%</b><ul>${(candidate.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul></article>`;
}

function developmentRecommendationCard(item) {
  const action = item.recommendation?.primary_action;
  const candidates = item.recommendation?.candidates || [];
  const deficit = item.deficit || {};
  const taskLinks = (item.tasks || []).map((task) => `<button type="button" class="table-button" data-open-task="${attr(task.task_id)}">${escapeHtml(task.task_id)}${task.title && task.title !== task.task_id ? ` · ${escapeHtml(task.title)}` : ''}</button>`).join('');
  const planCritical = item.category === 'PLAN_CRITICAL';
  const busFactor = deficit.bus_factor == null ? '' : `<div><span>Bus Factor</span><b>BF ${number(deficit.bus_factor)}</b></div>`;
  const hire = `<div class="development-hire-profile"><span>Альтернатива: профиль для найма</span><b>${escapeHtml(item.recommendation?.hire_profile?.label || 'Дополнительный специалист')}</b><p>Количество специалистов и влияние на план не прогнозируются — план пересчитывается после фактического изменения состава.</p></div>`;
  const primary = candidates.length
    ? `<div class="development-primary"><span>Приоритетный кандидат на дообучение</span>${developmentCandidate(candidates[0], true)}</div>`
    : '<div class="empty-state">Подходящих кандидатов на дообучение в команде не найдено.</div>';
  const alternatives = candidates.length > 1 ? `<details class="development-alternatives"><summary>Остальные кандидаты (${number(candidates.length - 1)})</summary>${candidates.slice(1).map((candidate) => developmentCandidate(candidate)).join('')}</details>` : '';
  const title = developmentDeficitTitle(item);
  const scopeLabel = item.scope === 'company' ? 'Вся компания' : item.team_id;
  return `<details class="panel development-card ${String(item.severity || '').toLowerCase()} ${planCritical ? 'plan-critical' : ''}"><summary class="development-card-summary" aria-label="Открыть рекомендацию: ${attr(title)}"><div class="development-card-summary-copy"><span>${escapeHtml(scopeLabel)}</span><h2>${escapeHtml(title)}</h2></div><div class="development-card-summary-status"><span class="development-action upskill_or_hire">${developmentActionLabel(action)}</span><i aria-hidden="true">⌄</i></div></summary><div class="development-card-body"><div class="eyebrow">${deficit.type === 'ROLE' ? 'РОЛЬ' : 'НАВЫК'} · ${developmentTaskCountLabel(item.priority?.covered_task_count || (item.tasks || []).length)}</div><div class="development-facts">${busFactor}</div><div class="development-task-demand"><span>${planCritical ? 'Незапланированные задачи с этим требованием' : 'Задачи квартала с этим требованием'}</span><div>${taskLinks || '<span class="muted">Связанные задачи не определены</span>'}</div></div>${primary}${alternatives}${hire}</div></details>`;
}

function developmentCategorySection(title, description, items, critical = false) {
  if (!items.length) return '';
  return `<section class="development-category ${critical ? 'critical' : ''}"><div class="development-category-heading"><div><div class="eyebrow">${critical ? 'ГЛАВНЫЙ ПРИОРИТЕТ' : 'ВТОРОЙ ПРИОРИТЕТ'}</div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div><span>${number(items.length)}</span></div>${items.map(developmentRecommendationCard).join('')}</section>`;
}

function renderDevelopment() {
  const stateBox = $('#developmentState');
  const content = $('#developmentContent');
  if (!data.development && !state.developmentLoading && !state.developmentError) {
    loadDevelopmentRecommendations();
  }
  if (state.developmentLoading) {
    stateBox.innerHTML = empty('Собираем требования и кандидатов…');
    content.hidden = true;
    return;
  }
  if (state.developmentError) {
    stateBox.innerHTML = empty(state.developmentError);
    content.hidden = true;
    return;
  }
  if (!data.development) {
    stateBox.innerHTML = empty('Рекомендации ещё не рассчитаны.');
    content.hidden = true;
    return;
  }
  if (data.development.status === 'NO_APPROVED_PLAN') {
    stateBox.innerHTML = `<div class="empty-state">${escapeHtml(data.development.message || 'Сначала утвердите квартальный план.')}</div>`;
    content.hidden = true;
    return;
  }
  stateBox.innerHTML = '';
  content.hidden = false;
  const filters = data.development.filters || {};
  if (state.developmentTeam !== 'all' && !(filters.teams || []).includes(state.developmentTeam)) state.developmentTeam = 'all';
  if (state.developmentRole !== 'all' && !(filters.roles || []).includes(state.developmentRole)) state.developmentRole = 'all';
  if (state.developmentSkill !== 'all' && !(filters.skills || []).includes(state.developmentSkill)) state.developmentSkill = 'all';
  $('#developmentTeamFilter').innerHTML = developmentSelectOptions(filters.teams, state.developmentTeam, 'Все команды');
  $('#developmentRoleFilter').innerHTML = developmentSelectOptions(filters.roles, state.developmentRole, 'Все роли');
  $('#developmentSkillFilter').innerHTML = developmentSelectOptions(filters.skills, state.developmentSkill, 'Все навыки');
  const recommendations = (data.development.recommendations || []).filter((item) => {
    if (item.category === 'TEAM_WEAKNESS') {
      if (state.developmentTeam === 'all' && item.scope !== 'company') return false;
      if (state.developmentTeam !== 'all' && (item.scope === 'company' || item.team_id !== state.developmentTeam)) return false;
    } else if (state.developmentTeam !== 'all' && item.team_id !== state.developmentTeam) return false;
    if (state.developmentRole !== 'all' && item.deficit?.role !== state.developmentRole) return false;
    if (state.developmentSkill !== 'all' && !(item.deficit?.skills || []).includes(state.developmentSkill)) return false;
    return true;
  });
  const planCritical = recommendations.filter((item) => item.category === 'PLAN_CRITICAL');
  const weaknesses = recommendations.filter((item) => item.category === 'TEAM_WEAKNESS');
  const summary = {
    plan_critical: planCritical.length,
    team_weakness: weaknesses.length,
    total: recommendations.length,
    roles: recommendations.filter((item) => item.deficit?.type === 'ROLE').length,
    skills: recommendations.filter((item) => item.deficit?.type === 'SKILL').length,
  };
  $('#developmentSummary').innerHTML = [['Для незапланированных задач', summary.plan_critical], ['Слабые места BF = 1', summary.team_weakness], ['Всего требований', summary.total], ['Роли', summary.roles], ['Навыки', summary.skills]].map(([label, value], index) => `<div class="${index === 0 && Number(value) ? 'critical' : ''}"><span>${escapeHtml(label)}</span><b>${number(value)}</b></div>`).join('');
  $('#developmentList').innerHTML = recommendations.length ? [
    developmentCategorySection('Критично по плану', 'Только роли и навыки, отсутствие которых фактически заблокировало добавление задач в план.', planCritical, true),
    developmentCategorySection('Слабые места команд', state.developmentTeam === 'all' ? 'Все роли и навыки с Bus Factor = 1 по компании.' : 'Все роли и навыки с Bus Factor = 1 в выбранной команде.', weaknesses),
  ].join('') : empty('Для выбранных фильтров рекомендаций нет.');
}

function developmentTeamBlock(teamId) {
  const recommendations = (data.development?.recommendations || []).filter((item) => item.team_id === teamId);
  const rows = recommendations.slice(0, 3).map((item) => `<li><b>${escapeHtml(developmentDeficitTitle(item))}</b><span>${developmentTaskCountLabel(item.tasks?.length).toLowerCase()}</span></li>`).join('');
  return `<article class="panel entity-detail-panel development-profile-block"><div class="panel-heading"><div><h2>Дефициты команды</h2><p class="hint">Единый read-only расчёт по утверждённому плану.</p></div><button class="btn btn-outline" type="button" data-open-development-team="${attr(teamId)}">Открыть рекомендации ↗</button></div>${data.development ? (rows ? `<ul>${rows}</ul>` : '<p class="muted">Активных рекомендаций для команды нет.</p>') : '<p class="muted">Расчёт выполняется при открытии раздела «Развитие».</p>'}</article>`;
}

function developmentEngineerBlock(engineerId) {
  if (!data.development) return '';
  const matches = (data.development.recommendations || []).flatMap((item) => (item.recommendation?.candidates || []).filter((candidate) => candidate.engineer_id === engineerId).map((candidate) => ({item, candidate})));
  if (!matches.length) return '';
  return `<article class="panel entity-detail-panel development-profile-block"><div class="panel-heading"><div><h2>Рекомендации по развитию</h2><p class="hint">Квартальный расчёт всех подходящих направлений.</p></div><button class="btn btn-outline" type="button" data-page="development">Открыть раздел ↗</button></div><ul>${matches.slice(0, 3).map(({item, candidate}) => `<li><b>${escapeHtml((item.deficit?.skills || []).join(', ') || item.deficit?.role)}</b><span>${candidate.quarter_unassigned ? 'Приоритет: нет задач на квартал · ' : ''}подходит на ${number(candidate.score_percent)}%</span></li>`).join('')}</ul></article>`;
}

async function loadTeamDetail(teamId) {
  if (data.teamDetails[teamId] && data.teamHistories[teamId]) return renderTeamDetail();
  try {
    const [detail, history] = await Promise.all([
      window.postcodeApi.getTeam(teamId),
      window.postcodeApi.getTeamHistory(teamId),
    ]);
    data.teamDetails[teamId] = detail;
    data.teamHistories[teamId] = history;
    if (state.page === 'team-detail' && state.teamId === teamId) renderTeamDetail();
  } catch (error) {
    data.teamDetails[teamId] = {load_error: error?.message || 'Не удалось загрузить страницу команды.'};
    if (state.page === 'team-detail' && state.teamId === teamId) renderTeamDetail();
  }
}

function teamTaskCard(task) {
  const owner = task.participation_type === 'OWNER';
  const roles = (task.roles || []).join(', ');
  const remaining = Object.entries(task.remaining_role_hours || {}).map(([role, hours]) => `${escapeHtml(role)}: ${number(hours)} ч`).join(', ');
  const carried = owner && Number(task.planned_sprints?.[0]) < Number(task.sprint) ? `<span class="tag orange">Перенесена из Sprint ${number(task.planned_sprints[0])}</span>` : '';
  return `<article class="entity-work-card ${owner ? '' : 'cross-team'}"><div><button class="table-button" type="button" data-open-task="${attr(task.task_id)}">${escapeHtml(task.task_id)} ↗</button><h3>${escapeHtml(task.title || 'Название не указано')}</h3><p>${owner ? `Владелец: ${escapeHtml(task.assigned_team_id || '—')}` : `Помощь для ${escapeHtml(task.owner_team_id || task.assigned_team_id || '—')}`}</p>${carried}</div><div class="entity-work-meta">${owner ? `<span>Исходно: ${number(task.original_story_points)} SP</span><span>Осталось: ${number(task.remaining_story_points)} SP</span><span>${remaining ? `Остаток: ${remaining}` : 'Остаток по ролям отсутствует'}</span>` : `<span>${escapeHtml(roles || 'Роль не указана')}</span><span>Планово: ${number(task.planned_hours)} ч</span>`}</div></article>`;
}

function renderTeamOverview(detail) {
  const roleRows = detail.capacity?.roles || [];
  const current = detail.current_tasks || [];
  const received = detail.cross_team?.received || [];
  return `<div class="entity-detail-grid"><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Sprint ${number(detail.current_sprint)}</h2><p class="hint">SP команды и плановые часы специалистов показаны раздельно.</p></div></div><div class="entity-capacity-list"><div class="entity-capacity-row primary"><span>Story Points</span><b>${number(detail.capacity?.sp?.used)} / ${number(detail.capacity?.sp?.capacity)} SP</b></div>${roleRows.map((row) => `<div class="entity-capacity-row"><span>${escapeHtml(row.role)}</span><b>${number(row.used_hours)} / ${number(row.capacity_hours)} ч</b></div>`).join('') || '<p class="hint">Нет данных о ролевой ёмкости.</p>'}</div></article><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Текущая работа</h2><p class="hint">Owner-задачи и помощь другим командам.</p></div><span class="tag">${current.length}</span></div><div class="entity-work-list">${current.map(teamTaskCard).join('') || empty('В текущем спринте нет работы команды.')}</div></article></div><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Cross-team участие</h2><p class="hint">Плановая помощь в текущем спринте.</p></div></div><div class="entity-detail-grid"><div><h3>Команда предоставляет</h3>${(detail.cross_team?.provided || []).map(teamTaskCard).join('') || empty('Команда не предоставляет помощь.')}</div><div><h3>Команда получает</h3>${received.map((task) => `<article class="entity-work-card cross-team"><button class="table-button" type="button" data-open-task="${attr(task.task_id)}">${escapeHtml(task.task_id)} ↗</button><p>Из команд: ${escapeHtml((task.source_team_ids || []).join(', '))}</p><b>Планово: ${number(task.planned_hours)} ч</b></article>`).join('') || empty('Внешняя помощь не требуется.')}</div></div></article>${developmentTeamBlock(detail.identity?.team_id || detail.team_id)}`;
}

function renderTeamTasks(detail) {
  const current = detail.current_tasks || [];
  const future = detail.future_sprints || [];
  return `<article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Текущий спринт</h2><p class="hint">Утверждённые owner-задачи и cross-team участие.</p></div></div><div class="entity-work-list">${current.map(teamTaskCard).join('') || empty('Текущих задач нет.')}</div></article><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Следующие спринты</h2><p class="hint">Только последний утверждённый план.</p></div></div><div class="future-sprint-list">${future.map((item) => `<details class="future-sprint"><summary><span>Sprint ${item.sprint}</span><b>${item.task_count} задач · ${number(item.capacity_sp_load)} SP</b></summary><div class="entity-work-list">${item.tasks.map((task) => `<article class="entity-work-card"><button class="table-button" type="button" data-open-task="${attr(task.task_id)}">${escapeHtml(task.task_id)} ↗</button><h3>${escapeHtml(task.title)}</h3><p>${number(task.sprint_sp)} SP в спринте</p></article>`).join('') || empty('Задач нет.')}</div></details>`).join('')}</div></article>`;
}

function renderTeamMembers(detail) {
  const rows = (detail.members || []).map((member) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(member.engineer_id)}">${escapeHtml(member.name || member.engineer_id)} ↗</button></td><td>${escapeHtml((member.roles || []).join(', ') || '—')}</td><td>${escapeHtml(member.grade || '—')}</td><td>${percent(Number(member.capacity_rate || 0) * 100)}</td><td>${number(member.capacity_hours)} ч</td><td>${number(member.planned_hours)} ч</td><td>${number(member.cross_team_planned_hours)} ч</td></tr>`);
  return `<article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Состав команды</h2><p class="hint">Каждый bucket участия показан отдельно и не переносится между командами.</p></div></div><div class="table-scroll">${rows.length ? table(['Инженер', 'Роли', 'Grade', 'Участие', 'Ёмкость участия', 'План Sprint', 'Межкомандная помощь'], rows) : empty('В команде нет активных инженеров.')}</div></article>`;
}

function teamOutcomeLabel(outcome) {
  if (!outcome) return 'Продолжается по плану';
  return outcome.completed ? 'Завершена' : 'Не завершена';
}

function renderTeamHistory(detail) {
  const history = data.teamHistories[detail.identity.team_id];
  const sprints = history?.closed_sprints || [];
  return `<div class="history-timeline">${sprints.map((item) => `<article class="panel history-sprint"><div class="panel-heading"><div><div class="eyebrow">ЗАКРЫТЫЙ СПРИНТ</div><h2>Sprint ${item.sprint}</h2><p class="hint">Approved revision ${item.planned_revision ?? '—'} → ${item.resulting_revision ?? '—'}</p></div></div><div class="history-columns"><div><h3>Owner-задачи</h3>${item.owner_tasks.map((task) => `<div class="history-row"><button class="table-button" type="button" data-open-task="${attr(task.task_id)}">${escapeHtml(task.task_id)} ↗</button><span>${escapeHtml(teamOutcomeLabel(task.outcome))}</span>${task.outcome?.status === 'UNFINISHED' ? `<small>${escapeHtml(task.outcome.incomplete_reason || 'Причина не указана')} · остаток: ${Object.entries(task.outcome.remaining_role_hours || {}).map(([role, hours]) => `${escapeHtml(role)} ${number(hours)} ч`).join(', ')}</small>` : ''}</div>`).join('') || empty('Owner-задач не было.')}</div><div><h3>Cross-team</h3><p>Предоставлено планово: <b>${number(item.cross_team_provided.reduce((sum, row) => sum + Number(row.planned_hours || 0), 0))} ч</b></p><p>Получено планово: <b>${number(item.cross_team_received.reduce((sum, row) => sum + Number(row.planned_hours || 0), 0))} ч</b></p></div></div>${item.ownership_changes.length ? `<div class="history-changes"><h3>Изменение владельца</h3>${item.ownership_changes.map((change) => `<p><button class="table-button" type="button" data-open-task="${attr(change.task_id)}">${escapeHtml(change.task_id)} ↗</button> · ${escapeHtml(change.from_team_id)} → ${escapeHtml(change.to_team_id)}</p>`).join('')}</div>` : ''}${item.replanning_changes.length ? `<div class="history-changes"><h3>После перепланирования</h3>${item.replanning_changes.map((change) => `<p>${escapeHtml(change.task_id)} · ${escapeHtml((change.fields || [change.change]).join(', '))}</p>`).join('')}</div>` : ''}</article>`).join('') || empty('Закрытых спринтов пока нет.')}</div>`;
}

function renderTeamSkills(detail) {
  const competencies = detail.competencies || [];
  return `<article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Компетенции и Bus Factor</h2><p class="hint">Расчёт совпадает со Звёздной картой и использует уникальных активных инженеров.</p></div><button class="btn btn-outline" type="button" data-open-risk-hub="teams">Открыть Звёздную карту ↗</button></div><div class="competency-card-grid">${competencies.map((item) => `<article class="competency-summary ${attr(item.risk_level)}"><div><h3>${escapeHtml(item.skill)}</h3><p>${escapeHtml(item.risk_label)}</p></div><b>BF ${number(item.bus_factor)}</b></article>`).join('') || empty('Нет данных о компетенциях.')}</div></article>`;
}

function renderTeamDetail() {
  const detail = data.teamDetails[state.teamId];
  const stateBox = $('#teamDetailState');
  const content = $('#teamDetailContent');
  if (!detail) {
    $('#teamDetailTitle').textContent = teamBy(state.teamId).name || state.teamId || 'Команда';
    stateBox.innerHTML = empty('Загружаем профиль команды…');
    content.hidden = true;
    return;
  }
  if (detail.load_error) {
    stateBox.innerHTML = empty(detail.load_error);
    content.hidden = true;
    return;
  }
  content.hidden = false;
  stateBox.innerHTML = '';
  $('#teamDetailTitle').textContent = detail.identity?.name || detail.team_id;
  $('#teamDetailMeta').textContent = `${detail.approved_revision ? `Approved Plan · Revision ${detail.approved_revision}` : 'Рабочий каталог'} · Текущий спринт: Sprint ${detail.current_sprint}`;
  $('#teamDetailSummary').innerHTML = `<div><span>Инженеров</span><b>${number((detail.members || []).length)}</b></div><div><span>Текущих owner-задач</span><b>${number((detail.current_tasks || []).filter((item) => item.participation_type === 'OWNER').length)}</b></div><div><span>Cross-team участий</span><b>${number((detail.cross_team?.provided || []).length + (detail.cross_team?.received || []).length)}</b></div>`;
  $$('[data-team-detail-tab]').forEach((button) => button.classList.toggle('active', button.dataset.teamDetailTab === state.teamDetailTab));
  $('#teamDetailBody').innerHTML = ({overview: renderTeamOverview, tasks: renderTeamTasks, members: renderTeamMembers, history: renderTeamHistory, skills: renderTeamSkills}[state.teamDetailTab] || renderTeamOverview)(detail);
}

async function loadEngineerDetail(engineerId) {
  if (data.engineerDetails[engineerId] && data.engineerHistories[engineerId]) return renderEngineerDetail();
  try {
    const [detail, history] = await Promise.all([
      window.postcodeApi.getEngineer(engineerId),
      window.postcodeApi.getEngineerHistory(engineerId),
    ]);
    data.engineerDetails[engineerId] = detail;
    data.engineerHistories[engineerId] = history;
    if (state.page === 'engineer-detail' && state.engineerId === engineerId) renderEngineerDetail();
  } catch (error) {
    data.engineerDetails[engineerId] = {load_error: error?.message || 'Не удалось загрузить страницу инженера.'};
    if (state.page === 'engineer-detail' && state.engineerId === engineerId) renderEngineerDetail();
  }
}

function engineerAllocationCard(item) {
  const crossTeam = item.participation_type === 'CROSS_TEAM';
  return `<article class="entity-work-card ${crossTeam ? 'cross-team' : ''}"><div><button class="table-button" type="button" data-open-task="${attr(item.task_id)}">${escapeHtml(item.task_id)} ↗</button><h3>${escapeHtml(item.task_title || 'Название не указано')}</h3><p>${escapeHtml(item.role || 'Роль не указана')} · Sprint ${number(item.sprint)}</p></div><div class="entity-work-meta"><span>Плановое участие</span><b>${number(item.allocated_hours)} ч</b><span>${crossTeam ? `Помощь ${escapeHtml(item.owner_team_id || 'другой команде')}` : escapeHtml(item.source_team_id || item.owner_team_id || 'Без команды')}</span></div></article>`;
}

function renderEngineerOverview(detail) {
  const memberships = detail.memberships || [];
  const current = detail.current_allocations || [];
  return `<div class="entity-detail-grid"><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Участие в командах</h2><p class="hint">Capacity bucket каждого membership показан отдельно.</p></div></div><div class="entity-capacity-list">${memberships.map((membership) => {
    const bucket = (detail.capacity_buckets || []).find((item) => item.team_id === membership.team_id) || membership;
    return `<button class="profile-link" type="button" data-open-team="${attr(membership.team_id)}"><span>${escapeHtml(teamBy(membership.team_id).name || membership.team_id)}</span> · ${percent(Number(membership.capacity_rate || 0) * 100)} · ${number(membership.planned_hours)} / ${number(bucket.capacity_hours)} ч планово ↗</button>`;
  }).join('') || empty('Инженер не состоит в активной команде.')}</div></article><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Sprint ${number(detail.current_sprint)}</h2><p class="hint">Только плановые назначения из утверждённого плана.</p></div></div><div class="entity-capacity-list"><div class="entity-capacity-row primary"><span>Плановое участие</span><b>${number(current.reduce((sum, item) => sum + Number(item.allocated_hours || 0), 0))} ч</b></div><div class="entity-capacity-row"><span>Назначений</span><b>${number(current.length)}</b></div><div class="entity-capacity-row"><span>Cross-team назначений</span><b>${number(current.filter((item) => item.participation_type === 'CROSS_TEAM').length)}</b></div></div></article></div><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Текущая работа</h2><p class="hint">Часы обозначают плановое участие, а не фактические трудозатраты.</p></div></div><div class="entity-work-list">${current.map(engineerAllocationCard).join('') || empty('В текущем спринте назначений нет.')}</div></article>${developmentEngineerBlock(detail.identity?.engineer_id || detail.engineer_id)}`;
}

function renderEngineerProfileTasks(detail) {
  const current = detail.current_allocations || [];
  const futureBySprint = new Map();
  (detail.future_allocations || []).forEach((item) => {
    const sprint = Number(item.sprint);
    if (!futureBySprint.has(sprint)) futureBySprint.set(sprint, []);
    futureBySprint.get(sprint).push(item);
  });
  return `<article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Текущий спринт</h2><p class="hint">Плановые назначения из последней утверждённой ревизии.</p></div></div><div class="entity-work-list">${current.map(engineerAllocationCard).join('') || empty('Текущих назначений нет.')}</div></article><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Следующие спринты</h2><p class="hint">Будущие назначения могут измениться после закрытия спринта и перепланирования.</p></div></div><div class="future-sprint-list">${[...futureBySprint.entries()].sort(([left], [right]) => left - right).map(([sprint, items]) => `<details class="future-sprint"><summary><span>Sprint ${sprint}</span><b>${items.length} назначений · ${number(items.reduce((sum, item) => sum + Number(item.allocated_hours || 0), 0))} ч</b></summary><div class="entity-work-list">${items.map(engineerAllocationCard).join('')}</div></details>`).join('') || empty('Будущих назначений нет.')}</div></article>`;
}

function renderEngineerHistory(detail) {
  const history = data.engineerHistories[detail.identity.engineer_id];
  const sprints = history?.closed_sprints || [];
  return `<div class="history-timeline">${sprints.map((item) => `<article class="panel history-sprint"><div class="panel-heading"><div><div class="eyebrow">ЗАКРЫТЫЙ СПРИНТ</div><h2>Sprint ${item.sprint}</h2><p class="hint">Показано утверждённое плановое участие. Фактические часы не рассчитываются из плана.</p></div></div><div class="entity-work-list">${item.participations.map((row) => {
    const remaining = Object.entries(row.outcome?.remaining_role_hours || {}).map(([role, hours]) => `${escapeHtml(role)}: ${number(hours)} ч`).join(', ');
    return `<article class="entity-work-card ${row.participation_type === 'CROSS_TEAM' ? 'cross-team' : ''}"><div><button class="table-button" type="button" data-open-task="${attr(row.task_id)}">${escapeHtml(row.task_id)} ↗</button><h3>${escapeHtml(row.task_title || 'Название не указано')}</h3><p>${escapeHtml(row.role || 'Роль не указана')} · ${escapeHtml(row.source_team_id || 'Без команды')}${row.participation_type === 'CROSS_TEAM' ? ` → ${escapeHtml(row.owner_team_id)}` : ''}</p></div><div class="entity-work-meta"><span>Плановое участие</span><b>${number(row.planned_hours)} ч</b><span>Результат задачи: ${escapeHtml(teamOutcomeLabel(row.outcome))}</span>${row.outcome?.status === 'UNFINISHED' ? `<small>${escapeHtml(row.outcome.incomplete_reason || 'Причина не указана')}</small>${remaining ? `<small>По задаче осталось: ${remaining}</small>` : ''}` : ''}</div></article>`;
  }).join('') || empty('В этом спринте плановых назначений не было.')}</div>${item.allocation_changes.length ? `<div class="history-changes"><h3>Изменения будущих назначений после перепланирования</h3>${item.allocation_changes.map((change) => `<p><button class="table-button" type="button" data-open-task="${attr(change.task_id)}">${escapeHtml(change.task_id)} ↗</button> · план обновлён</p>`).join('')}</div>` : ''}</article>`).join('') || empty('Закрытых спринтов пока нет.')}</div>`;
}

function renderEngineerSkills(detail) {
  const criticality = detail.team_criticality || [];
  return `<div class="entity-detail-grid"><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Роли и навыки</h2><p class="hint">Показаны значения из профиля инженера без добавления уровней владения.</p></div></div><h3>Роли</h3><div class="skill-tags">${(detail.roles || []).map((role) => `<span>${escapeHtml(role)}</span>`).join('') || '<span>Нет данных</span>'}</div><h3 class="needs-title">Навыки</h3><div class="skill-tags">${(detail.skills || []).map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></article><article class="panel entity-detail-panel"><div class="panel-heading"><div><h2>Критичность для команд</h2><p class="hint">Тот же Bus Factor, который используется в Звёздной карте.</p></div></div><div class="competency-card-grid">${criticality.map((item) => `<button class="competency-summary ${attr(item.risk_level)}" type="button" data-open-team="${attr(item.team_id)}"><div><h3>${escapeHtml(item.team_name || item.team_id)} · ${escapeHtml(item.skill)}</h3><p>${escapeHtml(item.risk_label)}</p></div><b>BF ${number(item.bus_factor)}</b></button>`).join('') || empty('Критичные компетенции для команд не выявлены.')}</div></article></div>`;
}

function renderEngineerDetail() {
  const detail = data.engineerDetails[state.engineerId];
  const stateBox = $('#engineerDetailState');
  const content = $('#engineerDetailContent');
  if (!detail) {
    $('#engineerDetailTitle').textContent = engineerBy(state.engineerId)?.name || state.engineerId || 'Инженер';
    stateBox.innerHTML = empty('Загружаем профиль инженера…');
    content.hidden = true;
    return;
  }
  if (detail.load_error) {
    stateBox.innerHTML = empty(detail.load_error);
    content.hidden = true;
    return;
  }
  content.hidden = false;
  stateBox.innerHTML = '';
  $('#engineerDetailTitle').textContent = detail.identity?.name || detail.engineer_id;
  $('#engineerDetailMeta').textContent = `${detail.engineer_id} · ${(detail.roles || []).join(', ') || 'Роль не указана'} · ${(detail.memberships || []).map((item) => `${item.team_id} ${Number(item.capacity_rate || 0).toLocaleString('ru-RU')}`).join(' · ') || 'Без команды'} · Sprint ${detail.current_sprint}`;
  const planned = (detail.current_allocations || []).reduce((sum, item) => sum + Number(item.allocated_hours || 0), 0);
  $('#engineerDetailSummary').innerHTML = `<div><span>Команд</span><b>${number((detail.memberships || []).length)}</b></div><div><span>Capacity за спринт</span><b>${number(detail.capacity_hours)} ч</b></div><div><span>Плановое участие в Sprint ${number(detail.current_sprint)}</span><b>${number(planned)} ч</b></div>`;
  $$('[data-engineer-detail-tab]').forEach((button) => button.classList.toggle('active', button.dataset.engineerDetailTab === state.engineerDetailTab));
  $('#engineerDetailBody').innerHTML = ({overview: renderEngineerOverview, tasks: renderEngineerProfileTasks, history: renderEngineerHistory, skills: renderEngineerSkills}[state.engineerDetailTab] || renderEngineerOverview)(detail);
}

function taskRoleRequirements(task) {
  const source = task?.role_requirements || {};
  if (Array.isArray(source)) {
    return source
      .map((item) => ({role: item.role, required_hours: Number(item.required_hours || 0)}))
      .filter((item) => item.role && item.required_hours > 0);
  }
  return Object.entries(source)
    .map(([role, requiredHours]) => ({role, required_hours: Number(requiredHours || 0)}))
    .filter((item) => item.role && item.required_hours > 0);
}

function renderTaskExecutionAlert(task) {
  const alert = $('#taskExecutionAlert');
  const statuses = [task.status, task.current_status].map((value) => String(value || '').toUpperCase());
  if (!statuses.some((status) => ['MOVED', 'UNPLANNABLE'].includes(status))) {
    alert.hidden = true;
    alert.innerHTML = '';
    return;
  }
  const blocks = task.reason_blocks || [];
  const requirementFallback = taskRoleRequirements(task).map((item) => `${item.role}: требуется ${number(item.required_hours)} ч`);
  const rows = blocks.length
    ? blocks.map((block) => `<li><b>${escapeHtml(block.title)}</b>${(block.details || []).map((detail) => `<span>${escapeHtml(detail)}</span>`).join('')}</li>`).join('')
    : `<li><b>Не выделены необходимые ресурсы</b>${requirementFallback.length ? requirementFallback.map((detail) => `<span>${escapeHtml(detail)}</span>`).join('') : '<span>Уточните команду, роли и доступную ёмкость для размещения задачи.</span>'}</li>`;
  alert.innerHTML = `<div class="task-execution-alert-icon" aria-hidden="true">!</div><div><div class="eyebrow">ТРЕБУЕТ ВНИМАНИЯ</div><h2>Что мешает выполнить задачу</h2><p>Чтобы включить задачу в квартальный план, устраните следующие ограничения:</p><ul>${rows}</ul></div>`;
  alert.hidden = false;
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
    $('#taskExecutionAlert').hidden = true;
    $('#taskExecutionAlert').innerHTML = '';
    stateBox.innerHTML = empty(state.taskId ? `Задача ${state.taskId} отсутствует в текущем наборе.` : 'Задача не выбрана.');
    content.hidden = true;
    return;
  }
  content.hidden = false;
  stateBox.innerHTML = '';
  $('#taskDetailTitle').textContent = task.title || 'Название не указано';
  $('#taskHeroId').textContent = task.task_id;
  $('#taskHeroStatus').innerHTML = `<span class="hero-status-dot ${taskStatusClass(task)}" aria-hidden="true"></span>${escapeHtml(taskDisplayStatus(task))}`;
  renderTaskExecutionAlert(task);
  $('#taskTopActions').innerHTML = state.workspaceMode === 'engineer' ? '' : (task.archived ? archiveState(task) : `<button class="btn btn-danger" type="button" data-archive-entity="task" data-archive-id="${attr(task.task_id)}" data-current-plan-reference="${inQuarter(task)}">Удалить</button>`);
  const sprints = taskSprints(task);
  const selectedSprint = state.taskSprint && sprints.includes(state.taskSprint) ? state.taskSprint : (sprints[0] || null);
  state.taskSprint = selectedSprint;
  const allocations = (task.allocations || []).filter((item) => selectedSprint == null || item.sprint === selectedSprint);
  const teamValue = task.assigned_team_id
    ? `<button type="button" class="fact-link" data-open-team="${attr(task.assigned_team_id)}">${escapeHtml(task.assigned_team_id)} ↗</button>`
    : '<b>—</b>';
  $('#taskFacts').innerHTML = `<div class="task-fact"><span>Команда</span>${teamValue}</div><div class="task-fact"><span>Спринт</span><b>${escapeHtml(sprintLabel(task))}</b></div><div class="task-fact"><span>Story Points задачи</span><b>${number(task.story_points)} SP</b></div><div class="task-fact"><span>Rung</span>${rungBadge(task)}</div><div class="task-fact"><span>Инициатива</span><b>${escapeHtml(task.initiative_id || '—')}</b></div>${task.team_reassigned ? `<p class="task-team-note">Исходная команда: <b>${escapeHtml(task.original_team_id || '—')}</b></p>` : ''}`;
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
  const requiredSkills = task.required_skills || [];
  const skillSource = (item) => item.source === 'both' ? 'Указано вручную и найдено в названии' : item.source === 'title' ? 'Найдено в названии' : 'Указано вручную';
  const skillRows = requiredSkills.map((item) => `<div class="task-required-skill"><div><b>${escapeHtml(item.technology)}</b><small>${escapeHtml(skillSource(item))}${item.role ? ` · ${escapeHtml(item.role)}` : ' · Для всей задачи'}</small></div>${item.confirmed ? '<span>Подтверждено</span>' : '<span class="pending">Требует подтверждения</span>'}</div>`).join('');
  $('#taskDependencies').innerHTML = `<section class="task-required-skills"><h2>Требуемые компетенции</h2>${skillRows || '<p class="muted">Требуемые компетенции не указаны</p>'}</section><h2>Зависимости</h2><p class="hint">Положение в квартальном плане</p><div class="dependency-grid"><div class="dependency-box"><strong>Блокирующие задачи</strong>${incomingRows.length ? incomingRows.join('') : '<p>Нет</p>'}</div><div class="dependency-box"><strong>Зависимые задачи</strong>${outgoingRows.length ? outgoingRows.join('') : '<p>Нет</p>'}</div></div>`;
  const allocationRows = allocations.map((item) => {
    const crossTeam = Boolean(item.cross_team) || Boolean(item.source_team_id && task.assigned_team_id && item.source_team_id !== task.assigned_team_id);
    return `<tr class="${crossTeam ? 'cross-team-row' : ''}"><td><button class="table-button" type="button" data-open-employee="${attr(item.engineer_id)}">${escapeHtml(item.engineer_id)} ↗</button></td><td>${escapeHtml(item.role || '—')}</td><td>${escapeHtml(item.source_team_id || '—')}${crossTeam ? '<span class="cross-team-badge" title="Сотрудник привлечён из другой команды">⚠ Другая команда</span>' : ''}</td><td>${number(item.allocated_hours)} ч</td></tr>`;
  });
  const localAllocations = allocations.filter((item) => !item.cross_team);
  const externalAllocations = allocations.filter((item) => item.cross_team);
  const ownership = task.ownership || {};
  const performerRows = (rows, external) => [...new Set(rows.map((item) => item.engineer_id))].map((engineerId) => {
    const rowsForEngineer = rows.filter((item) => item.engineer_id === engineerId);
    const hours = rowsForEngineer.reduce((sum, item) => sum + Number(item.allocated_hours || 0), 0);
    const source = external ? ` · ${escapeHtml(rowsForEngineer[0].source_team_id)}` : '';
    return `<button type="button" class="person-link" data-open-employee="${attr(engineerId)}"><b>${escapeHtml(engineerId)}${source}</b><span>${number(hours)} ч ↗</span></button>`;
  }).join('');
  const ownershipSummary = ownership.total_hours > 0
    ? `<div class="ownership-summary"><b>Команда выполняет ${number(ownership.owner_share_percent)}% трудозатрат</b><span>${number(ownership.owner_engineer_count)} инженера команды + ${number(ownership.external_engineer_count)} внешних специалиста</span><small>${escapeHtml(ownership.status_message || '')}</small></div>`
    : '';
  const sprintPicker = task.multi_sprint && selectedSprint ? `<label class="task-sprint-picker">Показать спринт<select id="taskSprintSelect">${sprints.map((sprint) => `<option value="${sprint}" ${sprint === selectedSprint ? 'selected' : ''}>Sprint ${sprint}</option>`).join('')}</select></label>` : '';
  const sprintLoad = selectedSprint == null ? '' : `<p class="hint">Нагрузка Sprint ${selectedSprint}: <b>${number(task.sp_load_by_sprint?.[String(selectedSprint)] || 0)} SP</b>; отдельно назначено <b>${number(task.hours_by_sprint?.[String(selectedSprint)] || 0)} ч</b>. Всего у задачи: <b>${number(task.story_points)} SP</b>.</p>`;
  const roleRequirements = taskRoleRequirements(task);
  const requirementRows = roleRequirements.map((requirement) => {
    const roleAllocations = (task.allocations || []).filter((item) => item.role === requirement.role);
    const assignedHours = roleAllocations.reduce((sum, item) => sum + Number(item.allocated_hours || 0), 0);
    const people = [...new Set(roleAllocations.map((item) => item.engineer_id).filter(Boolean))];
    const skills = requiredSkills.filter((item) => !item.role || item.role === requirement.role).map((item) => item.technology);
    return `<article class="task-role-requirement"><div><span>Требуемая роль</span><b>${escapeHtml(requirement.role)}</b>${skills.length ? `<small>Компетенции: ${escapeHtml([...new Set(skills)].join(', '))}</small>` : ''}</div><div><span>Требуется</span><b>${number(requirement.required_hours)} ч</b></div><div><span>Назначено</span><b class="${assignedHours + 1e-6 < requirement.required_hours ? 'shortage' : 'covered'}">${number(assignedHours)} ч</b><small>${people.length ? `${number(people.length)} чел.` : 'Сотрудники не назначены'}</small></div></article>`;
  }).join('');
  const requiredPeople = `<section class="task-role-requirements"><div><h2>Требуемые сотрудники</h2><p class="hint">Роли и трудозатраты из исходных требований задачи — независимо от того, попала ли она в квартальный план.</p></div>${requirementRows || empty('Требования к ролям и сотрудникам не указаны.')}</section>`;
  $('#taskHours').innerHTML = `${requiredPeople}<div class="task-resource-divider"></div><div class="panel-heading"><div><h2>Распределение ресурсов</h2><p class="hint">Фактические назначения planner-а${selectedSprint ? ` для Sprint ${selectedSprint}` : ''}.</p>${sprintLoad}</div>${sprintPicker}</div>${ownershipSummary}<h3 class="needs-title">Основная команда — ${escapeHtml(task.assigned_team_id || '—')}</h3>${localAllocations.length ? performerRows(localAllocations, false) : empty('У основной команды нет назначенных часов.')}<h3 class="needs-title">Помощь других команд</h3>${externalAllocations.length ? performerRows(externalAllocations, true) : empty('Внешняя помощь не требуется.')}${allocationRows.length ? table(['Инженер', 'Роль', 'Исходная команда', 'Выделено'], allocationRows) : empty('Распределения ресурсов отсутствуют.')}`;
  renderEngineerParticipation(task);
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

function renderRiskList() {
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
  const workflow = data.replanWorkflow;
  if (!hasPendingReplan()) {
    show('current-sprint', 'replace');
    return;
  }
  $('#recalcTitle').textContent = `Изменения после Sprint ${number(workflow.sprint_number)}`;
  $('#recalcSubtitle').textContent = 'Проверьте только изменения оставшегося плана и подтвердите новую редакцию.';
  $('#recalcResult').hidden = false;
  renderReplanWorkflow(workflow);
}

const REPLAN_CHANGE_LABELS = Object.freeze({
  CARRY_OVER: 'Перенос незавершённой задачи',
  TASK_COMPLETED: 'Задача завершена',
  ADDED_TO_REMAINING_QUARTER: 'Добавлена в оставшийся план',
  REMOVED_FROM_REMAINING_QUARTER: 'Не помещается в оставшийся квартал',
  TASK_REMOVED: 'Убрана из плана',
  SPRINT_CHANGED: 'Изменён спринт',
  COMPLETION_CHANGED: 'Изменён срок завершения',
  TEAM_CHANGED: 'Изменена команда-владелец',
  ENGINEERS_CHANGED: 'Изменены назначения инженеров',
  HOURS_CHANGED: 'Изменена нагрузка в часах',
  SP_LOAD_CHANGED: 'Изменена SP-нагрузка',
  CROSS_TEAM_CHANGED: 'Изменена межкомандная помощь',
  ALLOCATION_CHANGED: 'Изменено распределение часов',
  STATUS_CHANGED: 'Изменён статус',
});

function replanSprintValue(view) {
  return (view?.planned_sprints || []).map((item) => `Sprint ${number(item)}`).join(', ') || 'Не запланирована';
}

function replanSpValue(view) {
  return Object.values(view?.sp_load_by_sprint || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function replanAllocationChanges(changes) {
  if (!changes?.length) return '';
  const rows = changes.map((row) => {
    const engineer = engineerBy(row.engineer_id)?.name || row.engineer_id || 'Инженер не указан';
    const context = `Sprint ${number(row.sprint)} · ${escapeHtml(row.role || 'роль не указана')}${row.cross_team ? ` · cross-team ${escapeHtml(row.source_team_id)} → ${escapeHtml(row.owner_team_id)}` : ''}`;
    if (row.kind === 'ADDED') return `<li class="added"><b>+</b><span>${escapeHtml(engineer)}<small>${context}</small></span><strong>${number(row.after_hours)} ч</strong></li>`;
    if (row.kind === 'REMOVED') return `<li class="removed"><b>−</b><span>${escapeHtml(engineer)}<small>${context}</small></span><strong>${number(row.before_hours)} ч</strong></li>`;
    return `<li><b>↻</b><span>${escapeHtml(engineer)}<small>${context}</small></span><strong>${number(row.before_hours)} → ${number(row.after_hours)} ч</strong></li>`;
  }).join('');
  return `<div class="replan-detail"><span>Назначения</span><ul class="replan-allocation-delta">${rows}</ul></div>`;
}

function replanChangeCard(change) {
  const fields = new Set(change.fields || []);
  const before = change.before;
  const after = change.after;
  const carry = change.carry_over;
  const removed = fields.has('REMOVED_FROM_REMAINING_QUARTER') || fields.has('TASK_REMOVED') || carry?.outcome === 'EXCLUDED';
  const added = fields.has('ADDED_TO_REMAINING_QUARTER') && !carry;
  const details = [];
  if (carry) {
    const destination = carry.outcome === 'SCHEDULED' ? replanSprintValue(after) : 'Не запланирована в оставшемся квартале';
    details.push(`<div class="replan-detail"><span>Перенос</span><b>Sprint ${number(carry.closed_sprint)} → ${escapeHtml(destination)}</b></div>`);
    const roles = Object.entries(carry.remaining_role_hours || {}).map(([role, hours]) => `${escapeHtml(role)} · ${number(hours)} ч`).join(', ');
    details.push(`<div class="replan-detail"><span>Остаток</span><b>${roles || 'Не указан'}${carry.remaining_story_points == null ? '' : ` · ${number(carry.remaining_story_points)} SP`}</b></div>`);
    if (carry.incomplete_reason) details.push(`<div class="replan-detail"><span>Факт Sprint ${number(carry.closed_sprint)}</span><b>${escapeHtml(carry.incomplete_reason)}</b></div>`);
  } else if (added) {
    details.push(`<div class="replan-detail"><span>Планирование</span><b>Добавлена · ${escapeHtml(replanSprintValue(after))}${after?.rung == null ? '' : ` · R${number(after.rung)}`}</b></div>`);
  } else if (removed) {
    details.push(`<div class="replan-detail"><span>Планирование</span><b>${escapeHtml(replanSprintValue(before))} → Не запланирована в оставшемся квартале</b></div>`);
  } else if (fields.has('SPRINT_CHANGED') || fields.has('COMPLETION_CHANGED')) {
    details.push(`<div class="replan-detail"><span>Sprint</span><b>${escapeHtml(replanSprintValue(before))} → ${escapeHtml(replanSprintValue(after))}</b></div>`);
  }
  if (fields.has('TEAM_CHANGED')) details.push(`<div class="replan-detail"><span>Команда</span><b>${escapeHtml(before?.team_id || 'Не указана')} → ${escapeHtml(after?.team_id || 'Не указана')}</b></div>`);
  details.push(replanAllocationChanges(change.allocation_changes));
  if (fields.has('HOURS_CHANGED')) details.push(`<div class="replan-detail"><span>Всего часов</span><b>${number(before?.total_hours)} → ${number(after?.total_hours)} ч</b></div>`);
  if (fields.has('SP_LOAD_CHANGED')) details.push(`<div class="replan-detail"><span>SP-нагрузка</span><b>${number(replanSpValue(before))} → ${number(replanSpValue(after))} SP</b></div>`);
  if (fields.has('CROSS_TEAM_CHANGED')) details.push('<div class="replan-detail"><span>Cross-team</span><b>Изменён состав межкомандной помощи</b></div>');
  if (fields.has('STATUS_CHANGED') && !added && !removed && !carry) details.push(`<div class="replan-detail"><span>Статус планирования</span><b>${escapeHtml(before?.status || '—')} → ${escapeHtml(after?.status || '—')}</b></div>`);
  const tags = [...fields].map((field) => `<span>${escapeHtml(REPLAN_CHANGE_LABELS[field] || field)}</span>`).join('');
  return `<article class="replan-change-card ${removed ? 'critical' : ''}"><div class="replan-change-card-heading"><div><b>${escapeHtml(change.task_id)}</b><h3>${escapeHtml(change.title || 'Название не указано')}</h3></div><div class="replan-change-tags">${tags}</div></div><div class="replan-change-details">${details.filter(Boolean).join('')}</div>${change.reason ? `<p class="replan-change-reason">${escapeHtml(change.reason)}</p>` : ''}</article>`;
}

function renderReplanWorkflow(workflow) {
  const target = $('#recalcResult');
  if (workflow.status === 'REPLANNING_FAILED') {
    target.innerHTML = `<article class="panel replan-review"><div class="replan-review-heading"><div><div class="eyebrow">ФАКТ СОХРАНЁН</div><h2>Пересчёт не завершён</h2><p>Результаты Sprint ${number(workflow.sprint_number)} сохранены. Утверждённый план не изменён.</p></div><span class="status moved">Нужен повтор</span></div><div class="replan-error">${escapeHtml(workflow.error || 'Неизвестная ошибка пересчёта.')}</div><button class="btn btn-blue" type="button" data-retry-replan="${attr(workflow.workflow_id)}">Повторить пересчёт</button></article>`;
    return;
  }
  if (workflow.status !== 'READY_FOR_REVIEW') {
    target.innerHTML = `<article class="panel"><h2>Фактические результаты сохранены</h2><p>Статус пересчёта: ${escapeHtml(workflow.status)}.</p></article>`;
    return;
  }
  const diff = workflow.diff || {};
  const changes = diff.affected_elements || diff.changed_tasks || [];
  const isQuarterClose = workflow.kind === 'QUARTER_CLOSE_REVIEW';
  const content = changes.length
    ? `<div class="replan-change-list">${changes.map(replanChangeCard).join('')}</div>`
    : '<div class="replan-no-changes"><b>Изменений в плане не требуется</b><p>Фактический результат сохранён, а оставшийся план бизнес-эквивалентен утверждённому.</p></div>';
  target.innerHTML = `<article class="panel replan-review"><div class="replan-review-heading"><div><div class="eyebrow">${isQuarterClose ? 'ИТОГИ КВАРТАЛА' : 'ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ'}</div><h2>${changes.length ? 'Внимание, следующие элементы будут затронуты:' : 'Изменения не требуются'}</h2><p>Факт Sprint ${number(workflow.sprint_number)} уже сохранён. До подтверждения действует предыдущий утверждённый план.</p></div></div>${content}<div class="replan-actions"><button class="btn btn-outline" type="button" data-page="current-sprint">Вернуться к текущему спринту</button><button class="btn btn-blue" type="button" data-approve-replan="${attr(workflow.workflow_id)}">${isQuarterClose ? 'Зафиксировать итоги квартала' : changes.length ? 'Утвердить изменения' : 'Подтвердить без изменений'}</button></div></article>`;
}

async function retryReplan(workflowId) {
  try {
    data.replanWorkflow = await window.postcodeApi.retryReplanDraft(workflowId);
    renderRecalc();
    showToast(data.replanWorkflow.status === 'READY_FOR_REVIEW' ? 'Черновик пересчёта готов к проверке.' : 'Повторный пересчёт не завершён.');
  } catch (error) {
    showToast(`Не удалось повторить пересчёт: ${error?.message || error}`);
  }
}

async function approveReplan(workflowId) {
  try {
    await window.postcodeApi.approveReplanDraft(workflowId);
    const dashboard = await window.postcodeApi.getDashboard();
    hydrate(dashboard);
    data.replanWorkflow = {workflow_id: workflowId, status: 'APPROVED'};
    show('current-sprint');
    showToast('Новый план утверждён. Открыт текущий спринт.');
  } catch (error) {
    showToast(`Не удалось утвердить план: ${error?.message || error}`);
  }
}

function renderCurrentSprint() {
  const sprint = currentSprint();
  const pendingReview = data.replanWorkflow
    && ['READY_FOR_REVIEW', 'REPLANNING_FAILED', 'ACTUAL_SAVED'].includes(data.replanWorkflow.status)
    && Number(data.replanWorkflow.sprint_number) === sprint;
  const quarterDone = data.plan?.quarter?.current_sprint === 6
    && data.plan?.sprints?.every((item) => item.status === 'COMPLETED');
  const sprintRows = sprintBy(sprint)?.tasks || [];
  const sprintTasks = sprintRows.map((row) => taskBy(row.task_id)).filter(Boolean);
  const allocations = sprintTasks.flatMap((task) => (task.allocations || [])
    .filter((row) => Number(row.sprint) === sprint)
    .map((row) => ({...row, task_id: task.task_id, task_title: task.title, owner_team_id: task.assigned_team_id})));
  const teamIds = new Set([
    ...sprintTasks.map((task) => task.assigned_team_id),
    ...allocations.flatMap((row) => [row.owner_team_id, row.source_team_id]),
  ].filter(Boolean));
  const totalSp = sprintRows.reduce((sum, row) => sum + Number(row.capacity_sp_load || 0), 0);
  const totalHours = allocations.reduce((sum, row) => sum + Number(row.allocated_hours || 0), 0);
  $('#openSprintCloseDialog').disabled = quarterDone || pendingReview;
  $('#currentSprintIntro').textContent = pendingReview
    ? `Sprint ${sprint} закрыт · ожидается проверка нового плана`
    : quarterDone
    ? 'Sprint 6 завершён. Квартальный workflow закрыт.'
    : `Sprint ${sprint} · ${sprintTasks.length} задач · ${teamIds.size} команд`;
  if (pendingReview) {
    $('#currentSprintNotice').innerHTML = '<aside class="development-notice pending-review-notice" role="status"><span class="development-icon">↻</span><div><b>Требуется подтверждение изменений плана</b><p>Факт спринта сохранён. Проверьте черновик пересчёта и подтвердите новую редакцию.</p></div><button class="btn btn-blue" type="button" data-page="recalc">Посмотреть изменения</button></aside>';
  } else if (quarterDone) {
    $('#currentSprintNotice').innerHTML = '<aside class="development-notice" role="status"><span class="development-icon">✓</span><div><b>Квартал завершён</b><p>Спринт 6 зафиксирован. Новый спринт не создаётся.</p></div></aside>';
  } else $('#currentSprintNotice').innerHTML = '';

  $('#currentSprintSummary').innerHTML = [
    ['Спринт', sprint], ['Задач', sprintTasks.length], ['Команд', teamIds.size], ['Нагрузка', `${number(totalSp)} SP`], ['Назначено', `${number(totalHours)} ч`],
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join('');
  $('#currentSprintTaskCount').textContent = `${sprintTasks.length}`;
  $('#currentSprintTasks').innerHTML = sprintTasks.length ? sprintTasks.map((task) => {
    const expanded = state.currentSprintExpandedTaskId === task.task_id;
    const detailId = `current-sprint-task-${String(task.task_id).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
    const taskAllocations = (task.allocations || []).filter((row) => Number(row.sprint) === sprint);
    const assignmentsByEngineer = new Map();
    taskAllocations.forEach((row) => {
      const profile = engineerBy(row.engineer_id);
      const assignment = assignmentsByEngineer.get(row.engineer_id) || {
        engineer_id: row.engineer_id,
        name: profile?.name || row.engineer_id,
        roles: new Set(),
        team_ids: new Set(profile?.team_ids || []),
        task_hours: 0,
        sprint_hours: Number(profile?.used_hours_by_sprint?.[String(sprint)] || 0),
        capacity_hours: Number(profile?.capacity_hours || 0),
      };
      if (row.role) assignment.roles.add(row.role);
      if (row.source_team_id) assignment.team_ids.add(row.source_team_id);
      assignment.task_hours += Number(row.allocated_hours || 0);
      assignmentsByEngineer.set(row.engineer_id, assignment);
    });
    const engineerRows = [...assignmentsByEngineer.values()].map((assignment) => {
      const sprintHours = Math.max(assignment.sprint_hours, assignment.task_hours);
      const teams = [...assignment.team_ids];
      const teamLabels = teams.length
        ? teams.map((teamId) => `<button class="current-sprint-engineer-team" type="button" data-open-team="${attr(teamId)}">${escapeHtml(teamBy(teamId).name || teamId)}</button>`).join('')
        : '<span class="current-sprint-engineer-team unknown">Команда не указана</span>';
      return `<article class="current-sprint-engineer"><div class="current-sprint-engineer-person"><button class="table-button" type="button" data-open-employee="${attr(assignment.engineer_id)}">${escapeHtml(assignment.name)}</button><div class="current-sprint-engineer-teams">${teamLabels}</div><small>${escapeHtml([...assignment.roles].join(', ') || 'Роль не указана')}</small></div><div class="current-sprint-engineer-load"><span>На задачу <b>${number(assignment.task_hours)} ч</b></span><span>Всего в спринте <b>${number(sprintHours)}${assignment.capacity_hours > 0 ? ` / ${number(assignment.capacity_hours)}` : ''} ч</b></span>${assignment.capacity_hours > 0 ? meter(sprintHours, assignment.capacity_hours) : ''}</div></article>`;
    }).join('');
    const details = expanded
      ? `<section class="current-sprint-task-details" id="${attr(detailId)}"><div class="current-sprint-task-details-heading"><div><h4>Задействованные инженеры</h4><p>${assignmentsByEngineer.size ? `${assignmentsByEngineer.size} чел. · ${number(taskAllocations.reduce((sum, row) => sum + Number(row.allocated_hours || 0), 0))} ч на задачу` : 'Назначений на этот спринт нет'}</p></div></div><div class="current-sprint-engineer-list">${engineerRows || empty('На задачу пока не назначены инженеры.')}</div></section>`
      : '';
    return `<article class="current-sprint-task ${expanded ? 'expanded' : ''}"><div class="current-sprint-task-summary"><div class="current-sprint-task-primary"><span class="current-sprint-task-id">${escapeHtml(task.task_id)}</span><h3>${escapeHtml(task.title)}</h3></div><span class="current-sprint-task-sp">${number(task.story_points)} SP</span><span class="current-sprint-task-rung">${rungBadge(task)}</span><span class="current-sprint-task-team">${task.assigned_team_id ? `<button class="table-button" type="button" data-open-team="${attr(task.assigned_team_id)}">${escapeHtml(task.assigned_team_id)}</button>` : 'Команда не указана'}</span><button type="button" class="btn btn-outline current-sprint-task-more" data-toggle-current-sprint-task="${attr(task.task_id)}" aria-expanded="${expanded}" aria-controls="${attr(detailId)}">${expanded ? 'Скрыть' : 'Подробнее'}<span aria-hidden="true">⌄</span></button></div>${details}</article>`;
  }).join('') : empty(quarterDone ? 'Квартал завершён.' : 'В текущем спринте нет задач.');

  const involvedTeamIds = [...teamIds];
  $('#currentSprintTeamLoad').innerHTML = involvedTeamIds.length ? involvedTeamIds.map((teamId) => {
    const team = teamBy(teamId);
    const sp = teamSprintResource(teamId, sprint);
    const usedHours = Number(team.source_used_hours_by_sprint?.[String(sprint)] || 0);
    const capacityHours = Number(team.member_capacity_hours || 0);
    const roleHours = {};
    allocations.filter((row) => row.source_team_id === teamId).forEach((row) => { roleHours[row.role] = Number(roleHours[row.role] || 0) + Number(row.allocated_hours || 0); });
    return `<section class="current-sprint-team-card"><h3><button class="table-button" type="button" data-open-team="${attr(teamId)}">${escapeHtml(team.name || teamId)} ↗</button></h3><div class="team-load-line"><span>SP команды</span><b>${number(sp.used)} / ${number(sp.capacity)} SP</b></div>${meter(sp.used, sp.capacity)}<div class="team-load-line"><span>Часы сотрудников</span><b>${number(usedHours)} / ${number(capacityHours)} ч</b></div>${meter(usedHours, capacityHours)}<div class="team-role-load">${Object.entries(roleHours).map(([role, hours]) => `<span><b>${escapeHtml(role)}</b>${number(hours)} ч запланировано</span>`).join('') || '<span>Назначений по ролям нет</span>'}</div></section>`;
  }).join('') : empty('В текущем спринте нет задействованных команд.');

  const crossTeam = allocations.filter((row) => row.cross_team);
  $('#currentSprintCrossTeam').innerHTML = crossTeam.length ? crossTeam.map((row) => `<article class="current-sprint-help"><button type="button" class="table-button" data-open-employee="${attr(row.engineer_id)}">${escapeHtml(engineerBy(row.engineer_id)?.name || row.engineer_id)}</button><span>${escapeHtml(row.source_team_id)} → ${escapeHtml(row.owner_team_id)}</span><span>${escapeHtml(row.role)}</span><button type="button" class="table-button" data-open-task="${attr(row.task_id)}">${escapeHtml(row.task_id)}</button><strong>${number(row.allocated_hours)} ч</strong></article>`).join('') : empty('Межкомандная помощь в текущем спринте не используется.');

}

function renderSprintCloseDialog() {
  const sprint = currentSprint();
  const sprintTasks = (sprintBy(sprint)?.tasks || []).map((row) => taskBy(row.task_id)).filter(Boolean);
  $('#sprintCloseTitle').textContent = `Завершение Sprint ${sprint}`;
  $('#sprintCloseSubtitle').textContent = 'Зафиксируйте фактический результат текущего спринта.';
  $('#sprintCloseDueCount').textContent = `Задач в спринте: ${sprintTasks.length}`;
  $('#sprintCloseTasks').innerHTML = sprintTasks.length ? sprintTasks.map((task) => {
    const roles = roleHoursForTask(task, sprint);
    const startingSp = Number(task.remaining_story_points ?? task.story_points ?? 0);
    return `<article class="sprint-close-task" data-close-task="${attr(task.task_id)}" data-starting-sp="${attr(startingSp)}"><label class="sprint-close-check"><input type="checkbox" data-task-deviation disabled><span><b>${escapeHtml(task.task_id)}</b>${escapeHtml(task.title)}</span></label><div class="sprint-close-task-fields" hidden><label class="fact-reason">Причина отклонения<select data-task-reason disabled><option value="">Выберите причину</option><option>Не хватило времени</option><option>Недоступность необходимых ресурсов</option><option>Зависимость не была завершена</option><option>Объём задачи оказался больше запланированного</option><option>Изменились приоритеты</option><option>Задача была приостановлена</option><option>Другая организационная причина</option></select></label><div class="fact-sp-block"><label class="fact-sp"><span>Осталось по задаче</span><span class="fact-role-input"><input type="number" min="0.01" max="${attr(startingSp)}" step="0.1" data-fact-sp placeholder="0" disabled><i>SP</i></span></label><small>Не более ${number(startingSp)} SP. Выполненное будет рассчитано автоматически.</small></div><p>Сколько ещё потребуется времени</p><div class="fact-roles">${Object.entries(roles).map(([role, hours]) => `<label class="fact-role"><span>${escapeHtml(role)}</span><span class="fact-role-input"><input type="number" min="0" step="any" data-fact-role="${attr(role)}" value="${escapeHtml(hours)}" disabled><i>ч</i></span></label>`).join('')}</div></div></article>`;
  }).join('') : empty('В этом спринте нет задач.');
  $('#sprintUnfinishedSection').hidden = true;
  $$('input[name="hasPlanDeviations"]').forEach((input) => { input.checked = false; });
  $('#sprintCloseError').hidden = true;
  $('#confirmCloseCurrentSprint').disabled = true;
}

function syncSprintCloseControls() {
  const deviationsEnabled = $('input[name="hasPlanDeviations"]:checked')?.value === 'yes';
  $('#sprintUnfinishedSection').hidden = !deviationsEnabled;
  $$('#sprintCloseTasks [data-close-task]').forEach((card) => {
    const checkbox = $('[data-task-deviation]', card);
    checkbox.disabled = !deviationsEnabled;
    const taskEnabled = deviationsEnabled && checkbox.checked;
    $('.sprint-close-task-fields', card).hidden = !taskEnabled;
    $('[data-task-reason]', card).disabled = !taskEnabled;
    $('[data-fact-sp]', card).disabled = !taskEnabled;
    $$('[data-fact-role]', card).forEach((input) => { input.disabled = !taskEnabled; });
  });
}

function openSprintCloseDialog() {
  renderSprintCloseDialog();
  $('#sprintCloseDialog').showModal();
}

function closeSprintCloseDialog() {
  $('#sprintCloseDialog').close();
}

function showSprintResult(kpi) {
  if (!kpi) return;
  const status = kpi.status === 'ON_TARGET' ? 'В пределах бизнес-нормы' : kpi.status === 'BELOW_TARGET' ? 'Ниже бизнес-нормы' : kpi.status === 'ABOVE_TARGET' ? 'Выше бизнес-нормы' : 'Недостаточно данных';
  const transferredSp = kpi.transferred_sp ?? kpi.remaining_sp;
  $('#sprintResultTitle').textContent = `Итоги Sprint ${number(kpi.sprint)}`;
  $('#sprintResultContent').innerHTML = `<div class="sprint-result-score ${attr(kpi.status)}"><span>Sprint Say/Do</span><strong>${kpi.completion_percent == null ? '—' : percent(kpi.completion_percent)}</strong><p>${escapeHtml(status)} · норма ${number(kpi.target_min_percent)}–${number(kpi.target_max_percent)}%</p></div><div class="sprint-result-metrics"><div><span>Было запланировано</span><b>${number(kpi.baseline_sp)} SP</b></div><div><span>Реально выполнено</span><b>${number(kpi.actual_sp)} SP</b></div><div class="transferred"><span>Перенесено</span><b>${number(transferredSp)} SP</b></div></div>`;
  $('#sprintResultDialog').showModal();
}

function closeSprintResult() {
  const dialog = $('#sprintResultDialog');
  if (dialog?.open) dialog.close();
}

function roleHoursForTask(task, sprint) {
  const future = {};
  Object.entries(task.role_hours_by_sprint || {}).forEach(([sprintKey, roles]) => {
    if (Number(sprintKey) > sprint) Object.entries(roles || {}).forEach(([role, hours]) => { future[role] = Number(future[role] || 0) + Number(hours || 0); });
  });
  return Object.keys(future).length ? future : {...(task.role_hours_by_sprint?.[String(sprint)] || task.role_requirements || {})};
}

async function closeCurrentSprint(event) {
  event?.preventDefault();
  const sprint = currentSprint();
  const deviationAnswer = $('input[name="hasPlanDeviations"]:checked')?.value;
  if (!deviationAnswer) return;
  const byTask = new Map();
  if (deviationAnswer === 'yes') {
    $$('#sprintCloseTasks [data-close-task]').forEach((card) => {
      if (!$('[data-task-deviation]', card)?.checked) return;
      const remaining = {};
      $$('[data-fact-role]', card).forEach((input) => { const value = Number(input.value); if (value > 0) remaining[input.dataset.factRole] = value; });
      byTask.set(card.dataset.closeTask, {task_id: card.dataset.closeTask, task_completed: false, incomplete_reason: $('[data-task-reason]', card)?.value || null, remaining_story_points: Number($('[data-fact-sp]', card)?.value), starting_story_points: Number(card.dataset.startingSp), remaining_role_hours: remaining, remaining_engineer_allocations: {}, unavailable_next_sprint: false, unavailable_engineer_ids: []});
    });
  }
  const results = [...byTask.values()];
  if (deviationAnswer === 'yes' && results.length === 0) {
    $('#sprintCloseError').textContent = 'Выберите хотя бы одну задачу с отклонением.';
    $('#sprintCloseError').hidden = false;
    return;
  }
  if (results.some((row) => !row.incomplete_reason)) {
    $('#sprintCloseError').textContent = 'Для каждой задачи выберите причину отклонения.';
    $('#sprintCloseError').hidden = false;
    return;
  }
  if (results.some((row) => !(row.remaining_story_points > 0) || row.remaining_story_points > row.starting_story_points)) {
    $('#sprintCloseError').textContent = 'Для каждой задачи укажите остаток SP больше 0 и не больше её текущего объёма.';
    $('#sprintCloseError').hidden = false;
    return;
  }
  if (results.some((row) => !row.task_completed && !Object.values(row.remaining_role_hours).some((hours) => Number(hours) > 0))) {
    $('#sprintCloseError').textContent = 'Для каждой незавершённой задачи укажите остаток часов хотя бы по одной роли.';
    $('#sprintCloseError').hidden = false;
    return;
  }
  const button = $('#confirmCloseCurrentSprint');
  button.disabled = true;
  let result;
  try {
    result = await window.postcodeApi.closeSprint({
      sprint_number: sprint,
      all_completed: deviationAnswer === 'no',
      results,
    });
  } catch (error) {
    $('#sprintCloseError').textContent = `Не удалось закрыть спринт: ${error?.message || error}`;
    $('#sprintCloseError').hidden = false;
    button.disabled = false;
    return;
  }

  closeSprintCloseDialog();
  if (result.replanning_required === false) {
    data.replanWorkflow = null;
    try {
      const dashboard = await window.postcodeApi.getDashboard();
      hydrate(dashboard);
      show('current-sprint');
      showToast(result.quarter_completed
        ? 'Sprint 6 завершён. Квартал закрыт.'
        : `Sprint ${sprint} завершён. Текущий спринт — Sprint ${number(result.current_sprint)}.`);
      showSprintResult(result.sprint_result?.kpi);
    } catch (error) {
      showToast(`Спринт сохранён, но экран не обновился: ${error?.message || error}. Обновите страницу.`);
    }
    return;
  }
  data.replanWorkflow = result;
  show('recalc');
  showToast(result.status === 'READY_FOR_REVIEW' ? 'Факт сохранён. Проверьте черновик нового плана.' : 'Факт сохранён, но пересчёт нужно повторить.');
  showSprintResult(result.sprint_result?.kpi);
}

function renderRisks() {
  updateRiskStatusIndicators();
  $$('[data-risk-hub-tab]', $('#page-risks')).forEach((button) => {
    const active = button.dataset.riskHubTab === state.riskHubTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  $$('.star-view', $('#page-risks')).forEach((element) => element.classList.toggle('active', element.id === ({teams: 'starTeams', risks: 'riskHubRisks', skills: 'starSkills'}[state.riskHubTab])));
  if (state.riskHubTab === 'teams') { closeRiskModal(); renderStarTeams(); }
  if (state.riskHubTab === 'risks') renderRiskList();
  if (state.riskHubTab === 'skills') { closeRiskModal(); renderStarSkills(); }
}

function updateRiskStatusIndicators() {
  const risks = Array.isArray(data.risks) ? data.risks : [];
  const hasRisks = risks.length > 0;
  const title = hasRisks ? `Есть активные риски: ${risks.length}` : 'Активных рисков нет';
  $$('[data-page="risks"]').forEach((element) => {
    element.classList.toggle('risk-nav-has-risks', hasRisks);
    element.classList.toggle('risk-nav-clear', !hasRisks);
    element.dataset.riskCount = String(risks.length);
    element.title = title;
  });
  $$('[data-risk-hub-tab="risks"]').forEach((element) => {
    element.classList.toggle('risk-tab-has-risks', hasRisks);
    element.classList.toggle('risk-tab-clear', !hasRisks);
    element.dataset.riskCount = String(risks.length);
    element.title = title;
  });
}

function busRiskTag(competency) {
  return `<span class="bf-risk-tag ${attr(competency.risk_level)}">${escapeHtml(competency.risk_label)}</span>`;
}

function starTeamMode(team) {
  const key = state.starQuarterOnly ? 'quarter' : 'all';
  const mode = team?.competency_modes?.[key];
  if (mode) return mode;
  const technologies = (team?.competencies || []).map((item) => ({
    technology: item.skill,
    team_bus_factor: item.bus_factor,
    engineer_ids: (item.engineers || []).map((engineer) => engineer.engineer_id),
    helpers: item.external_engineers || [],
  }));
  return {technologies, roles: [], entries: technologies, zone_counts: {total: technologies.length}};
}

function resetStarViewport() {
  starViewport.scale = 1;
  starViewport.x = 0;
  starViewport.y = 0;
}

function clampStarViewport(stage) {
  if (!stage) return;
  const limitX = stage.clientWidth * (0.38 + starViewport.scale * 0.45);
  const limitY = stage.clientHeight * (0.38 + starViewport.scale * 0.45);
  starViewport.x = Math.max(-limitX, Math.min(limitX, starViewport.x));
  starViewport.y = Math.max(-limitY, Math.min(limitY, starViewport.y));
}

function applyStarViewport(stage) {
  if (!stage) return;
  clampStarViewport(stage);
  const canvas = $('.star-graph-canvas', stage);
  if (canvas) canvas.style.transform = `translate3d(${starViewport.x}px, ${starViewport.y}px, 0) scale(${starViewport.scale})`;
}

function embedStarFilters() {
  const panel = $('.star-filter-panel');
  const map = $('.star-network-panel');
  const graph = $('#teamGraph');
  const detail = $('#selectedCompetencyPanel');
  if (panel && map && graph) {
    let workspace = $('.star-map-workspace', map);
    if (!workspace) {
      workspace = document.createElement('div');
      workspace.className = 'star-map-workspace';
      map.insertBefore(workspace, graph);
    }
    if (panel.parentElement !== workspace) workspace.append(panel);
    if (graph.parentElement !== workspace) workspace.append(graph);
    if (detail && detail.parentElement !== workspace) workspace.append(detail);
  }
  const search = $('#starEngineerSearch');
  if (search && !$('#starEngineerSuggestions')) {
    const suggestions = document.createElement('datalist');
    suggestions.id = 'starEngineerSuggestions';
    search.setAttribute('list', suggestions.id);
    search.insertAdjacentElement('afterend', suggestions);
  }
}

function updateStarEngineerSuggestions(engineers) {
  const suggestions = $('#starEngineerSuggestions');
  if (!suggestions) return;
  suggestions.innerHTML = engineers
    .slice()
    .sort((left, right) => String(left.name || left.engineer_id).localeCompare(String(right.name || right.engineer_id)))
    .map((engineer) => `<option value="${attr(engineer.engineer_id)}">${escapeHtml(engineer.name || engineer.engineer_id)} · ${escapeHtml((engineer.roles || []).join(', '))}</option>`)
    .join('');
}

function bindStarMapNavigation() {
  const graph = $('#teamGraph');
  if (!graph) return;
  graph.addEventListener('wheel', (event) => {
    const stage = event.target.closest('.star-graph-stage');
    if (!stage) return;
    event.preventDefault();
    const rect = stage.getBoundingClientRect();
    const pointX = event.clientX - rect.left;
    const pointY = event.clientY - rect.top;
    const previousScale = starViewport.scale;
    const nextScale = Math.max(starViewport.minZoom, Math.min(starViewport.maxZoom, previousScale * Math.exp(-event.deltaY * 0.0012)));
    const worldX = (pointX - starViewport.x) / previousScale;
    const worldY = (pointY - starViewport.y) / previousScale;
    starViewport.scale = nextScale;
    starViewport.x = pointX - worldX * nextScale;
    starViewport.y = pointY - worldY * nextScale;
    applyStarViewport(stage);
  }, {passive: false});
  graph.addEventListener('pointerdown', (event) => {
    const stage = event.target.closest('.star-graph-stage');
    if (!stage || event.button !== 0 || event.target.closest('[data-star-competency], [data-star-center]')) return;
    starViewport.pointerId = event.pointerId;
    starViewport.startX = event.clientX;
    starViewport.startY = event.clientY;
    starViewport.originX = starViewport.x;
    starViewport.originY = starViewport.y;
    starViewport.dragged = false;
    stage.classList.add('panning');
    stage.setPointerCapture(event.pointerId);
  });
  graph.addEventListener('pointermove', (event) => {
    if (starViewport.pointerId !== event.pointerId) return;
    const stage = event.target.closest('.star-graph-stage') || $('.star-graph-stage', graph);
    if (!stage) return;
    const deltaX = event.clientX - starViewport.startX;
    const deltaY = event.clientY - starViewport.startY;
    if (Math.hypot(deltaX, deltaY) >= 5) starViewport.dragged = true;
    starViewport.x = starViewport.originX + deltaX;
    starViewport.y = starViewport.originY + deltaY;
    applyStarViewport(stage);
  });
  const finishPan = (event) => {
    if (starViewport.pointerId !== event.pointerId) return;
    const stage = $('.star-graph-stage', graph);
    if (stage?.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
    stage?.classList.remove('panning');
    starViewport.pointerId = null;
  };
  graph.addEventListener('pointerup', finishPan);
  graph.addEventListener('pointercancel', finishPan);
}

function starGraphItems(team) {
  const mode = starTeamMode(team);
  const source = state.starGraphMode === 'role' ? (mode.roles || []) : (mode.technologies || []);
  const byId = new Map((data.starMap?.engineers || []).map((engineer) => [engineer.engineer_id, engineer]));
  const query = state.starEngineerSearch.trim().toLowerCase();
  const roles = new Set(state.starRoleFilters || []);
  if (!query && !roles.size) return source;
  return source.filter((item) => {
    const ids = state.starQuarterOnly ? (item.quarter_engineer_ids || item.engineer_ids || []) : (item.engineer_ids || []);
    return ids.some((id) => {
      const engineer = byId.get(id);
      if (!engineer) return false;
      if (query && !`${id} ${engineer.name || ''} ${(engineer.roles || []).join(' ')}`.toLowerCase().includes(query)) return false;
      if (roles.size && !(engineer.roles || []).some((role) => roles.has(role))) return false;
      return true;
    });
  });
}

function starSkillText(label, maxLines = 4, maxChars = 15, lineHeight = 16) {
  const words = String(label || '').replace(/\//g, '/ ').split(/\s+/).filter(Boolean);
  const lines = [];
  let current = '';
  words.forEach((word) => {
    const parts = word.length > 13 ? word.match(/.{1,11}/g) || [word] : [word];
    parts.forEach((part) => {
      const candidate = current ? `${current} ${part}` : part;
      if (current && candidate.length > maxChars) { lines.push(current); current = part; } else current = candidate;
    });
  });
  if (current) lines.push(current);
  const visible = lines.slice(0, maxLines);
  if (lines.length > maxLines) visible[maxLines - 1] = `${visible[maxLines - 1]}…`;
  const start = -((visible.length - 1) * lineHeight) / 2 - 3;
  return visible.map((line, index) => `<tspan x="0" y="${start + index * lineHeight}">${escapeHtml(line)}</tspan>`).join('');
}

function starGraph(team, selected) {
  const items = starGraphItems(team);
  if (!items.length) return empty(state.starQuarterOnly ? 'Для текущего квартала компетенции не определены.' : 'Для команды пока нет данных о компетенциях.');
  const nodeRadius = items.length > 36 ? 46 : items.length > 20 ? 52 : items.length > 12 ? 58 : 70;
  const nodeGap = items.length > 36 ? 14 : 18;
  const rings = [];
  let remaining = items.length;
  let ringRadius = 104 + nodeRadius + 52;
  while (remaining > 0) {
    const capacity = Math.max(1, Math.floor((Math.PI * 2 * ringRadius) / (nodeRadius * 2 + nodeGap)));
    const count = Math.min(remaining, capacity);
    rings.push({radius: ringRadius, count});
    remaining -= count;
    ringRadius += nodeRadius * 2 + nodeGap + 22;
  }
  const extent = rings.at(-1).radius + nodeRadius + 30;
  const width = extent * 2; const height = extent * 2; const cx = extent; const cy = extent;
  const center = `<g class="star-team-node" data-star-center transform="translate(${cx} ${cy})"><circle r="104"/><text text-anchor="middle" y="-8">${escapeHtml(team.team_name || team.team_id)}</text><text class="team-count" text-anchor="middle" y="23">${number(team.member_count ?? team.engineer_count)} инженеров</text></g>`;
  let itemIndex = 0;
  const nodes = rings.flatMap((ring, ringIndex) => Array.from({length: ring.count}, (_, ringItemIndex) => {
    const item = items[itemIndex++];
    const angleOffset = ringIndex % 2 ? Math.PI / ring.count : 0;
    const angle = -Math.PI / 2 + angleOffset + (ringItemIndex * Math.PI * 2 / ring.count);
    const x = cx + Math.cos(angle) * ring.radius; const y = cy + Math.sin(angle) * ring.radius;
    const name = item.technology || item.competency || item.role || 'Компетенция';
    const bf = Number(item.team_bus_factor ?? item.bus_factor ?? 0);
    const tone = bf <= 1 ? 'critical' : bf === 2 ? 'warn' : 'safe';
    const active = name === (selected?.skill || selected?.competency);
    const compact = nodeRadius <= 52;
    return `<path class="star-network-edge ${tone}" d="M ${cx} ${cy} L ${x.toFixed(1)} ${y.toFixed(1)}"/><g class="star-skill-node ${tone} ${active ? 'selected' : ''} ${compact ? 'compact' : ''}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})" data-star-competency="${attr(name)}" tabindex="0" role="button" aria-label="${attr(`${name}, Bus Factor ${bf}`)}"><circle r="${nodeRadius}"/><text class="skill-name" text-anchor="middle">${starSkillText(name, compact ? 3 : 4, compact ? 12 : 16, compact ? 14 : 17)}</text><title>${escapeHtml(name)} · BF ${number(bf)}</title></g>`;
  })).join('');
  return `<div class="star-graph-stage"><div class="star-graph-canvas" style="transform:translate3d(${starViewport.x}px, ${starViewport.y}px, 0) scale(${starViewport.scale})"><svg class="star-network-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Звёздная карта команды ${attr(team.team_id)}"><g>${nodes}${center}</g></svg></div></div>`;
}

function renderCompetencyMap(team, selected) {
  const legacyCompetencies = team.competencies.map((item) => item);
  if (!team.bus_factor_available) {
    $('#competencyMap').innerHTML = empty(team.message || 'Для этой команды пока нет данных о компетенциях.');
    $('#teamGraph').innerHTML = empty(team.message || 'Недостаточно данных для расчёта Bus Factor.');
    $('#competencyDetail').innerHTML = empty(team.message || 'Недостаточно данных для расчёта Bus Factor.');
    $('#selectedCompetencyPanel').hidden = true;
    $('#selectedCompetencyPanel').innerHTML = empty(team.message || 'Недостаточно данных для расчёта Bus Factor.');
    $('#busRiskList').innerHTML = empty('Критичных рисков Bus Factor не обнаружено.');
    return;
  }
  const items = starGraphItems(team).length ? starGraphItems(team) : legacyCompetencies;
  $('#teamGraph').innerHTML = starGraph(team, selected);
  $('#competencyMap').innerHTML = '';
  const critical = items.filter((item) => Number(item.team_bus_factor ?? item.bus_factor ?? 0) === 1);
  $('#busRiskList').innerHTML = critical.length ? critical.map((item, index) => {
    const name = item.technology || item.competency || item.role || 'Компетенция';
    return `<button type="button" class="bus-risk-item ${name === (selected?.skill || selected?.competency) ? 'selected' : ''}" data-star-competency="${attr(name)}"><span class="risk-position">${index + 1}</span><span class="risk-copy"><b>${escapeHtml(name)}</b><small>Только один инженер покрывает эту компетенцию.</small></span><span class="risk-value">BF 1</span></button>`;
  }).join('') : empty('Компетенций с Bus Factor 1 не обнаружено.');
  if (!selected) {
    $('#competencyDetail').innerHTML = empty('Выберите компетенцию на карте.');
    $('#selectedCompetencyPanel').hidden = true;
    $('#selectedCompetencyPanel').innerHTML = '';
    return;
  }
  const holders = selected.engineers.length
    ? selected.engineers.map((engineer) => `<button type="button" class="engineer-chip" data-open-employee="${attr(engineer.engineer_id)}"><span>${escapeHtml(engineer.name)}</span><small>${escapeHtml(engineer.roles.join(', ') || engineer.engineer_id)}</small></button>`).join('')
    : empty('В команде нет инженеров с этой компетенцией.');
  const external = selected.external_engineers?.length
    ? `<div class="external-backup"><h3>Резерв в других командах</h3><p>Эти инженеры не считаются локальными участниками команды.</p><div class="engineer-chip-row">${selected.external_engineers.map((engineer) => `<button type="button" class="engineer-chip secondary" data-open-employee="${attr(engineer.engineer_id)}"><span>${escapeHtml(engineer.name)}</span><small>${escapeHtml(engineer.source_team_ids.join(', '))}</small></button>`).join('')}</div></div>`
    : '';
  const developmentRecommendation = (data.development?.recommendations || []).find((item) => item.team_id === team.team_id && (item.deficit?.skills || []).includes(selected.skill));
  const developmentQuickView = developmentRecommendation ? `<div class="star-development-quick"><h3>Рекомендация по развитию за квартал</h3><b>${escapeHtml(developmentActionLabel(developmentRecommendation.recommendation?.primary_action))}</b>${developmentRecommendation.recommendation?.candidates?.[0] ? `<button type="button" class="table-button" data-open-employee="${attr(developmentRecommendation.recommendation.candidates[0].engineer_id)}">${escapeHtml(developmentRecommendation.recommendation.candidates[0].name)} · приоритет для дообучения ↗</button>` : `<span>${escapeHtml(developmentRecommendation.recommendation?.hire_profile?.label || '')}</span>`}</div>` : '';
  const detail = `<div class="competency-detail-heading"><div><span class="eyebrow">ВЫБРАННАЯ КОМПЕТЕНЦИЯ</span><h2>${escapeHtml(selected.skill)}</h2></div><div class="bf-number"><span>Bus Factor</span><b>${number(selected.bus_factor)}</b>${busRiskTag(selected)}</div></div><p class="competency-reason">${escapeHtml(selected.reason)}</p>${selected.recommendation ? `<p class="competency-recommendation">${escapeHtml(selected.recommendation)}</p>` : ''}<h3>Кто покрывает компетенцию</h3><div class="engineer-chip-row">${holders}</div>${external}${developmentQuickView}`;
  $('#competencyDetail').innerHTML = detail;
  $('#selectedCompetencyPanel').hidden = !state.starCompetencyOpen;
  $('#selectedCompetencyPanel').innerHTML = detail;
}

function renderStarTeams() {
  const availableTeams = [data.starMap?.company, ...(data.starMap?.teams || [])].filter(Boolean);
  if (!availableTeams.length) {
    $('#starTeamSelect').innerHTML = '';
    $('#teamMetrics').innerHTML = '';
    $('#teamLifecycleActions').innerHTML = '';
    $('#competencyMap').innerHTML = empty(data.starMap?.message || 'Недостаточно данных для расчёта Bus Factor.');
    $('#teamGraph').innerHTML = empty(data.starMap?.message || 'Недостаточно данных для расчёта Bus Factor.');
    $('#competencyDetail').innerHTML = empty('Для этой команды пока нет данных о компетенциях.');
    $('#selectedCompetencyPanel').hidden = true;
    $('#selectedCompetencyPanel').innerHTML = empty('Для этой команды пока нет данных о компетенциях.');
    $('#busRiskList').innerHTML = empty('Критичных рисков Bus Factor не обнаружено.');
    return;
  }
  if (!starTeamBy(state.teamId)) state.teamId = data.starMap?.teams?.[0]?.team_id || availableTeams[0].team_id;
  const busTeam = starTeamBy(state.teamId);
  const companyMode = busTeam.scope === 'company';
  const team = teamBy(busTeam.team_id);
  const activeMemberIds = new Set(busTeam.member_ids || []);
  const engineers = data.engineers.filter((engineer) => activeMemberIds.has(engineer.engineer_id));
  updateStarEngineerSuggestions((data.starMap?.engineers || []).filter((engineer) => activeMemberIds.has(engineer.engineer_id)));
  $('#starTeamSelect').innerHTML = availableTeams.map((item) => `<option value="${attr(item.team_id)}" ${item.team_id === busTeam.team_id ? 'selected' : ''}>${escapeHtml(item.scope === 'company' ? 'Вся компания' : item.team_name)}</option>`).join('');
  const summary = busTeam.summary;
  const metrics = busTeam.bus_factor_available
    ? [['Инженеров', busTeam.engineer_count, 'neutral'], ['Компетенций', busTeam.competency_count, 'neutral'], ['BF = 1', summary.single_owner_count, 'critical'], ['Требуют внимания', summary.warning_count, 'warning'], ['Устойчивое покрытие', summary.healthy_count, 'healthy']]
    : [['Инженеров', busTeam.engineer_count, 'neutral'], ['Компетенции', 'Нет данных', 'neutral']];
  $('#teamMetrics').innerHTML = metrics.map(([label, value, tone]) => `<div class="bf-summary-item ${tone}"><span>${escapeHtml(label)}</span><b>${typeof value === 'number' ? number(value) : escapeHtml(value)}</b></div>`).join('');
  $('#teamLifecycleActions').innerHTML = `<div class="entity-actions">${companyMode
    ? '<button class="btn btn-outline" type="button" disabled aria-disabled="true" title="Для всей компании отдельная страница команды недоступна">Открыть страницу команды ↗</button>'
    : `<button class="btn btn-outline" type="button" data-open-team="${attr(team.team_id)}">Открыть страницу команды ↗</button>`}</div>`;
  const mode = starTeamMode(busTeam);
  const items = starGraphItems(busTeam);
  const roles = [...new Set((mode.roles || []).map((item) => item.role || item.competency).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  $('#starRoleFilters').innerHTML = roles.length ? roles.map((role) => `<label><input type="checkbox" value="${attr(role)}" ${state.starRoleFilters.includes(role) ? 'checked' : ''}><span>${escapeHtml(role)}</span></label>`).join('') : '<span class="muted">Роли не найдены</span>';
  const selectedName = state.competency;
  const selectedItem = selectedName
    ? items.find((item) => (item.technology || item.competency || item.role) === selectedName) || null
    : null;
  const selectedSkill = selectedItem ? (selectedItem.technology || selectedItem.competency || selectedItem.role) : null;
  state.competency = selectedSkill;
  const holderIds = selectedItem?.engineer_ids || selectedItem?.quarter_engineer_ids || [];
  const scopeName = companyMode ? 'компании' : (busTeam.team_name || busTeam.team_id);
  const selected = selectedItem ? {
    skill: selectedSkill,
    bus_factor: Number(selectedItem.team_bus_factor ?? selectedItem.bus_factor ?? 0),
    engineers: (data.starMap?.engineers || []).filter((engineer) => holderIds.includes(engineer.engineer_id)),
    external_engineers: (selectedItem.helpers || []).map((helper) => ({...helper, name: helper.engineer_id, roles: [], source_team_ids: helper.source_team_id ? [helper.source_team_id] : []})),
    risk_level: Number(selectedItem.team_bus_factor ?? 0) <= 1 ? 'critical' : Number(selectedItem.team_bus_factor ?? 0) === 2 ? 'warning' : 'healthy',
    risk_label: Number(selectedItem.team_bus_factor ?? 0) <= 1 ? 'Критично' : Number(selectedItem.team_bus_factor ?? 0) === 2 ? 'Требует внимания' : 'Достаточное покрытие',
    reason: Number(selectedItem.team_bus_factor ?? 0) === 1 ? `Только один инженер в ${scopeName} покрывает эту компетенцию.` : `Компетенцию покрывают ${Number(selectedItem.team_bus_factor ?? 0)} инженера ${companyMode ? 'компании' : 'команды'}.`,
    recommendation: Number(selectedItem.team_bus_factor ?? 0) <= 2 ? 'Требуется дополнительное покрытие компетенции.' : null,
  } : null;
  const unique = (mode.technologies || []).filter((item) => Number(item.team_bus_factor) === 1).map((item) => item.technology);
  $('#starTeamProfileDetails').innerHTML = unique.length ? `<div class="skill-tags compact">${unique.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('')}</div><p class="hint">Для этих технологий ${companyMode ? 'в компании' : 'в команде'} найден только один носитель.</p>` : '<p class="hint">Единичных носителей не найдено.</p>';
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
  const teamScope = state.skillTeam === 'all' ? null : starTeamBy(state.skillTeam);
  const demandScope = teamScope || data.starMap?.company || null;
  const selectedMode = starTeamMode(demandScope);
  const demandSkills = (selectedMode.technologies || []).map((item) => item.technology).filter(Boolean);
  const availableSkills = state.starQuarterOnly ? [...new Set(demandSkills)].sort() : allSkills;
  if (state.skillTech !== 'all' && !availableSkills.includes(state.skillTech)) state.skillTech = 'all';
  $('#skillTech').innerHTML = `<option value="all">Все технологии</option>${availableSkills.map((skill) => `<option value="${attr(skill)}" ${state.skillTech === skill ? 'selected' : ''}>${escapeHtml(skill)}</option>`).join('')}`;
  $('#criticalOnly').checked = state.criticalOnly;
  $('#competencyQuarterOnly').checked = state.starQuarterOnly;
  const scopeIds = teamScope ? new Set(teamScope.member_ids) : activeIds;
  const scope = activeEngineers.filter((engineer) => scopeIds.has(engineer.engineer_id));
  const criticalSkills = new Set((selectedMode.technologies || []).filter((item) => Number(item.team_bus_factor) === 1).map((item) => item.technology));
  const columns = availableSkills.filter((skill) => (state.skillTech === 'all' || skill === state.skillTech) && (!state.criticalOnly || criticalSkills.has(skill)));
  const subset = scope.filter((engineer) => state.skillRole === 'all' || engineer.roles.includes(state.skillRole));
  const noApprovedPlan = state.starQuarterOnly && data.starMap?.quarter_demand_status === 'NO_APPROVED_PLAN';
  const emptyMessage = noApprovedPlan
    ? 'Утверждённый квартальный план ещё не построен.'
    : state.starQuarterOnly && !demandSkills.length
      ? 'Для текущего квартала требования к компетенциям не определены.'
      : 'Для выбранных фильтров сотрудников или технологий нет.';
  $('#heatmap').innerHTML = subset.length && columns.length ? table(['Инженер', ...columns], subset.map((engineer) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button><div class="muted">${escapeHtml(engineer.roles.join(', '))}</div></td>${columns.map((skill) => `<td>${engineer.skills.includes(skill) ? '<span class="heat-yes">Есть</span>' : '<span class="heat-no">—</span>'}</td>`).join('')}</tr>`), 'heatmap-table') : empty(emptyMessage);
}

function renderEngineerSelector() {
  const select = $('#engineerWorkspaceSelect');
  if (!select) return;
  const active = (data.engineerCatalog.length ? data.engineerCatalog : data.engineers).filter((engineer) => !engineer.archived);
  if (!active.some((engineer) => engineer.engineer_id === state.engineerWorkspaceId)) {
    state.engineerWorkspaceId = active[0]?.engineer_id || null;
  }
  select.innerHTML = active.map((engineer) => `<option value="${attr(engineer.engineer_id)}" ${engineer.engineer_id === state.engineerWorkspaceId ? 'selected' : ''}>${escapeHtml(engineer.name || engineer.engineer_id)} · ${escapeHtml(engineer.engineer_id)}</option>`).join('');
  select.disabled = !active.length;
}

async function loadEngineerWorkspace() {
  renderEngineerSelector();
  if (!state.engineerWorkspaceId) {
    data.engineerWorkspace = null;
    renderPage(state.page);
    return;
  }
  const requestedId = state.engineerWorkspaceId;
  state.engineerWorkspaceLoading = true;
  renderPage(state.page);
  try {
    const payload = await window.postcodeApi.getEngineerWorkspace(requestedId);
    if (state.engineerWorkspaceId !== requestedId) return;
    data.engineerWorkspace = payload;
  } catch (error) {
    if (state.engineerWorkspaceId !== requestedId) return;
    data.engineerWorkspace = {status: 'ERROR', message: error?.message || 'Не удалось загрузить рабочее пространство инженера.'};
  } finally {
    if (state.engineerWorkspaceId === requestedId) {
      state.engineerWorkspaceLoading = false;
      renderPage(state.page);
    }
  }
}

function engineerWorkspaceReady(target) {
  if (state.engineerWorkspaceLoading) {
    target.innerHTML = empty('Загружаем утверждённый план инженера…');
    return null;
  }
  const workspace = data.engineerWorkspace;
  if (!workspace || workspace.status === 'ERROR') {
    target.innerHTML = empty(workspace?.message || 'Выберите инженера.');
    return null;
  }
  return workspace;
}

function engineerHero(workspace, title, subtitle) {
  const engineer = workspace.engineer || {};
  const current = workspace.quarter?.current_sprint || 1;
  const total = workspace.quarter?.sprint_count || 6;
  return `<div class="page-intro engineer-hero"><div><div class="eyebrow">CURRENT APPROVED PLAN</div><h1>${escapeHtml(title)}</h1><p>${escapeHtml(subtitle)}</p><div class="engineer-identity"><span>${escapeHtml(engineer.name || engineer.engineer_id)}</span><span>${escapeHtml((engineer.roles || []).join(', ') || 'Роль не указана')}</span><span>${escapeHtml((engineer.team_ids || []).join(', ') || 'Команда не указана')}</span><span>Спринт ${number(current)} из ${number(total)}</span></div></div><div class="page-intro-icon" aria-hidden="true">◎</div></div>`;
}

function engineerEmpty(workspace) {
  return `<div class="empty-state engineer-empty">${escapeHtml(workspace.message || 'В утверждённом плане для вас пока нет назначений.')}</div>`;
}

function engineerAssignmentCard(item) {
  const roles = item.roles || (item.role ? [item.role] : []);
  return `<article class="engineer-assignment ${item.cross_team ? 'engineer-cross-team' : ''}"><div><h3>${escapeHtml(item.task_id)}${Number.isFinite(Number(item.rung)) ? ` · R${escapeHtml(item.rung)}` : ''}</h3><p>${escapeHtml(item.title || 'Название не указано')}</p><div class="engineer-assignment-meta"><span>Команда: ${escapeHtml(item.owner_team_id || '—')}</span><span>Роль: ${escapeHtml(roles.join(', ') || '—')}</span><span>Мои часы: ${number(item.allocated_hours)} ч</span>${item.continuation ? '<span>Продолжение</span>' : ''}${item.cross_team ? `<span>Помощь из ${escapeHtml((item.source_team_ids || [item.source_team_id]).filter(Boolean).join(', '))}</span>` : ''}</div><small>Завершение по плану: ${item.completion_sprint ? `Спринт ${number(item.completion_sprint)}` : '—'}</small></div><button class="btn btn-outline" type="button" data-open-task="${attr(item.task_id)}">Открыть задачу</button></article>`;
}

function renderEngineerSprint() {
  const target = $('#engineerSprintContent');
  const workspace = engineerWorkspaceReady(target);
  if (!workspace) return;
  target.innerHTML = engineerHero(workspace, 'Мой спринт', 'Текущие назначения, личная загрузка и блокирующие зависимости.');
  if (!workspace.approved_plan || workspace.status === 'NO_ASSIGNMENTS') {
    target.insertAdjacentHTML('beforeend', engineerEmpty(workspace));
    return;
  }
  const summary = workspace.summary || {};
  const current = (workspace.sprints || []).find((item) => item.current) || {};
  target.insertAdjacentHTML('beforeend', `<div class="engineer-summary"><div class="metric"><span>Мои задачи</span><b>${number(summary.current_task_count)}</b></div><div class="metric"><span>Запланировано</span><b>${number(summary.planned_hours)} ч</b></div><div class="metric"><span>Доступно</span><b>${number(summary.capacity_hours)} ч</b></div><div class="metric"><span>Свободно</span><b>${number(summary.free_hours)} ч</b></div><div class="metric"><span>Заблокировано</span><b>${number(summary.blocked_task_count)}</b></div></div><div class="engineer-layout"><article class="panel"><h2 class="engineer-section-title">Задачи текущего спринта</h2><div class="engineer-assignment-list">${(workspace.current_assignments || []).length ? workspace.current_assignments.map(engineerAssignmentCard).join('') : empty('На этот спринт задачи не назначены.')}</div></article><aside class="panel"><h2>Моя загрузка</h2><div class="engineer-load-number">${number(current.allocated_hours)} / ${number(current.capacity_hours)} ч</div>${meter(current.allocated_hours, current.capacity_hours)}<div class="engineer-load-breakdown"><div class="engineer-load-row"><span>Своя команда</span><b>${number(current.own_team_hours)} ч</b></div><div class="engineer-load-row"><span>Помощь другим командам</span><b>${number(current.cross_team_hours)} ч</b></div><div class="engineer-load-row"><span>Свободно</span><b>${number(current.free_hours)} ч</b></div></div><div class="engineer-bucket-list">${(current.capacity_buckets || []).map((bucket) => `<div class="engineer-bucket"><span>${escapeHtml(bucket.team_id)}</span><b>${number(bucket.allocated_hours)} / ${number(bucket.capacity_hours)} ч</b></div>`).join('')}</div></aside></div>`);
  const currentCrossTeam = (workspace.cross_team_assignments || []).filter((item) => item.sprint === workspace.quarter.current_sprint);
  if (currentCrossTeam.length) target.insertAdjacentHTML('beforeend', `<article class="panel engineer-empty"><div class="eyebrow">ВРЕМЕННОЕ УЧАСТИЕ</div><h2>Помощь другим командам</h2><div class="engineer-assignment-list">${currentCrossTeam.map(engineerAssignmentCard).join('')}</div></article>`);
}

function renderEngineerQuarter() {
  const target = $('#engineerQuarterContent');
  const workspace = engineerWorkspaceReady(target);
  if (!workspace) return;
  target.innerHTML = engineerHero(workspace, 'Мой квартал', 'Персональный план по шести спринтам в часах.');
  if (!workspace.approved_plan || workspace.status === 'NO_ASSIGNMENTS') {
    target.insertAdjacentHTML('beforeend', engineerEmpty(workspace));
    return;
  }
  target.insertAdjacentHTML('beforeend', `<article class="panel"><div class="engineer-quarter-viewport"><div class="engineer-quarter-board">${(workspace.sprints || []).map((sprint) => `<section class="engineer-sprint-column ${sprint.current ? 'current' : ''}"><div class="engineer-sprint-head"><b>Спринт ${number(sprint.sprint)}</b><span>${number(sprint.allocated_hours)} / ${number(sprint.capacity_hours)} ч · свободно ${number(sprint.free_hours)} ч</span></div>${(sprint.assignments || []).length ? sprint.assignments.map((item) => `<button type="button" class="engineer-sprint-task ${item.cross_team ? 'cross-team' : ''}" data-open-task="${attr(item.task_id)}"><b>${item.continuation ? '↳ ' : ''}${escapeHtml(item.task_id)}</b><span>${escapeHtml(item.title || '')}</span><small>${item.continuation ? 'Продолжение · ' : ''}${number(item.allocated_hours)} ч${item.cross_team ? ` · помощь ${escapeHtml(item.owner_team_id)}` : ''}</small></button>`).join('') : '<div class="sprint-empty">Нет назначений</div>'}</section>`).join('')}</div></div></article>`);
}

function engineerTaskCard(task) {
  const blockers = task.dependencies || [];
  return `<article class="engineer-task-card"><div><h3>${escapeHtml(task.task_id)} · ${escapeHtml(task.title || '')}</h3><p>${escapeHtml(task.status_label || task.status || '—')}</p><div class="engineer-task-card-meta"><span>${escapeHtml(task.owner_team_id || '—')}</span><span>${escapeHtml((task.roles || []).join(', ') || '—')}</span><span>${number(task.allocated_hours)} ч</span><span>${(task.planned_sprints || []).map((sprint) => `S${sprint}`).join(', ')}</span>${task.cross_team ? '<span>Cross-team помощь</span>' : ''}</div>${blockers.length ? `<div class="engineer-blocker">Ожидает: ${blockers.map((item) => escapeHtml(item.blocking_task_id)).join(', ')}</div>` : ''}</div><button class="btn btn-outline" type="button" data-open-task="${attr(task.task_id)}">Открыть</button></article>`;
}

function renderEngineerTasks() {
  const target = $('#engineerTasksContent');
  const workspace = engineerWorkspaceReady(target);
  if (!workspace) return;
  target.innerHTML = engineerHero(workspace, 'Мои задачи', 'Все назначения из утверждённого квартального плана.');
  if (!workspace.approved_plan || workspace.status === 'NO_ASSIGNMENTS') {
    target.insertAdjacentHTML('beforeend', engineerEmpty(workspace));
    return;
  }
  const query = state.engineerTaskSearch.trim().toLowerCase();
  const groups = [['current', 'Текущий спринт'], ['upcoming', 'Предстоящие'], ['completed', 'Завершённые']];
  target.insertAdjacentHTML('beforeend', `<article class="panel"><div class="engineer-task-toolbar"><div><h2>Персональный список</h2><p class="hint">Задачи команды без вашего назначения сюда не попадают.</p></div><label>Поиск по ID или названию<input id="engineerTaskSearch" class="engineer-task-search" type="search" value="${attr(state.engineerTaskSearch)}" placeholder="Например, DB-202"></label></div><div class="engineer-task-groups">${groups.map(([key, label]) => { const rows=(workspace.tasks?.[key] || []).filter((task) => !query || `${task.task_id} ${task.title}`.toLowerCase().includes(query)); return `<section class="panel engineer-task-group ${key}"><h2>${label}</h2>${rows.length ? rows.map(engineerTaskCard).join('') : empty('Задач в этом разделе нет.')}</section>`; }).join('')}</div></article>`);
}

function renderEngineerParticipation(task) {
  const panel = $('#taskEngineerParticipation');
  if (!panel) return;
  const workspace = data.engineerWorkspace;
  const personal = workspace?.tasks?.all?.find((item) => item.task_id === task?.task_id);
  panel.hidden = state.workspaceMode !== 'engineer' || !personal;
  if (panel.hidden) { panel.innerHTML = ''; return; }
  panel.innerHTML = `<div class="eyebrow">ПЕРСОНАЛЬНОЕ НАЗНАЧЕНИЕ</div><h2>Моё участие</h2><div class="engineer-participation-grid"><div><span>Роль</span><b>${escapeHtml((personal.roles || []).join(', ') || '—')}</b></div><div><span>Мои часы</span><b>${number(personal.allocated_hours)} ч</b></div><div><span>Команда-владелец</span><b>${escapeHtml(personal.owner_team_id || '—')}</b></div><div><span>Участие</span><b>${personal.cross_team ? 'Помощь другой команде' : 'Своя команда'}</b></div><div><span>Завершение</span><b>${personal.completion_sprint ? `Спринт ${number(personal.completion_sprint)}` : '—'}</b></div></div><div class="engineer-bucket-list">${Object.entries(personal.hours_by_sprint || {}).map(([sprint, hours]) => `<div class="engineer-bucket"><span>Спринт ${escapeHtml(sprint)}</span><b>${number(hours)} ч</b></div>`).join('')}</div>${personal.dependencies?.length ? `<div class="engineer-blocker">Активные зависимости: ${personal.dependencies.map((item) => escapeHtml(item.blocking_task_id)).join(', ')}</div>` : ''}`;
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
  const memberships = engineer.capacity_buckets.map((bucket) => bucket.team_id
    ? `<button class="profile-link" data-employee-team="${attr(bucket.team_id)}" type="button">${escapeHtml(bucket.team_id)} · доступность ${percent(Number(bucket.capacity_rate) * 100)} · ${number(bucket.capacity_hours)} ч ↗</button>`
    : `<div class="profile-link">Без команды · доступность ${percent(Number(bucket.capacity_rate) * 100)} · ${number(bucket.capacity_hours)} ч</div>`).join('');
  $('#employeeContent').innerHTML = `<div class="eyebrow">КАРТОЧКА СОТРУДНИКА</div><h2>${escapeHtml(engineer.name || engineer.engineer_id)}</h2><p>${escapeHtml(engineer.roles.join(', ') || engineer.engineer_id)}${grade}</p><div class="metric-grid employee-metrics"><div class="metric"><span>Команд</span><b>${engineer.team_ids.length}</b></div><div class="metric"><span>Доступно за спринт</span><b>${number(engineer.capacity_hours)} ч</b></div><div class="metric"><span>Занято в Sprint ${sprint}</span><b>${number(engineer.used_hours_by_sprint[String(sprint)])} ч</b></div><div class="metric"><span>Свободно</span><b>${number(engineer.free_hours_by_sprint[String(sprint)])} ч</b></div></div><div class="drawer-section"><h3>Команды и участие</h3>${memberships || '<p>Без команды</p>'}</div><div class="drawer-section"><h3>Технологии</h3><div class="skill-tags">${displayedSkills.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></div><div class="drawer-section"><h3>Критичность для команд</h3><div class="engineer-impact-list">${impact}</div></div><div class="drawer-section"><h3>Назначения Sprint ${sprint}</h3>${allocations.length ? allocations.map((item) => `<p>${escapeHtml(item.task_id)} · ${escapeHtml(item.role)} · ${number(item.allocated_hours)} ч${item.cross_team ? ` · помогает ${escapeHtml(item.assigned_task_team_id)} из ${escapeHtml(item.source_team_id)}` : ''}</p>`).join('') : '<p>Назначений нет.</p>'}</div>${archiveState(engineer)}${engineer.archived ? '' : `<div class="entity-actions"><button class="btn btn-danger" type="button" data-archive-entity="engineer" data-archive-id="${attr(engineer.engineer_id)}" data-current-plan-reference="${engineer.allocations.length > 0}">Удалить</button></div>`}`;
  $('#employeeDrawer').classList.add('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'false');
}

function closeEmployee() {
  $('#employeeDrawer').classList.remove('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'true');
}

function bindEvents() {
  embedStarFilters();
  bindStarMapNavigation();
  $$('[data-workspace-mode]').forEach((button) => button.addEventListener('click', () => {
    state.workspaceMode = button.dataset.workspaceMode;
    window.localStorage.setItem('postcode-workspace-mode', state.workspaceMode);
    data.engineerWorkspace = null;
    if (state.workspaceMode === 'engineer') {
      show('engineer-sprint');
      loadEngineerWorkspace();
    } else show('home');
  }));
  $('#engineerWorkspaceSelect')?.addEventListener('change', (event) => {
    state.engineerWorkspaceId = event.target.value;
    data.engineerWorkspace = null;
    loadEngineerWorkspace();
  });
  $('#openResetData')?.addEventListener('click', openResetDataDialog);
  $('#resetDataConfirm')?.addEventListener('change', (event) => { $('#confirmResetData').disabled = !event.target.checked; });
  $('#resetDataForm')?.addEventListener('submit', submitResetData);
  $('#resetDataDialog')?.addEventListener('click', (event) => { if (event.target === $('#resetDataDialog')) closeResetDataDialog(); });
  $('#addTaskFromTasks')?.addEventListener('click', () => window.planConstructor.openTaskForm());
  $('#addEngineerFromEngineers')?.addEventListener('click', () => window.planConstructor.openEngineerForm());
  $('#addTeamFromTeams')?.addEventListener('click', () => window.planConstructor.openTeamForm());
  window.addEventListener('postcode:constructor-task-added', (event) => {
    show('planning');
    showToast(`Задача ${event.detail?.taskId || ''} добавлена в черновик сценария и выбрана.`);
  });
  window.addEventListener('postcode:constructor-engineer-added', (event) => {
    show('planning');
    showToast(`Инженер ${event.detail?.engineerId || ''} добавлен в черновик сценария.`);
  });
  window.addEventListener('postcode:constructor-team-added', (event) => {
    show('planning');
    showToast(`Команда ${event.detail?.teamId || ''} добавлена в черновик сценария и выбрана.`);
  });
  $('#starTeamSelect').addEventListener('change', (event) => { state.teamId = event.target.value; state.competency = null; state.starCompetencyOpen = false; resetStarViewport(); renderStarTeams(); });
  $('#starEngineerSearch')?.addEventListener('input', (event) => { state.starEngineerSearch = event.target.value; renderStarTeams(); });
  $('#starQuarterOnly')?.addEventListener('change', (event) => { state.starQuarterOnly = event.target.checked; resetStarViewport(); renderStarTeams(); });
  $('#starRoleFilters')?.addEventListener('change', (event) => { if (!event.target.matches('input[type="checkbox"]')) return; state.starRoleFilters = $$('input[type="checkbox"]:checked', $('#starRoleFilters')).map((input) => input.value); renderStarTeams(); });
  $('#starGraphMode')?.addEventListener('click', (event) => { const button = event.target.closest('[data-star-mode]'); if (!button) return; state.starGraphMode = button.dataset.starMode; state.competency = null; state.starCompetencyOpen = false; resetStarViewport(); $$('#starGraphMode [data-star-mode]').forEach((item) => item.classList.toggle('active', item === button)); renderStarTeams(); });
  $('#skillTeam').addEventListener('change', (event) => { state.skillTeam = event.target.value; renderStarSkills(); });
  $('#skillRole').addEventListener('change', (event) => { state.skillRole = event.target.value; renderStarSkills(); });
  $('#skillTech').addEventListener('change', (event) => { state.skillTech = event.target.value; renderStarSkills(); });
  $('#competencyQuarterOnly')?.addEventListener('change', (event) => { state.starQuarterOnly = event.target.checked; state.skillTech = 'all'; renderStarSkills(); });
  $('#criticalOnly').addEventListener('change', (event) => { state.criticalOnly = event.target.checked; renderStarSkills(); });
  $('#openSprintCloseDialog').addEventListener('click', openSprintCloseDialog);
  $('#sprintCloseForm').addEventListener('submit', closeCurrentSprint);
  $('#sprintCloseDialog').addEventListener('click', (event) => { if (event.target === $('#sprintCloseDialog')) closeSprintCloseDialog(); });
  $('#confirmSprintResult').addEventListener('click', closeSprintResult);
  $('#sprintResultDialog').addEventListener('click', (event) => { if (event.target === $('#sprintResultDialog')) closeSprintResult(); });
  $('#archiveForm').addEventListener('submit', (event) => { event.preventDefault(); submitArchive(); });
  $('#archiveReason').addEventListener('change', updateArchiveConfirmation);
  $('#archiveReasonText').addEventListener('input', updateArchiveConfirmation);
  $('#archiveImpact').addEventListener('change', updateArchiveConfirmation);
  $('#archiveReplanConfirm').addEventListener('change', updateArchiveConfirmation);
  $('#archiveDialog').addEventListener('click', (event) => { if (event.target === $('#archiveDialog')) closeArchiveModal(); });
  $('#riskDialog')?.addEventListener('click', (event) => { if (event.target === $('#riskDialog')) closeRiskModal(); });
  $('#riskDialog')?.addEventListener('close', () => { state.riskModalOpen = false; renderRisks(); });
  $('#riskSearch')?.addEventListener('input', (event) => { state.riskSearch = event.target.value; renderRisks(); });
  document.addEventListener('click', (event) => {
    const approveReplanButton = event.target.closest('[data-approve-replan]');
    if (approveReplanButton) { approveReplan(approveReplanButton.dataset.approveReplan); return; }
    const retryReplanButton = event.target.closest('[data-retry-replan]');
    if (retryReplanButton) { retryReplan(retryReplanButton.dataset.retryReplan); return; }
    const kpiToggle = event.target.closest('[data-toggle-kpi-details]');
    if (kpiToggle) { toggleKpiDetails(); return; }
    if (event.target.closest('.catalog-skill-more')) return;
    if (event.target.closest('[data-close-sprint-dialog]')) { closeSprintCloseDialog(); return; }
    if (event.target.closest('[data-close-archive]')) { closeArchiveModal(); return; }
    if (event.target.closest('[data-close-reset-data]')) { closeResetDataDialog(); return; }
    const archive = event.target.closest('[data-archive-entity]');
    if (archive) { openArchiveModal(archive.dataset.archiveEntity, archive.dataset.archiveId, archive.dataset.currentPlanReference === 'true'); return; }
    if (event.target.closest('[data-close-drawer]')) { closeEmployee(); return; }
    if (event.target.closest('[data-task-back]')) { show(state.taskReturnPage || 'tasks'); return; }
    const riskHubLink = event.target.closest('[data-open-risk-hub]'); if (riskHubLink) { state.riskHubTab = riskHubLink.dataset.openRiskHub; show('risks'); return; }
    const nav = event.target.closest('button[data-page]');
    if (nav) {
      closeRiskModal();
      if (nav.dataset.page === 'risks') state.riskHubTab = 'risks';
      show(nav.dataset.page);
      return;
    }
    const taskSort = event.target.closest('[data-task-sort]');
    if (taskSort) {
      const key = taskSort.dataset.taskSort;
      if (state.taskSortKey === key) state.taskSortDirection = state.taskSortDirection === 'asc' ? 'desc' : 'asc';
      else { state.taskSortKey = key; state.taskSortDirection = 'asc'; }
      renderTasks();
      return;
    }
    const engineerSort = event.target.closest('[data-engineer-sort]');
    if (engineerSort) {
      const key = engineerSort.dataset.engineerSort;
      if (state.engineerSortKey === key) state.engineerSortDirection = state.engineerSortDirection === 'asc' ? 'desc' : 'asc';
      else { state.engineerSortKey = key; state.engineerSortDirection = 'asc'; }
      renderEngineers();
      return;
    }
    const teamSort = event.target.closest('[data-team-sort]');
    if (teamSort) {
      const key = teamSort.dataset.teamSort;
      if (state.teamSortKey === key) state.teamSortDirection = state.teamSortDirection === 'asc' ? 'desc' : 'asc';
      else { state.teamSortKey = key; state.teamSortDirection = 'asc'; }
      renderTeams();
      return;
    }
    const planTask = event.target.closest('[data-plan-task]'); if (planTask) { closeRiskModal(); gotoTask(planTask.dataset.planTask); return; }
    const sprintTaskToggle = event.target.closest('[data-toggle-current-sprint-task]');
    if (sprintTaskToggle) {
      const taskId = sprintTaskToggle.dataset.toggleCurrentSprintTask;
      state.currentSprintExpandedTaskId = state.currentSprintExpandedTaskId === taskId ? null : taskId;
      renderCurrentSprint();
      requestAnimationFrame(() => $(`[data-toggle-current-sprint-task="${CSS.escape(taskId)}"]`)?.focus());
      return;
    }
    const task = event.target.closest('[data-open-task]'); if (task) { closeRiskModal(); gotoTask(task.dataset.openTask); return; }
    const risk = event.target.closest('[data-open-risk]'); if (risk) { state.riskId = risk.dataset.openRisk; state.riskFilter = 'Все'; state.riskSearch = ''; state.riskModalOpen = true; state.riskHubTab = 'risks'; show('risks'); return; }
    const filter = event.target.closest('[data-risk-filter]'); if (filter) { state.riskFilter = filter.dataset.riskFilter; renderRisks(); return; }
    const riskRow = event.target.closest('[data-risk-id]'); if (riskRow) { state.riskId = riskRow.dataset.riskId; openRiskModal(riskRow.dataset.riskId); renderRisks(); return; }
    const competency = event.target.closest('[data-star-competency]');
    if (competency) {
      state.competency = competency.dataset.starCompetency;
      state.starCompetencyOpen = true;
      renderStarTeams();
      return;
    }
    const emptyStarArea = event.target.closest('.star-graph-stage');
    if (emptyStarArea && !starViewport.dragged && !event.target.closest('[data-star-center], button, a, input, select, label, [role="button"]')) {
      state.competency = null;
      state.starCompetencyOpen = false;
      $('#selectedCompetencyPanel').hidden = true;
      $$('[data-star-competency].selected', $('#starTeams')).forEach((node) => node.classList.remove('selected'));
      return;
    }
    const developmentTeam = event.target.closest('[data-open-development-team]');
    if (developmentTeam) {
      state.developmentTeam = developmentTeam.dataset.openDevelopmentTeam || 'all';
      state.developmentRole = 'all';
      state.developmentSkill = 'all';
      show('development');
      return;
    }
    const starDevelopment = event.target.closest('[data-open-star-development]');
    if (starDevelopment) {
      const selectedTeam = starTeamBy(state.teamId);
      state.developmentTeam = selectedTeam?.scope === 'company' ? 'all' : (selectedTeam?.team_id || 'all');
      state.developmentRole = 'all';
      state.developmentSkill = 'all';
      show('development');
      return;
    }
    const teamBack = event.target.closest('[data-team-back]'); if (teamBack) { show(state.teamReturnPage || 'teams'); return; }
    const teamDetailTab = event.target.closest('[data-team-detail-tab]'); if (teamDetailTab) { state.teamDetailTab = teamDetailTab.dataset.teamDetailTab; renderTeamDetail(); return; }
    const engineerBack = event.target.closest('[data-engineer-back]'); if (engineerBack) { show(state.engineerReturnPage || 'engineers'); return; }
    const engineerDetailTab = event.target.closest('[data-engineer-detail-tab]'); if (engineerDetailTab) { state.engineerDetailTab = engineerDetailTab.dataset.engineerDetailTab; renderEngineerDetail(); return; }
    const team = event.target.closest('[data-open-team]'); if (team) { closeRiskModal(); gotoTeam(team.dataset.openTeam); return; }
    const employee = event.target.closest('[data-open-employee]'); if (employee) { gotoEmployee(employee.dataset.openEmployee); return; }
    const backToTeam = event.target.closest('[data-employee-team]'); if (backToTeam) { closeEmployee(); gotoTeam(backToTeam.dataset.employeeTeam); return; }
    const riskHubTab = event.target.closest('[data-risk-hub-tab]'); if (riskHubTab) { state.riskHubTab = riskHubTab.dataset.riskHubTab; renderRisks(); return; }
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'developmentTeamFilter') { state.developmentTeam = event.target.value; renderDevelopment(); return; }
    if (event.target.id === 'developmentRoleFilter') { state.developmentRole = event.target.value; renderDevelopment(); return; }
    if (event.target.id === 'developmentSkillFilter') { state.developmentSkill = event.target.value; renderDevelopment(); return; }
    if (event.target.id === 'taskSprintSelect') { state.taskSprint = Number(event.target.value); renderTaskDetail(); return; }
    if (event.target.matches('[data-task-deviation]')) { syncSprintCloseControls(); return; }
    if (event.target.name === 'hasPlanDeviations') { syncSprintCloseControls(); $('#confirmCloseCurrentSprint').disabled = false; $('#sprintCloseError').hidden = true; }
  });
  document.addEventListener('input', (event) => {
    if (event.target.id !== 'engineerTaskSearch') return;
    state.engineerTaskSearch = event.target.value;
    renderEngineerTasks();
    $('#engineerTaskSearch')?.focus();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { closeArchiveModal(); closeRiskModal(); closeEmployee(); }
    const starNode = event.target.closest?.('[data-star-competency]');
    if (starNode && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      state.competency = starNode.dataset.starCompetency;
      state.starCompetencyOpen = true;
      renderStarTeams();
      return;
    }
    const taskRow = event.target.closest?.('[data-task-row]');
    if (taskRow && event.target === taskRow && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); gotoTask(taskRow.dataset.openTask); }
    const engineerRow = event.target.closest?.('[data-engineer-row]');
    if (engineerRow && event.target === engineerRow && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); gotoEmployee(engineerRow.dataset.openEmployee); }
    const teamRow = event.target.closest?.('[data-team-row]');
    if (teamRow && event.target === teamRow && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); gotoTeam(teamRow.dataset.openTeam); }
  });
  window.addEventListener('popstate', () => {
    const route = routeFromLocation();
    if (route.workspaceMode) state.workspaceMode = route.workspaceMode;
    state.taskId = route.taskId || state.taskId;
    state.teamId = route.teamId || state.teamId;
    state.engineerId = route.engineerId || state.engineerId;
    state.taskSprint = null;
    show(route.page, false);
    if (route.page === 'team-detail' && state.teamId) loadTeamDetail(state.teamId);
    if (route.page === 'engineer-detail' && state.engineerId) loadEngineerDetail(state.engineerId);
    if (state.workspaceMode === 'engineer' && !data.engineerWorkspace) loadEngineerWorkspace();
  });
  window.addEventListener('resize', () => applyStarViewport($('.star-graph-stage')));
}

function hydrate(payload) {
  data.teamDetails = {};
  data.teamHistories = {};
  data.engineerDetails = {};
  data.engineerHistories = {};
  data.development = null;
  state.developmentLoading = false;
  state.developmentError = null;
  data.plan = payload.plan;
  data.summary = payload.summary;
  data.tasks = payload.tasks || [];
  data.initiatives = payload.initiatives || [];
  data.risks = payload.risks || [];
  updateRiskStatusIndicators();
  data.teams = payload.teams || [];
  data.engineers = payload.engineers || [];
  data.starMap = payload.star_map || {teams: [], engineers: []};
  data.kpis = payload.kpis || null;
  data.features = payload.features || {};
  data.status = payload.status || 'READY';
  data.dataState = payload.data_state || 'READY';
  data.message = payload.message || null;
  data.ready = true;
  state.loading = false;
  state.error = null;
  if (!state.taskId) state.taskId = data.tasks.find((task) => task.status === 'PLANNED')?.task_id || data.tasks[0]?.task_id || null;
  if (!state.teamId) state.teamId = data.starMap.teams?.[0]?.team_id || data.teams[0]?.team_id || null;
  if (!state.engineerId) state.engineerId = data.engineers[0]?.engineer_id || null;
  state.competency = null;
  state.riskSearch = '';
  state.riskModalOpen = false;
  state.riskId = data.risks[0]?.risk_id || null;
  renderEngineerSelector();
}

async function initialize() {
  bindEvents();
  const resetCompleted = new URLSearchParams(window.location.search).get('reset') === '1';
  const initialRoute = routeFromLocation();
  const savedMode = window.localStorage.getItem('postcode-workspace-mode');
  state.workspaceMode = initialRoute.workspaceMode || (savedMode === 'engineer' ? 'engineer' : 'manager');
  if (state.workspaceMode === 'engineer' && !initialRoute.page.startsWith('engineer-') && initialRoute.page !== 'task-detail') initialRoute.page = 'engineer-sprint';
  state.taskId = initialRoute.taskId;
  state.teamId = initialRoute.teamId;
  state.engineerId = initialRoute.engineerId;
  show(initialRoute.page, 'replace');
  try {
    const [dashboard, constructorCatalog, archiveReasons, replanWorkflow] = await Promise.all([
      window.postcodeApi.getDashboard(),
      window.postcodeApi.getConstructorCatalog(),
      window.postcodeApi.getArchiveReasons(),
      window.postcodeApi.getReplanDraft(),
    ]);
    data.historicalTasks = (constructorCatalog.tasks || [])
      .filter((task) => ['done', 'completed'].includes(String(task.status || '').toLowerCase()))
      .map(historicalTaskFromCatalog);
    data.engineerCatalog = constructorCatalog.engineers || [];
    hydrate(dashboard);
    data.replanWorkflow = replanWorkflow.status === 'NONE' ? null : replanWorkflow;
    data.archiveReasons = archiveReasons;
    window.planConstructor.initialize(constructorCatalog, window.postcodeApi, (plannerResult) => {
      hydrate(plannerResult);
      show('home');
    }, (updatedCatalog) => {
      data.historicalTasks = (updatedCatalog.tasks || [])
        .filter((task) => ['done', 'completed'].includes(String(task.status || '').toLowerCase()))
        .map(historicalTaskFromCatalog);
    });
    if (state.workspaceMode === 'engineer') await loadEngineerWorkspace();
    if (hasPendingReplan() && state.page === 'home') show('recalc');
    else renderPage(state.page);
    if (state.page === 'team-detail' && state.teamId) loadTeamDetail(state.teamId);
    if (state.page === 'engineer-detail' && state.engineerId) loadEngineerDetail(state.engineerId);
    if (resetCompleted) showToast('Все данные удалены. Можно загрузить новый датасет или собрать план вручную.');
  } catch (error) {
    state.loading = false;
    state.error = error?.message || 'неизвестная ошибка';
    renderUnavailable();
  }
}

initialize();
