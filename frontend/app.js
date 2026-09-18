'use strict';

const state = {
  page: 'home', taskId: null, taskSprint: null,
  teamId: null, engineerId: null, starTab: 'teams', riskId: null,
  riskFilter: 'Все', skillTeams: [], skillRole: 'all', skillTechs: [],
  skillCoverageSort: 'none', skillTeamCountSort: 'none', skillCountSort: 'none', skillRoleCountSort: 'none', skillSortOrder: [],
  starEngineerId: null, starEngineerSearch: '', starRoleFilters: [], starCompetencyFilter: 'all', starSelectedSkill: null, starQuarterOnly: false, starGraphMode: 'technology',
  starGraphPanned: false,
  taskReturnPage: 'current-plan', loading: true, error: null, archive: null,
};

const data = {
  plan: null, summary: {}, tasks: [], initiatives: [], risks: [], teams: [],
  engineers: [], starMap: null, kpis: null, features: {}, archiveReasons: {}, ready: false,
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
const riskClass = (type) => ({'Сроки': 'deadline', 'Зависимости': 'dependency', 'Ресурсы': 'resource', 'Компетенции': 'competence'}[type] || 'resource');
const teamEngineers = (teamId) => data.engineers.filter((engineer) => engineer.team_ids.includes(teamId));
const teamSprintResource = (teamId, sprint) => teamBy(teamId).sp_by_sprint?.[String(sprint)] || {capacity: 0, used: 0, remaining: 0};
const tButton = (task, label = task.task_id) => `<button type="button" class="table-button" data-open-task="${attr(task.task_id)}">${escapeHtml(label)}</button>`;
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
    closeArchiveModal();
    if (entityType === 'task') renderTasks();
    if (entityType === 'engineer') openEmployee(entityId);
    if (entityType === 'team') renderStarTeams();
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

function show(page) {
  state.page = page;
  $$('.page').forEach((element) => element.classList.toggle('active', element.id === `page-${page}`));
  $$('.nav-link').forEach((element) => element.classList.toggle('active', element.dataset.page === page));
  closeEmployee();
  renderPage(page);
  window.scrollTo({top: 0, behavior: 'instant'});
}

function renderPage(page) {
  if (!data.ready) {
    renderUnavailable();
    return;
  }
  ({home: renderHome, planning: window.planConstructor.render, 'current-plan': renderPlanning, tasks: renderTasks, risks: renderRisks, recalc: renderRecalc, star: renderStar})[page]?.();
}

function renderUnavailable() {
  const message = state.error ? `Backend недоступен: ${state.error}` : 'Загружаем данные planner…';
  if ($('#homeSprints')) $('#homeSprints').innerHTML = empty(message);
  if ($('#homeTasks')) $('#homeTasks').innerHTML = empty(message);
  if ($('#homeRisks')) $('#homeRisks').innerHTML = empty(message);
}

function gotoTask(taskId) {
  if (!taskBy(taskId)) return;
  if (state.page !== 'tasks') state.taskReturnPage = state.page;
  state.taskId = taskId;
  state.taskSprint = null;
  show('tasks');
}

function gotoTeam(teamId) {
  if (!teamBy(teamId).team_id) return;
  state.teamId = teamId;
  state.starTab = 'teams';
  show('star');
}

function gotoEmployee(engineerId) {
  const exists = data.starMap?.engineers?.some((engineer) => engineer.engineer_id === engineerId) || engineerBy(engineerId);
  if (!exists) return;
  state.engineerId = engineerId;
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
  const rungValues = [...new Set(data.tasks.filter(inQuarter).map((task) => Number(task.rung)).filter(Number.isFinite))].sort((left, right) => left - right);
  const rungLevel = (task) => {
    if (rungValues.length < 2) return 'medium';
    const index = rungValues.indexOf(Number(task.rung));
    if (rungValues.length === 2) return index === 1 ? 'high' : 'low';
    const third = Math.floor(rungValues.length / 3);
    if (index >= rungValues.length - third) return 'high';
    return index < third ? 'low' : 'medium';
  };
  const rungBadge = (task) => Number.isFinite(Number(task.rung))
    ? `<span class="rung-badge rung-${rungLevel(task)}">R${escapeHtml(task.rung)}</span>`
    : '';
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
  const task = taskBy(state.taskId) || data.tasks[0];
  if (!task) {
    $('#taskSummary').innerHTML = empty('Задач нет.');
    return;
  }
  state.taskId = task.task_id;
  $('#taskHeroId').textContent = task.task_id;
  $('#taskHeroDescription').textContent = task.title;
  const sprints = taskSprints(task);
  const selectedSprint = state.taskSprint && sprints.includes(state.taskSprint) ? state.taskSprint : (sprints[0] || null);
  state.taskSprint = selectedSprint;
  const team = teamBy(task.assigned_team_id);
  const resource = selectedSprint ? teamSprintResource(team.team_id, selectedSprint) : null;
  const shownSp = selectedSprint ? portion(task, selectedSprint) : null;
  const summary = [
    ['Спринт', sprintLabel(task)], ['В квартальном плане', inQuarter(task) ? 'Да' : 'Нет'],
    ['Story Points', `${number(task.story_points)} SP`], ['Статус', task.status_label],
    ['Приоритет', task.priority_label], ['Инициатива', task.initiative_id || '—'],
  ];
  $('#taskSummary').innerHTML = summary.map(([name, value]) => `<div class="summary-item"><span>${escapeHtml(name)}</span><b>${escapeHtml(value)}</b></div>`).join('');
  const teamSwitch = task.team_reassigned ? `<p class="hint">Исходная команда: <b>${escapeHtml(task.original_team_id)}</b>. Назначенная команда: <b>${escapeHtml(task.assigned_team_id)}</b>.</p>` : '';
  $('#taskTeamResource').innerHTML = `<div class="panel-heading"><div><div class="eyebrow">КОМАНДНЫЙ РЕСУРС · STORY POINTS</div><h2>${escapeHtml(team.name)} · ${selectedSprint ? `Sprint ${selectedSprint}` : 'вне квартального плана'}</h2></div>${task.multi_sprint && selectedSprint ? `<label class="filters">Выбранный спринт <select id="taskSprintSelect">${sprints.map((sprint) => `<option value="${sprint}" ${sprint === selectedSprint ? 'selected' : ''}>Sprint ${sprint}</option>`).join('')}</select></label>` : ''}</div>${resource ? `<div class="metrics"><div class="metric"><span>Доступно</span><b>${number(resource.capacity)} SP</b></div><div class="metric"><span>Всего использовано</span><b>${number(resource.used)} SP</b></div><div class="metric"><span>Доля задачи</span><b>${number(shownSp)} SP</b></div></div>${meter(resource.used, resource.capacity)}` : empty('Ресурс спринта не указан для этой задачи.')}${teamSwitch}`;
  const riskMatches = data.risks.filter((risk) => risk.task_id === task.task_id);
  $('#taskDescription').innerHTML = `<div class="eyebrow">ОПИСАНИЕ</div><h2>${escapeHtml(task.title)}</h2><p style="margin-top:15px">${escapeHtml(task.description || 'Описание отсутствует.')}</p><div class="detail-grid"><div><span>Задача</span><b>${escapeHtml(task.task_id)}</b></div><div><span>Инициатива</span><b>${escapeHtml(task.initiative_id || '—')}</b></div><div><span>SP</span><b>${number(task.story_points)}</b></div><div><span>Rung</span><b>${number(task.rung)}</b></div><div><span>Статус</span><b>${escapeHtml(task.status_label)}</b></div><div><span>Спринт</span><b>${escapeHtml(sprintLabel(task))}</b></div></div>${riskMatches.length ? `<h3 class="needs-title">Связанные риски</h3>${riskMatches.map((risk) => `<p>${escapeHtml(risk.title)}: ${escapeHtml(risk.cause)}</p>`).join('')}` : ''}${archiveState(task)}${task.archived ? '' : `<div class="entity-actions"><button class="btn btn-danger" type="button" data-archive-entity="task" data-archive-id="${attr(task.task_id)}" data-current-plan-reference="${inQuarter(task)}">Удалить</button></div>`}`;
  const allocations = task.allocations.filter((item) => selectedSprint == null || item.sprint === selectedSprint);
  const people = [...new Set(allocations.map((item) => item.engineer_id))];
  $('#taskPeople').innerHTML = `<div class="eyebrow">КОМАНДА И СПЕЦИАЛИСТЫ</div><h2>Исполнители</h2><button type="button" class="profile-link" data-open-team="${attr(task.assigned_team_id)}">Команда: ${escapeHtml(task.assigned_team_id)} ↗</button>${people.length ? people.map((engineerId) => { const engineer = engineerBy(engineerId); const engineerAllocations = allocations.filter((item) => item.engineer_id === engineerId); const external = engineerAllocations.some((item) => item.cross_team); const hours = engineerAllocations.reduce((sum, item) => sum + Number(item.allocated_hours), 0); return `<button type="button" class="person-link" data-open-employee="${attr(engineerId)}"><b>${escapeHtml(engineerId)}${external ? ` · привлечён из ${escapeHtml(engineerAllocations[0].source_team_id)}` : ''}</b><span>${escapeHtml(engineer?.roles?.join(', ') || engineerAllocations[0].role)} · ${number(hours)} ч ↗</span></button>`; }).join('') : empty('Исполнители не назначены.')}`;
  const incoming = task.dependencies.incoming || [];
  const outgoing = task.dependencies.outgoing || [];
  const incomingRows = incoming.map((item) => {
    const title = item.blocking_task_title ? ` · ${escapeHtml(item.blocking_task_title)}` : '';
    const cssClass = item.state === 'HISTORICAL_DONE' ? 'dependency-satisfied' : (item.state === 'MISSING' ? 'dependency-missing' : '');
    const mark = item.state === 'HISTORICAL_DONE' ? '✓ ' : '';
    return `<p class="${cssClass}"><b>${mark}${escapeHtml(item.blocking_task_id)}${title}</b><br><span>${escapeHtml(item.status_message)} · ${escapeHtml(item.dependency_type)}</span></p>`;
  });
  $('#taskDependencies').innerHTML = `<div class="eyebrow">ЗАВИСИМОСТИ</div><h2>Положение в квартальном плане</h2><p class="hint">Показывается сохранённое backend-направление blocking_task_id → blocked_task_id вместе с исходным dependency_type.</p><div class="dependency-grid"><div class="dependency-box"><strong>Блокирующие задачи</strong>${incomingRows.length ? incomingRows.join('') : '<p>Нет</p>'}</div><div class="dependency-box"><strong>Зависимые задачи</strong>${outgoing.length ? outgoing.map((item) => `<p>${escapeHtml(item.blocked_task_id)} · ${escapeHtml(item.dependency_type)}</p>`).join('') : '<p>Нет</p>'}</div></div>`;
  const hours = selectedSprint ? sprintHours(task, selectedSprint) : task.role_requirements;
  const allocationRows = allocations.map((item) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(item.engineer_id)}">${escapeHtml(item.engineer_id)} ↗</button></td><td>${escapeHtml(item.role)}</td><td>${escapeHtml(item.source_team_id)}</td><td>${number(item.allocated_hours)} ч</td><td>${item.cross_team ? 'Да' : 'Нет'}</td><td>${item.capacity_used_share == null ? '—' : `${number(Number(item.capacity_used_share) * 100)}%`}</td></tr>`);
  const roleRows = Object.entries(hours || {}).map(([role, required]) => `<tr><td>${escapeHtml(role)}</td><td>${number(required)} ч</td></tr>`);
  $('#taskHours').innerHTML = `<div class="eyebrow">РАБОЧЕЕ ВРЕМЯ · ${selectedSprint ? `SPRINT ${selectedSprint}` : 'ВСЯ ЗАДАЧА'}</div><h2>Распределение ресурсов</h2><p class="hint">Часы и доля capacity получены от planner API; SP не переводятся в часы.</p>${allocationRows.length ? table(['Инженер', 'Роль', 'Исходная команда', 'Выделено', 'Cross-team', 'Доля bucket'], allocationRows) : empty('Распределений инженеров нет.')}<h3 class="needs-title">Трудозатраты по ролям</h3>${roleRows.length ? table(['Роль', 'Часы'], roleRows) : empty('Часы по ролям не предоставлены.')}`;
  const reasonBox = $('#taskReason');
  const structured = [...task.reasons.map((item) => ({kind: 'Причина', ...item})), ...task.risks.map((item) => ({kind: 'Риск', title: item.code, ...item})), ...task.recommendations.map((item) => ({kind: 'Рекомендация', severity: item.available ? 'INFO' : 'WARNING', ...item}))];
  reasonBox.hidden = !structured.length;
  if (structured.length) reasonBox.innerHTML = `<div class="eyebrow">ОБЪЯСНЕНИЯ BACKEND</div><h2>${escapeHtml(task.summary)}</h2><div class="stack">${structured.map((item) => `<div class="reason-card"><b>${escapeHtml(item.kind)} · ${escapeHtml(item.code)} · ${escapeHtml(item.title || '')}</b><p>${escapeHtml(item.message || '')}</p></div>`).join('')}</div>`;
}

function renderRisks() {
  renderDevelopmentNotice('#risksDevelopmentNotice', 'Страница «Риски» пока не завершена. Отображаемые данные и структура могут измениться.');
  $$('#riskFilters button').forEach((button) => button.classList.toggle('selected', button.dataset.riskFilter === state.riskFilter));
  const rows = data.risks.filter((risk) => state.riskFilter === 'Все' || risk.type === state.riskFilter);
  if (!rows.some((risk) => risk.risk_id === state.riskId)) state.riskId = rows[0]?.risk_id || null;
  $('#riskCount').textContent = `${rows.length}`;
  $('#riskRows').innerHTML = rows.map((risk) => `<tr data-risk-id="${attr(risk.risk_id)}" class="risk-row-${riskClass(risk.type)} ${state.riskId === risk.risk_id ? 'selected' : ''}"><td><b>${escapeHtml(risk.title)}</b></td><td><span class="risk-pill ${riskClass(risk.type)}">${escapeHtml(risk.type)}</span></td><td>${escapeHtml(risk.object)}</td><td>${risk.sprint ? `Sprint ${risk.sprint}` : '—'}</td><td>${escapeHtml(risk.cause)}</td><td>${escapeHtml(risk.effect || 'Не рассчитано')}</td></tr>`).join('');
  if (!rows.length) $('#riskRows').innerHTML = '<tr><td colspan="6">Рисков этого типа нет.</td></tr>';
  const risk = data.risks.find((item) => item.risk_id === state.riskId);
  const detail = $('#riskDetail');
  detail.className = `panel risk-detail${risk ? ` risk-accent-${riskClass(risk.type)}` : ''}`;
  detail.innerHTML = risk ? `<div class="eyebrow">ДЕТАЛИЗАЦИЯ</div><h2>${escapeHtml(risk.title)}</h2><span class="risk-pill ${riskClass(risk.type)}">${escapeHtml(risk.type)}</span><div class="detail-row"><strong>Связанный объект</strong>${escapeHtml(risk.object)}${risk.sprint ? ` · Sprint ${risk.sprint}` : ''}</div><div class="detail-row"><strong>Причина</strong>${escapeHtml(risk.cause)}</div><div class="detail-row"><strong>Возможное последствие</strong>${escapeHtml(risk.effect || 'Backend пока не рассчитывает отдельное последствие.')}</div><div><button class="btn btn-blue" type="button" data-open-task="${attr(risk.task_id)}">Открыть ${escapeHtml(risk.task_id)} ↗</button>${risk.team_id ? `<button class="btn btn-outline" type="button" data-open-team="${attr(risk.team_id)}">Команда ${escapeHtml(risk.team_id)} ↗</button>` : ''}</div>` : '<p>Риски данного типа отсутствуют.</p>';
}

function renderRecalc() {
  renderDevelopmentNotice('#recalcDevelopmentNotice', 'Страница пока не завершена. Отображаемые данные и структура могут измениться.');
  $('#recalcPlaceholder').hidden = false;
  $('#recalcResult').hidden = true;
  $('#runDemo').disabled = true;
  $('#runDemo').textContent = 'Backend пересчёта не подключён';
}

function starSelectSearch(key) {
  return `<div class="star-select-search-wrap"><input type="text" class="star-select-search" data-star-filter-search="${attr(key)}" placeholder="Поиск" autocomplete="off" spellcheck="false"></div>`;
}

function renderStarSelect(targetId, key, value, options, ariaLabel, searchable = false) {
  const target = $(`#${targetId}`);
  if (!target) return;
  const selected = options.find((option) => option.value === value) || options[0];
  const search = searchable ? starSelectSearch(key) : '';
  target.innerHTML = `<details class="star-select" data-star-select="${attr(key)}"><summary aria-label="${attr(ariaLabel)}"><span>${escapeHtml(selected?.label || '')}</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 7.5 10 12.5 15 7.5"/></svg></summary><div class="star-select-menu" role="listbox">${search}${options.map((option) => `<button type="button" role="option" aria-selected="${option.value === selected?.value}" class="star-select-option ${option.value === selected?.value ? 'selected' : ''}" data-star-select-option="${attr(key)}" data-value="${attr(option.value)}" data-search-label="${attr(option.label.toLowerCase())}">${escapeHtml(option.label)}</button>`).join('')}<div class="star-select-empty" hidden>Ничего не найдено</div></div></details>`;
}

function renderStarMultiSelect(targetId, key, values, options, allLabel, ariaLabel, searchable = false) {
  const target = $(`#${targetId}`);
  if (!target) return;
  const selectedValues = new Set(values || []);
  const selectedOptions = options.filter((option) => selectedValues.has(option.value));
  const summary = !selectedOptions.length ? allLabel : selectedOptions.length === 1 ? selectedOptions[0].label : `Выбрано: ${selectedOptions.length}`;
  const allSelected = selectedOptions.length === 0;
  const search = searchable ? starSelectSearch(key) : '';
  const optionButtons = [
    `<button type="button" role="option" aria-selected="${allSelected}" class="star-multi-option ${allSelected ? 'selected' : ''}" data-star-multi-option="${attr(key)}" data-value="all" data-search-label="${attr(allLabel.toLowerCase())}"><span class="star-check" aria-hidden="true">${allSelected ? '<svg viewBox="0 0 16 16"><path d="m3.5 8 2.8 2.8 6.2-6.2"/></svg>' : ''}</span><span>${escapeHtml(allLabel)}</span></button>`,
    ...options.map((option) => {
      const selected = selectedValues.has(option.value);
      return `<button type="button" role="option" aria-selected="${selected}" class="star-multi-option ${selected ? 'selected' : ''}" data-star-multi-option="${attr(key)}" data-value="${attr(option.value)}" data-search-label="${attr(option.label.toLowerCase())}"><span class="star-check" aria-hidden="true">${selected ? '<svg viewBox="0 0 16 16"><path d="m3.5 8 2.8 2.8 6.2-6.2"/></svg>' : ''}</span><span>${escapeHtml(option.label)}</span></button>`;
    }),
  ].join('');
  target.innerHTML = `<details class="star-select star-multiselect" data-star-select="${attr(key)}"><summary aria-label="${attr(ariaLabel)}" title="${attr(selectedOptions.map((option) => option.label).join(', ') || allLabel)}"><span>${escapeHtml(summary)}</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 7.5 10 12.5 15 7.5"/></svg></summary><div class="star-select-menu" role="listbox" aria-multiselectable="true">${search}${optionButtons}<div class="star-select-empty" hidden>Ничего не найдено</div></div></details>`;
}

function filterStarSelectOptions(input) {
  const details = input.closest('.star-select');
  if (!details) return;
  const query = input.value.trim().toLowerCase();
  const options = [...details.querySelectorAll('[data-search-label]')];
  let visible = 0;
  options.forEach((option) => {
    const isAll = option.dataset.value === 'all';
    const matches = !query || isAll || (option.dataset.searchLabel || '').includes(query);
    option.hidden = !matches;
    if (matches && !isAll) visible += 1;
  });
  const emptyState = $('.star-select-empty', details);
  if (emptyState) emptyState.hidden = visible > 0 || !query;
}

function closeStarSelects(except = null) {
  $$('details.star-select[open]').forEach((details) => {
    if (details !== except) details.removeAttribute('open');
  });
}

function engineerTeamCoverage(engineer, teamId) {
  const team = data.starMap?.teams?.find((item) => item.team_id === teamId);
  const teamSkills = new Set((team?.skill_coverage || []).map((item) => item.technology));
  const covered = engineer.skills.filter((skill) => teamSkills.has(skill)).length;
  const total = teamSkills.size;
  return {covered, total, percentage: total ? covered / total * 100 : 0};
}

function busFactorTone(value) {
  const busFactor = Number(value || 0);
  if (busFactor <= 1) return 'critical';
  if (busFactor === 2) return 'warn';
  return 'safe';
}

function busFactorSortOrder(value) {
  const busFactor = Number(value || 0);
  if (busFactor >= 3) return 0;
  if (busFactor === 2) return 1;
  return 2;
}

function sortBusFactorItems(items, labelKey = 'technology') {
  return [...items].sort((left, right) => {
    const toneDiff = busFactorSortOrder(left.team_bus_factor) - busFactorSortOrder(right.team_bus_factor);
    if (toneDiff !== 0) return toneDiff;
    return String(left[labelKey] || '').localeCompare(String(right[labelKey] || ''), 'ru');
  });
}

function normalizeStarMode(rawMode = {}) {
  const technologyMap = new Map();
  (rawMode.technologies || []).forEach((item) => {
    const key = String(item.technology || '').trim();
    if (!key) return;
    const existing = technologyMap.get(key);
    const mergedEngineerIds = [...new Set([...(existing?.engineer_ids || []), ...(item.engineer_ids || [])])];
    const mergedQuarterEngineerIds = [...new Set([...(existing?.quarter_engineer_ids || []), ...(item.quarter_engineer_ids || [])])];
    const mergedHelpers = [...new Map([...(existing?.helpers || []), ...(item.helpers || [])].map((helper) => [helper.engineer_id || `${helper.source_team_id || ''}-${helper.free_hours || ''}`, helper])).values()];
    technologyMap.set(key, {
      ...(existing || {}),
      ...item,
      technology: key,
      engineer_ids: mergedEngineerIds,
      quarter_engineer_ids: mergedQuarterEngineerIds,
      helpers: mergedHelpers,
      team_bus_factor: Math.max(Number(existing?.team_bus_factor || 0), Number(item.team_bus_factor || 0), mergedEngineerIds.length),
    });
  });
  const roleMap = new Map();
  (rawMode.roles || []).forEach((item) => {
    const key = String(item.competency || '').trim();
    if (!key) return;
    const existing = roleMap.get(key);
    roleMap.set(key, {
      ...(existing || {}),
      ...item,
      competency: key,
      team_bus_factor: Math.max(Number(existing?.team_bus_factor || 0), Number(item.team_bus_factor || 0)),
    });
  });
  const technologies = [...technologyMap.values()].sort((left, right) => left.technology.localeCompare(right.technology, 'ru'));
  const roles = [...roleMap.values()].sort((left, right) => left.competency.localeCompare(right.competency, 'ru'));
  const zone_counts = technologies.reduce((result, item) => {
    const value = Number(item.team_bus_factor || 0);
    if (value >= 3) result.stable += 1;
    else if (value === 2) result.limited += 1;
    else result.critical += 1;
    result.total += 1;
    return result;
  }, {critical: 0, limited: 0, stable: 0, total: 0});
  return {...rawMode, technologies, roles, entries: technologies, zone_counts};
}

function renderStar() {
  $$('.subtab').forEach((button) => {
    const active = button.dataset.starTab === state.starTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  $$('.star-view').forEach((element) => element.classList.toggle('active', element.id === ({teams: 'starTeams', skills: 'starSkills', distribution: 'starDistribution'}[state.starTab])));
  const quarterToggle = $('.star-quarter-toggle');
  if (quarterToggle) quarterToggle.hidden = state.starTab !== 'teams';
  if (state.starTab === 'teams') renderStarTeams();
  if (state.starTab === 'skills') renderStarSkills();
  if (state.starTab === 'distribution') renderDistribution();
}

function starTeamMode(team) {
  const key = state.starQuarterOnly ? 'quarter' : 'all';
  const mode = team.competency_modes?.[key];
  if (mode) return normalizeStarMode(mode);
  const technologies = state.starQuarterOnly ? (team.graph_competencies || []) : (team.skill_coverage || []).map((item) => ({...item, team_bus_factor: item.engineer_count || 0}));
  return normalizeStarMode({technologies, roles: [], entries: technologies});
}

function starSkillText(label) {
  const text = String(label || '').trim();
  if (!text) return '';
  const prepared = text.replace(/\//g, '/ ');
  const words = prepared.split(/\s+/).filter(Boolean).flatMap((word) => {
    if (word.length <= 12) return [word];
    const parts = [];
    for (let index = 0; index < word.length; index += 10) parts.push(word.slice(index, index + 10));
    return parts;
  });
  const lines = [];
  let current = '';
  words.forEach((word) => {
    const candidate = current ? `${current} ${word}` : word;
    if (!current || candidate.length <= 12) current = candidate;
    else {
      lines.push(current);
      current = word;
    }
  });
  if (current) lines.push(current);
  const visible = lines.filter(Boolean).slice(0, 5);
  const longest = visible.reduce((max, line) => Math.max(max, line.length), 0);
  const fontSize = longest > 12 ? 10 : longest > 10 ? 11 : 12;
  const lineHeight = fontSize + 2;
  const startY = -((visible.length - 1) * lineHeight) / 2 + 1;
  return `<text class="skill-name" text-anchor="middle" font-size="${fontSize}">${visible.map((line, index) => `<tspan x="0" y="${(startY + index * lineHeight).toFixed(1)}">${escapeHtml(line)}</tspan>`).join('')}</text>`;
}

function starCurvePath(x1, y1, x2, y2, bend = 0) {
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.hypot(dx, dy) || 1;
  const cx = mx - dy / length * bend;
  const cy = my + dx / length * bend;
  return `M ${x1.toFixed(1)} ${y1.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
}

function currentGraphCompetencies(team) {
  const mode = starTeamMode(team);
  if (state.starGraphMode === 'role') {
    return (mode.roles || []).map((item) => ({
      technology: item.competency,
      team_bus_factor: item.team_bus_factor,
      engineer_ids: item.engineer_ids || [],
      role: true
    }));
  }
  return mode.technologies || [];
}

function starGraph(team, engineers) {
  const mode = starTeamMode(team);
  const byId = new Map(engineers.map((engineer) => [engineer.engineer_id, engineer]));
  const query = state.starEngineerSearch.trim().toLowerCase();
  const selectedRoles = new Set(state.starRoleFilters || []);
  const competencies = currentGraphCompetencies(team);
  const filtered = competencies.filter((item) => {
    if (!selectedRoles.size && !query) return true;
    const sourceIds = state.starQuarterOnly ? (item.quarter_engineer_ids || []) : (item.engineer_ids || []);
    return sourceIds.some((engineerId) => {
      const engineer = byId.get(engineerId);
      if (!engineer) return false;
      const bucket = engineer.capacity_buckets.find((entry) => entry.team_id === team.team_id);
      if (selectedRoles.size && !selectedRoles.has(bucket?.role || engineer.roles[0])) return false;
      if (query && !`${engineer.engineer_id} ${engineer.roles.join(' ')}`.toLowerCase().includes(query)) return false;
      return true;
    });
  });
  if (!filtered.length) {
    const scope = state.starQuarterOnly ? 'текущего квартала' : 'команды';
    return empty(`Для выбранных фильтров нет технологий ${scope}.`);
  }

  const width = 2600;
  const height = 2600;
  const cx = width / 2;
  const cy = height / 2;
  const skillRadius = filtered.length <= 10 ? 138 : filtered.length <= 30 ? 128 : 118;
  const spacing = skillRadius * 2 + 34;
  const orbits = [];
  let orbit = 360;
  let totalCapacity = 0;
  while (totalCapacity < filtered.length) {
    const capacity = Math.max(6, Math.floor(Math.PI * 2 * orbit / spacing));
    orbits.push({orbit, capacity});
    totalCapacity += capacity;
    orbit += 270;
  }
  const capacityTotal = orbits.reduce((sum, item) => sum + item.capacity, 0);
  const ringSizes = orbits.map((item) => Math.max(1, Math.floor(filtered.length * item.capacity / capacityTotal)));
  let assigned = ringSizes.reduce((sum, value) => sum + value, 0);
  let cursor = orbits.length - 1;
  while (assigned < filtered.length) {
    ringSizes[cursor] += 1;
    assigned += 1;
    cursor = cursor === 0 ? orbits.length - 1 : cursor - 1;
  }
  while (assigned > filtered.length) {
    if (ringSizes[cursor] > 1) {
      ringSizes[cursor] -= 1;
      assigned -= 1;
    }
    cursor = cursor === 0 ? orbits.length - 1 : cursor - 1;
  }

  const edgeMarkup = [];
  const nodeMarkup = [];
  let offset = 0;
  ringSizes.forEach((size, ringIndex) => {
    const ringOrbit = orbits[ringIndex].orbit;
    const angleOffset = -Math.PI / 2 + (ringIndex % 2 ? Math.PI / Math.max(size, 1) : 0);
    for (let localIndex = 0; localIndex < size; localIndex += 1) {
      const item = filtered[offset + localIndex];
      const angle = angleOffset + localIndex * Math.PI * 2 / size;
      const x = cx + Math.cos(angle) * ringOrbit;
      const y = cy + Math.sin(angle) * ringOrbit;
      const tone = busFactorTone(item.team_bus_factor);
      const selected = state.starSelectedSkill === item.technology ? 'selected' : '';
      const bend = (ringIndex % 2 ? 1 : -1) * (16 + ringIndex * 6);
      edgeMarkup.push(`<path class="star-network-edge ${tone}" d="${starCurvePath(cx, cy, x, y, bend)}"/>`);
      nodeMarkup.push(`<g class="star-skill-node ${tone} ${selected}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})" data-graph-skill="${attr(item.technology)}"><circle r="${skillRadius}"/>${starSkillText(item.technology)}<title>${escapeHtml(item.technology)}</title></g>`);
    }
    offset += size;
  });

  nodeMarkup.push(`<g class="star-team-node" transform="translate(${cx} ${cy})"><circle r="128"/><text text-anchor="middle" y="-9">${escapeHtml(team.team_id)}</text><text class="team-count" text-anchor="middle" y="29">${team.member_count} инженеров</text></g>`);
  return `<div class="star-graph-stage"><svg class="star-network-svg" viewBox="0 0 ${width} ${height}" data-star-graph-width="${width}" data-star-graph-height="${height}" role="img" aria-label="Звёздная карта команды ${attr(team.team_id)}"><g class="star-map-canvas"><g class="star-edge-layer">${edgeMarkup.join('')}</g><g class="star-node-layer">${nodeMarkup.join('')}</g></g></svg><div class="star-graph-controls" aria-label="Масштаб карты"><button type="button" data-star-graph-action="zoom-in" aria-label="Приблизить">+</button><button type="button" data-star-graph-action="zoom-out" aria-label="Отдалить">−</button><button type="button" data-star-graph-action="fit" aria-label="Показать всю карту">⌂</button></div></div>`;
}

function activateStarGraphViewport() {
  const host = $('#teamGraph');
  const svg = $('.star-network-svg', host);
  if (!host || !svg) return;
  const baseWidth = Number(svg.dataset.starGraphWidth || 1800);
  const baseHeight = Number(svg.dataset.starGraphHeight || 1800);
  let view = {x: 0, y: 0, width: baseWidth, height: baseHeight};
  let drag = null;

  const apply = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`);
  const fit = () => {
    view = {x: 0, y: 0, width: baseWidth, height: baseHeight};
    apply();
  };
  const zoomAt = (clientX, clientY, factor) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratioX = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const ratioY = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
    const nextWidth = Math.min(baseWidth * 2.4, Math.max(baseWidth * 0.22, view.width * factor));
    const nextHeight = nextWidth * baseHeight / baseWidth;
    view.x += (view.width - nextWidth) * ratioX;
    view.y += (view.height - nextHeight) * ratioY;
    view.width = nextWidth;
    view.height = nextHeight;
    apply();
  };

  svg.addEventListener('wheel', (event) => {
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 0.86 : 1.16);
  }, {passive: false});

  svg.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.target.closest('[data-graph-skill]')) return;
    drag = {clientX: event.clientX, clientY: event.clientY, x: view.x, y: view.y, moved: false};
    svg.setPointerCapture?.(event.pointerId);
    svg.classList.add('is-panning');
  });
  svg.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dx = event.clientX - drag.clientX;
    const dy = event.clientY - drag.clientY;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    view.x = drag.x - dx * view.width / rect.width;
    view.y = drag.y - dy * view.height / rect.height;
    apply();
  });
  const stopDrag = (event) => {
    if (!drag) return;
    if (drag.moved) {
      state.starGraphPanned = true;
      window.setTimeout(() => { state.starGraphPanned = false; }, 0);
    }
    drag = null;
    svg.releasePointerCapture?.(event.pointerId);
    svg.classList.remove('is-panning');
  };
  svg.addEventListener('pointerup', stopDrag);
  svg.addEventListener('pointercancel', stopDrag);

  $$('.star-graph-controls button', host).forEach((button) => button.addEventListener('click', () => {
    const rect = svg.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    if (button.dataset.starGraphAction === 'zoom-in') zoomAt(centerX, centerY, 0.78);
    if (button.dataset.starGraphAction === 'zoom-out') zoomAt(centerX, centerY, 1.28);
    if (button.dataset.starGraphAction === 'fit') fit();
  }));

  $$('[data-graph-skill]', host).forEach((node) => node.addEventListener('click', (event) => {
    event.stopPropagation();
    const value = node.dataset.graphSkill;
    state.starSelectedSkill = state.starSelectedSkill === value ? null : value;
    $$('.star-skill-node', host).forEach((item) => item.classList.toggle('selected', item.dataset.graphSkill === state.starSelectedSkill));
    const team = (data.starMap?.teams || []).find((item) => item.team_id === state.teamId);
    const engineers = (data.starMap?.engineers || []).filter((engineer) => engineer.team_ids.includes(state.teamId));
    if (team) renderSelectedCompetency(team, engineers);
  }));

  fit();
}

