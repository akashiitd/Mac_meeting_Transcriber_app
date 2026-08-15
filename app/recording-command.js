function buildRecordingArgs({ scriptPath, sessionName, summarize = false }) {
  const args = ['-u', scriptPath, 'record', '3600', sessionName];
  if (summarize) {
    args.push('--summarize');
  }
  return args;
}

module.exports = { buildRecordingArgs };
