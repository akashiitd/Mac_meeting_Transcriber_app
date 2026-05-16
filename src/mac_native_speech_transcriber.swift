import AVFoundation
import CoreMedia
import CoreGraphics
import Foundation
import ScreenCaptureKit
import Speech

struct TranscriptEvent: Encodable {
    let event: String
    let source: String
    let speaker: String
    let text: String
    let start_time: Double
    let end_time: Double
    let confidence: Double
    let is_final: Bool
}

struct StatusEvent: Encodable {
    let event: String
    let message: String
}

struct ErrorEvent: Encodable {
    let event: String
    let message: String
}

final class JSONEmitter: @unchecked Sendable {
    private let encoder = JSONEncoder()
    private let lock = NSLock()

    func status(_ message: String) {
        emit(StatusEvent(event: "status", message: message))
    }

    func error(_ message: String) {
        emit(ErrorEvent(event: "error", message: message))
    }

    func transcript(_ event: TranscriptEvent) {
        emit(event)
    }

    private func emit<T: Encodable>(_ value: T) {
        lock.lock()
        defer { lock.unlock() }

        guard let data = try? encoder.encode(value),
              let line = String(data: data, encoding: .utf8) else {
            return
        }

        print(line, flush: true)
    }
}

extension Swift.String {
    init(_ attributedString: AttributedString) {
        self = String(attributedString.characters)
    }
}

func print(_ items: Any..., separator: String = " ", terminator: String = "\n", flush: Bool) {
    var output = ""
    for (index, item) in items.enumerated() {
        if index > 0 {
            output += separator
        }
        output += "\(item)"
    }
    output += terminator
    if let data = output.data(using: .utf8) {
        FileHandle.standardOutput.write(data)
    }
    if flush {
        fflush(stdout)
    }
}

@available(macOS 26.0, *)
final class SpeechSource {
    let source: String
    let speaker: String

    private let emitter: JSONEmitter
    private let inputFormat: AVAudioFormat
    private let analysisFormat: AVAudioFormat
    private let transcriber: SpeechTranscriber
    private let analyzer: SpeechAnalyzer
    private let inputContinuation: AsyncStream<AnalyzerInput>.Continuation
    private let inputStream: AsyncStream<AnalyzerInput>
    private let converterQueue = DispatchQueue(label: "macmeetingtranscriber.apple-speech.converter")

    private var resultsTask: Task<Void, Never>?
    private var analyzerTask: Task<Void, Never>?
    private var converter: AVAudioConverter?
    private var lastText = ""
    private var lastEmissionTime = Date.distantPast

    init(source: String, speaker: String, localeIdentifier: String, contextTerms: [String], qualityMode: String, inputFormat: AVAudioFormat, emitter: JSONEmitter) async throws {
        self.source = source
        self.speaker = speaker
        self.emitter = emitter
        self.inputFormat = inputFormat

        guard SpeechTranscriber.isAvailable else {
            throw NSError(domain: "MacMeetingTranscriberAppleSpeech", code: 10, userInfo: [
                NSLocalizedDescriptionKey: "SpeechTranscriber is not available on this Mac."
            ])
        }

        let requestedLocale = Locale(identifier: localeIdentifier)
        guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: requestedLocale) else {
            throw NSError(domain: "MacMeetingTranscriberAppleSpeech", code: 11, userInfo: [
                NSLocalizedDescriptionKey: "Apple Speech does not support locale \(localeIdentifier)."
            ])
        }

        let reportingOptions: Set<SpeechTranscriber.ReportingOption>
        switch qualityMode {
        case "balanced":
            reportingOptions = [.volatileResults]
        default:
            reportingOptions = [.fastResults]
        }

