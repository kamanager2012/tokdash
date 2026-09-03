const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const { execFile } = require('child_process');
const fs = require('fs');

let mainWindow = null;
let tray = null;
const ROOT_DIR = path.resolve(__dirname, '..');
const SCRIPT_PATH = path.join(ROOT_DIR, 'usage.30s.py');
const ICON_PATH = path.join(ROOT_DIR, 'icon.png');

function runPython(args) {
  return new Promise((resolve, reject) => {
    execFile('python3', [SCRIPT_PATH, ...args], { cwd: ROOT_DIR, maxBuffer: 10 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        console.error(`Python error for ${args.join(' ')}:`, stderr);
        return reject(error);
      }
      try {
        const data = JSON.parse(stdout.trim());
        resolve(data);
      } catch (e) {
        console.error('Failed to parse Python JSON output:', stdout.slice(0, 200));
        reject(e);
      }
    });
  });
}

function createTray() {
  try {
    const icon = nativeImage.createFromPath(ICON_PATH);
    tray = new Tray(icon.resize({ width: 22, height: 22 }));
    tray.setToolTip('TokDash - AI 编程用量监控');
    
    const contextMenu = Menu.buildFromTemplate([
      { label: '显示窗口', click: () => { mainWindow?.show(); mainWindow?.focus(); } },
      { label: '隐藏窗口', click: () => { mainWindow?.hide(); } },
      { type: 'separator' },
      { label: '退出 TokDash', click: () => { app.isQuitting = true; app.quit(); } }
    ]);
    
    tray.setContextMenu(contextMenu);
    tray.on('click', () => {
      if (!mainWindow) return;
      if (mainWindow.isVisible()) {
        mainWindow.hide();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    });
  } catch (err) {
    console.warn('Tray creation skipped or failed:', err.message);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1080,
    height: 720,
    minWidth: 920,
    minHeight: 620,
    frame: false,
    titleBarStyle: 'hidden',
    backgroundColor: '#0f0f13',
    icon: ICON_PATH,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    }
  });

  const isDev = process.env.TOKDASH_DEV === '1' || process.env.NODE_ENV === 'development';
  const distIndex = path.join(ROOT_DIR, 'dist', 'index.html');

  if (fs.existsSync(distIndex)) {
    mainWindow.loadFile(distIndex);
  } else if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    console.error('Fatal: Production build missing (dist/index.html not found). Run "pnpm build" first.');
    app.quit();
    return;
  }

  // Security: deny all in-app navigation outside local bundled files
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('file://') && !(isDev && url.startsWith('http://localhost:5173'))) {
      event.preventDefault();
    }
  });

  // Security: deny auxiliary windows/popups
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
}

app.whenReady().then(() => {
  createTray();
  createWindow();

  ipcMain.handle('get-usage', async () => {
    return runPython(['--json']);
  });

  ipcMain.handle('get-daily-costs', async () => {
    return runPython(['--daily-costs']);
  });

  ipcMain.handle('get-projects', async () => {
    return runPython(['--projects']);
  });

  ipcMain.handle('update-prices', async () => {
    return runPython(['--update-prices']);
  });

  ipcMain.handle('window-minimize', () => {
    mainWindow?.minimize();
  });

  ipcMain.handle('window-close', () => {
    mainWindow?.hide();
  });

  ipcMain.handle('window-toggle-maximize', () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  } else {
    mainWindow?.show();
  }
});
