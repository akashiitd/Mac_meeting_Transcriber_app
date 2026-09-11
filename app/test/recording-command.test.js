const test = require('node:test');
const assert = require('node:assert/strict');

const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { buildRecordingArgs, waitForRecordingReady } = require('../recording-command');

test('recording command leaves Ollama summarization off by default', () => {
  assert.deepEqual(
    buildRecordingArgs({ scriptPath: '/app/simple_recorder.py', sessionName: 'Planning' }),
    ['-u', '/app/simple_recorder.py', 'record', '3600', 'Planning'],
  );
});

test('recording command ignores legacy summary options', () => {
  assert.deepEqual(
    buildRecordingArgs({
      scriptPath: '/app/simple_recorder.py',
      sessionName: 'Planning',
      summarize: true,
    }),
    ['-u', '/app/simple_recorder.py', 'record', '3600', 'Planning'],
  );
});

test('startup waits for actual readiness and handles fragmented output without a fixed delay', async () => {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  const ready = waitForRecordingReady(child);
  let settled = false;
  ready.then(() => { settled = true; });
  child.stdout.write('Starting helper\n{"event":"recording-');
  await new Promise(setImmediate);
  assert.equal(settled, false);
  child.stdout.write('ready"}\n');
  assert.deepEqual(await ready, { success: true });
  assert.equal(child.listenerCount('close'), 0);
});

test('startup reports early exit, spawn failure, and timeout', async () => {
  for (const failure of ['close', 'error', 'timeout']) {
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    let killed = false;
    child.kill = () => { killed = true; };
    const ready = waitForRecordingReady(child, 20);
    if (failure === 'close') child.emit('close', 1);
    if (failure === 'error') child.emit('error', new Error('spawn failed'));
    assert.equal((await ready).success, false);
    assert.equal(killed, failure === 'timeout');
    assert.equal(child.listenerCount('error'), 0);
  }
});
