const { app, BrowserWindow, ipcMain, dialog, shell, systemPreferences } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const fs = require('fs');
const https = require('https');
const os = require('os');
const { RecordingSessionManager } = require('./recording-session-manager');
const { buildRecordingArgs, waitForRecordingReady } = require('./recording-command');

let mainWindow;
let settingsWindow = null;
let pythonProcess;

/**
 * Validate that a file path is within allowed directories (security)
 * Prevents path traversal attacks by ensuring files are only accessed
 * within the app's designated data directories
 */
function validateSafeFilePath(filepath, allowedBaseDirs) {
  if (!filepath) return false;

  try {
    // Resolve to absolute path and normalize
    const resolvedPath = path.resolve(filepath);

    // Ensure it's within one of the allowed base directories
    for (const baseDir of allowedBaseDirs) {
      const resolvedBase = path.resolve(baseDir);
      if (resolvedPath.startsWith(resolvedBase + path.sep) || resolvedPath === resolvedBase) {
        return true;
      }
    }

    return false;
  } catch (error) {
    console.error('Error validating file path:', error);
    return false;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 1000,
    minHeight: 600,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    },
    titleBarStyle: 'hiddenInset',
    show: false
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));
  
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    if (pythonProcess) {
      pythonProcess.kill();
    }
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

function createSettingsWindow() {
  if (settingsWindow) {
    settingsWindow.focus();
    return;
  }

  settingsWindow = new BrowserWindow({
    width: 900,
    height: 700,
    minWidth: 800,
    minHeight: 600,
    parent: mainWindow,
    modal: false,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    },
    titleBarStyle: 'hiddenInset',
    show: false,
    backgroundColor: '#1a1a1a'
  });

  settingsWindow.loadFile(path.join(__dirname, 'settings.html'));

  settingsWindow.once('ready-to-show', () => {
    settingsWindow.show();
  });

  settingsWindow.on('closed', () => {
    settingsWindow = null;
  });
}


