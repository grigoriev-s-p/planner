'use strict';

const state = {
  page: 'home', selectedSprint: 1, taskId: null, taskSprint: null,
  teamId: null, engineerId: null, starTab: 'teams', riskId: null,
  riskFilter: 'Все', skillTeam: 'all', skillRole: 'all', skillTech: 'all',
  selectedPlanTask: null, loading: true, error: null,
};

const data = {
  plan: null, summary: {}, tasks: [], initiatives: [], risks: [], teams: [],
  engineers: [], features: {}, ready: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? '—').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const attr = escapeHtml;
const number = (value) => value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toLocaleString('ru-RU', {maximumFractionDigits: 2});
const teamBy = (id) => data.teams.find((team) => team.team_id === id) || {team_id: id, name: id || 'Команда не указана', sp_by_sprint: {}};
const engineerBy = (id) => data.engineers.find((engineer) => engineer.engineer_id === id);
const taskBy = (id) => data.tasks.find((task) => task.task_id === id);
const currentSprint = () => Number(data.plan?.quarter?.current_sprint || 1);
const sprintBy = (sprint) => data.plan?.sprints?.find((item) => item.sprint_number === sprint);
const taskSprints = (task) => task?.planned_sprints || [];
const inQuarter = (task) => task?.status === 'PLANNED' && taskSprints(task).length > 0;
const portion = (task, sprint) => Number(task?.sp_load_by_sprint?.[String(sprint)] || 0);
const sprintHours = (task, sprint) => task?.role_hours_by_sprint?.[String(sprint)] || null;
const predecessors = (taskId) => taskBy(taskId)?.dependencies?.incoming?.map((item) => item.blocking_task_id).filter(Boolean) || [];
const successors = (taskId) => taskBy(taskId)?.dependencies?.outgoing?.map((item) => item.blocked_task_id).filter(Boolean) || [];
const taskLabel = (task) => `${task.task_id} — ${task.title} · ${task.initiative_id || 'без PRODF'}`;
const taskMatches = (task, query) => `${task.task_id} ${task.title} ${task.initiative_id || ''}`.toLowerCase().includes(query.toLowerCase());
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

function setDataMode(kind, title, message) {
  const ribbon = $('#dataModeRibbon');
  ribbon.dataset.mode = kind;
  ribbon.innerHTML = `<span class="ribbon-dot"></span><b>${escapeHtml(title)}</b><span>${escapeHtml(message)}</span>`;
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
  ({home: renderHome, planning: renderPlanning, tasks: renderTasks, risks: renderRisks, recalc: renderRecalc, star: renderStar})[page]?.();
}

function renderUnavailable() {
  const message = state.error ? `Backend недоступен: ${state.error}` : 'Загружаем данные planner…';
  if ($('#homeSprints')) $('#homeSprints').innerHTML = empty(message);
  if ($('#homeTasks')) $('#homeTasks').innerHTML = empty(message);
  if ($('#homeRisks')) $('#homeRisks').innerHTML = empty(message);
}

function gotoTask(taskId) {
  if (!taskBy(taskId)) return;
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
  const active = data.tasks.filter((task) => String(task.source_status || '').toLowerCase() === 'inprogress').slice(0, 3);
  $('#homeTasks').innerHTML = active.length ? active.map((task) => `<button class="list-button" data-open-task="${attr(task.task_id)}" type="button"><span><strong>${escapeHtml(task.task_id)} · ${escapeHtml(task.title)}</strong><small>${escapeHtml(task.assigned_team_id)} · ${escapeHtml(sprintLabel(task))}</small></span><span class="end-label">В работе ↗</span></button>`).join('') : empty('В исходных данных нет задач со статусом InProgress.');
  $('#homeRisks').innerHTML = data.risks.slice(0, 3).map((risk) => `<button class="list-button" type="button" data-open-risk="${attr(risk.risk_id)}"><span><strong>${escapeHtml(risk.title)}</strong><small>${escapeHtml(risk.cause)}</small></span><span class="end-label">${escapeHtml(risk.type)} ↗</span></button>`).join('') || empty('Рассчитанных рисков нет.');
}

