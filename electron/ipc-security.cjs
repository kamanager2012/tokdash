const path = require('path');
const { fileURLToPath } = require('url');

const IPC_CHANNELS = Object.freeze({
  GET_SNAPSHOT: 'get-snapshot',
  UPDATE_PRICES: 'update-prices',
  WINDOW_MINIMIZE: 'window-minimize',
  WINDOW_CLOSE: 'window-close',
  WINDOW_TOGGLE_MAXIMIZE: 'window-toggle-maximize',
});

const ALLOWED_CHANNELS = new Set(Object.values(IPC_CHANNELS));

function isTrustedRendererUrl(rawUrl, { isDev = false, distIndex } = {}) {
  if (typeof rawUrl !== 'string' || rawUrl.length === 0) return false;

  try {
    const parsed = new URL(rawUrl);

    if (isDev && parsed.origin === 'http://localhost:5173') {
      return true;
    }

    if (parsed.protocol !== 'file:' || !distIndex) {
      return false;
    }

    return path.resolve(fileURLToPath(parsed)) === path.resolve(distIndex);
  } catch {
    return false;
  }
}

function assertTrustedIpcRequest({
  event,
  mainWindow,
  channel,
  args = [],
  isDev = false,
  distIndex,
}) {
  if (!ALLOWED_CHANNELS.has(channel)) {
    throw new Error(`Rejected IPC channel: ${channel}`);
  }

  if (!Array.isArray(args) || args.length !== 0) {
    throw new Error(`Rejected IPC arguments for channel: ${channel}`);
  }

  if (!mainWindow || (typeof mainWindow.isDestroyed === 'function' && mainWindow.isDestroyed())) {
    throw new Error(`Rejected IPC without active main window: ${channel}`);
  }

  if (!event || event.sender !== mainWindow.webContents) {
    throw new Error(`Rejected IPC sender for channel: ${channel}`);
  }

  const senderFrame = event.senderFrame;
  if (!senderFrame) {
    throw new Error(`Rejected IPC without live sender frame: ${channel}`);
  }

  if (event.sender.mainFrame && senderFrame !== event.sender.mainFrame) {
    throw new Error(`Rejected IPC from non-main frame: ${channel}`);
  }

  if (!isTrustedRendererUrl(senderFrame.url, { isDev, distIndex })) {
    throw new Error(`Rejected IPC from untrusted URL: ${channel}`);
  }
}

module.exports = {
  IPC_CHANNELS,
  isTrustedRendererUrl,
  assertTrustedIpcRequest,
};
