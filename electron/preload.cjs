const { contextBridge, ipcRenderer } = require('electron');

// Frozen IPC surface: only these invoke channels are exposed to the renderer.
const INVOKE = Object.freeze({
  fetchSnapshot: 'get-snapshot',
  fetchUsage: 'get-usage',
  fetchDailyCosts: 'get-daily-costs',
  fetchProjects: 'get-projects',
  updatePrices: 'update-prices',
  minimizeWindow: 'window-minimize',
  closeWindow: 'window-close',
  toggleMaximize: 'window-toggle-maximize',
});

const api = Object.freeze({
  fetchSnapshot: () => ipcRenderer.invoke(INVOKE.fetchSnapshot),
  fetchUsage: () => ipcRenderer.invoke(INVOKE.fetchUsage),
  fetchDailyCosts: () => ipcRenderer.invoke(INVOKE.fetchDailyCosts),
  fetchProjects: () => ipcRenderer.invoke(INVOKE.fetchProjects),
  updatePrices: () => ipcRenderer.invoke(INVOKE.updatePrices),
  minimizeWindow: () => ipcRenderer.invoke(INVOKE.minimizeWindow),
  closeWindow: () => ipcRenderer.invoke(INVOKE.closeWindow),
  toggleMaximize: () => ipcRenderer.invoke(INVOKE.toggleMaximize),
});

contextBridge.exposeInMainWorld('cognitally', api);
// Legacy alias for older TokDash renderer builds.
contextBridge.exposeInMainWorld('tokdash', api);
