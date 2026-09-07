const { contextBridge, ipcRenderer } = require('electron');

const api = {
  fetchSnapshot: () => ipcRenderer.invoke('get-snapshot'),
  fetchUsage: () => ipcRenderer.invoke('get-usage'),
  fetchDailyCosts: () => ipcRenderer.invoke('get-daily-costs'),
  fetchProjects: () => ipcRenderer.invoke('get-projects'),
  updatePrices: () => ipcRenderer.invoke('update-prices'),
  minimizeWindow: () => ipcRenderer.invoke('window-minimize'),
  closeWindow: () => ipcRenderer.invoke('window-close'),
  toggleMaximize: () => ipcRenderer.invoke('window-toggle-maximize')
};

contextBridge.exposeInMainWorld('cognitally', api);
contextBridge.exposeInMainWorld('tokdash', api);
