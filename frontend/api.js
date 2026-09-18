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
    getTasks: () => request('/api/tasks'),
    getTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}`),
    getRisks: () => request('/api/risks'),
    getTeams: () => request('/api/teams'),
    getTeam: (teamId) => request(`/api/teams/${encodeURIComponent(teamId)}`),
    getEngineers: () => request('/api/engineers'),
    getEngineer: (engineerId) => request(`/api/engineers/${encodeURIComponent(engineerId)}`),
    getStarMap: () => request('/api/star-map'),
    getConstructorCatalog: () => request('/api/plan-constructor/catalog'),
    getArchiveReasons: () => request('/api/catalog/archive-reasons'),
    archiveEntity: (entityType, entityId, reason) => {
      const collection = {task: 'tasks', engineer: 'engineers', team: 'teams'}[entityType];
      return request(`/api/${collection}/${encodeURIComponent(entityId)}/archive`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(reason),
      });
    },
    buildConstructorPlanFromDataset: (file) => request(`/api/plan-constructor/plan/upload?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/octet-stream'},
      body: file,
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
  });
})();