function renderPlanning() {
  const totalInitiatives = Number(data.summary.total_initial_initiatives || 0);
  const completed = Number(data.summary.completed_initiatives || 0);
  const forecast = totalInitiatives ? Math.round(completed / totalInitiatives * 100) : 0;
  $('#forecastPct').innerHTML = `${forecast}<span>%</span>`;
  $('#forecastDetails').textContent = `${completed} из ${totalInitiatives} инициатив полностью входят в текущий квартальный план.`;
  $('#forecastRing').style.setProperty('--angle', `${forecast}%`);
  $('#currentSprintName').textContent = `Sprint ${currentSprint()}`;
  $('#planVersion').textContent = data.plan.quarter.plan_version;
  $('#finishedSprints').textContent = `Sprint ${currentSprint()} · исходный baseline`;
  $('#sprintBoard').innerHTML = Array.from({length: 6}, (_, index) => {
    const sprint = index + 1;
    const starts = data.tasks.filter((task) => inQuarter(task) && taskSprints(task)[0] === sprint);
    const continuing = data.tasks.filter((task) => inQuarter(task) && taskSprints(task)[0] < sprint && taskSprints(task).includes(sprint));
    const total = (sprintBy(sprint)?.teams || []).reduce((sum, team) => sum + Number(team.used_sp || 0), 0);
    const cards = starts.map((task) => {
      const related = state.selectedPlanTask && [...predecessors(state.selectedPlanTask), ...successors(state.selectedPlanTask)].includes(task.task_id);
      return `<button class="sprint-card ${task.task_id === state.selectedPlanTask ? 'focus' : ''} ${related ? 'related' : ''} ${state.selectedPlanTask && !related && task.task_id !== state.selectedPlanTask ? 'dim' : ''}" type="button" data-plan-task="${attr(task.task_id)}"><span class="card-top">${escapeHtml(task.task_id)}<span>${number(portion(task, sprint))} SP</span></span><span class="card-title">${escapeHtml(task.title)}</span><span class="card-meta">${escapeHtml(task.assigned_team_id)} · ${escapeHtml(task.initiative_id || 'без PRODF')}</span><span class="card-meta">${escapeHtml(sprintLabel(task))}${task.multi_sprint ? ' · несколько спринтов' : ''}</span><span class="card-chip">${escapeHtml(task.priority_label)} · ${escapeHtml(task.status_label)}</span></button>`;
    }).join('');
    const continuations = continuing.map((task) => `<button class="sprint-continuation" type="button" data-plan-task="${attr(task.task_id)}"><b>↳ Продолжение ${escapeHtml(task.task_id)}</b><span>${escapeHtml(task.title)} · ${number(portion(task, sprint))} SP · ${number(task.hours_by_sprint?.[String(sprint)])} ч</span></button>`).join('');
    return `<div class="sprint-column ${sprint === state.selectedSprint ? 'selected' : ''}" data-sprint-column="${sprint}"><button class="sprint-column-header" data-select-sprint="${sprint}" type="button"><b>Sprint ${sprint}</b><span>${escapeHtml(sprintStatus(sprint))}</span><span class="sp-small">Запланировано: ${number(total)} SP</span></button>${cards}${continuations}${!starts.length && !continuing.length ? '<div class="muted">Нет задач</div>' : ''}</div>`;
  }).join('');

  if (state.selectedPlanTask && taskBy(state.selectedPlanTask)) {
    const task = taskBy(state.selectedPlanTask);
    $('#planDependencyHint').innerHTML = `<b>${escapeHtml(task.task_id)} — ${escapeHtml(task.title)}</b><br>Блокирующие задачи: ${escapeHtml(predecessors(task.task_id).join(', ') || 'нет')}; зависимые задачи: ${escapeHtml(successors(task.task_id).join(', ') || 'нет')}. <button type="button" class="table-button" data-open-task="${attr(task.task_id)}">Открыть карточку ↗</button>`;
  } else {
    $('#planDependencyHint').textContent = 'Выберите карточку задачи, чтобы увидеть сохранённые blocking_task_id → blocked_task_id.';
  }

  $('#initiativeList').innerHTML = data.initiatives.map((initiative) => `<details class="initiative-item"><summary>${escapeHtml(initiative.initiative_id)} · ${initiative.planned_task_count}/${initiative.task_count} задач · ${initiative.completed_in_quarter ? 'закрывается в квартале' : 'не закрывается полностью'}</summary><div class="initiative-content">${initiative.task_ids.map((taskId) => { const task = taskBy(taskId); return `<button class="list-button" data-open-task="${attr(taskId)}" type="button"><span><strong>${escapeHtml(taskId)} · ${escapeHtml(task?.title)}</strong><small>${escapeHtml(sprintLabel(task))} · ${escapeHtml(task?.status_label)}</small></span><span class="end-label">Детали ↗</span></button>`; }).join('')}${initiative.summary ? `<p class="hint">${escapeHtml(initiative.summary)}</p>` : ''}</div></details>`).join('') || empty('Инициативы не найдены.');
  $('#resourceTeam').innerHTML = data.teams.map((team) => `<option value="${attr(team.team_id)}" ${team.team_id === state.teamId ? 'selected' : ''}>${escapeHtml(team.name)}</option>`).join('');
  renderPlanningResource();

  const selected = state.selectedSprint;
  $('#selectedSprintTag').textContent = `Sprint ${selected}`;
  const active = data.tasks.filter((task) => inQuarter(task) && taskSprints(task).includes(selected));
  $('#selectedSprintTasks').innerHTML = active.length ? active.map((task) => `<button class="list-button" type="button" data-open-task="${attr(task.task_id)}"><span><strong>${escapeHtml(task.task_id)} · ${escapeHtml(task.title)}</strong><small>${escapeHtml(sprintLabel(task))} · ${escapeHtml(task.assigned_team_id)} · ${number(portion(task, selected))} SP в спринте · ${escapeHtml(task.priority_label)}</small></span><span class="end-label">Открыть ↗</span></button>`).join('') : empty('На этот спринт задач нет.');
  $('#sprintFact').innerHTML = '<div class="empty-state"><b>Факт спринта не загружен</b><p>Replanning backend ещё не реализован. Прогнозные SP не выдаются за фактически завершённые.</p></div>';
  const outside = data.tasks.filter((task) => ['MOVED', 'UNPLANNABLE'].includes(task.status));
  $('#outsideTasks').innerHTML = outside.length ? outside.map((task) => `<button class="list-button" type="button" data-open-task="${attr(task.task_id)}"><span><strong>${escapeHtml(task.task_id)} · ${escapeHtml(task.title)}</strong><small>${escapeHtml(task.summary)} · ${escapeHtml(task.reasons?.[0]?.title || 'Причина не предоставлена')}</small></span><span class="end-label">Детали ↗</span></button>`).join('') : empty('Все планируемые задачи вошли в квартал.');
}

