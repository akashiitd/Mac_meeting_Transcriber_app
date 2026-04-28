<div align="center">
  <img src="website/public/app-logo-512.svg" alt="Mac Meeting Transcriber Logo" width="120" height="120">

  # Mac Meeting Transcriber

  *Your very own transcriber for every meeting*
</div>

Mac meeting transcription app that captures your microphone and Mac system audio, transcribes meetings locally with Apple Speech or Whisper, and generates structured summaries with local LLMs through Ollama. Privacy first approach & zero service costs.

<div align="center">
  <img src="website/public/app-demo.png" alt="Mac Meeting Transcriber Interface" width="600">
</div>

<p align="center"><sub><i>Disclaimer: This is an independent open-source project for meeting-notes productivity and is not affiliated with, endorsed by, or associated with any similarly named company.</i></sub></p>

## Features

- **Local transcription** using OpenAI Whisper
- **Native Apple Speech live transcription** using macOS SpeechAnalyzer and ScreenCaptureKit for microphone plus system audio
- **AI summarization** with Ollama models
- **Multiple AI models** - Choose from 4 models optimized for different use cases
- **Privacy-first** - no cloud dependencies
- **macOS desktop app** with intuitive interface

## Models & Performance

**Transcription Backends:**
- `apple-speech`: Default backend. Uses the Mac built-in Apple Speech framework with microphone and system audio capture. No BlackHole or multi-output device is required. **(default)**
- `small`: Good accuracy and speed on Apple Silicon when using the Whisper backend
- `base`: Faster but lower accuracy for basic meetings
- `medium`: High accuracy for important meetings (slower)

**Summarization Models** (Ollama):
- `llama3.2:3b` (2GB): Fastest option for quick meetings **(default)**
- `gemma3:4b` (2.5GB): Lightweight and efficient
- `qwen3:8b` (4.7GB): Excellent at structured output and action items
- `deepseek-r1:8b` (4.7GB): Strong reasoning and analysis capabilities

**Switching Models:**
- Click the 🧠 AI Settings icon in the app
- Select your preferred model
- Models download automatically when selected
- ⚠️ Note: Downloads will pause any active summarization

## Future Roadmap

### Enhanced Features
- Custom summarization templates
- Speaker Diarisation

## Installation

Download the latest release for your Mac:

