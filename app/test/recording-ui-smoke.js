// Run with: electron test/recording-ui-smoke.js --user-data-dir=/tmp/transcriber-ui-test
const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const path = require('node:path');

let finishStart;
let starts = 0;
const responses = {
  'list-meetings': { success: true, meetings: Array.from({ length: 162 }, (_, index) => ({
    session_info: { name: `Meeting ${index}`, processed_at: '2026-09-11T10:00:00' },
    transcript: 'Saved transcript text. '.repeat(650),
  })) },
  'clear-state': { success: true },
  'get-status': { success: true, status: 'IDLE' },
  'check-microphone-permission': { success: true, status: 'granted' },
  'startup-setup-check': { success: true, allGood: true },
  'get-notifications': { success: true, notifications_enabled: false },
  'check-for-updates': { success: true, updateAvailable: false },
  'stop-recording-ui': { success: true },
};
for (const [channel, response] of Object.entries(responses)) {
  ipcMain.handle(channel, () => response);
}
ipcMain.handle('start-recording-ui', () => {
  starts++;
  return new Promise((resolve) => { finishStart = resolve; });
});

app.whenReady().then(async () => {
  const window = new BrowserWindow({ show: false, webPreferences: { nodeIntegration: true, contextIsolation: false } });
  const errors = [];
  window.webContents.on('console-message', (_, level, message) => {
    if (level >= 3) errors.push(message);
  });
  const evaluate = (source) => window.webContents.executeJavaScript(source);
  const until = (condition) => evaluate(`new Promise((resolve, reject) => {
    const deadline = Date.now() + 2000;
    function check() {
      if (${condition}) return resolve();
      if (Date.now() > deadline) return reject(new Error('Timed out: ${condition}'));
      setTimeout(check, 10);
    }
    check();
  })`);
  try {
    await window.loadFile(path.join(__dirname, '..', 'index.html'));
    await until('meetingsLastLoaded > 0 && uiState === "ready"');
    const historyStats = await evaluate(`(() => {
      const start = performance.now();
      const payload = JSON.stringify(meetings);
      for (let offset = 0; offset < payload.length; offset += 65536) {
        debugLog(payload.slice(offset, offset + 65536));
      }
      return {
        durationMs: Math.round(performance.now() - start),
        debugCharacters: document.getElementById('debug-console-panel').textContent.length,
        rows: document.querySelectorAll('.meeting-item').length,
      };
    })()`);
    console.log('History UI fixture:', historyStats);
    assert.equal(historyStats.rows, 162);
    assert.ok(historyStats.debugCharacters <= 50000, 'Debug output must stay bounded');
    assert.equal(await evaluate('document.getElementById("summarize-toggle")'), null);
    await evaluate('toggleAISettings(); startBtn.click(); startBtn.click();');
    await until('uiState === "starting" && startBtn.disabled');
    assert.equal(await evaluate('recordingTimer'), null);
    // Wait for the permission IPC to reach the start handler.
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(starts, 1);
    finishStart({ success: true });
    await until('uiState === "recording"');
    await evaluate('stopBtn.click()');
    await until('uiState === "ready"');
    await evaluate('startBtn.click()');
    await until('uiState === "starting"');
    window.webContents.send('processing-complete', { success: true, sessionName: 'older-meeting' });
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(await evaluate('uiState'), 'starting');
    finishStart({ success: false, error: 'Capture permission denied' });
    await until('uiState === "ready"');
    assert.equal(await evaluate('statusText.textContent'), 'Capture permission denied');
    assert.deepEqual(errors, []);
    console.log('PASS: transcription UI, readiness, duplicate click, immediate stop, earlier completion, startup failure');
    app.exit(0);
  } catch (error) {
    console.error(error);
    app.exit(1);
  }
});
