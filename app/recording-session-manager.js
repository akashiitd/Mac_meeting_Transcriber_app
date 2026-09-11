class RecordingSessionManager {
  constructor({ findCompletedMeeting, onProcessingComplete, log = console }) {
    this.findCompletedMeeting = findCompletedMeeting;
    this.onProcessingComplete = onProcessingComplete;
    this.log = log;
    this.activeSession = null;
    this.finalizingSessions = new Map();
  }

  get finalizingCount() {
    return this.finalizingSessions.size;
  }

  start(sessionName, child) {
    if (this.activeSession) {
      return { success: false, error: 'Recording already in progress' };
    }

    const session = { child, sessionName, settled: false };
    this.activeSession = session;
    child.once('close', (code) => this._handleClose(session, code));
    child.once('error', (error) => this._handleError(session, error));
    return { success: true, session };
  }

  stop() {
    const session = this.activeSession;
    if (!session) {
      return { success: false, error: 'No recording in progress' };
    }

    let signalSent;
    try {
      signalSent = session.child.kill('SIGTERM');
    } catch (error) {
      return { success: false, error: `Unable to stop the recording process: ${error.message}` };
    }
    if (!signalSent) {
      return { success: false, error: 'Unable to signal the recording process to stop' };
    }

    this.activeSession = null;
    this.finalizingSessions.set(session.child, session);
    return { success: true, session };
  }

  async _handleClose(session, code) {
    if (session.settled) return;
    session.settled = true;

    if (this.activeSession === session) {
      this.activeSession = null;
    }
    this.finalizingSessions.delete(session.child);

    try {
      const meetingData = await this.findCompletedMeeting(session.sessionName);
      if (meetingData) {
        this.onProcessingComplete({
          success: true,
          sessionName: session.sessionName,
          message: 'Recording and processing completed successfully',
          meetingData,
        });
        return;
      }

      this.onProcessingComplete({
        success: false,
        sessionName: session.sessionName,
        error: `Recording process exited with code ${code} before a completed meeting was saved`,
      });
    } catch (error) {
      this.log.error('Unable to verify recording completion:', error);
      this.onProcessingComplete({
        success: false,
        sessionName: session.sessionName,
        error: `Recording finished, but its saved meeting could not be loaded: ${error.message}`,
      });
    }
  }

  _handleError(session, error) {
    if (session.settled) return;
    session.settled = true;

    if (this.activeSession === session) {
      this.activeSession = null;
    }
    this.finalizingSessions.delete(session.child);
    this.onProcessingComplete({
      success: false,
      sessionName: session.sessionName,
      error: `Recording process error: ${error.message}`,
    });
  }
}

module.exports = { RecordingSessionManager };