function renderSelectedCompetency(team, engineers) {
  const panel = $('#selectedCompetencyPanel');
  if (!panel) return;
  const mode = starTeamMode(team);
  const competency = currentGraphCompetencies(team).find((item) => item.technology === state.starSelectedSkill);
  if (!competency) {
    panel.hidden = true;
    panel.innerHTML = '';
    return;
  }
  panel.hidden = false;
  const tone = busFactorTone(competency.team_bus_factor);
  const status = tone === 'critical' ? 'Критично' : tone === 'warn' ? 'Риск' : 'Устойчиво';
  const holderIds = state.starQuarterOnly ? (competency.quarter_engineer_ids || []) : (competency.engineer_ids || []);
  const holders = holderIds.map((engineerId) => engineers.find((engineer) => engineer.engineer_id === engineerId)).filter(Boolean);
  const holderCards = holders.length ? holders.map((engineer) => {
    const bucket = engineer.capacity_buckets.find((item) => item.team_id === team.team_id);
    return `<div class="competency-person"><b>${escapeHtml(engineer.engineer_id)}</b><span>${escapeHtml(bucket?.role || engineer.roles.join(', ') || 'Роль не указана')}</span></div>`;
  }).join('') : '<p class="hint">В выбранном режиме локальных носителей нет.</p>';
  const helpers = competency.helpers || [];
  const helperCards = helpers.length ? helpers.map((helper) => `<div class="competency-person"><b>${escapeHtml(helper.engineer_id)}</b><span>${escapeHtml(helper.source_team_id || helper.team_ids?.join(', ') || 'Другая команда')}${helper.free_hours != null ? ` · ${number(helper.free_hours)} ч свободно` : ''}</span></div>`).join('') : '<p class="hint">Готовый резерв в других командах не найден.</p>';
  panel.innerHTML = `<div class="selected-competency-head"><div><div class="eyebrow">ВЫБРАННАЯ КОМПЕТЕНЦИЯ</div><h2>${escapeHtml(competency.technology)}</h2></div><div class="selected-competency-bf"><span>Bus Factor</span><b>${competency.team_bus_factor}</b><em class="${tone}">${status}</em></div></div><div class="competency-detail-section"><h3>Кто покрывает компетенцию</h3><div class="competency-people">${holderCards}</div></div><div class="competency-detail-section"><h3>Резерв в других командах</h3><div class="competency-people">${helperCards}</div></div>`;
}