function renderPlanningResource() {
  const team = teamBy(state.teamId);
  const sprint = state.selectedSprint;
  const resource = teamSprintResource(team.team_id, sprint);
  $('#planSpPanel').innerHTML = `<div class="resource-box"><h3>Story Points · ${escapeHtml(team.name)}</h3><div class="resource-line"><span>Средняя историческая производительность</span><b>${number(team.average_velocity_sp)} SP</b></div><div class="resource-line"><span>Доступно: × ${number(team.focus_factor)}</span><b>${number(resource.capacity)} SP</b></div><div class="resource-line"><span>Запланировано</span><b>${number(resource.used)} SP</b></div><div class="resource-line"><span>Остаток</span><b class="warn-blue">${number(resource.remaining)} SP</b></div>${meter(resource.used, resource.capacity)}</div>`;
  const roles = (sprintBy(sprint)?.role_resources || []).filter((item) => item.team_id === team.team_id);
  $('#planRolePanel').innerHTML = `<div class="resource-box"><h3>Роли · рабочие часы</h3>${roles.length ? roles.map((item) => `<div class="resource-line"><span>${escapeHtml(item.role)}</span><b>требуется ${number(item.required_hours)} ч · доступность: ${item.available_hours == null ? 'не сохранена в результате' : `${number(item.available_hours)} ч`}</b></div>`).join('') : '<p class="hint">В этом спринте нет распределённых часов команды.</p>'}</div>`;
  $('#planResourceNote').textContent = 'SP и часы — отдельные показатели. Интерфейс показывает значения, полученные от planner API.';
}

