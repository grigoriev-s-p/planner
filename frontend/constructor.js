'use strict';

window.planConstructor = (() => {
  const STORAGE_KEY = 'postcode.plan-constructor.v2';
  const emptyDraft = () => ({teams: [], tasks: []});
  let catalog = {teams: [], engineers: [], tasks: [], options: {roles: [], skills: [], grades: []}};
  let draft = emptyDraft();
  let result = null;
  let autoResult = null;
  let mode = 'manual';
  let api = null;
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
    const button = $c('#uploadConstructorDataset');
    button.disabled = true;
    button.textContent = 'Строим план…';
    setDatasetStatus(`Проверяем ${file.name} и рассчитываем шесть спринтов…`);
    try {
      autoResult = await api.buildConstructorPlanFromDataset(file);
      renderAutoResult();
      const planned = (autoResult.tasks || []).filter((task) => task.status === 'PLANNED').length;
      setDatasetStatus(`${file.name} · план готов · ${planned} задач вошли в квартал`, 'success');
      $c('#constructorAutoResult').scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (error) {
      autoResult = null;
      $c('#constructorAutoResult').hidden = true;
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
    result = null;
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

  function renderCatalogs() {
    const teamQuery = searchValue('#constructorTeamSearch');
    const teams = catalog.teams
      .filter((team) => `${team.team_id} ${team.name}`.toLowerCase().includes(teamQuery))
      .sort((left, right) => (
        Number(right.expected_velocity_sp || 0) - Number(left.expected_velocity_sp || 0)
        || String(left.name || left.team_id).localeCompare(String(right.name || right.team_id), 'ru')
      ));
    $c('#constructorTeamCatalog').innerHTML = teams.map((team) => `<label class="constructor-option"><input type="checkbox" data-constructor-pick="teams" value="${esc(team.team_id)}" ${findDraft('teams', team.team_id) ? 'checked' : ''}><span><b>${esc(team.name || team.team_id)}</b><small>${esc(team.expected_velocity_sp)} SP</small></span></label>`).join('') || '<div class="empty-state">Команды не найдены.</div>';

    const taskQuery = searchValue('#constructorTaskSearch');
    const tasks = catalogWithDraft('tasks').filter((task) => `${task.task_id} ${task.title} ${task.initiative_id || ''}`.toLowerCase().includes(taskQuery));
    $c('#constructorTaskCatalog').innerHTML = tasks.map((task) => `<label class="constructor-option constructor-task-option"><input type="checkbox" data-constructor-pick="tasks" value="${esc(task.task_id)}" ${findDraft('tasks', task.task_id) ? 'checked' : ''}><span><b>${esc(task.task_id)} · ${esc(task.title)}</b></span></label>`).join('') || '<div class="empty-state">Задачи не найдены.</div>';
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
    $c('#constructorResult').hidden = !result;
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
      content.innerHTML = `<div class="eyebrow">КОНСТРУКТОР / КОМАНДА</div><h2>${item ? 'Изменить' : 'Добавить'} команду</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="teams" data-editing="${Boolean(item)}"><label>ID команды<input name="team_id" value="${esc(team.team_id)}" ${item ? 'readonly' : ''} required></label><label>Название<input name="name" value="${esc(team.name)}" required></label><label>Ожидаемая velocity, SP<input name="expected_velocity_sp" type="number" min="0" step="0.01" value="${esc(team.expected_velocity_sp)}" required></label><label>Focus Factor<input name="focus_factor" value="0.8" readonly><small>Зафиксирован правилами кейса.</small></label><div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
    }
    if (type === 'tasks') {
      const task = item || {task_id: '', title: '', initiative_id: '', team_id: null, status: 'ToDo', rung: 0, story_points: 1, role_requirements: [{role: '', required_hours: 8, skills: []}], depends_on: []};
      content.innerHTML = `<div class="eyebrow">КОНСТРУКТОР / ЗАДАЧА</div><h2>${item ? 'Изменить' : 'Добавить'} задачу</h2><form id="constructorEntityForm" class="constructor-form" data-form-type="tasks" data-editing="${Boolean(item)}"><div class="constructor-form-grid"><label>ID задачи<input name="task_id" value="${esc(task.task_id)}" ${item ? 'readonly' : ''} required></label><label>Название<input name="title" value="${esc(task.title)}" required></label><label>Инициатива<input name="initiative_id" list="constructorInitiativeOptions" value="${esc(task.initiative_id)}" placeholder="Выберите или введите PRODF"></label><label>Статус<select name="status">${optionList(['ToDo', 'InProgress', 'Done'], task.status)}</select></label><label>Приоритет Rung<input name="rung" type="number" step="0.01" value="${esc(task.rung)}" required></label><label class="constructor-sp-field">Story Points<input name="story_points" type="text" inputmode="decimal" pattern="[0-9]+([.,][0-9]+)?" value="${esc(task.story_points)}" required><small>Отдельно от человеко-часов.</small></label>${dependencyPicker(task)}</div><h3>Трудозатраты и обязательные навыки</h3><div id="constructorRequirementRows">${task.role_requirements.map(requirementRow).join('')}</div><button class="text-button" type="button" data-add-requirement>+ Добавить роль</button>${sharedDataLists()}<div class="constructor-form-actions"><button class="btn btn-blue" type="submit">Сохранить</button><button class="btn btn-outline" type="button" data-close-constructor-form>Отмена</button></div></form>`;
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
    result = null;
    saveDraft();
    $c('#constructorDialog').close();
    render();
  }

  function renderPlanResult(planResult, ids, firstMetricLabel) {
    const container = $c(ids.container);
    container.hidden = !planResult;
    if (!planResult) return;
    const tasks = planResult.tasks || [];
    const planned = tasks.filter((task) => task.status === 'PLANNED');
    const outside = tasks.filter((task) => !['PLANNED', 'DONE'].includes(task.status));
    $c(ids.summary).innerHTML = [
      [firstMetricLabel, tasks.length], ['Вошли в квартал', planned.length], ['Не вошли', outside.length], ['Закрываются инициативы', Number(planResult.summary?.completed_initiatives || 0)],
    ].map(([label, value]) => `<div class="metric"><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join('');
    $c(ids.board).innerHTML = Array.from({length: 6}, (_, index) => {
      const sprint = index + 1;
      const sprintTasks = planned.filter((task) => (task.planned_sprints || []).includes(sprint));
      const cards = sprintTasks.map((task) => {
        const allocation = (task.allocations || []).filter((item) => item.sprint === sprint);
        const hours = allocation.reduce((sum, item) => sum + Number(item.allocated_hours || 0), 0);
        const continuation = task.planned_sprints[0] !== sprint;
        return `<div class="sprint-card"><span class="card-top">${continuation ? '↳ ' : ''}${esc(task.task_id)}<span>${esc(Number(task.sp_load_by_sprint?.[String(sprint)] || 0))} SP</span></span><span class="card-title">${esc(task.title)}</span><span class="card-meta">${esc(task.assigned_team_id)} · ${esc(hours)} ч</span><span class="card-meta">${esc(allocation.map((item) => item.engineer_id).join(', ') || 'без назначений')}</span></div>`;
      }).join('');
      return `<div class="sprint-column"><div class="sprint-column-header"><b>Sprint ${sprint}</b><span>${sprintTasks.length} задач</span></div>${cards || '<div class="muted">Нет работ</div>'}</div>`;
    }).join('');
    $c(ids.outside).innerHTML = outside.length ? outside.map((task) => `<div class="constructor-outside-row"><span><b>${esc(task.task_id)} · ${esc(task.title)}</b><small>${esc(task.status_label)} · ${esc(task.summary || task.reasons?.[0]?.message || 'Причина не указана')}</small></span><span class="status moved">${esc(task.status)}</span></div>`).join('') : '<div class="empty-state">Все планируемые задачи вошли в квартал.</div>';
  }

  function renderResult() {
    renderPlanResult(result, {
      container: '#constructorResult', summary: '#constructorResultSummary',
      board: '#constructorSprintBoard', outside: '#constructorOutsideTasks',
    }, 'Задач в сценарии');
  }

  function renderAutoResult() {
    renderPlanResult(autoResult, {
      container: '#constructorAutoResult', summary: '#constructorAutoResultSummary',
      board: '#constructorAutoSprintBoard', outside: '#constructorAutoOutsideTasks',
    }, 'Задач в датасете');
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
      result = await api.buildConstructorPlan({teams: draft.teams, engineers: automaticEngineers(), tasks});
      showErrors([]);
      render();
      $c('#constructorResult').scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (error) {
      showErrors(String(error?.message || error).split('\n').filter(Boolean));
    } finally {
      button.disabled = false;
      button.textContent = 'Построить план';
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
    $$c('[data-constructor-mode]').forEach((button) => button.addEventListener('click', () => setMode(button.dataset.constructorMode)));
    $c('#buildConstructorPlan').addEventListener('click', buildPlan);
    $c('#clearConstructorDraft').addEventListener('click', () => {
      if (!confirm('Очистить весь локальный черновик? Исходный датасет не изменится.')) return;
      draft = emptyDraft(); result = null; saveDraft(); showErrors([]); render();
    });
    $c('#constructorDialog').addEventListener('close', () => document.body.classList.remove('constructor-modal-open'));
    document.addEventListener('change', (event) => {
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
    if (!$c('#page-constructor')) return;
    setMode(mode);
    renderCatalogs();
    renderDraft();
    renderResult();
    renderAutoResult();
  }

  function initialize(sourceCatalog, apiClient) {
    catalog = sourceCatalog;
    api = apiClient;
    loadDraft();
    bindEvents();
    render();
  }

  return Object.freeze({initialize, render});
})();
