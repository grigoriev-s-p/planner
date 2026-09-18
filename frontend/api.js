'use strict';

window.postcodeApi = (() => {
  async function request(path) {
    const response = await fetch(path, {headers: {'Accept': 'application/json'}});
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload.detail || message;
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
  });
})();