function renderTaskOptions(query = '') {
  const q = query.trim();
  const options = data.tasks.filter((task) => !q || taskMatches(task, q));
  const box = $('#taskOptions');
  box.innerHTML = options.length ? options.map((task) => `<button class="combo-option ${task.task_id === state.taskId ? 'active' : ''}" type="button" role="option" aria-selected="${task.task_id === state.taskId}" data-task-option="${attr(task.task_id)}"><b>${escapeHtml(task.task_id)} — ${escapeHtml(task.title)}</b><span>${escapeHtml(task.initiative_id || 'без PRODF')} · ${escapeHtml(task.assigned_team_id)} · ${escapeHtml(sprintLabel(task))}</span></button>`).join('') : '<div class="combo-empty">Задачи по такому запросу не найдены.</div>';
  box.hidden = false;
  $('#taskSearch').setAttribute('aria-expanded', 'true');
}

function closeTaskOptions() {
  const box = $('#taskOptions');
  if (box) {
    box.hidden = true;
    $('#taskSearch').setAttribute('aria-expanded', 'false');
  }
}

function selectTaskOption(taskId) {
  const task = taskBy(taskId);
  if (!task) return;
  state.taskId = taskId;
  state.taskSprint = null;
  $('#taskSearch').value = taskLabel(task);
  closeTaskOptions();
  renderTasks();
}