function renderRoleBusFactors(team) {
  const mode = starTeamMode(team);
  const roles = sortBusFactorItems(mode.roles || [], 'competency');
  $('#teamVulnerable').innerHTML = roles.length ? `<div class="bf-role-list">${roles.map((role) => {
    const tone = busFactorTone(role.team_bus_factor);
    return `<div><b>${escapeHtml(role.competency)}</b><i class="${tone}"></i><em>BF ${role.team_bus_factor}</em></div>`;
  }).join('')}</div>` : '<p class="hint">Роли для выбранного режима не найдены.</p>';
}

function renderTechnologyBusFactors(team) {
  const mode = starTeamMode(team);
  const technologies = sortBusFactorItems(mode.technologies || [], 'technology');
  const target = $('#teamTechnologyBusFactor');
  if (!target) return;
  target.innerHTML = technologies.length ? `<div class="bf-role-list">${technologies.map((item) => {
    const tone = busFactorTone(item.team_bus_factor);
    return `<div><b>${escapeHtml(item.technology)}</b><i class="${tone}"></i><em>BF ${item.team_bus_factor}</em></div>`;
  }).join('')}</div>` : '<p class="hint">Технологии для выбранного режима не найдены.</p>';
}

function renderStarTeamProfile(team, engineers) {
  const mode = starTeamMode(team);
  const technologies = mode.technologies || [];
  const counts = technologies.reduce((result, item) => {
    const value = Number(item.team_bus_factor ?? item.engineer_count ?? 0);
    if (value >= 3) result.stable += 1;
    else if (value === 2) result.limited += 1;
    else result.critical += 1;
    return result;
  }, {critical: 0, limited: 0, stable: 0, total: technologies.length});
  const unique = technologies.filter((item) => Number(item.team_bus_factor ?? item.engineer_count ?? 0) === 1).map((item) => item.technology);
  const scope = state.starQuarterOnly ? 'в текущем квартале' : 'в команде в целом';
  let conclusion;
  if (!counts.total) conclusion = state.starQuarterOnly ? 'Для текущего квартала компетенции не определены.' : 'Для команды компетенции не определены.';
  else if (counts.critical) conclusion = `${counts.critical} критических зон ${scope}. Основной приоритет — резерв и снижение зависимости от единичных носителей.`;
  else if (counts.limited) conclusion = `Критических зон нет, но ${counts.limited} зон ${scope} имеют ограниченное покрытие.`;
  else conclusion = `Все рассматриваемые зоны ${scope} имеют устойчивое покрытие.`;

  $('#starTeamProfile').innerHTML = `<div class="panel-heading compact"><div><h2>${escapeHtml(team.team_id)}</h2></div></div><div class="team-profile-grid"><div><span>Инженеров</span><b>${team.member_count}</b></div><div><span>Технологий</span><b>${counts.total}</b></div><div class="zone-critical"><span>Критических зон</span><b>${counts.critical}</b></div><div class="zone-limited"><span>Ограниченных зон</span><b>${counts.limited}</b></div><div class="zone-stable"><span>Устойчивых зон</span><b>${counts.stable}</b></div></div>`;

  const details = $('#starTeamProfileDetails');
  if (details) details.innerHTML = `<div class="eyebrow team-profile-details-title">Единичные носители технологий</div>${unique.length ? `<div class="profile-unique"><div class="skill-tags compact">${unique.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('')}</div></div>` : '<p class="hint">Единичных носителей не найдено.</p>'}<div class="team-conclusion"><b>Вывод</b><p>${escapeHtml(conclusion)}</p></div>`;

  const baseTeam = teamBy(team.team_id);
  const active = data.tasks.filter((task) => task.assigned_team_id === team.team_id && inQuarter(task));
  $('#teamLifecycleActions').innerHTML = `${archiveState(baseTeam)}${baseTeam.archived ? '' : `<div class="entity-actions"><button class="btn btn-danger" type="button" data-archive-entity="team" data-archive-id="${attr(team.team_id)}" data-current-plan-reference="${active.length > 0 || engineers.length > 0}">Удалить</button></div>`}`;
}

