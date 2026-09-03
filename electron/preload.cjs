const { contextBridge, ipcRenderer } = require('electron');

const tokdashApi = Object.freeze({
  fetchSnapshot: () => ipcRenderer.invoke('get-snapshot'),
  updatePrices: () => ipcRenderer.invoke('update-prices'),
  minimizeWindow: () => ipcRenderer.invoke('window-minimize'),
  closeWindow: () => ipcRenderer.invoke('window-close'),
  toggleMaximize: () => ipcRenderer.invoke('window-toggle-maximize'),
});

// Expose only narrow, zero-argument capabilities. Never expose ipcRenderer itself
// or a generic invoke/send primitive to renderer code.
contextBridge.exposeInMainWorld('tokdash', tokdashApi);