- [Apple Silicon (M1/M2/M3/M4)](https://github.com/akashiitd/Mac_meeting_Transcriber_app/releases/latest/download/mac-meeting-transcriber-macos-arm64.dmg)
- [Intel Macs](https://github.com/akashiitd/Mac_meeting_Transcriber_app/releases/latest/download/mac-meeting-transcriber-macos-x64.dmg) Performance on Intel Macs is limited due to lack of dedicated AI inference capabilities on these older chips.

### Installing on macOS

1. **Download and open the DMG file**
2. **Drag the app to Applications**
3. **When you first launch the app**, macOS may show a security warning
4. **To fix this warning:**
   - Go to **System Settings > Privacy & Security** and click **"Open Anyway"**

   **Alternatively:**
   - Right-click Mac Meeting Transcriber in Applications and select **"Open"**
   - Or run in Terminal: `xattr -cr /Applications/Mac Meeting Transcriber.app`
5. **The app will work normally on subsequent launches**

You can run it locally as well (see below) if you dont want to install a dmg.

## Local Development/Use Locally

### Prerequisites
- Python 3.8+
- Node.js 18+
- Homebrew
- Xcode 26+ or matching Command Line Tools for building the Apple Speech helper

### Setup
```bash
git clone https://github.com/akashiitd/Mac_meeting_Transcriber_app.git
cd Mac_meeting_Transcriber_app

# Backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install Ollama
brew install ollama
ollama serve &
ollama pull llama3.2:3b

# Install ffmpeg (required for audio processing)
brew install ffmpeg

# Frontend
cd app
npm install
npm start
```

### Apple Speech live transcription

The `apple-speech` backend uses Apple's Speech framework (`SpeechAnalyzer` and `SpeechTranscriber`) and ScreenCaptureKit. It captures:

- `microphone` as `You`
- `system` audio as `Other`

On first use, macOS may request Microphone, Speech Recognition, and Screen & System Audio Recording permissions. If system audio capture is denied, enable it in **System Settings > Privacy & Security > Screen & System Audio Recording**, then restart Mac Meeting Transcriber.

### Build
```bash
cd app
npm run build
```

## Release Process

### Simple Release Commands
```bash
cd app

# Patch release (bug fixes): 0.0.5 → 0.0.6
npm version patch
git add package.json package-lock.json
git commit -m "Version bump to $(node -p "require('./package.json').version")"
git push
git tag v$(node -p "require('./package.json').version")
git push origin v$(node -p "require('./package.json').version")

# Minor release (new features): 0.0.6 → 0.1.0
npm version minor
git add package.json package-lock.json
git commit -m "Version bump to $(node -p "require('./package.json').version")"
git push
git tag v$(node -p "require('./package.json').version")
git push origin v$(node -p "require('./package.json').version")

# Major release (breaking changes): 0.0.6 → 1.0.0
npm version major
git add package.json package-lock.json
git commit -m "Version bump to $(node -p "require('./package.json').version")"
git push
git tag v$(node -p "require('./package.json').version")
git push origin v$(node -p "require('./package.json').version")
```

**What happens:**
1. `npm version` updates package.json and package-lock.json locally
2. Manual commit ensures version changes are saved to git
3. `git push` sends the version commit to GitHub
4. `git tag` creates the version tag locally
5. `git push origin tag` triggers GitHub Actions workflow
6. Workflow automatically builds DMGs for Intel & Apple Silicon
7. Creates GitHub release with downloadable assets

## Project Structure

```
mac-meeting-transcriber/
├── app/                  # Electron desktop app
├── src/                  # Python backend
├── website/              # Marketing site
├── recordings/           # Audio files
├── transcripts/          # Text output
└── output/              # Summaries
```

## Troubleshooting

### Debug Logs

Mac Meeting Transcriber includes a built-in debug panel for troubleshooting issues:

**In-App Debug Panel:**
1. Launch Mac Meeting Transcriber
2. Click the 🔨 hammer icon (next to settings)
3. The debug panel shows real-time logs of all operations

**Terminal Logging (Advanced):**
For detailed system-level logs, run the app from Terminal:
```bash
# Launch Mac Meeting Transcriber with full logging
/Applications/Mac Meeting Transcriber.app/Contents/MacOS/Mac Meeting Transcriber
```

This displays comprehensive logs including:
- Python subprocess output
- Whisper transcription details  
- Ollama API communication
- HTTP requests and responses
- Error stack traces
- Performance timing

**System Console Logs:**
For system-level debugging:
```bash
# View recent Mac Meeting Transcriber-related logs
log show --last 10m --predicate 'process CONTAINS "Mac Meeting Transcriber" OR eventMessage CONTAINS "ollama"' --info

# Monitor live logs
log stream --predicate 'eventMessage CONTAINS "ollama" OR process CONTAINS "Mac Meeting Transcriber"' --level info
```

**Common Issues:**
- **Recording stops early**: Check microphone permissions and available disk space
- **"Processing failed"**: Usually Ollama service or model issues - check terminal logs
- **Empty transcripts**: Whisper couldn't detect speech - verify audio input levels
- **Slow processing**: Normal for longer recordings - Ollama processing is CPU-intensive especially on older intel Macs

### Logs Location
- **User Data**: `~/Library/Application Support/mac-meeting-transcriber/`
- **Recordings**: `~/Library/Application Support/mac-meeting-transcriber/recordings/`
- **Transcripts**: `~/Library/Application Support/mac-meeting-transcriber/transcripts/`
- **Summaries**: `~/Library/Application Support/mac-meeting-transcriber/output/`

## License

**Mac Meeting Transcriber is free for personal, non-commercial use.**

CC BY-NC 4.0 (Creative Commons Attribution-NonCommercial 4.0 International)
