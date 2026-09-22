'use strict';

window.postcodeLoading = (() => {
  let pending = 0;

  function render() {
    const indicator = document.getElementById('globalLoadingIndicator');
    if (!indicator) return;
    const active = pending > 0;
    if (typeof indicator.showPopover === 'function') {
      if (active && !indicator.matches(':popover-open')) indicator.showPopover();
      if (!active && indicator.matches(':popover-open')) indicator.hidePopover();
    } else {
      indicator.hidden = !active;
    }
    indicator.setAttribute('aria-busy', String(active));
  }

  return Object.freeze({
    begin() {
      pending += 1;
      render();
    },
    end() {
      pending = Math.max(0, pending - 1);
      render();
    },
  });
})();

window.postcodeApi = (() => {
  async function request(path, options = {}) {
    window.postcodeLoading.begin();
    try {
      const response = await fetch(path, {
        ...options,
        headers: {'Accept': 'application/json', ...(options.headers || {})},
      });
      if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          const detail = payload.detail;
          if (typeof detail === 'string') message = detail;
          else if (detail?.message) message = [detail.message, ...(detail.errors || [])].join('\n');
          else if (Array.isArray(detail)) message = detail.map((item) => item.msg || String(item)).join('\n');
        } catch (_) {
          // The status is still sufficient for a user-facing availability error.
        }
        throw new Error(message);
      }
      return await response.json();
    } finally {
      window.postcodeLoading.end();
    }
  }

  return Object.freeze({
    getDashboard: () => request('/api/dashboard'),
    getPlan: () => request('/api/plan'),
    getKpis: () => request('/api/kpis'),
    closeSprint: (payload) => request('/api/replanning/close-sprint', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    getEngineerAvailability: () => request('/api/engineers/availability'),
    updateEngineerAvailability: (updates) => request('/api/engineers/availability', {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({updates}),
    }),
    getReplacementCandidates: (taskId, engineerId, sprint, role = '', hours = 0) => request(`/api/replanning/replacement-candidates?task_id=${encodeURIComponent(taskId)}&engineer_id=${encodeURIComponent(engineerId)}&sprint=${encodeURIComponent(sprint)}&role=${encodeURIComponent(role)}&hours=${encodeURIComponent(hours)}`),
    getReplanDraft: () => request('/api/replanning/draft'),
    retryReplanDraft: (workflowId) => request(`/api/replanning/drafts/${encodeURIComponent(workflowId)}/retry`, {method: 'POST'}),
    approveReplanDraft: (workflowId) => request(`/api/replanning/drafts/${encodeURIComponent(workflowId)}/approve`, {method: 'POST'}),
    selectReplanTaskChanges: (workflowId, selectedTaskIds) => request(`/api/replanning/drafts/${encodeURIComponent(workflowId)}/task-choices`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({selected_task_ids: selectedTaskIds}),
    }),
    cancelReplanDraft: (workflowId) => request(`/api/replanning/drafts/${encodeURIComponent(workflowId)}/cancel`, {method: 'POST'}),
    getSprintExpansion: () => request('/api/sprint-expansion'),
    refreshSprintExpansion: () => request('/api/sprint-expansion/refresh', {method: 'POST'}),
    selectSprintExpansion: (recommendationId, action) => request(`/api/sprint-expansion/recommendations/${encodeURIComponent(recommendationId)}/${action}`, {method: 'POST'}),
    applyAllSprintExpansion: () => request('/api/sprint-expansion/apply-all', {method: 'POST'}),
    ignoreSprintExpansion: () => request('/api/sprint-expansion/ignore', {method: 'POST'}),
    approveSprintExpansion: () => request('/api/sprint-expansion/approve', {method: 'POST'}),
    getTasks: () => request('/api/tasks'),
    createTask: (payload) => request('/api/tasks', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    getTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}`),
    permanentlyDeleteEntity: (entityType, entityId) => request(`/api/${entityType}s/${encodeURIComponent(entityId)}/permanent`, {method: 'DELETE'}),
    permanentlyDeleteEntity: (entityType, entityId) => request(`/api/${entityType}s/${encodeURIComponent(entityId)}/permanent`, {method: 'DELETE'}),
    getRisks: () => request('/api/risks'),
    getDevelopmentRecommendations: () => request('/api/development/recommendations'),
    analyzeTraining: (payload) => request('/api/development/trainings/analyze', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    confirmTraining: (payload) => request('/api/development/trainings/confirm', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    getEngineerTrainings: (engineerId) => request(`/api/engineers/${encodeURIComponent(engineerId)}/trainings`),
    getTeams: () => request('/api/teams'),
    createTeam: (payload) => request('/api/teams', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    getTeam: (teamId) => request(`/api/teams/${encodeURIComponent(teamId)}`),
    getTeamHistory: (teamId) => request(`/api/teams/${encodeURIComponent(teamId)}/history`),
    getEngineers: () => request('/api/engineers'),
    createEngineer: (payload) => request('/api/engineers', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    addEngineerMembership: (engineerId, payload) => request(`/api/engineers/${encodeURIComponent(engineerId)}/memberships`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    getEngineer: (engineerId) => request(`/api/engineers/${encodeURIComponent(engineerId)}`),
    getEngineerHistory: (engineerId) => request(`/api/engineers/${encodeURIComponent(engineerId)}/history`),
    getEngineerWorkspace: (engineerId) => request(`/api/engineer-workspace/${encodeURIComponent(engineerId)}`),
    getStarMap: () => request('/api/star-map'),
    resetAllData: () => request('/api/admin/reset-data', {method: 'POST'}),
    getConstructorCatalog: () => request('/api/plan-constructor/catalog'),
    extractTaskSkills: (payload) => request('/api/skills/extract', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    getArchiveReasons: () => request('/api/catalog/archive-reasons'),
    getDeletionImpact: (entityType, entityId) => {
      const collection = {task: 'tasks', engineer: 'engineers', team: 'teams'}[entityType];
      return request(`/api/${collection}/${encodeURIComponent(entityId)}/deletion-impact`);
    },
    archiveEntity: (entityType, entityId, reason) => {
      const collection = {task: 'tasks', engineer: 'engineers', team: 'teams'}[entityType];
      return request(`/api/${collection}/${encodeURIComponent(entityId)}/archive`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(reason),
      });
    },
    preflightConstructorDataset: (file) => request(`/api/plan-constructor/plan/upload?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/octet-stream'},
      body: file,
    }),
    continueConstructorDataset: (uploadId, decisions = {}, existingDecisions = {}) => request(`/api/plan-constructor/plan/upload/${encodeURIComponent(uploadId)}/continue`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({decisions, existing_decisions: existingDecisions}),
    }),
    buildConstructorPlan: (scenario) => request('/api/plan-constructor/plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(scenario),
    }),
    approveConstructorPlan: (draftId) => request('/api/plan-constructor/drafts/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({draft_id: draftId}),
    }),
    applyConstructorRecommendation: (draftId, recommendationId) => request(`/api/plan-constructor/drafts/${encodeURIComponent(draftId)}/recommendations/apply`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({recommendation_id: recommendationId}),
    }),
    rejectConstructorRecommendation: (draftId, recommendationId) => request(`/api/plan-constructor/drafts/${encodeURIComponent(draftId)}/recommendations/reject`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({recommendation_id: recommendationId}),
    }),
    applyAllConstructorRecommendations: (draftId) => request(`/api/plan-constructor/drafts/${encodeURIComponent(draftId)}/recommendations/apply-all`, {method: 'POST'}),
    refreshConstructorRecommendations: (draftId, revision) => request(`/api/plan-constructor/drafts/${encodeURIComponent(draftId)}/recommendations/refresh`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision}),
    }),
    undoConstructorChange: (draftId, changeId) => request(`/api/plan-constructor/drafts/${encodeURIComponent(draftId)}/changes/undo`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({change_id: changeId}),
    }),
    resetConstructorDraft: (draftId) => request(`/api/plan-constructor/drafts/${encodeURIComponent(draftId)}/reset`, {method: 'POST'}),
  });
})();
