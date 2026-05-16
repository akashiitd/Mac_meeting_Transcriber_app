import json
import tempfile
import time
import unittest
from pathlib import Path

from src.apple_speech_transcriber import (
    AppleSpeechTranscriber,
    PARTIAL_FLUSH_DELAY_SECONDS,
)
from src.config import Config
from src.realtime_transcriber import (
    LiveTranscriptLogger,
    RealtimeTranscriber,
    TranscriptSegment,
)


def apple_payload(text, start_time, end_time, is_final, confidence=0.8):
    return {
        "event": "transcript",
        "source": "microphone",
        "speaker": "You",
        "text": text,
        "start_time": start_time,
        "end_time": end_time,
        "confidence": confidence,
        "is_final": is_final,
    }


class AppleSpeechPartialTests(unittest.TestCase):
    def test_final_result_replaces_pending_volatile_partials(self):
        captured = []
        transcriber = AppleSpeechTranscriber(
            callback=captured.append,
            context_terms=[],
            quality_mode="balanced",
        )

        transcriber._handle_transcript_payload(apple_payload(
            "And someone who has experience and cloud services like",
            1267.98,
            1275.00,
            False,
            confidence=0.0,
        ))
        transcriber._handle_transcript_payload(apple_payload(
            "And someone who has experience and cloud services like what XCI sage maker asure am that it is good to have",
            1267.98,
            1278.86,
            False,
            confidence=0.0,
        ))
        transcriber._handle_transcript_payload(apple_payload(
            "And someone who has experience in cloud services like what XCI, sage maker, as you are, that it is good to have.",
            1269.36,
            1278.84,
            True,
            confidence=0.70,
        ))

        self.assertEqual(1, len(captured))
        self.assertEqual(
            "And someone who has experience in cloud services like what XCI, sage maker, as you are, that it is good to have.",
            captured[0].text,
        )
        self.assertEqual({}, transcriber.pending_by_source)

    def test_non_final_results_emit_only_after_fallback_delay(self):
        captured = []
        transcriber = AppleSpeechTranscriber(
            callback=captured.append,
            context_terms=[],
            quality_mode="balanced",
        )
        self.assertGreaterEqual(PARTIAL_FLUSH_DELAY_SECONDS, 4.0)
        transcriber.partial_flush_delay = 0.01

        transcriber._handle_transcript_payload(apple_payload(
            "So we are looking for someone who has experience",
            1245.42,
            1248.18,
            False,
            confidence=0.0,
        ))

        self.assertEqual([], captured)
        time.sleep(0.05)
        self.assertEqual(1, len(captured))
        self.assertEqual(
            "So we are looking for someone who has experience",
            captured[0].text,
        )

    def test_realtime_transcriber_collapses_overlapping_replacements(self):
        transcriber = RealtimeTranscriber(
            enable_system_audio=False,
            enable_microphone=False,
            transcription_backend="apple-speech",
        )

        transcriber._handle_external_transcript_segment(TranscriptSegment(
            text="And someone who has experience and cloud services like",
            start_time=1267.98,
            end_time=1275.00,
            speaker="You",
            source="microphone",
        ))
        transcriber._handle_external_transcript_segment(TranscriptSegment(
            text="And someone who has experience in cloud services like what XCI, sage maker, as you are, that it is good to have.",
            start_time=1269.36,
            end_time=1278.84,
            speaker="You",
            source="microphone",
        ))

        segments = transcriber.get_segments()
        self.assertEqual(1, len(segments))
        self.assertEqual(
            "And someone who has experience in cloud services like what XCI, sage maker, as you are, that it is good to have.",
            segments[0].text,
        )

    def test_live_logger_rewrites_replaced_partial_segments(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            logger = LiveTranscriptLogger(output_dir=tmp_dir, session_name="partial-test")
            partial = TranscriptSegment(
                text="And someone who has experience and cloud services like",
                start_time=1267.98,
                end_time=1275.00,
                speaker="You",
                source="microphone",
            )
            final = TranscriptSegment(
                text="And someone who has experience in cloud services like what XCI, sage maker, as you are, that it is good to have.",
                start_time=1269.36,
                end_time=1278.84,
                speaker="You",
                source="microphone",
            )

            logger.log_segment(partial)
            logger.log_segment(final)

            with open(Path(tmp_dir) / "partial-test_live.json") as f:
                payload = json.load(f)
            self.assertEqual(1, len(payload["segments"]))
            self.assertEqual(final.text, payload["segments"][0]["text"])

            text_log = (Path(tmp_dir) / "partial-test_live.txt").read_text()
            self.assertNotIn(partial.text, text_log)
            self.assertIn(final.text, text_log)


class ConfigQualityModeTests(unittest.TestCase):
    def test_missing_transcription_context_defaults_to_fast(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "model": "llama3.2:3b",
                "realtime_transcription_model": "apple-speech",
                "notifications_enabled": True,
                "version": "1.0",
            }))

            self.assertEqual("fast", Config(config_path).get_transcription_quality_mode())

    def test_explicit_balanced_quality_mode_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "transcription_context": {
                    "quality_mode": "balanced",
                },
            }))

            self.assertEqual("balanced", Config(config_path).get_transcription_quality_mode())


if __name__ == "__main__":
    unittest.main()
