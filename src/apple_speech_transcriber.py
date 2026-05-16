"""
Bridge for Apple's native macOS Speech framework live transcription.

The Swift helper captures built-in macOS system audio and microphone streams
with ScreenCaptureKit, transcribes each stream with SpeechAnalyzer, and emits
JSON lines that this bridge converts into TranscriptSegment objects.
"""

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from src.realtime_transcriber import TranscriptSegment, is_hallucination
from src.config import get_config

logger = logging.getLogger(__name__)

MIN_CONFIDENCE_THRESHOLD = 0.3
PARTIAL_FLUSH_DELAY_SECONDS = 4.5


class AppleSpeechTranscriber:
    """Run the native Swift Apple Speech helper as a subprocess."""

    def __init__(
        self,
        language: str = "en",
        enable_system_audio: bool = True,
        enable_microphone: bool = True,
        callback: Optional[Callable[[TranscriptSegment], None]] = None,
        context_terms: Optional[list] = None,
        quality_mode: Optional[str] = None,
        save_audio_path: Optional[str] = None,
    ):
        config = get_config()
        self.language = language
        self.enable_system_audio = enable_system_audio
        self.enable_microphone = enable_microphone
        self.callback = callback
        self.context_terms = context_terms if context_terms is not None else config.get_context_terms()
        self.quality_mode = quality_mode or config.get_transcription_quality_mode()
        self.save_audio_path = save_audio_path

        self.process: Optional[subprocess.Popen] = None
        self.stdout_thread: Optional[threading.Thread] = None
        self.stderr_thread: Optional[threading.Thread] = None
        self.running = False
        self.last_text_by_source: dict[str, tuple[str, float]] = {}
        self.pending_by_source: dict[str, dict] = {}
        self.pending_timers: dict[str, threading.Timer] = {}
        self.pending_lock = threading.Lock()
        self.partial_flush_delay = PARTIAL_FLUSH_DELAY_SECONDS

    def start(self) -> bool:
        """Compile if needed, then start the helper process."""
        if self.running:
            return True

        helper = self._ensure_helper_binary()
        source = self._source_argument()
        if source is None:
            logger.error("Apple Speech requires microphone, system audio, or both to be enabled")
            return False

        command = [
            str(helper),
            "--locale",
            self._locale_identifier(),
            "--source",
            source,
            "--quality",
            self.quality_mode,
        ]

        if self.context_terms:
            command.extend(["--context-terms", ",".join(self.context_terms)])

        if self.save_audio_path:
            command.extend(["--save-audio", self.save_audio_path])

        logger.info("Starting Apple Speech helper: %s", " ".join(command))

        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            logger.error("Failed to start Apple Speech helper: %s", e)
            return False

        self.running = True
        self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()
        return True

    def stop(self) -> None:
        """Stop the helper process and reader threads."""
        self.running = False

        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

        if self.stdout_thread:
            self.stdout_thread.join(timeout=2)
        if self.stderr_thread:
            self.stderr_thread.join(timeout=2)

        self._flush_all_pending()

        self.process = None

    def _read_stdout(self) -> None:
        if not self.process or not self.process.stdout:
            return

        for line in self.process.stdout:
            if not self.running and not line:
                break
            self._handle_line(line.strip())

    def _read_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return

        for line in self.process.stderr:
            line = line.strip()
            if line:
                logger.warning("Apple Speech helper stderr: %s", line)

    def _handle_line(self, line: str) -> None:
        if not line:
            return

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Apple Speech helper output: %s", line)
            return

        event = payload.get("event")
        if event == "status":
            logger.info("Apple Speech: %s", payload.get("message", ""))
            return
        if event == "error":
            logger.error("Apple Speech: %s", payload.get("message", ""))
            return
        if event != "transcript":
            return

        self._handle_transcript_payload(payload)

    def _handle_transcript_payload(self, payload: dict) -> None:
        text = (payload.get("text") or "").strip()
        if len(text) < 4 or is_hallucination(text):
            return

        source = payload.get("source") or "unknown"
        if payload.get("is_final") is False:
            self._schedule_pending_flush(source, payload)
            return

        self._cancel_pending_flush(source)
        self._emit_payload(payload)

    def _schedule_pending_flush(self, source: str, payload: dict) -> None:
        flush_now = None

        with self.pending_lock:
            existing = self.pending_timers.get(source)
            if existing:
                existing.cancel()

            previous_payload = self.pending_by_source.get(source)
            if previous_payload and not self._is_same_phrase_update(previous_payload, payload):
                flush_now = previous_payload

            self.pending_by_source[source] = payload
            timer = threading.Timer(
                self.partial_flush_delay,
                self._flush_pending_source,
                args=(source,),
            )
            timer.daemon = True
            self.pending_timers[source] = timer
            timer.start()

        if flush_now:
            self._emit_payload(flush_now)

    def _cancel_pending_flush(self, source: str) -> None:
        with self.pending_lock:
            timer = self.pending_timers.pop(source, None)
            if timer:
                timer.cancel()
            self.pending_by_source.pop(source, None)

    def _flush_pending_source(self, source: str) -> None:
        with self.pending_lock:
            payload = self.pending_by_source.pop(source, None)
            self.pending_timers.pop(source, None)

        if payload:
            self._emit_payload(payload)

    def _flush_all_pending(self) -> None:
        with self.pending_lock:
            pending = list(self.pending_by_source.values())
            timers = list(self.pending_timers.values())
            self.pending_by_source.clear()
            self.pending_timers.clear()

        for timer in timers:
            timer.cancel()
        for payload in pending:
            self._emit_payload(payload)

    def _emit_payload(self, payload: dict) -> None:
        text = (payload.get("text") or "").strip()
        if len(text) < 4 or is_hallucination(text):
            return

        confidence = float(payload.get("confidence") or 0.0)
        if confidence > 0.0 and confidence < MIN_CONFIDENCE_THRESHOLD:
            logger.debug("Skipping low-confidence segment (%.2f): %s", confidence, text[:50])
            return

        source = payload.get("source") or "unknown"
        now = time.time()
        previous_text, previous_time = self.last_text_by_source.get(source, ("", 0.0))
        if text == previous_text and now - previous_time < 1.5:
            return
        self.last_text_by_source[source] = (text, now)

        segment = TranscriptSegment(
            text=text,
            start_time=float(payload.get("start_time") or 0.0),
            end_time=float(payload.get("end_time") or 0.0),
            speaker=payload.get("speaker") or ("You" if source == "microphone" else "Other"),
            source=source,
            confidence=confidence,
        )

        if self.callback:
            self.callback(segment)

    def _is_same_phrase_update(self, previous_payload: dict, payload: dict) -> bool:
        previous_start = float(previous_payload.get("start_time") or 0.0)
        previous_end = float(previous_payload.get("end_time") or previous_start)
        current_start = float(payload.get("start_time") or 0.0)

        if current_start <= previous_end + 0.3:
            return True

        previous_text = (previous_payload.get("text") or "").strip().lower()
        current_text = (payload.get("text") or "").strip().lower()
        return current_text.startswith(previous_text) or previous_text.startswith(current_text)

    def _ensure_helper_binary(self) -> Path:
        source = Path(__file__).with_name("mac_native_speech_transcriber.swift")
        info_plist = Path(__file__).with_name("mac_native_speech_transcriber_info.plist")
        bundled = self._find_bundled_helper()
        if bundled:
            return bundled

        cache_dir = Path.home() / "Library" / "Application Support" / "mac-meeting-transcriber" / "helpers"
        cache_dir.mkdir(parents=True, exist_ok=True)
        binary = cache_dir / "mac_native_speech_transcriber"

        if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
            return binary

        logger.info("Compiling Apple Speech helper")
        command = [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            str(source),
            "-Xlinker",
            "-sectcreate",
            "-Xlinker",
            "__TEXT",
            "-Xlinker",
            "__info_plist",
            "-Xlinker",
            str(info_plist),
            "-o",
            str(binary),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to compile Apple Speech helper. "
                "Install Xcode Command Line Tools and retry.\n"
                f"{result.stderr}"
            )

        return binary

    def _find_bundled_helper(self) -> Optional[Path]:
        """Find a prebuilt helper in development or packaged Electron layouts."""
        candidates = [
            Path(__file__).parent.parent / "app" / "native" / "mac_native_speech_transcriber",
            Path.cwd() / "app" / "native" / "mac_native_speech_transcriber",
            Path.cwd() / "native" / "mac_native_speech_transcriber",
        ]

        resources_path = os.environ.get("RESOURCEPATH")
        if resources_path:
            candidates.extend([
                Path(resources_path) / "native" / "mac_native_speech_transcriber",
                Path(resources_path) / "app" / "native" / "mac_native_speech_transcriber",
            ])

        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _source_argument(self) -> Optional[str]:
        if self.enable_system_audio and self.enable_microphone:
            return "both"
        if self.enable_system_audio:
            return "system"
        if self.enable_microphone:
            return "microphone"
        return None

    def _locale_identifier(self) -> str:
        config = get_config()
        config_locale = config.get_transcription_locale()
        if config_locale and config_locale != "en_US":
            return config_locale

        language = (self.language or "en").replace("-", "_")
        if "_" in language:
            return language

        language_map = {
            "en": "en_US",
            "hi": "hi_IN",
            "es": "es_ES",
            "fr": "fr_FR",
            "de": "de_DE",
            "it": "it_IT",
            "ja": "ja_JP",
            "ko": "ko_KR",
            "pt": "pt_BR",
            "zh": "zh_CN",
        }
        return language_map.get(language, language)
