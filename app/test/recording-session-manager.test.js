const { EventEmitter } = require('node:events');
const test = require('node:test');
const assert = require('node:assert/strict');

const { RecordingSessionManager } = require('../recording-session-manager');

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.signals = [];
    this.killResult = true;
  }

  kill(signal) {
    this.signals.push(signal);
    return this.killResult;
  }
}

test('keeps a newer recording active while an earlier recording finalizes', async () => {
  const completions = [];
  const manager = new RecordingSessionManager({
    findCompletedMeeting: async (sessionName) => ({ session_info: { name: sessionName } }),
    onProcessingComplete: (result) => completions.push(result),
  });
  const first = new FakeChild();
  const second = new FakeChild();

  assert.equal(manager.start('first', first).success, true);
  assert.equal(manager.stop().success, true);
  assert.deepEqual(first.signals, ['SIGTERM']);
  assert.equal(manager.activeSession, null);
  assert.equal(manager.finalizingCount, 1);

  assert.equal(manager.start('second', second).success, true);
  first.emit('close', 0);
  await new Promise(setImmediate);

  assert.equal(manager.activeSession.sessionName, 'second');
  assert.equal(manager.finalizingCount, 0);
  assert.deepEqual(completions, [{
    success: true,
    sessionName: 'first',
    message: 'Recording and processing completed successfully',
    meetingData: { session_info: { name: 'first' } },
  }]);
});

test('does not report stop success when the operating system rejects the signal', () => {
  const manager = new RecordingSessionManager({
    findCompletedMeeting: async () => undefined,
    onProcessingComplete: () => {},
  });
  const child = new FakeChild();
  child.killResult = false;

  manager.start('meeting', child);
  const result = manager.stop();

  assert.equal(result.success, false);
  assert.equal(manager.activeSession.sessionName, 'meeting');
  assert.equal(manager.finalizingCount, 0);
});

test('reports a child-process error only once when it is followed by close', async () => {
  const completions = [];
  const manager = new RecordingSessionManager({
    findCompletedMeeting: async () => undefined,
    onProcessingComplete: (result) => completions.push(result),
  });
  const child = new FakeChild();

  manager.start('meeting', child);
  child.emit('error', new Error('spawn failed'));
  child.emit('close', 1);
  await new Promise(setImmediate);

  assert.deepEqual(completions, [{
    success: false,
    sessionName: 'meeting',
    error: 'Recording process error: spawn failed',
  }]);
});