function renderStarTeams() {
  const teams = data.starMap?.teams || [];
  const team = teams.find((item) => item.team_id === state.teamId) || teams[0];
  if (!team) return;
  state.teamId = team.team_id;
  const engineers = (data.starMap?.engineers || []).filter((engineer) => engineer.team_ids.includes(team.team_id));
  const mode = starTeamMode(team);
  renderStarSelect('starTeamSelect', 'team', team.team_id, teams.map((item) => ({value: item.team_id, label: item.team_id})), 'Выбрать команду', true);
  $('#teamGraphTitle').textContent = `Звёздная карта: ${team.team_id}`;
  const graphToggle = $('#starGraphMode');
  if (graphToggle) graphToggle.innerHTML = `<button type="button" class="chip ${state.starGraphMode === 'technology' ? 'selected' : ''}" data-graph-mode="technology">Технологии</button><button type="button" class="chip ${state.starGraphMode === 'role' ? 'selected' : ''}" data-graph-mode="role">Роли</button>`;
  const quarterToggle = $('#starQuarterOnly');
  if (quarterToggle) quarterToggle.checked = state.starQuarterOnly;
  const roles = (mode.roles || []).map((item) => item.competency);
  state.starRoleFilters = state.starRoleFilters.filter((role) => roles.includes(role));
  $('#starRoleFilters').innerHTML = roles.length ? roles.map((role) => `<label class="star-role-option"><input type="checkbox" data-star-role-filter="${attr(role)}" ${state.starRoleFilters.includes(role) ? 'checked' : ''}><span>${escapeHtml(role)}</span></label>`).join('') : '<span class="muted">Роли не найдены</span>';

  const competencies = mode.technologies || [];
  const competencyValues = competencies.map((item) => item.technology);
  if (!state.starSelectedSkill || !competencyValues.includes(state.starSelectedSkill)) state.starSelectedSkill = competencyValues[0] || null;

  const searchInput = $('#starEngineerSearch');
  if (searchInput && searchInput.value !== state.starEngineerSearch) searchInput.value = state.starEngineerSearch;
  $('#teamGraph').innerHTML = starGraph(team, engineers);
  activateStarGraphViewport();
  renderStarTeamProfile(team, engineers);
  renderSelectedCompetency(team, engineers);
  renderRoleBusFactors(team);
  renderTechnologyBusFactors(team);
}

