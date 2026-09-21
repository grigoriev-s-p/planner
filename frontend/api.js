'use strict';

window.postcodeApi = (() => {
  async function request(path, options = {}) {
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
    return response.json();
  }

  return Object.freeze({
    getDashboard: () => request('/api/dashboard'),
    getPlan: () => request('/api/plan'),
    getKpis: () => request('/api/kpis'),
    closeSprint: (payload) => request('/api/replanning/close-sprint', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    getReplanDraft: () => request('/api/replanning/draft'),
    retryReplanDraft: (workflowId) => request(`/api/replanning/drafts/${encodeURIComponent(workflowId)}/retry`, {method: 'POST'}),
    approveReplanDraft: (workflowId) => request(`/api/replanning/drafts/${encodeURIComponent(workflowId)}/approve`, {method: 'POST'}),
    getTasks: () => request('/api/tasks'),
    getTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}`),
    getRisks: () => request('/api/risks'),
    getDevelopmentRecommendations: () => request('/api/development/recommendations'),
    getTeams: () => request('/api/teams'),
    getTeam: (teamId) => request(`/api/teams/${encodeURIComponent(teamId)}`),
    getTeamHistory: (teamId) => request(`/api/teams/${encodeURIComponent(teamId)}/history`),
    getEngineers: () => request('/api/engineers'),
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