        self.transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: reportingOptions,
            attributeOptions: [.audioTimeRange, .transcriptionConfidence]
        )

        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            emitter.status("Installing Apple Speech assets for \(locale.identifier).")
            try await request.downloadAndInstall()
        }

        self.analysisFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
            compatibleWith: [transcriber],
            considering: inputFormat
        ) ?? inputFormat

        let streamPair = AsyncStream.makeStream(of: AnalyzerInput.self)
        self.inputStream = streamPair.stream
        self.inputContinuation = streamPair.continuation

        self.analyzer = SpeechAnalyzer(
            modules: [transcriber],
            options: SpeechAnalyzer.Options(priority: .userInitiated, modelRetention: .processLifetime)
        )

        try await analyzer.prepareToAnalyze(in: analysisFormat)

        if !contextTerms.isEmpty {
            let context = AnalysisContext()
            context.contextualStrings[.general] = contextTerms
            try await analyzer.setContext(context)
            emitter.status("Set \(contextTerms.count) contextual vocabulary terms.")
        }
    }

    func start() {
        resultsTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await result in transcriber.results {
                    await self.emit(result)
                }
            } catch {
                emitter.error("\(source) Apple Speech results failed: \(error.localizedDescription)")
            }
        }

        analyzerTask = Task { [weak self] in
            guard let self else { return }
            do {
                _ = try await analyzer.analyzeSequence(inputStream)
            } catch {
                emitter.error("\(source) Apple Speech analysis failed: \(error.localizedDescription)")
            }
        }
    }

    func append(sampleBuffer: CMSampleBuffer) {
        guard let pcmBuffer = Self.makePCMBuffer(from: sampleBuffer, fallbackFormat: inputFormat) else {
            return
        }

        converterQueue.async { [weak self] in
            guard let self else { return }
            guard let converted = self.convertIfNeeded(pcmBuffer) else { return }
            self.inputContinuation.yield(AnalyzerInput(buffer: converted))
        }
    }

    func finish() async {
        inputContinuation.finish()
        try? await analyzer.finalizeAndFinishThroughEndOfInput()
        analyzerTask?.cancel()
        resultsTask?.cancel()
    }

    private func emit(_ result: SpeechTranscriber.Result) async {
        let text = String(result.text).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        let now = Date()
        if text == lastText && now.timeIntervalSince(lastEmissionTime) < 1.0 {
            return
        }
        lastText = text
        lastEmissionTime = now

        var totalConfidence = 0.0
        var wordCount = 0
        for run in result.text.runs {
            if let conf = run.transcriptionConfidence {
                totalConfidence += conf
                wordCount += 1
            }
        }
        let avgConfidence = wordCount > 0 ? totalConfidence / Double(wordCount) : 0.0

        emitter.transcript(TranscriptEvent(
            event: "transcript",
            source: source,
            speaker: speaker,
            text: text,
            start_time: result.range.start.seconds.isFinite ? result.range.start.seconds : 0,
            end_time: result.range.end.seconds.isFinite ? result.range.end.seconds : 0,
            confidence: avgConfidence,
            is_final: result.isFinal
        ))
    }

    private func convertIfNeeded(_ inputBuffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard inputBuffer.format != analysisFormat else {
            return inputBuffer
        }

        if converter == nil || converter?.inputFormat != inputBuffer.format {
            converter = AVAudioConverter(from: inputBuffer.format, to: analysisFormat)
        }

        guard let converter else { return nil }

        let ratio = analysisFormat.sampleRate / inputBuffer.format.sampleRate
        let frameCapacity = AVAudioFrameCount(Double(inputBuffer.frameLength) * ratio) + 1024
        guard let outputBuffer = AVAudioPCMBuffer(pcmFormat: analysisFormat, frameCapacity: frameCapacity) else {
            return nil
        }

        var didProvideInput = false
        var conversionError: NSError?
        converter.convert(to: outputBuffer, error: &conversionError) { _, status in
            if didProvideInput {
                status.pointee = .noDataNow
                return nil
            }
            didProvideInput = true
            status.pointee = .haveData
            return inputBuffer
        }

        if let conversionError {
            emitter.error("\(source) audio conversion failed: \(conversionError.localizedDescription)")
            return nil
        }

        return outputBuffer.frameLength > 0 ? outputBuffer : nil
    }

    private static func makePCMBuffer(from sampleBuffer: CMSampleBuffer, fallbackFormat: AVAudioFormat) -> AVAudioPCMBuffer? {
        let frameCount = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frameCount > 0 else { return nil }

        let format: AVAudioFormat
        if let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
           let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription),
           let sampleFormat = AVAudioFormat(streamDescription: streamDescription) {
            format = sampleFormat
        } else {
            format = fallbackFormat
        }

        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)) else {
            return nil
        }

        buffer.frameLength = AVAudioFrameCount(frameCount)
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer,
            at: 0,
            frameCount: Int32(frameCount),
            into: buffer.mutableAudioBufferList
        )

        return status == noErr ? buffer : nil
    }
}