function renderStarSkills(keepOpenKey = null, keepScrollTop = 0, keepSearch = '') {
  const engineers = data.starMap?.engineers || [];
  const allSkills = data.starMap?.skills || [];
  const allRoles = data.starMap?.roles || [];
  const selectedTeams = state.skillTeams || [];
  const selectedTechs = state.skillTechs || [];
  renderStarMultiSelect('skillTeam', 'skill-team', selectedTeams, (data.starMap?.teams || []).map((team) => ({value: team.team_id, label: team.team_id})), 'Все команды', 'Фильтр по команде', true);
  renderStarSelect('skillRole', 'skill-role', state.skillRole, [{value: 'all', label: 'Все роли'}, ...allRoles.map((role) => ({value: role, label: role}))], 'Фильтр по роли', true);
  renderStarMultiSelect('skillTech', 'skill-tech', selectedTechs, allSkills.map((skill) => ({value: skill, label: skill})), 'Все технологии', 'Фильтр по технологии', true);
  const scopedEngineers = engineers.filter((engineer) => {
    if (selectedTeams.length && !engineer.team_ids.some((teamId) => selectedTeams.includes(teamId))) return false;
    if (state.skillRole !== 'all') {
      const matchesRole = !selectedTeams.length
        ? engineer.roles.includes(state.skillRole)
        : engineer.capacity_buckets.some((bucket) => selectedTeams.includes(bucket.team_id) && bucket.role === state.skillRole);
      if (!matchesRole) return false;
    }
    if (selectedTechs.length && !selectedTechs.every((skill) => engineer.skills.includes(skill))) return false;
    return true;
  });
  const coverageValue = (engineer) => {
    const visibleTeams = !selectedTeams.length ? engineer.team_ids : engineer.team_ids.filter((teamId) => selectedTeams.includes(teamId));
    const values = visibleTeams.map((teamId) => engineerTeamCoverage(engineer, teamId).percentage);
    return values.length ? Math.max(...values) : 0;
  };
  const sortValue = (engineer, key) => ({roles: engineer.roles.length, skills: engineer.skills.length, teams: engineer.team_ids.length, coverage: coverageValue(engineer)})[key] ?? 0;
  const sortDirection = (key) => ({roles: state.skillRoleCountSort, skills: state.skillCountSort, teams: state.skillTeamCountSort, coverage: state.skillCoverageSort})[key] || 'none';
  const activeSorts = state.skillSortOrder.filter((key) => sortDirection(key) !== 'none');
  if (activeSorts.length) {
    scopedEngineers.sort((left, right) => {
      for (const key of activeSorts) {
        const delta = sortValue(left, key) - sortValue(right, key);
        if (delta !== 0) return sortDirection(key) === 'asc' ? delta : -delta;
      }
      return left.engineer_id.localeCompare(right.engineer_id, 'ru');
    });
  }
  const hasMultiRoleEngineers = engineers.some((engineer) => engineer.roles.length > 1);
  $('#skillsScope').textContent = scopedEngineers.length ? `${scopedEngineers.length} специалистов. Покрытие показывает долю технологий команды, которыми владеет инженер.` : 'Для выбранных фильтров нет специалистов.';
  const sortIcon = (key, direction, label) => {
    const icon = direction === 'asc' ? '<path d="M5 12 10 7l5 5"/>' : direction === 'desc' ? '<path d="M5 8l5 5 5-5"/>' : '<path d="M4 6h12M4 10h12M4 14h12"/>';
    return `<button type="button" class="table-sort ${direction !== 'none' ? 'active' : ''}" data-skill-sort="${attr(key)}" aria-label="${attr(label)}"><svg viewBox="0 0 20 20" aria-hidden="true">${icon}</svg></button>`;
  };
  const rows = scopedEngineers.map((engineer) => {
    const visibleTeams = !selectedTeams.length ? engineer.team_ids : engineer.team_ids.filter((teamId) => selectedTeams.includes(teamId));
    const coverageRows = visibleTeams.map((teamId) => {
      const coverage = engineerTeamCoverage(engineer, teamId);
      return `<div class="engineer-coverage-row"><span><b>${escapeHtml(teamId)}</b><small>${coverage.covered} из ${coverage.total} технологий</small></span><strong>${number(coverage.percentage)}%</strong></div>`;
    }).join('');
    const visibleRoles = !selectedTeams.length ? engineer.roles : [...new Set(engineer.capacity_buckets.filter((bucket) => selectedTeams.includes(bucket.team_id)).map((bucket) => bucket.role))];
    return `<tr><td><button type="button" class="table-button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td><div class="role-tags">${visibleRoles.map((role) => `<span>${escapeHtml(role)}</span>`).join('') || '<span>—</span>'}</div></td><td><div class="skill-tags compact">${engineer.skills.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></td><td><div class="team-tags">${engineer.team_ids.map((teamId) => `<span>${escapeHtml(teamId)}</span>`).join('')}</div></td><td><div class="engineer-coverage">${coverageRows || '<span class="muted">Нет данных</span>'}</div></td></tr>`;
  }).join('');
  const roleHeading = hasMultiRoleEngineers ? `<span class="sortable-heading"><span>Роль</span>${sortIcon('roles', state.skillRoleCountSort, 'Сортировать по количеству ролей')}</span>` : 'Роль';
  $('#skillsTable').innerHTML = scopedEngineers.length ? `<div class="table-scroll"><table class="data-table engineer-skills-table"><thead><tr><th>Инженер</th><th>${roleHeading}</th><th><span class="sortable-heading"><span>Навыки</span>${sortIcon('skills', state.skillCountSort, 'Сортировать по количеству навыков')}</span></th><th><span class="sortable-heading"><span>Команды</span>${sortIcon('teams', state.skillTeamCountSort, 'Сортировать по количеству команд')}</span></th><th><span class="sortable-heading"><span>Покрытие компетенций</span>${sortIcon('coverage', state.skillCoverageSort, 'Сортировать по покрытию компетенций')}</span></th></tr></thead><tbody>${rows}</tbody></table></div>` : empty('Специалисты для выбранных фильтров не найдены.');
  if (keepOpenKey) {
    const details = $(`.star-select[data-star-select="${keepOpenKey}"]`);
    if (details) {
      details.setAttribute('open', '');
      const menu = $('.star-select-menu', details);
      if (menu) menu.scrollTop = keepScrollTop;
      const search = $('[data-star-filter-search]', details);
      if (search) {
        search.value = keepSearch;
        filterStarSelectOptions(search);
        search.focus({preventScroll: true});
      }
    }
  }
}

