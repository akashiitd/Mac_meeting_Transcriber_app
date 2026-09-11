const { createInterface } = require('node:readline');

function buildRecordingArgs({ scriptPath, sessionName }) {
  return ['-u', scriptPath, 'record', '3600', sessionName];
}

function waitForRecordingReady(child, timeoutMs = 125000) {
  return new Promise((resolve) => {
    const lines = createInterface({ input: child.stdout });
    const finish = (result) => {
      clearTimeout(timer);
      lines.close();
      child.removeListener('error', onError);
      child.removeListener('close', onClose);
      resolve(result);
    };
    const onError = (error) => finish({ success: false, error: error.message });
    const onClose = (code) => finish({ success: false, error: `Capture failed to start (exit ${code}). Check microphone and system audio permissions.` });
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      finish({ success: false, error: 'Recording startup timed out. Check Apple Speech assets and macOS permissions.' });
    }, timeoutMs);
    child.once('error', onError);
    child.once('close', onClose);
    lines.on('line', (line) => {
      let event;
      try { event = JSON.parse(line); } catch { return; }
      if (event?.event === 'recording-ready') finish({ success: true });
    });
  });
}

module.exports = { buildRecordingArgs, waitForRecordingReady };