function renderTasks() {
  const task = taskBy(state.taskId) || data.tasks[0];
  if (!task) {
    $('#taskSummary').innerHTML = empty('Задач нет.');
    return;
  }
  state.taskId = task.task_id;
  $('#taskSearch').value = taskLabel(task);
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
  $('#taskDescription').innerHTML = `<div class="eyebrow">ОПИСАНИЕ</div><h2>${escapeHtml(task.title)}</h2><p style="margin-top:15px">${escapeHtml(task.description || 'Описание отсутствует.')}</p><div class="detail-grid"><div><span>Задача</span><b>${escapeHtml(task.task_id)}</b></div><div><span>Инициатива</span><b>${escapeHtml(task.initiative_id || '—')}</b></div><div><span>SP</span><b>${number(task.story_points)}</b></div><div><span>Rung</span><b>${number(task.rung)}</b></div><div><span>Статус</span><b>${escapeHtml(task.status_label)}</b></div><div><span>Спринт</span><b>${escapeHtml(sprintLabel(task))}</b></div></div>${riskMatches.length ? `<h3 class="needs-title">Связанные риски</h3>${riskMatches.map((risk) => `<p>${escapeHtml(risk.title)}: ${escapeHtml(risk.cause)}</p>`).join('')}` : ''}`;
  const allocations = task.allocations.filter((item) => selectedSprint == null || item.sprint === selectedSprint);
  const people = [...new Set(allocations.map((item) => item.engineer_id))];
  $('#taskPeople').innerHTML = `<div class="eyebrow">КОМАНДА И СПЕЦИАЛИСТЫ</div><h2>Исполнители</h2><button type="button" class="profile-link" data-open-team="${attr(task.assigned_team_id)}">Команда: ${escapeHtml(task.assigned_team_id)} ↗</button>${people.length ? people.map((engineerId) => { const engineer = engineerBy(engineerId); const engineerAllocations = allocations.filter((item) => item.engineer_id === engineerId); const external = engineerAllocations.some((item) => item.cross_team); const hours = engineerAllocations.reduce((sum, item) => sum + Number(item.allocated_hours), 0); return `<button type="button" class="person-link" data-open-employee="${attr(engineerId)}"><b>${escapeHtml(engineerId)}${external ? ` · привлечён из ${escapeHtml(engineerAllocations[0].source_team_id)}` : ''}</b><span>${escapeHtml(engineer?.roles?.join(', ') || engineerAllocations[0].role)} · ${number(hours)} ч ↗</span></button>`; }).join('') : empty('Исполнители не назначены.')}`;
  const incoming = task.dependencies.incoming || [];
  const outgoing = task.dependencies.outgoing || [];
  $('#taskDependencies').innerHTML = `<div class="eyebrow">ЗАВИСИМОСТИ</div><h2>Положение в квартальном плане</h2><p class="hint">Показывается сохранённое backend-направление blocking_task_id → blocked_task_id вместе с исходным dependency_type.</p><div class="dependency-grid"><div class="dependency-box"><strong>Блокирующие задачи</strong>${incoming.length ? incoming.map((item) => `<p>${escapeHtml(item.blocking_task_id)} · ${escapeHtml(item.dependency_type)}</p>`).join('') : '<p>Нет</p>'}</div><div class="dependency-box"><strong>Зависимые задачи</strong>${outgoing.length ? outgoing.map((item) => `<p>${escapeHtml(item.blocked_task_id)} · ${escapeHtml(item.dependency_type)}</p>`).join('') : '<p>Нет</p>'}</div></div>`;
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

function renderStarTeams() {
  const team = teamBy(state.teamId);
  const engineers = teamEngineers(team.team_id);
  const sprint = currentSprint();
  const sourceUsed = Number(team.source_used_hours_by_sprint?.[String(sprint)] || 0);
  const memberCapacity = Number(team.member_capacity_hours || 0);
  const resource = teamSprintResource(team.team_id, sprint);
  $('#starTeamSelect').innerHTML = data.teams.map((item) => `<option value="${attr(item.team_id)}" ${item.team_id === team.team_id ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('');
  const metrics = [['Сотрудников', team.member_ids?.length || 0], ['Занято у участников', `${number(sourceUsed)} ч`], ['Свободно', `${number(Math.max(memberCapacity - sourceUsed, 0))} ч`], [`SP Sprint ${sprint}`, `${number(resource.used)} / ${number(resource.capacity)}`], ['Технологий', team.technologies?.length || 0], ['Bus Factor', 'Не рассчитан']];
  $('#teamMetrics').innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join('');
  $('#teamMembers').innerHTML = engineers.length ? table(['Сотрудник', 'Роли', 'Grade', 'Ресурс', 'Занято', 'Свободно'], engineers.map((engineer) => `<tr><td><button type="button" class="table-button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td>${escapeHtml(engineer.roles.join(', '))}</td><td>${escapeHtml(engineer.grade)}</td><td>${number(engineer.capacity_hours)} ч</td><td>${number(engineer.used_hours_by_sprint[String(sprint)])} ч</td><td>${number(engineer.free_hours_by_sprint[String(sprint)])} ч</td></tr>`)) : empty('В команде нет инженеров.');
  $('#teamWorkload').innerHTML = `<div class="forecast-number" style="font-size:52px">${memberCapacity ? Math.round(sourceUsed / memberCapacity * 100) : 0}%</div>${meter(sourceUsed, memberCapacity)}<div class="resource-line"><span>Суммарный ресурс bucket</span><b>${number(memberCapacity)} ч</b></div><div class="resource-line"><span>Использовано из bucket команды</span><b>${number(sourceUsed)} ч</b></div><div class="resource-line"><span>Работа задач назначенной команды</span><b>${number(team.task_work_hours_by_sprint?.[String(sprint)])} ч</b></div><div class="resource-line"><span>SP команды</span><b>${number(resource.used)} / ${number(resource.capacity)} SP</b></div>`;
  const active = data.tasks.filter((task) => task.assigned_team_id === team.team_id && inQuarter(task));
  $('#teamTasks').innerHTML = active.length ? table(['Задача', 'Инициатива', 'Период', 'Статус'], active.map((task) => `<tr><td>${tButton(task)}</td><td>${escapeHtml(task.initiative_id || '—')}</td><td>${escapeHtml(sprintLabel(task))}</td><td>${badge(task)}</td></tr>`)) : empty('Текущих задач нет.');
  $('#busScope').textContent = team.name;
  $('#busTable').innerHTML = empty('Bus Factor backend ещё не реализован. Технологии показаны без искусственной оценки кадрового риска.');
}

function renderStarSkills() {
  const allSkills = [...new Set(data.engineers.flatMap((engineer) => engineer.skills))].sort();
  const allRoles = [...new Set(data.engineers.flatMap((engineer) => engineer.roles))].sort();
  $('#skillTeam').innerHTML = `<option value="all">Все команды</option>${data.teams.map((team) => `<option value="${attr(team.team_id)}" ${state.skillTeam === team.team_id ? 'selected' : ''}>${escapeHtml(team.name)}</option>`).join('')}`;
  $('#skillRole').innerHTML = `<option value="all">Все роли</option>${allRoles.map((role) => `<option value="${attr(role)}" ${state.skillRole === role ? 'selected' : ''}>${escapeHtml(role)}</option>`).join('')}`;
  $('#skillTech').innerHTML = `<option value="all">Все технологии</option>${allSkills.map((skill) => `<option value="${attr(skill)}" ${state.skillTech === skill ? 'selected' : ''}>${escapeHtml(skill)}</option>`).join('')}`;
  $('#criticalOnly').checked = false;
  $('#criticalOnly').disabled = true;
  $('#heatScope').textContent = 'Bus Factor не рассчитан; фильтр критических технологий недоступен.';
  const scope = state.skillTeam === 'all' ? data.engineers : teamEngineers(state.skillTeam);
  const columns = allSkills.filter((skill) => state.skillTech === 'all' || skill === state.skillTech);
  const subset = scope.filter((engineer) => state.skillRole === 'all' || engineer.roles.includes(state.skillRole));
  $('#heatmap').innerHTML = subset.length && columns.length ? table(['Инженер', ...columns], subset.map((engineer) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button><div class="muted">${escapeHtml(engineer.roles.join(', '))}</div></td>${columns.map((skill) => `<td>${engineer.skills.includes(skill) ? '<span class="heat-yes">Есть</span>' : '<span class="heat-no">—</span>'}</td>`).join('')}</tr>`), 'heatmap-table') : empty('Для выбранных фильтров сотрудников или технологий нет.');
}

function renderDistribution() {
  const sprint = currentSprint();
  $('#distributionTable').innerHTML = data.engineers.length ? table(['Сотрудник', 'Команды / bucket', 'Роли', 'Ресурс', 'Занято', 'Свободно'], data.engineers.map((engineer) => `<tr><td><button class="table-button" type="button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td>${engineer.capacity_buckets.map((bucket) => `${escapeHtml(bucket.team_id)}: ${number(bucket.capacity_hours)} ч`).join('<br>')}</td><td>${escapeHtml(engineer.roles.join(', '))}</td><td>${number(engineer.capacity_hours)} ч</td><td>${number(engineer.used_hours_by_sprint[String(sprint)])} ч</td><td>${number(engineer.free_hours_by_sprint[String(sprint)])} ч</td></tr>`)) : empty('Инженеры не найдены.');
}

function openEmployee(engineerId) {
  const engineer = engineerBy(engineerId);
  if (!engineer) return;
  const sprint = currentSprint();
  const allocations = engineer.allocations.filter((item) => item.sprint === sprint);
  $('#employeeContent').innerHTML = `<div class="eyebrow">КАРТОЧКА СОТРУДНИКА</div><h2>${escapeHtml(engineer.engineer_id)}</h2><p>${escapeHtml(engineer.roles.join(', '))} · Grade: ${escapeHtml(engineer.grade)}</p><div class="metric-grid" style="margin:20px 0"><div class="metric"><span>Команд</span><b>${engineer.team_ids.length}</b></div><div class="metric"><span>Ресурс bucket</span><b>${number(engineer.capacity_hours)} ч</b></div><div class="metric"><span>Занято в Sprint ${sprint}</span><b>${number(engineer.used_hours_by_sprint[String(sprint)])} ч</b></div><div class="metric"><span>Свободно</span><b>${number(engineer.free_hours_by_sprint[String(sprint)])} ч</b></div></div><div class="drawer-section"><h3>Команды и capacity bucket</h3>${engineer.capacity_buckets.map((bucket) => `<button class="profile-link" data-employee-team="${attr(bucket.team_id)}" type="button">${escapeHtml(bucket.team_id)} · capacity_rate ${number(bucket.capacity_rate)} · ${number(bucket.capacity_hours)} ч ↗</button>`).join('')}</div><div class="drawer-section"><h3>Технологии</h3><div class="skill-tags">${engineer.skills.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></div><div class="drawer-section"><h3>Назначения Sprint ${sprint}</h3>${allocations.length ? allocations.map((item) => `<p>${escapeHtml(item.task_id)} · ${escapeHtml(item.role)} · ${number(item.allocated_hours)} ч${item.cross_team ? ` · помогает ${escapeHtml(item.assigned_task_team_id)}` : ''}</p>`).join('') : '<p>Назначений нет.</p>'}</div><div class="drawer-section"><h3>Bus Factor</h3><p>Backend ещё не рассчитывает Bus Factor.</p></div>`;
  $('#employeeDrawer').classList.add('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'false');
}

function closeEmployee() {
  $('#employeeDrawer').classList.remove('open');
  $('#employeeDrawer').setAttribute('aria-hidden', 'true');
}

function bindEvents() {
  $('#resourceTeam').addEventListener('change', (event) => { state.teamId = event.target.value; renderPlanningResource(); });
  $('#taskSearch').addEventListener('focus', (event) => renderTaskOptions(taskBy(state.taskId) && event.target.value === taskLabel(taskBy(state.taskId)) ? '' : event.target.value));
  $('#taskSearch').addEventListener('input', (event) => renderTaskOptions(event.target.value));
  $('#taskSearch').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { const task = data.tasks.find((item) => taskMatches(item, event.target.value)); if (task) { event.preventDefault(); selectTaskOption(task.task_id); } }
    if (event.key === 'Escape') closeTaskOptions();
  });
  $('#taskComboToggle').addEventListener('click', () => { if ($('#taskOptions').hidden) renderTaskOptions(''); else closeTaskOptions(); });
  $('#starTeamSelect').addEventListener('change', (event) => { state.teamId = event.target.value; renderStarTeams(); });
  $('#skillTeam').addEventListener('change', (event) => { state.skillTeam = event.target.value; renderStarSkills(); });
  $('#skillRole').addEventListener('change', (event) => { state.skillRole = event.target.value; renderStarSkills(); });
  $('#skillTech').addEventListener('change', (event) => { state.skillTech = event.target.value; renderStarSkills(); });
  $('#chooseFact').addEventListener('click', () => $('#factInput').click());
  $('#factInput').addEventListener('change', (event) => { const file = event.target.files?.[0]; $('#uploadFileStatus').textContent = file ? `Выбран файл: ${file.name}. Backend пересчёта не подключён, файл не обработан.` : 'Файл не выбран'; });
  document.addEventListener('click', (event) => {
    const taskOption = event.target.closest('[data-task-option]'); if (taskOption) { selectTaskOption(taskOption.dataset.taskOption); return; }
    if (!event.target.closest('#taskCombobox')) closeTaskOptions();
    if (event.target.closest('[data-close-drawer]')) { closeEmployee(); return; }
    const nav = event.target.closest('button[data-page]'); if (nav) { show(nav.dataset.page); return; }
    const homeSprint = event.target.closest('[data-home-sprint]'); if (homeSprint) { state.selectedSprint = Number(homeSprint.dataset.homeSprint); show('planning'); $(`[data-sprint-column="${state.selectedSprint}"]`)?.scrollIntoView({behavior: 'smooth', inline: 'center', block: 'nearest'}); return; }
    const selectSprint = event.target.closest('[data-select-sprint]'); if (selectSprint) { state.selectedSprint = Number(selectSprint.dataset.selectSprint); state.selectedPlanTask = null; renderPlanning(); return; }
    const planTask = event.target.closest('[data-plan-task]'); if (planTask) { const taskId = planTask.dataset.planTask; if (state.selectedPlanTask === taskId) gotoTask(taskId); else { state.selectedPlanTask = taskId; renderPlanning(); } return; }
    const task = event.target.closest('[data-open-task]'); if (task) { gotoTask(task.dataset.openTask); return; }
    const risk = event.target.closest('[data-open-risk]'); if (risk) { state.riskId = risk.dataset.openRisk; state.riskFilter = 'Все'; show('risks'); return; }
    const filter = event.target.closest('[data-risk-filter]'); if (filter) { state.riskFilter = filter.dataset.riskFilter; renderRisks(); return; }
    const riskRow = event.target.closest('[data-risk-id]'); if (riskRow) { state.riskId = riskRow.dataset.riskId; renderRisks(); return; }
    const team = event.target.closest('[data-open-team]'); if (team) { gotoTeam(team.dataset.openTeam); return; }
    const employee = event.target.closest('[data-open-employee]'); if (employee) { gotoEmployee(employee.dataset.openEmployee); return; }
    const backToTeam = event.target.closest('[data-employee-team]'); if (backToTeam) { closeEmployee(); gotoTeam(backToTeam.dataset.employeeTeam); return; }
    const starTab = event.target.closest('[data-star-tab]'); if (starTab) { state.starTab = starTab.dataset.starTab; renderStar(); return; }
  });
  document.addEventListener('change', (event) => { if (event.target.id === 'taskSprintSelect') { state.taskSprint = Number(event.target.value); renderTasks(); } });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeTaskOptions(); closeEmployee(); } });
}

