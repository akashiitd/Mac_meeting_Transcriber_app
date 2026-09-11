const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { createRequire } = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('meeting history reaches the UI without copying transcript data into debug logs', async () => {
  const mainPath = path.join(__dirname, '..', 'main.js');
  const realRequire = createRequire(mainPath);
  const handlers = new Map();
  const logs = [];
  const payload = [{ session_info: { name: 'History fixture' }, transcript: 'Private meeting content. '.repeat(100000) }];
  const source = JSON.stringify(payload);
  const context = vm.createContext({
    __dirname: path.dirname(mainPath), process, setTimeout, clearTimeout,
    console: { log: (...args) => logs.push(args.join(' ')), error: () => {} },
    require(name) {
      if (name === 'electron') return {
        app: { whenReady: () => ({ then() {} }), on() {} },
        ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
      };
      if (name === 'child_process') return {
        spawn() {
          const child = new EventEmitter();
          child.stdout = new PassThrough();
          child.stderr = new PassThrough();
          setImmediate(() => {
            child.stdout.end(source);
            child.emit('close', 0);
          });
          return child;
        },
      };
      return realRequire(name);
    },
    send: (_, message) => logs.push(message),
  });
  vm.runInContext(fs.readFileSync(mainPath, 'utf8'), context);
  vm.runInContext('mainWindow = { webContents: { send } }', context);

  const result = await handlers.get('list-meetings')();
  assert.equal(result.success, true);
  assert.equal(result.meetings[0].transcript, payload[0].transcript);
  const loggedBytes = logs.reduce((total, line) => total + Buffer.byteLength(line), 0);
  assert.ok(loggedBytes < 1000, `Meeting load copied ${loggedBytes} bytes into logs`);
  assert.ok(logs.every((line) => !line.includes('Private meeting content.')));
});