function renderDistribution() {
  const engineers = (data.starMap?.engineers || []).filter((engineer) => engineer.team_ids.length > 1);
  $('#distributionTable').innerHTML = engineers.length ? table(['Сотрудник', 'Команды / capacity', 'Роль', 'Свободно'], engineers.map((engineer) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td>${engineer.capacity_buckets.map((bucket) => `${escapeHtml(bucket.team_id)}: ${number(bucket.capacity_hours)} ч`).join('<br>')}</td><td>${escapeHtml(engineer.roles.join(', '))}</td><td>${number(engineer.free_hours)} ч</td></tr>`)) : empty('Сотрудников с несколькими командами нет.');
  const flows = data.starMap?.cross_team_flows || [];
  $('#crossTeamFlows').innerHTML = flows.length ? `<div class="cross-team-list">${flows.map((flow) => `<div class="cross-team-item"><b>${escapeHtml(flow.source_team_id)} → ${escapeHtml(flow.target_team_id)}</b><span>${number(flow.hours)} ч · ${flow.engineer_ids.length} инженер(а) · ${flow.task_ids.length} задач(а)</span><div class="muted">${escapeHtml(flow.engineer_ids.join(', '))}</div></div>`).join('')}</div>` : empty('В текущем спринте межкомандной помощи нет.');
}

function openEmployee(engineerId) {
  const engineer = data.starMap?.engineers?.find((item) => item.engineer_id === engineerId);
  if (!engineer) return;
  const sprint = Number(data.starMap?.current_sprint || currentSprint());
  const lifecycle = engineerBy(engineerId) || {};
  const vulnerabilities = (data.starMap?.teams || []).flatMap((team) => (team.bus_factor?.risks || []).filter((risk) => engineer.team_ids.includes(team.team_id) && risk.team_bus_factor <= 2 && (risk.competency_type === 'skill' ? engineer.skills.includes(risk.competency) : engineer.roles.includes(risk.competency))).map((risk) => ({...risk, team_id: team.team_id})));
  $('#employeeContent').innerHTML = `<div class="eyebrow">КАРТОЧКА СОТРУДНИКА</div><h2>${escapeHtml(engineer.engineer_id)}</h2><p>${escapeHtml(engineer.roles.join(', '))} · Grade: ${escapeHtml(engineer.grade || '—')}</p><div class="metric-grid" style="margin:20px 0"><div class="metric"><span>Команд</span><b>${engineer.team_ids.length}</b></div><div class="metric"><span>Ресурс bucket</span><b>${number(engineer.capacity_hours)} ч</b></div><div class="metric"><span>Занято в Sprint ${sprint}</span><b>${number(engineer.used_hours)} ч</b></div><div class="metric"><span>Свободно</span><b>${number(engineer.free_hours)} ч</b></div></div><div class="drawer-section"><h3>Команды и capacity bucket</h3>${engineer.capacity_buckets.map((bucket) => `<button class="profile-link" data-employee-team="${attr(bucket.team_id)}" type="button">${escapeHtml(bucket.team_id)} · ${escapeHtml(bucket.role)} · capacity_rate ${number(bucket.capacity_rate)} · свободно ${number(bucket.free_hours)} ч ↗</button>`).join('')}</div><div class="drawer-section"><h3>Технологии</h3><div class="skill-tags">${engineer.skills.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></div><div class="drawer-section"><h3>Назначения Sprint ${sprint}</h3>${engineer.allocations.length ? engineer.allocations.map((item) => `<p>${escapeHtml(item.task_id)} · ${escapeHtml(item.role)} · ${number(item.allocated_hours)} ч${item.cross_team ? ` · ${escapeHtml(item.source_team_id)} → ${escapeHtml(item.assigned_task_team_id)}` : ''}</p>`).join('') : '<p>Назначений нет.</p>'}</div><div class="drawer-section"><h3>Резерв специалистов</h3>${engineer.reserve_candidates.length ? `<div class="reserve-list">${engineer.reserve_candidates.map((candidate) => `<button type="button" class="reserve-person" data-open-employee="${attr(candidate.engineer_id)}"><span><b>${escapeHtml(candidate.engineer_id)}</b><small>${escapeHtml(candidate.roles.join(', '))} · ${escapeHtml(candidate.team_ids.join(', '))}</small></span><span>${candidate.shared_skill_count} общих навыка · ${number(candidate.free_hours)} ч свободно</span></button>`).join('')}</div>` : '<p>Подходящий резерв по роли не найден.</p>'}</div><div class="drawer-section"><h3>Bus Factor</h3>${vulnerabilities.length ? `<div class="employee-bf-list">${vulnerabilities.slice(0, 8).map((item) => `<span class="${busFactorTone(item.team_bus_factor)}"><b>${escapeHtml(item.competency)}</b><small>${escapeHtml(item.team_id)} · BF ${item.team_bus_factor}</small></span>`).join('')}</div>` : '<p>Уязвимые компетенции этого инженера в его командах не найдены.</p>'}</div>${archiveState(lifecycle)}${lifecycle.archived ? '' : `<div class="entity-actions"><button class="btn btn-danger" type="button" data-archive-entity="engineer" data-archive-id="${attr(engineer.engineer_id)}" data-current-plan-reference="${engineer.quarter_allocations.length > 0}">Удалить</button></div>`}`;
  $('#employeeDrawer').classList.add('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'false');
}

function closeEmployee() {
  $('#employeeDrawer').classList.remove('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'true');
}

function bindEvents() {
  $('#chooseFact').addEventListener('click', () => $('#factInput').click());
  $('#factInput').addEventListener('change', (event) => { const file = event.target.files?.[0]; $('#uploadFileStatus').textContent = file ? `Выбран файл: ${file.name}. Backend пересчёта не подключён, файл не обработан.` : 'Файл не выбран'; });
  $('#archiveForm').addEventListener('submit', (event) => { event.preventDefault(); submitArchive(); });
  $('#archiveReason').addEventListener('change', updateArchiveConfirmation);
  $('#archiveReasonText').addEventListener('input', updateArchiveConfirmation);
  $('#archiveDialog').addEventListener('click', (event) => { if (event.target === $('#archiveDialog')) closeArchiveModal(); });
  document.addEventListener('click', (event) => {
    const starMultiOption = event.target.closest('[data-star-multi-option]');
    if (starMultiOption) {
      const key = starMultiOption.dataset.starMultiOption;
      const value = starMultiOption.dataset.value;
      const menu = starMultiOption.closest('.star-select-menu');
      const scrollTop = menu?.scrollTop || 0;
      const searchValue = $('[data-star-filter-search]', menu)?.value || '';
      const stateKey = key === 'skill-team' ? 'skillTeams' : key === 'skill-tech' ? 'skillTechs' : null;
      if (!stateKey) return;
      if (value === 'all') state[stateKey] = [];
      else {
        const current = new Set(state[stateKey] || []);
        if (current.has(value)) current.delete(value); else current.add(value);
        state[stateKey] = [...current];
      }
      renderStarSkills(key, scrollTop, searchValue);
      return;
    }
    const starSelectOption = event.target.closest('[data-star-select-option]');
    if (starSelectOption) {
      const key = starSelectOption.dataset.starSelectOption;
      const value = starSelectOption.dataset.value;
      closeStarSelects();
      if (key === 'team') {
        state.teamId = value;
        state.starEngineerId = null;
        state.starEngineerSearch = '';
        state.starRoleFilters = [];
        state.starCompetencyFilter = 'all';
        state.starSelectedSkill = null;
        renderStarTeams();
        return;
      }
      if (key === 'star-competency-filter') {
        state.starCompetencyFilter = value;
        state.starSelectedSkill = null;
        renderStarTeams();
        return;
      }
      if (key === 'skill-role') state.skillRole = value;
      renderStarSkills();
      return;
    }
    const starSelectSummary = event.target.closest('.star-select > summary');
    if (starSelectSummary) {
      const details = starSelectSummary.parentElement;
      closeStarSelects(details);
      setTimeout(() => {
        if (details.open) $('[data-star-filter-search]', details)?.focus({preventScroll: true});
      }, 0);
      return;
    }
    if (!event.target.closest('.star-select')) closeStarSelects();
    const skillSort = event.target.closest('[data-skill-sort]');
    if (skillSort) {
      const key = skillSort.dataset.skillSort;
      const stateKey = {roles: 'skillRoleCountSort', skills: 'skillCountSort', teams: 'skillTeamCountSort', coverage: 'skillCoverageSort'}[key];
      if (!stateKey) return;
      const nextSort = (value) => value === 'none' ? 'desc' : value === 'desc' ? 'asc' : 'none';
      const next = nextSort(state[stateKey]);
      state[stateKey] = next;
      state.skillSortOrder = state.skillSortOrder.filter((item) => item !== key);
      if (next !== 'none') state.skillSortOrder.push(key);
      renderStarSkills();
      return;
    }
    const graphSkill = event.target.closest('[data-graph-skill]');
    if (graphSkill) {
      if (state.starGraphPanned) return;
      const value = graphSkill.dataset.graphSkill;
      state.starSelectedSkill = state.starSelectedSkill === value ? null : value;
      $$('.star-skill-node').forEach((node) => node.classList.toggle('selected', node.dataset.graphSkill === state.starSelectedSkill));
      const team = (data.starMap?.teams || []).find((item) => item.team_id === state.teamId);
      const engineers = (data.starMap?.engineers || []).filter((engineer) => engineer.team_ids.includes(state.teamId));
      if (team) renderSelectedCompetency(team, engineers);
      return;
    }
    if (event.target.closest('[data-close-archive]')) { closeArchiveModal(); return; }
    const archive = event.target.closest('[data-archive-entity]');
    if (archive) { openArchiveModal(archive.dataset.archiveEntity, archive.dataset.archiveId, archive.dataset.currentPlanReference === 'true'); return; }
    if (event.target.closest('[data-close-drawer]')) { closeEmployee(); return; }
    if (event.target.closest('[data-task-back]')) { show(state.taskReturnPage || 'current-plan'); return; }
    const nav = event.target.closest('button[data-page]'); if (nav) { show(nav.dataset.page); return; }
    const homeSprint = event.target.closest('[data-home-sprint]'); if (homeSprint) { const sprint = Number(homeSprint.dataset.homeSprint); show('current-plan'); $(`[data-sprint-column="${sprint}"]`)?.scrollIntoView({behavior: 'smooth', inline: 'center', block: 'nearest'}); return; }
    const planTask = event.target.closest('[data-plan-task]'); if (planTask) { gotoTask(planTask.dataset.planTask); return; }
    const task = event.target.closest('[data-open-task]'); if (task) { gotoTask(task.dataset.openTask); return; }
    const risk = event.target.closest('[data-open-risk]'); if (risk) { state.riskId = risk.dataset.openRisk; state.riskFilter = 'Все'; show('risks'); return; }
    const filter = event.target.closest('[data-risk-filter]'); if (filter) { state.riskFilter = filter.dataset.riskFilter; renderRisks(); return; }
    const riskRow = event.target.closest('[data-risk-id]'); if (riskRow) { state.riskId = riskRow.dataset.riskId; renderRisks(); return; }
    const team = event.target.closest('[data-open-team]'); if (team) { gotoTeam(team.dataset.openTeam); return; }
    const employee = event.target.closest('[data-open-employee]'); if (employee) { gotoEmployee(employee.dataset.openEmployee); return; }
    const backToTeam = event.target.closest('[data-employee-team]'); if (backToTeam) { closeEmployee(); gotoTeam(backToTeam.dataset.employeeTeam); return; }
    const graphMode = event.target.closest('[data-graph-mode]'); if (graphMode) { state.starGraphMode = graphMode.dataset.graphMode; state.starSelectedSkill = null; renderStarTeams(); return; }
      const starTab = event.target.closest('[data-star-tab]'); if (starTab) { state.starTab = starTab.dataset.starTab; renderStar(); return; }
  });
  document.addEventListener('input', (event) => {
    const search = event.target.closest('[data-star-filter-search]');
    if (search) { filterStarSelectOptions(search); return; }
    if (event.target.id === 'starEngineerSearch') {
      state.starEngineerSearch = event.target.value;
      renderStarTeams();
    }
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'taskSprintSelect') { state.taskSprint = Number(event.target.value); renderTasks(); return; }
    if (event.target.id === 'starQuarterOnly') {
      state.starQuarterOnly = event.target.checked;
      state.starSelectedSkill = null;
      state.starCompetencyFilter = 'all';
      state.starRoleFilters = [];
      state.starEngineerId = null;
      renderStarTeams();
      return;
    }
    const role = event.target.closest('[data-star-role-filter]');
    if (role) {
      const value = role.dataset.starRoleFilter;
      const selected = new Set(state.starRoleFilters || []);
      if (role.checked) selected.add(value); else selected.delete(value);
      state.starRoleFilters = [...selected];
      renderStarTeams();
    }
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeArchiveModal(); closeStarSelects(); closeEmployee(); } });
}

function hydrate(payload) {
  data.plan = payload.plan;
  data.summary = payload.summary;
  data.tasks = payload.tasks || [];
  data.initiatives = payload.initiatives || [];
  data.risks = payload.risks || [];
  data.teams = payload.teams || [];
  data.engineers = payload.engineers || [];
  data.starMap = payload.star_map || null;
  data.kpis = payload.kpis || null;
  data.features = payload.features || {};
  data.ready = true;
  state.loading = false;
  state.error = null;
  state.taskId = data.tasks.find((task) => task.status === 'PLANNED')?.task_id || data.tasks[0]?.task_id || null;
  state.teamId = data.starMap?.teams?.[0]?.team_id || data.teams[0]?.team_id || null;
  state.starEngineerId = null;
  state.riskId = data.risks[0]?.risk_id || null;
}

async function initialize() {
  bindEvents();
  show('home');
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
