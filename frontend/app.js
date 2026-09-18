'use strict';

const state = {
  page: 'home', selectedSprint: 1, taskId: null, taskSprint: null,
  teamId: null, engineerId: null, starTab: 'teams', riskId: null,
  riskFilter: 'Все', skillTeams: [], skillRole: 'all', skillTechs: [], skillCoverageSort: 'none', skillTeamCountSort: 'none', skillCountSort: 'none', skillRoleCountSort: 'none', skillSortOrder: [],
  selectedPlanTask: null, loading: true, error: null,
};

const data = {
  plan: null, summary: {}, tasks: [], initiatives: [], risks: [], teams: [],
  engineers: [], starMap: null, features: {}, ready: false,
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

function radarChart(coverage, memberCount) {
  const items = coverage.slice(0, 6);
  if (items.length < 3) return empty('Недостаточно технологий для построения карты.');
  const width = 700;
  const height = 530;
  const cx = 330;
  const cy = 258;
  const radius = 180;
  const angle = (index) => -Math.PI / 2 + index * Math.PI * 2 / items.length;
  const point = (index, scale) => {
    const a = angle(index);
    return [cx + Math.cos(a) * radius * scale, cy + Math.sin(a) * radius * scale];
  };
  const points = (scale) => items.map((_, index) => point(index, scale).map((value) => value.toFixed(1)).join(',')).join(' ');
  const grids = [0.2, 0.4, 0.6, 0.8, 1].map((scale) => `<polygon points="${points(scale)}" class="radar-grid"/>`).join('');
  const axes = items.map((_, index) => {
    const [x, y] = point(index, 1);
    return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="radar-axis"/>`;
  }).join('');
  const normalizedCoverage = (item) => Math.min(1, Number(item.coverage_pct || 0) / 100);
  const valuePoints = items.map((item, index) => point(index, normalizedCoverage(item)).map((value) => value.toFixed(1)).join(',')).join(' ');
  const labels = items.map((item, index) => {
    const [x, y] = point(index, 1.18);
    const anchor = x < cx - 20 ? 'end' : x > cx + 20 ? 'start' : 'middle';
    const label = item.technology.length > 21 ? `${item.technology.slice(0, 20)}…` : item.technology;
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${anchor}" class="radar-label"><title>${escapeHtml(item.technology)} · ${number(item.coverage_pct)}%</title>${escapeHtml(label)}</text>`;
  }).join('');
  const values = items.map((item, index) => {
    const [x, y] = point(index, normalizedCoverage(item));
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5.5" class="radar-dot"><title>${escapeHtml(item.technology)}: ${item.engineer_count} из ${memberCount} инженеров · ${number(item.coverage_pct)}%</title></circle>`;
  }).join('');
  return `<div class="radar-chart"><svg class="radar-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Покрытие технологий команды">${grids}${axes}<polygon points="${valuePoints}" class="radar-area"/>${values}${labels}</svg></div>`;
}

function renderStarTeams() {
  const starTeam = data.starMap?.teams?.find((team) => team.team_id === state.teamId) || data.starMap?.teams?.[0];
  if (!starTeam) return;
  const team = teamBy(starTeam.team_id);
  const engineers = (data.starMap?.engineers || []).filter((engineer) => engineer.team_ids.includes(starTeam.team_id));
  renderStarSelect(
    'starTeamSelect',
    'team',
    starTeam.team_id,
    (data.starMap?.teams || []).map((item) => ({value: item.team_id, label: item.team_id})),
    'Выбрать команду',
  );
  const utilization = starTeam.capacity_hours > 0 ? Math.round(starTeam.used_hours / starTeam.capacity_hours * 100) : 0;
  const metrics = [['Сотрудников', starTeam.member_count], ['Технологий', starTeam.technology_count], ['Свободно', `${number(starTeam.free_hours)} ч`], ['Загрузка', `${utilization}%`], ['Bus Factor', 'Не рассчитан']];
  $('#teamMetrics').innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join('');
  const visibleSkills = (starTeam.skill_coverage || []).slice(0, 6);
  $('#teamRadar').innerHTML = radarChart(visibleSkills, starTeam.member_count);
  $('#teamTechLegend').innerHTML = visibleSkills.length ? visibleSkills.map((item) => `<span><b title="${attr(item.technology)}">${escapeHtml(item.technology)}</b><em>${number(item.coverage_pct)}% · ${item.engineer_count} чел.</em></span>`).join('') : empty('Технологии команды не найдены.');
  $('#teamRoles').innerHTML = starTeam.role_coverage?.length ? table(['Роль', 'Людей', 'Ресурс', 'Свободно'], starTeam.role_coverage.map((item) => `<tr><td>${escapeHtml(item.role)}</td><td>${item.engineer_count}</td><td>${number(item.capacity_hours)} ч</td><td>${number(item.free_hours)} ч</td></tr>`), 'role-coverage-table') : empty('Роли команды не найдены.');
  $('#teamMembers').innerHTML = engineers.length ? table(['Сотрудник', 'Роль', 'Свободно'], engineers.map((engineer) => {
    const bucket = engineer.capacity_buckets.find((item) => item.team_id === starTeam.team_id);
    return `<tr><td><button type="button" class="table-button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td>${escapeHtml(bucket?.role || engineer.roles.join(', '))}</td><td>${number(bucket?.free_hours)} ч</td></tr>`;
  }), 'team-members-table') : empty('В команде нет инженеров.');
  $('#teamCrossTeam').innerHTML = starTeam.cross_team_links?.length ? `<div class="cross-team-list">${starTeam.cross_team_links.map((item) => `<div class="cross-team-item"><span class="cross-direction ${item.direction}">${item.direction === 'outgoing' ? 'Отдаёт ресурс' : 'Получает ресурс'}</span><b>${escapeHtml(item.source_team_id)} → ${escapeHtml(item.target_team_id)}</b><span>${number(item.hours)} ч · ${item.engineer_ids.length} инженер(а)</span></div>`).join('')}</div>` : empty('В текущем спринте межкомандных назначений нет.');
  $('#busScope').textContent = team.name;
  $('#busTable').innerHTML = empty('Bus Factor backend ещё не реализован. Технологии показаны без искусственной оценки кадрового риска.');
}

function renderStarSkills(keepOpenKey = null, keepScrollTop = 0, keepSearch = '') {
  const engineers = data.starMap?.engineers || [];
  const allSkills = data.starMap?.skills || [];
  const allRoles = data.starMap?.roles || [];
  const selectedTeams = state.skillTeams || [];
  const selectedTechs = state.skillTechs || [];
  renderStarMultiSelect(
    'skillTeam',
    'skill-team',
    selectedTeams,
    (data.starMap?.teams || []).map((team) => ({value: team.team_id, label: team.team_id})),
    'Все команды',
    'Фильтр по команде',
    true,
  );
  renderStarSelect(
    'skillRole',
    'skill-role',
    state.skillRole,
    [{value: 'all', label: 'Все роли'}, ...allRoles.map((role) => ({value: role, label: role}))],
    'Фильтр по роли',
    true,
  );
  renderStarMultiSelect(
    'skillTech',
    'skill-tech',
    selectedTechs,
    allSkills.map((skill) => ({value: skill, label: skill})),
    'Все технологии',
    'Фильтр по технологии',
    true,
  );
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
  const sortValue = (engineer, key) => ({
    roles: engineer.roles.length,
    skills: engineer.skills.length,
    teams: engineer.team_ids.length,
    coverage: coverageValue(engineer),
  })[key] ?? 0;
  const sortDirection = (key) => ({
    roles: state.skillRoleCountSort,
    skills: state.skillCountSort,
    teams: state.skillTeamCountSort,
    coverage: state.skillCoverageSort,
  })[key] || 'none';
  const compareByEngineerId = (left, right) => left.engineer_id.localeCompare(right.engineer_id, 'ru');
  const activeSorts = state.skillSortOrder.filter((key) => sortDirection(key) !== 'none');
  if (activeSorts.length) {
    scopedEngineers.sort((left, right) => {
      for (const key of activeSorts) {
        const delta = sortValue(left, key) - sortValue(right, key);
        if (delta !== 0) return sortDirection(key) === 'asc' ? delta : -delta;
      }
      return compareByEngineerId(left, right);
    });
  }
  const hasMultiRoleEngineers = engineers.some((engineer) => engineer.roles.length > 1);
  $('#skillsScope').textContent = scopedEngineers.length
    ? `${scopedEngineers.length} специалистов. Покрытие показывает долю технологий команды, которыми владеет инженер.`
    : 'Для выбранных фильтров нет специалистов.';
  const sortIcon = (key, direction, label) => {
    const icon = direction === 'asc'
      ? '<path d="M5 12 10 7l5 5"/>'
      : direction === 'desc'
        ? '<path d="M5 8l5 5 5-5"/>'
        : '<path d="M4 6h12M4 10h12M4 14h12"/>';
    const title = direction === 'asc' ? 'По возрастанию' : direction === 'desc' ? 'По убыванию' : 'Сортировать';
    return `<button type="button" class="table-sort ${direction !== 'none' ? 'active' : ''}" data-skill-sort="${attr(key)}" aria-label="${attr(label)}" title="${title}"><svg viewBox="0 0 20 20" aria-hidden="true">${icon}</svg></button>`;
  };
  const rows = scopedEngineers.map((engineer) => {
    const visibleTeams = !selectedTeams.length ? engineer.team_ids : engineer.team_ids.filter((teamId) => selectedTeams.includes(teamId));
    const coverageRows = visibleTeams.map((teamId) => {
      const coverage = engineerTeamCoverage(engineer, teamId);
      return `<div class="engineer-coverage-row"><span><b>${escapeHtml(teamId)}</b><small>${coverage.covered} из ${coverage.total} технологий</small></span><strong>${number(coverage.percentage)}%</strong></div>`;
    }).join('');
    const visibleRoles = !selectedTeams.length
      ? engineer.roles
      : [...new Set(engineer.capacity_buckets.filter((bucket) => selectedTeams.includes(bucket.team_id)).map((bucket) => bucket.role))];
    return `<tr><td><button type="button" class="table-button" data-open-employee="${attr(engineer.engineer_id)}">${escapeHtml(engineer.engineer_id)} ↗</button></td><td><div class="role-tags">${visibleRoles.map((role) => `<span>${escapeHtml(role)}</span>`).join('') || '<span>—</span>'}</div></td><td><div class="skill-tags compact">${engineer.skills.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></td><td><div class="team-tags">${engineer.team_ids.map((teamId) => `<span>${escapeHtml(teamId)}</span>`).join('')}</div></td><td><div class="engineer-coverage">${coverageRows || '<span class="muted">Нет данных</span>'}</div></td></tr>`;
  }).join('');
  const roleHeading = hasMultiRoleEngineers
    ? `<span class="sortable-heading"><span>Роль</span>${sortIcon('roles', state.skillRoleCountSort, 'Сортировать по количеству ролей')}</span>`
    : 'Роль';
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
  $('#employeeContent').innerHTML = `<div class="eyebrow">КАРТОЧКА СОТРУДНИКА</div><h2>${escapeHtml(engineer.engineer_id)}</h2><p>${escapeHtml(engineer.roles.join(', '))} · Grade: ${escapeHtml(engineer.grade)}</p><div class="metric-grid" style="margin:20px 0"><div class="metric"><span>Команд</span><b>${engineer.team_ids.length}</b></div><div class="metric"><span>Ресурс bucket</span><b>${number(engineer.capacity_hours)} ч</b></div><div class="metric"><span>Занято в Sprint ${sprint}</span><b>${number(engineer.used_hours)} ч</b></div><div class="metric"><span>Свободно</span><b>${number(engineer.free_hours)} ч</b></div></div><div class="drawer-section"><h3>Команды и capacity bucket</h3>${engineer.capacity_buckets.map((bucket) => `<button class="profile-link" data-employee-team="${attr(bucket.team_id)}" type="button">${escapeHtml(bucket.team_id)} · ${escapeHtml(bucket.role)} · capacity_rate ${number(bucket.capacity_rate)} · свободно ${number(bucket.free_hours)} ч ↗</button>`).join('')}</div><div class="drawer-section"><h3>Технологии</h3><div class="skill-tags">${engineer.skills.map((skill) => `<span>${escapeHtml(skill)}</span>`).join('') || '<span>Нет данных</span>'}</div></div><div class="drawer-section"><h3>Назначения Sprint ${sprint}</h3>${engineer.allocations.length ? engineer.allocations.map((item) => `<p>${escapeHtml(item.task_id)} · ${escapeHtml(item.role)} · ${number(item.allocated_hours)} ч${item.cross_team ? ` · ${escapeHtml(item.source_team_id)} → ${escapeHtml(item.assigned_task_team_id)}` : ''}</p>`).join('') : '<p>Назначений нет.</p>'}</div><div class="drawer-section"><h3>Резерв специалистов</h3>${engineer.reserve_candidates.length ? `<div class="reserve-list">${engineer.reserve_candidates.map((candidate) => `<button type="button" class="reserve-person" data-open-employee="${attr(candidate.engineer_id)}"><span><b>${escapeHtml(candidate.engineer_id)}</b><small>${escapeHtml(candidate.roles.join(', '))} · ${escapeHtml(candidate.team_ids.join(', '))}</small></span><span>${candidate.shared_skill_count} общих навыка · ${number(candidate.free_hours)} ч свободно</span></button>`).join('')}</div>` : '<p>Подходящий резерв по роли не найден.</p>'}</div><div class="drawer-section"><h3>Bus Factor</h3><p>Backend ещё не рассчитывает Bus Factor.</p></div>`;
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
  $('#chooseFact').addEventListener('click', () => $('#factInput').click());
  $('#factInput').addEventListener('change', (event) => { const file = event.target.files?.[0]; $('#uploadFileStatus').textContent = file ? `Выбран файл: ${file.name}. Backend пересчёта не подключён, файл не обработан.` : 'Файл не выбран'; });
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
      if (key === 'team') { state.teamId = value; renderStarTeams(); return; }
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
  document.addEventListener('input', (event) => {
    const search = event.target.closest('[data-star-filter-search]');
    if (search) filterStarSelectOptions(search);
  });
  document.addEventListener('change', (event) => { if (event.target.id === 'taskSprintSelect') { state.taskSprint = Number(event.target.value); renderTasks(); } });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeTaskOptions(); closeStarSelects(); closeEmployee(); } });
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
  data.features = payload.features || {};
  data.ready = true;
  state.loading = false;
  state.selectedSprint = currentSprint();
  state.taskId = data.tasks.find((task) => task.status === 'PLANNED')?.task_id || data.tasks[0]?.task_id || null;
  state.teamId = data.teams[0]?.team_id || null;
  state.riskId = data.risks[0]?.risk_id || null;
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