final class AudioFileWriter {
    private let audioFile: AVAudioFile
    private let lock = NSLock()
    private let writeFormat: AVAudioFormat

    init(path: String, sampleRate: Double = 48_000, channels: UInt32 = 1) throws {
        let url = URL(fileURLWithPath: path)
        writeFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: channels, interleaved: false)!
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: channels,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        audioFile = try AVAudioFile(forWriting: url, settings: settings)
    }

    func write(sampleBuffer: CMSampleBuffer) {
        let frameCount = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frameCount > 0 else { return }

        let format: AVAudioFormat
        if let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
           let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription),
           let sampleFormat = AVAudioFormat(streamDescription: streamDescription) {
            format = sampleFormat
        } else {
            format = writeFormat
        }

        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)) else { return }
        buffer.frameLength = AVAudioFrameCount(frameCount)

        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer, at: 0, frameCount: Int32(frameCount), into: buffer.mutableAudioBufferList
        )
        guard status == noErr else { return }

        lock.lock()
        defer { lock.unlock() }
        try? audioFile.write(from: buffer)
    }

    func close() {
        lock.lock()
        defer { lock.unlock() }
        // AVAudioFile flushes on dealloc
    }
}

@available(macOS 26.0, *)
final class CaptureCoordinator: NSObject, SCStreamOutput, SCStreamDelegate {
    private let emitter: JSONEmitter
    private let localeIdentifier: String
    private let contextTerms: [String]
    private let qualityMode: String
    private let captureSystemAudio: Bool
    private let captureMicrophone: Bool
    private let saveAudioPath: String?
    private let queue = DispatchQueue(label: "macmeetingtranscriber.apple-speech.capture")

    private var stream: SCStream?
    private var systemSource: SpeechSource?
    private var microphoneSource: SpeechSource?
    private var audioWriter: AudioFileWriter?

    init(localeIdentifier: String, contextTerms: [String], qualityMode: String, captureSystemAudio: Bool, captureMicrophone: Bool, saveAudioPath: String?, emitter: JSONEmitter) {
        self.localeIdentifier = localeIdentifier
        self.contextTerms = contextTerms
        self.qualityMode = qualityMode
        self.captureSystemAudio = captureSystemAudio
        self.captureMicrophone = captureMicrophone
        self.saveAudioPath = saveAudioPath
        self.emitter = emitter
    }