function hydrate(payload) {
  data.plan = payload.plan;
  data.summary = payload.summary;
  data.tasks = payload.tasks || [];
  data.initiatives = payload.initiatives || [];
  data.risks = payload.risks || [];
  data.teams = payload.teams || [];
  data.engineers = payload.engineers || [];
  data.features = payload.features || {};
  data.ready = true;
  state.loading = false;
  state.selectedSprint = currentSprint();
  state.taskId = data.tasks.find((task) => task.status === 'PLANNED')?.task_id || data.tasks[0]?.task_id || null;
  state.teamId = data.teams[0]?.team_id || null;
  state.riskId = data.risks[0]?.risk_id || null;
  $('.header-quarter').textContent = `${data.plan.quarter.plan_version} · 6 спринтов`;
  setDataMode('real', 'РЕАЛЬНЫЕ ДАННЫЕ', 'Processed dataset → quarter planner → structured API.');
}

async function initialize() {
  bindEvents();
  show('home');
  try {
    hydrate(await window.postcodeApi.getDashboard());
    renderPage(state.page);
  } catch (error) {
    state.loading = false;
    state.error = error?.message || 'неизвестная ошибка';
    setDataMode('error', 'BACKEND НЕДОСТУПЕН', state.error);
    renderUnavailable();
  }
}

initialize();
