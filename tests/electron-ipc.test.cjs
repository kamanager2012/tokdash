const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { pathToFileURL } = require('url');

const {
  IPC_CHANNELS,
  isTrustedRendererUrl,
  assertTrustedIpcRequest,
} = require('../electron/ipc-security.cjs');

const DIST_INDEX = path.resolve('/opt/tokdash/dist/index.html');
const DIST_URL = pathToFileURL(DIST_INDEX).href;

test('trusted renderer URL policy is exact and origin-safe', () => {
  assert.equal(isTrustedRendererUrl(DIST_URL, { distIndex: DIST_INDEX }), true);
  assert.equal(isTrustedRendererUrl('file:///tmp/evil.html', { distIndex: DIST_INDEX }), false);

  assert.equal(
    isTrustedRendererUrl('http://localhost:5173/', { isDev: true, distIndex: DIST_INDEX }),
    true,
  );
  assert.equal(
    isTrustedRendererUrl('http://localhost:5173/src/main.tsx', { isDev: true, distIndex: DIST_INDEX }),
    true,
  );
  assert.equal(
    isTrustedRendererUrl('http://localhost:5173@evil.example/', { isDev: true, distIndex: DIST_INDEX }),
    false,
  );
  assert.equal(
    isTrustedRendererUrl('https://localhost:5173/', { isDev: true, distIndex: DIST_INDEX }),
    false,
  );
});

function trustedHarness() {
  const mainFrame = { url: DIST_URL };
  const webContents = { mainFrame };
  const mainWindow = {
    webContents,
    isDestroyed: () => false,
  };
  const event = {
    sender: webContents,
    senderFrame: mainFrame,
  };

  return { mainFrame, webContents, mainWindow, event };
}

test('trusted IPC request accepts only the main window main frame with zero arguments', () => {
  const { mainWindow, event } = trustedHarness();

  assert.doesNotThrow(() => {
    assertTrustedIpcRequest({
      event,
      mainWindow,
      channel: IPC_CHANNELS.GET_SNAPSHOT,
      args: [],
      distIndex: DIST_INDEX,
    });
  });
});

test('IPC boundary rejects unknown channel, arguments, sender, subframe and untrusted URL', () => {
  const { mainFrame, webContents, mainWindow, event } = trustedHarness();

  assert.throws(() => {
    assertTrustedIpcRequest({
      event,
      mainWindow,
      channel: 'arbitrary-command',
      args: [],
      distIndex: DIST_INDEX,
    });
  }, /Rejected IPC channel/);

  assert.throws(() => {
    assertTrustedIpcRequest({
      event,
      mainWindow,
      channel: IPC_CHANNELS.GET_SNAPSHOT,
      args: ['unexpected'],
      distIndex: DIST_INDEX,
    });
  }, /Rejected IPC arguments/);

  assert.throws(() => {
    assertTrustedIpcRequest({
      event: { sender: {}, senderFrame: mainFrame },
      mainWindow,
      channel: IPC_CHANNELS.GET_SNAPSHOT,
      args: [],
      distIndex: DIST_INDEX,
    });
  }, /Rejected IPC sender/);

  const childFrame = { url: DIST_URL };
  assert.throws(() => {
    assertTrustedIpcRequest({
      event: { sender: webContents, senderFrame: childFrame },
      mainWindow,
      channel: IPC_CHANNELS.GET_SNAPSHOT,
      args: [],
      distIndex: DIST_INDEX,
    });
  }, /Rejected IPC from non-main frame/);

  assert.throws(() => {
    assertTrustedIpcRequest({
      event: {
        sender: webContents,
        senderFrame: { url: 'file:///tmp/evil.html' },
      },
      mainWindow: {
        webContents: {
          ...webContents,
          mainFrame: { url: 'file:///tmp/evil.html' },
        },
        isDestroyed: () => false,
      },
      channel: IPC_CHANNELS.GET_SNAPSHOT,
      args: [],
      distIndex: DIST_INDEX,
    });
  });
});