    func start() async throws {
        if let path = saveAudioPath {
            do {
                audioWriter = try AudioFileWriter(path: path, sampleRate: 48_000, channels: 1)
                emitter.status("Saving system audio to: \(path)")
            } catch {
                emitter.error("Failed to create audio file writer: \(error.localizedDescription)")
            }
        }

        if captureSystemAudio {
            try await requestScreenCaptureAccess()
        }

        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "MacMeetingTranscriberAppleSpeech", code: 20, userInfo: [
                NSLocalizedDescriptionKey: "No display is available for ScreenCaptureKit audio capture."
            ])
        }

        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.queueDepth = 3
        configuration.capturesAudio = captureSystemAudio
        configuration.captureMicrophone = captureMicrophone
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 1

        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        self.stream = stream

        let systemInputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 1, interleaved: false)!
        let microphoneInputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 1, interleaved: false)!

        if captureSystemAudio {
            systemSource = try await SpeechSource(
                source: "system",
                speaker: "Other",
                localeIdentifier: localeIdentifier,
                contextTerms: contextTerms,
                qualityMode: qualityMode,
                inputFormat: systemInputFormat,
                emitter: emitter
            )
            systemSource?.start()
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        }

        if captureMicrophone {
            microphoneSource = try await SpeechSource(
                source: "microphone",
                speaker: "You",
                localeIdentifier: localeIdentifier,
                contextTerms: contextTerms,
                qualityMode: qualityMode,
                inputFormat: microphoneInputFormat,
                emitter: emitter
            )
            microphoneSource?.start()
            try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }

        try await stream.startCapture()
        emitter.status("Apple Speech capture started.")
    }

    func stop() async {
        if let stream {
            try? await stream.stopCapture()
        }
        await systemSource?.finish()
        await microphoneSource?.finish()
        audioWriter?.close()
        emitter.status("Apple Speech capture stopped.")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sampleBuffer.isValid else { return }

        switch type {
        case .audio:
            systemSource?.append(sampleBuffer: sampleBuffer)
            audioWriter?.write(sampleBuffer: sampleBuffer)
        case .microphone:
            microphoneSource?.append(sampleBuffer: sampleBuffer)
        default:
            break
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        emitter.error("ScreenCaptureKit stopped: \(error.localizedDescription)")
    }

    private func requestScreenCaptureAccess() async throws {
        if CGPreflightScreenCaptureAccess() {
            return
        }

        emitter.status("Requesting Screen Recording permission for system audio capture.")
        let granted = CGRequestScreenCaptureAccess()
        if !granted {
            throw NSError(domain: "MacMeetingTranscriberAppleSpeech", code: 30, userInfo: [
                NSLocalizedDescriptionKey: "Screen Recording permission is required to capture system audio. Enable it in System Settings > Privacy & Security > Screen & System Audio Recording, then restart Mac Meeting Transcriber."
            ])
        }
    }

}

@main
struct MacNativeSpeechTranscriber {
    static func main() async {
        let emitter = JSONEmitter()

        guard #available(macOS 26.0, *) else {
            emitter.error("Apple SpeechTranscriber requires macOS 26.0 or newer.")
            exit(2)
        }

        let arguments = CommandLine.arguments
        let locale = value(after: "--locale", in: arguments) ?? "en_US"
        let source = value(after: "--source", in: arguments) ?? "both"
        let duration = Double(value(after: "--duration", in: arguments) ?? "")
        let qualityMode = value(after: "--quality", in: arguments) ?? "fast"
        let saveAudioPath = value(after: "--save-audio", in: arguments)
        let contextTermsRaw = value(after: "--context-terms", in: arguments) ?? ""
        let contextTerms = contextTermsRaw.isEmpty ? [String]() :
            contextTermsRaw.components(separatedBy: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }

        let captureSystemAudio = source == "both" || source == "system"
        let captureMicrophone = source == "both" || source == "microphone"

        guard captureSystemAudio || captureMicrophone else {
            emitter.error("Use --source both, --source system, or --source microphone.")
            exit(2)
        }

        let coordinator = CaptureCoordinator(
            localeIdentifier: locale,
            contextTerms: contextTerms,
            qualityMode: qualityMode,
            captureSystemAudio: captureSystemAudio,
            captureMicrophone: captureMicrophone,
            saveAudioPath: saveAudioPath,
            emitter: emitter
        )

        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)

        let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT)
        interruptSource.setEventHandler {
            Task {
                await coordinator.stop()
                exit(0)
            }
        }
        interruptSource.resume()

        let terminateSource = DispatchSource.makeSignalSource(signal: SIGTERM)
        terminateSource.setEventHandler {
            Task {
                await coordinator.stop()
                exit(0)
            }
        }
        terminateSource.resume()

        do {
            try await coordinator.start()
            if let duration {
                try await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
                await coordinator.stop()
                exit(0)
            }

            while true {
                try await Task.sleep(nanoseconds: 1_000_000_000)
            }
        } catch {
            emitter.error(error.localizedDescription)
            exit(1)
        }
    }

    private static func value(after flag: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: flag),
              arguments.indices.contains(index + 1) else {
            return nil
        }
        return arguments[index + 1]
    }
}
