const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('tokdash', {
  fetchSnapshot: () => ipcRenderer.invoke('get-snapshot'),
  fetchUsage: () => ipcRenderer.invoke('get-usage'),
  fetchDailyCosts: () => ipcRenderer.invoke('get-daily-costs'),
  fetchProjects: () => ipcRenderer.invoke('get-projects'),
  updatePrices: () => ipcRenderer.invoke('update-prices'),
  minimizeWindow: () => ipcRenderer.invoke('window-minimize'),
  closeWindow: () => ipcRenderer.invoke('window-close'),
  toggleMaximize: () => ipcRenderer.invoke('window-toggle-maximize')
});
