const test = require('node:test');
const assert = require('node:assert/strict');

const { buildRecordingArgs } = require('../recording-command');

test('recording command leaves Ollama summarization off by default', () => {
  assert.deepEqual(
    buildRecordingArgs({ scriptPath: '/app/simple_recorder.py', sessionName: 'Planning' }),
    ['-u', '/app/simple_recorder.py', 'record', '3600', 'Planning'],
  );
});

test('recording command opts into Ollama summarization when requested', () => {
  assert.deepEqual(
    buildRecordingArgs({
      scriptPath: '/app/simple_recorder.py',
      sessionName: 'Planning',
      summarize: true,
    }),
    ['-u', '/app/simple_recorder.py', 'record', '3600', 'Planning', '--summarize'],
  );
});