// Microphone permission handlers
ipcMain.handle('check-microphone-permission', async () => {
  try {
    const status = systemPreferences.getMediaAccessStatus('microphone');
    console.log('Microphone permission status:', status);
    return { success: true, status };
  } catch (error) {
    console.error('Error checking microphone permission:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('request-microphone-permission', async () => {
  try {
    console.log('Requesting microphone permission...');
    const granted = await systemPreferences.askForMediaAccess('microphone');
    console.log('Microphone permission granted:', granted);
    return { success: true, granted };
  } catch (error) {
    console.error('Error requesting microphone permission:', error);
    return { success: false, error: error.message };
  }
});

// IPC handler for opening settings
ipcMain.handle('open-settings', () => {
  createSettingsWindow();
});

// Debug functionality handled by side panel now

// Python backend communication
function runPythonScript(script, args = [], silent = false) {
  // Meeting JSON is data, not debug output; every history caller shares this path.
  silent = silent || args[0] === 'list-meetings';
  return new Promise((resolve, reject) => {
    const pythonPath = path.join(__dirname, '..', 'venv', 'bin', 'python');
    const scriptPath = path.join(__dirname, '..', script);

    // Log the command being executed (unless silent)
    const command = `${pythonPath} ${scriptPath} ${args.join(' ')}`;
    console.log('Running:', command);
    if (!silent) {
      sendDebugLog(`$ ${script} ${args.join(' ')}`);
    }

    const process = spawn(pythonPath, [scriptPath, ...args], {
      cwd: path.join(__dirname, '..')
    });

    let stdout = '';
    let stderr = '';

    process.stdout.on('data', (data) => {
      const output = data.toString();
      stdout += output;
      if (!silent) console.log('Python stdout:', output);
      // Stream stdout to debug panel in real-time (unless silent)
      if (!silent) {
        output.split('\n').forEach(line => {
          if (line.trim()) sendDebugLog(line.trim());
        });
      }
    });

    process.stderr.on('data', (data) => {
      const output = data.toString();
      stderr += output;
      console.log('Python stderr:', output);
      // Stream stderr to debug panel in real-time (unless silent)
      if (!silent) {
        output.split('\n').forEach(line => {
          if (line.trim()) sendDebugLog('STDERR: ' + line.trim());
        });
      }
    });

    process.on('close', (code) => {
      if (!silent) {
        sendDebugLog(`Command completed with exit code: ${code}`);
      }
      if (code === 0) {
        resolve(stdout);
      } else {
        reject(new Error(`Python script failed with code ${code}: ${stderr}`));
      }
    });
    
    process.on('error', (error) => {
      sendDebugLog(`Command error: ${error.message}`);
      reject(error);
    });
  });
}

// IPC Handlers - Separate start/stop with better error handling
ipcMain.handle('start-recording', async (event, sessionName) => {
  try {
    sendDebugLog(`Starting recording session: ${sessionName || 'Meeting'}`);
    sendDebugLog('$ python simple_recorder.py start');
    
    // Start recording (removed clear-state to prevent race conditions)
    const result = await runPythonScript('simple_recorder.py', ['start', sessionName || 'Meeting']);
    
    if (result.includes('SUCCESS')) {
      sendDebugLog('Recording started successfully');
      return { success: true, message: result };
    } else {
      sendDebugLog(`Recording failed: ${result}`);
      return { success: false, error: result };
    }
  } catch (error) {
    console.error('Start recording error:', error.message);
    sendDebugLog(`Recording error: ${error.message}`);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('stop-recording', async () => {
  try {
    const result = await runPythonScript('simple_recorder.py', ['stop']);
    
    if (result.includes('SUCCESS') || result.includes('Recording saved')) {
      return { success: true, message: result };
    } else {
      return { success: false, error: result };
    }
  } catch (error) {
    console.error('Stop recording error:', error.message);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('get-status', () => ({
  success: true,
  status: recordingSessions.activeSession ? 'RECORDING' : 'IDLE',
}));



ipcMain.handle('test-system', async () => {
  try {
    const result = await runPythonScript('simple_recorder.py', ['setup-check']);
    return { success: true, result: result };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('select-audio-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [
      { name: 'Audio Files', extensions: ['wav', 'mp3', 'm4a', 'aac'] }
    ]
  });
  
  if (!result.canceled && result.filePaths.length > 0) {
    return { success: true, filePath: result.filePaths[0] };
  }
  
  return { success: false, error: 'No file selected' };
});

ipcMain.handle('list-meetings', async () => {
  try {
    const result = await runPythonScript('simple_recorder.py', ['list-meetings']);
    return { success: true, meetings: JSON.parse(result) };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('clear-state', async () => {
  try {
    const result = await runPythonScript('simple_recorder.py', ['clear-state']);
    return { success: true, message: result };
  } catch (error) {
    return { success: false, error: error.message };
  }
});



ipcMain.handle('update-meeting', async (event, summaryFilePath, updates) => {
  try {
    const projectRoot = path.join(__dirname, '..');

    // Define allowed base directories for file operations
    const allowedBaseDirs = [
      projectRoot,
      path.join(os.homedir(), 'Library', 'Application Support', 'mac-meeting-transcriber')
    ];

    // Convert to absolute path if needed
    const absolutePath = path.isAbsolute(summaryFilePath)
      ? summaryFilePath
      : path.join(projectRoot, summaryFilePath);

    // Security: Validate file path is within allowed directories
    if (!validateSafeFilePath(absolutePath, allowedBaseDirs)) {
      console.error(`Security: Blocked attempt to update file outside allowed directories: ${absolutePath}`);
      return {
        success: false,
        error: 'Invalid file path'
      };
    }

    // Read existing data
    if (!fs.existsSync(absolutePath)) {
      return {
        success: false,
        error: 'Meeting file not found'
      };
    }

    const data = JSON.parse(fs.readFileSync(absolutePath, 'utf8'));

    // Update fields - only update fields that are provided
    if (updates.name !== undefined) {
      data.session_info.name = updates.name;
    }
    if (updates.summary !== undefined) {
      data.summary = updates.summary;
    }
    if (updates.participants !== undefined) {
      data.participants = updates.participants;
    }
    if (updates.key_points !== undefined) {
      data.key_points = updates.key_points;
    }
    if (updates.action_items !== undefined) {
      data.action_items = updates.action_items;
    }

    // Add updated timestamp
    data.session_info.updated_at = new Date().toISOString();

    // Write back to file
    fs.writeFileSync(absolutePath, JSON.stringify(data, null, 2), 'utf8');

    console.log(`Updated meeting: ${absolutePath}`);

    return {
      success: true,
      message: 'Meeting updated successfully',
      updatedData: data
    };
  } catch (error) {
    console.error('Update meeting error:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('delete-meeting', async (event, meetingData) => {
  try {
    const fs = require('fs');
    const path = require('path');

    // meetingData is the actual meeting object, not a file path
    const meeting = meetingData;

    // Build correct file paths from the meeting data - convert to absolute paths
    const projectRoot = path.join(__dirname, '..');

    // Define allowed base directories for file operations
    const allowedBaseDirs = [
      projectRoot,
      path.join(os.homedir(), 'Library', 'Application Support', 'mac-meeting-transcriber')
    ];

    const summaryFile = meeting.session_info?.summary_file;
    const transcriptFile = meeting.session_info?.transcript_file;

    // Convert relative paths to absolute paths
    const absolutePaths = [];
    if (summaryFile) {
      absolutePaths.push(path.isAbsolute(summaryFile) ? summaryFile : path.join(projectRoot, summaryFile));
    }
    if (transcriptFile) {
      absolutePaths.push(path.isAbsolute(transcriptFile) ? transcriptFile : path.join(projectRoot, transcriptFile));
    }

    console.log('Attempting to delete files:', absolutePaths);

    let deletedCount = 0;
    let validationErrors = 0;

    // Delete all related files with path validation
    for (const file of absolutePaths) {
      try {
        // Security: Validate file path is within allowed directories
        if (!validateSafeFilePath(file, allowedBaseDirs)) {
          console.error(`Security: Blocked attempt to delete file outside allowed directories: ${file}`);
          validationErrors++;
          continue;
        }

        if (fs.existsSync(file)) {
          fs.unlinkSync(file);
          deletedCount++;
          console.log(`Deleted: ${file}`);
        } else {
          console.log(`File not found (already deleted?): ${file}`);
        }
      } catch (err) {
        console.warn(`Could not delete ${file}:`, err.message);
      }
    }

    if (validationErrors > 0) {
      return {
        success: false,
        error: `Blocked ${validationErrors} file deletion(s) due to security validation`
      };
    }
    
    return { 
      success: true, 
      message: `Deleted meeting and ${deletedCount} associated files` 
    };
  } catch (error) {
    console.error('Delete meeting error:', error);
    return { success: false, error: error.message };
  }
});

// Queue status handler
ipcMain.handle('get-queue-status', async () => {
  return {
    success: true,
    isProcessing,
    queueSize: processingQueue.length,
    currentJob: currentProcessingJob?.sessionName || null,
    hasRecording: recordingSessions.activeSession !== null,
    finalizingRecordings: recordingSessions.finalizingCount,
  };
});

// Global recording state management
let processingQueue = [];
let isProcessing = false;
let currentProcessingJob = null;

function sendProcessingComplete(data) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('processing-complete', data);
  }
}

const recordingSessions = new RecordingSessionManager({
  async findCompletedMeeting(sessionName) {
    const meetingsResult = await runPythonScript('simple_recorder.py', ['list-meetings']);
    const allMeetings = JSON.parse(meetingsResult);
    return allMeetings.find((meeting) => meeting.session_info?.name === sessionName);
  },
  onProcessingComplete: sendProcessingComplete,
  log: console,
});

// Processing queue management
async function processNextInQueue() {
  if (isProcessing || processingQueue.length === 0) {
    return;
  }
  
  isProcessing = true;
  currentProcessingJob = processingQueue.shift();
  
  console.log(`🔄 Processing queued job: ${currentProcessingJob.sessionName}`);
  
  try {
    const result = await runPythonScript('simple_recorder.py', ['process', currentProcessingJob.audioFile, '--name', currentProcessingJob.sessionName]);
    console.log(`✅ Completed processing: ${currentProcessingJob.sessionName}`);
    
    // Notify frontend about completion with processed meeting data
    if (mainWindow) {
      try {
        // Get the specific processed meeting data
        const meetingsResult = await runPythonScript('simple_recorder.py', ['list-meetings']);
        const allMeetings = JSON.parse(meetingsResult);
        const processedMeeting = allMeetings.find(m => m.session_info?.name === currentProcessingJob.sessionName);
        
        mainWindow.webContents.send('processing-complete', { 
          success: true, 
          sessionName: currentProcessingJob.sessionName,
          message: 'Processing completed successfully',
          meetingData: processedMeeting
        });
      } catch (error) {
        console.error('Error getting processed meeting data:', error);
        mainWindow.webContents.send('processing-complete', { 
          success: true, 
          sessionName: currentProcessingJob.sessionName,
          message: 'Processing completed successfully'
        });
      }
    }
    
  } catch (error) {
    console.error(`❌ Processing failed for ${currentProcessingJob.sessionName}:`, error);
    
    // Notify frontend about failure
    if (mainWindow) {
      mainWindow.webContents.send('processing-complete', { 
        success: false, 
        sessionName: currentProcessingJob.sessionName,
        error: error.message
      });
    }
  } finally {
    isProcessing = false;
    currentProcessingJob = null;
    // Process next job in queue
    setTimeout(processNextInQueue, 1000);
  }
}

function addToProcessingQueue(audioFile, sessionName) {
  processingQueue.push({ audioFile, sessionName });
  console.log(`📋 Added to processing queue: ${sessionName} (Queue size: ${processingQueue.length})`);
  processNextInQueue();
}

ipcMain.handle('start-recording-ui', async (_, sessionName) => {
  try {
    if (recordingSessions.activeSession) {
      return { success: false, error: 'Recording already in progress' };
    }

    // Start recording (removed clear-state to prevent race conditions)
    
    console.log('Starting long recording process...');
    sendDebugLog(`Starting recording process: ${sessionName || 'Meeting'}`);
    sendDebugLog(
      `$ python -u simple_recorder.py record 3600 ${sessionName || 'Meeting'}`
    );
    
    const pythonPath = path.join(__dirname, '..', 'venv', 'bin', 'python');
    const scriptPath = path.join(__dirname, '..', 'simple_recorder.py');
    
    const actualSessionName = sessionName || 'Meeting';
    const recordingArgs = buildRecordingArgs({
      scriptPath,
      sessionName: actualSessionName,
    });
    
    // Start background recording with 60-minute limit
    const recordingProcess = spawn(pythonPath, recordingArgs, {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, PYTHONUNBUFFERED: '1' }
    });
    const ready = waitForRecordingReady(recordingProcess);
    const startResult = recordingSessions.start(actualSessionName, recordingProcess);
    if (!startResult.success) {
      recordingProcess.kill();
      return startResult;
    }

    recordingProcess.stdout.on('data', (data) => {
      const output = data.toString();
      console.log('Recording stdout:', output);
      
      // Send real-time output to debug panel (same as runPythonScript)
      output.split('\n').forEach(line => {
        if (line.trim()) sendDebugLog(line.trim());
      });
      
    });

    recordingProcess.stderr.on('data', (data) => {
      const output = data.toString();
      console.log('Recording stderr:', output);

      // Send real-time stderr to debug panel (same as runPythonScript)
      output.split('\n').forEach(line => {
        if (line.trim()) {
          sendDebugLog('STDERR: ' + line.trim());

          // Parse real-time transcript segments from log output
          // Format: "2026-01-10 00:08:36,042 - INFO - [system] Other: text here"
          // or: "2026-01-10 00:09:01,121 - INFO - [microphone] You: text here"
          const transcriptMatch = line.match(/\[(?:system|microphone)\]\s*(You|Other):\s*(.+)/);
          if (transcriptMatch && mainWindow && !mainWindow.isDestroyed()) {
            const speaker = transcriptMatch[1];
            const text = transcriptMatch[2];
            const timestamp = new Date().toLocaleTimeString();
            mainWindow.webContents.send('realtime-transcript', {
              speaker: speaker,
              text: text,
              timestamp: timestamp
            });
          }
        }
      });
    });

    recordingProcess.on('close', (code) => {
      console.log(`Recording process closed with code ${code}`);
      sendDebugLog(`Recording process completed with exit code: ${code}`);
    });

    const readiness = await ready;
    if (!readiness.success) return readiness;
    
    if (recordingSessions.activeSession?.child === recordingProcess) {
      return {
        success: true,
        message: 'Meeting transcription started',
      };
    } else {
      return { success: false, error: 'Failed to start recording process' };
    }
  } catch (error) {
    console.error('Start recording UI error:', error.message);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('stop-recording-ui', async () => {
  try {
    console.log('Stopping recording process...');
    const result = recordingSessions.stop();
    if (!result.success) return result;

    return {
      success: true,
      message: 'Recording stopped - processing will complete in background'
    };
  } catch (error) {
    console.error('Stop recording UI error:', error.message);
    return { success: false, error: error.message };
  }
});

// Setup IPC handlers

ipcMain.handle('startup-setup-check', async () => {
  try {
    console.log('Running startup setup check...');
    
    // Use Python backend to check setup
    const result = await runPythonScript('simple_recorder.py', ['setup-check']);
    console.log('Setup check result:', result);
    
    // Parse the output to determine if setup is complete
    const allGood = result.includes('🎉 System check passed!');
    
    // Extract check results for UI display
    const lines = result.split('\n');
    const checks = [];
    
    lines.forEach(line => {
      if (line.includes('✅') || line.includes('❌') || line.includes('⚠️')) {
        const parts = line.split(/\s{2,}/); // Split on multiple spaces
        if (parts.length >= 2) {
          checks.push([parts[0].trim(), parts[1].trim()]);
        }
      }
    });
    
    console.log('Parsed checks:', checks);
    console.log('All good:', allGood);
    
    return { 
      success: true, 
      allGood,
      checks
    };
  } catch (error) {
    console.error('Setup check error:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('setup-system-check', async () => {
  try {
    // Check Python installation
    const pythonResult = await new Promise((resolve) => {
      exec('python3 --version', (error, stdout, stderr) => {
        if (error) {
          resolve(false);
        } else {
          resolve(true);
        }
      });
    });
    
    if (!pythonResult) {
      return { success: false, error: 'Python 3 not found. Please install Python 3.8+' };
    }
    
    // Create required directories - match Python logic for DMG vs development
    const os = require('os');
    const currentPath = __dirname;
    let baseDir;
    
    // Detect if running from app bundle (DMG install) or development
    if (currentPath.includes('Mac Meeting Transcriber.app') || currentPath.includes('Applications')) {
      // DMG/Production: Use Application Support folder
      baseDir = path.join(os.homedir(), 'Library', 'Application Support', 'mac-meeting-transcriber');
    } else {
      // Development: Use project relative paths  
      baseDir = path.join(__dirname, '..');
    }
    
    const dirs = ['recordings', 'transcripts', 'output'];
    
    for (const dir of dirs) {
      const dirPath = path.join(baseDir, dir);
      if (!fs.existsSync(dirPath)) {
        fs.mkdirSync(dirPath, { recursive: true });
      }
    }
    
    // Create venv directory if it doesn't exist  
    const projectRoot = path.join(__dirname, '..');
    const venvPath = path.join(projectRoot, 'venv');
    if (!fs.existsSync(venvPath)) {
      await new Promise((resolve, reject) => {
        const process = spawn('python3', ['-m', 'venv', 'venv'], {
          cwd: projectRoot
        });
        
        process.on('close', (code) => {
          if (code === 0) {
            resolve();
          } else {
            reject(new Error('Failed to create virtual environment'));
          }
        });
        
        process.on('error', reject);
      });
    }
    
    return { success: true, message: 'System setup complete - Python and directories ready' };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('setup-python', async () => {
  try {
    const projectRoot = path.join(__dirname, '..');
    const venvPath = path.join(projectRoot, 'venv');
    
    sendDebugLog(`Working directory: ${projectRoot}`);
    
    // Create virtual environment if it doesn't exist
    if (!fs.existsSync(venvPath)) {
      sendDebugLog('Python virtual environment not found, creating...');
      sendDebugLog('$ python3 -m venv venv');
      
      await new Promise((resolve, reject) => {
        const process = spawn('python3', ['-m', 'venv', 'venv'], {
          cwd: projectRoot,
          stdio: 'pipe'
        });
        
        process.stdout.on('data', (data) => {
          sendDebugLog(data.toString().trim());
        });
        
        process.stderr.on('data', (data) => {
          sendDebugLog('STDERR: ' + data.toString().trim());
        });
        
        process.on('close', (code) => {
          if (code === 0) {
            sendDebugLog('Virtual environment created successfully');
            resolve();
          } else {
            sendDebugLog(`Virtual environment creation failed with exit code: ${code}`);
            reject(new Error('Failed to create virtual environment'));
          }
        });
        
        process.on('error', (error) => {
          sendDebugLog(`Process error: ${error.message}`);
          reject(error);
        });
      });
    } else {
      sendDebugLog('Python virtual environment already exists');
    }
    
    // Install requirements for Apple Speech
    sendDebugLog('Installing Python dependencies...');
    sendDebugLog('$ pip install -r requirements.txt');
    
    return new Promise((resolve) => {
      const pythonPath = path.join(venvPath, 'bin', 'python');
      const process = spawn(pythonPath, ['-m', 'pip', 'install', '-r', 'requirements.txt'], {
        cwd: projectRoot,
        stdio: 'pipe'
      });
      
      let output = '';
      
      process.stdout.on('data', (data) => {
        const text = data.toString().trim();
        if (text) {
          sendDebugLog(text);
          output += text;
        }
      });
      
      process.stderr.on('data', (data) => {
        const text = data.toString().trim();
        if (text) {
          sendDebugLog('STDERR: ' + text);
          output += text;
        }
      });
      
      process.on('close', (code) => {
        if (code === 0) {
          sendDebugLog('Python dependencies installation completed successfully');
          resolve({ success: true, message: 'Python dependencies installed' });
        } else {
          sendDebugLog(`Python dependencies installation failed with exit code: ${code}`);
          resolve({ success: false, error: `Installation failed: ${output}` });
        }
      });
      
      process.on('error', (error) => {
        resolve({ success: false, error: `Process error: ${error.message}` });
      });
    });
  } catch (error) {
    return { success: false, error: error.message };
  }
});

// Add IPC handler for sending debug logs to frontend
function sendDebugLog(message) {
  // Send to main window (both setup console and debug panel)
  if (mainWindow) {
    mainWindow.webContents.send('debug-log', message);
  }
}

ipcMain.handle('setup-test', async () => {
  try {
    sendDebugLog('Running system test...');
    sendDebugLog('$ python simple_recorder.py test');
    
    // Test the complete system
    const result = await runPythonScript('simple_recorder.py', ['setup-check']);
    
    // Log the full result to debug console
    result.split('\n').forEach(line => {
      if (line.trim()) sendDebugLog(line.trim());
    });
    
    if (result.includes('System check passed') || result.includes('SUCCESS')) {
      sendDebugLog('System test completed successfully');
      return { success: true, message: 'System test passed' };
    } else {
      // Extract specific error details from the output
      const errorLines = result.split('\n').filter(line => line.includes('ERROR:'));
      const specificError = errorLines.length > 0 ? errorLines[errorLines.length - 1].replace('ERROR: ', '') : 'Unknown error';
      sendDebugLog(`System test failed: ${specificError}`);
      return { success: false, error: `System test failed: ${specificError}`, details: result };
    }
  } catch (error) {
    sendDebugLog(`System test error: ${error.message}`);
    return { success: false, error: error.message };
  }
});

// Settings window IPC handlers  
ipcMain.handle('trigger-setup-wizard', async () => {
  try {
    console.log('🔧 Starting setup wizard from settings...');
    
    // Trigger the main window's setup flow
    if (mainWindow) {
      mainWindow.webContents.send('trigger-setup-flow');
    }
    
    return { success: true, message: 'Setup wizard triggered in main window' };
  } catch (error) {
    console.error('Setup wizard failed:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('get-app-version', async () => {
  try {
    const packagePath = path.join(__dirname, 'package.json');
    const packageContent = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
    return {
      success: true,
      version: packageContent.version,
      name: packageContent.productName || packageContent.name
    };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('get-notifications', async () => {
  try {
    const result = await runPythonScript('simple_recorder.py', ['get-notifications']);
    const jsonData = JSON.parse(result);

    return {
      success: true,
      ...jsonData
    };
  } catch (error) {
    sendDebugLog(`Error getting notification settings: ${error.message}`);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('set-notifications', async (event, enabled) => {
  try {
    sendDebugLog(`Setting notifications to: ${enabled}`);
    const result = await runPythonScript('simple_recorder.py', ['set-notifications', enabled ? 'True' : 'False']);

    // Extract JSON from output
    const jsonMatch = result.match(/\{.*\}/s);
    if (jsonMatch) {
      const jsonData = JSON.parse(jsonMatch[0]);
      return jsonData;
    }

    return { success: true, notifications_enabled: enabled };
  } catch (error) {
    sendDebugLog(`Error setting notifications: ${error.message}`);
    return { success: false, error: error.message };
  }
});

async function checkForUpdates() {
  return new Promise((resolve) => {
    const options = {
      hostname: 'api.github.com',
      path: '/repos/akashiitd/Mac_meeting_Transcriber_app/releases/latest',
      method: 'GET',
      headers: {
        'User-Agent': 'Mac Meeting Transcriber-Updater'
      }
    };

    const req = https.request(options, (res) => {
      let data = '';
      
      res.on('data', (chunk) => {
        data += chunk;
      });
      
      res.on('end', () => {
        try {
          const release = JSON.parse(data);
          const latestVersion = release.tag_name.replace(/^v/, ''); // Remove 'v' prefix if present
          
          // Get current version from package.json
          const packagePath = path.join(__dirname, 'package.json');
          const packageContent = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
          const currentVersion = packageContent.version;
          
          console.log(`Current version: ${currentVersion}, Latest version: ${latestVersion}`);
          
          // Simple version comparison (works for semantic versioning)
          const isUpdateAvailable = compareVersions(currentVersion, latestVersion) < 0;
          
          resolve({
            success: true,
            updateAvailable: isUpdateAvailable,
            currentVersion: currentVersion,
            latestVersion: latestVersion,
            releaseUrl: release.html_url,
            releaseName: release.name || `Version ${latestVersion}`,
            downloadUrl: getDownloadUrl(release.assets)
          });
        } catch (error) {
          console.error('Error parsing GitHub API response:', error);
          resolve({ success: false, error: 'Failed to parse update data' });
        }
      });
    });
    
    req.on('error', (error) => {
      console.error('Error checking for updates:', error);
      resolve({ success: false, error: error.message });
    });
    
    req.setTimeout(10000, () => {
      req.destroy();
      resolve({ success: false, error: 'Update check timeout' });
    });
    
    req.end();
  });
}

function compareVersions(current, latest) {
  const currentParts = current.split('.').map(Number);
  const latestParts = latest.split('.').map(Number);
  
  for (let i = 0; i < Math.max(currentParts.length, latestParts.length); i++) {
    const currentPart = currentParts[i] || 0;
    const latestPart = latestParts[i] || 0;
    
    if (currentPart < latestPart) return -1;
    if (currentPart > latestPart) return 1;
  }
  
  return 0;
}

function getDownloadUrl(assets) {
  // Find the appropriate download URL based on platform/architecture
  const platform = process.platform;
  const arch = process.arch;
  
  if (platform === 'darwin') {
    // Look for macOS DMG files
    const armAsset = assets.find(asset => 
      asset.name.includes('arm64') && asset.name.includes('dmg')
    );
    const intelAsset = assets.find(asset => 
      asset.name.includes('x64') && asset.name.includes('dmg')
    );
    
    // Prefer ARM64 for Apple Silicon, fallback to Intel
    if (arch === 'arm64' && armAsset) return armAsset.browser_download_url;
    if (intelAsset) return intelAsset.browser_download_url;
    if (armAsset) return armAsset.browser_download_url;
  }
  
  // Fallback to first asset or releases page
  return assets.length > 0 ? assets[0].browser_download_url : null;
}

ipcMain.handle('check-for-updates', async () => {
  return await checkForUpdates();
});

ipcMain.handle('open-release-page', async (event, url) => {
  try {
    await shell.openExternal(url);
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

// Real-time transcription handlers
let realtimeTranscriptionProcess = null;

ipcMain.handle('start-realtime-transcription', async (event, options = {}) => {
  try {
    if (realtimeTranscriptionProcess) {
      return { success: false, error: 'Real-time transcription already running' };
    }

    const pythonPath = path.join(__dirname, '..', 'venv', 'bin', 'python');
    const scriptPath = path.join(__dirname, '..', 'src', 'realtime_transcriber.py');

    sendDebugLog('Starting real-time transcription...');

    realtimeTranscriptionProcess = spawn(pythonPath, ['-u', scriptPath], {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['pipe', 'pipe', 'pipe']
    });

    realtimeTranscriptionProcess.stdout.on('data', (data) => {
      const output = data.toString();
      // Parse transcript segments and send to frontend
      if (output.includes('[') && output.includes(']:')) {
        mainWindow.webContents.send('realtime-transcript', { text: output.trim() });
      }
      sendDebugLog(output.trim());
    });

    realtimeTranscriptionProcess.stderr.on('data', (data) => {
      sendDebugLog('RT-STDERR: ' + data.toString().trim());
    });

    realtimeTranscriptionProcess.on('close', (code) => {
      sendDebugLog(`Real-time transcription ended with code: ${code}`);
      realtimeTranscriptionProcess = null;
      mainWindow.webContents.send('realtime-transcription-stopped');
    });

    return { success: true, message: 'Real-time transcription started' };
  } catch (error) {
    sendDebugLog(`Real-time transcription error: ${error.message}`);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('stop-realtime-transcription', async () => {
  try {
    if (!realtimeTranscriptionProcess) {
      return { success: false, error: 'No real-time transcription running' };
    }

    realtimeTranscriptionProcess.kill('SIGINT');
    realtimeTranscriptionProcess = null;

    return { success: true, message: 'Real-time transcription stopped' };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('get-realtime-status', async () => {
  return {
    success: true,
    isRunning: realtimeTranscriptionProcess !== null
  };
});
