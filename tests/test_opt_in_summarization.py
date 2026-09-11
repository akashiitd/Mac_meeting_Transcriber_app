import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock

from simple_recorder import SimpleRecorder


class OptInSummarizationTests(unittest.IsolatedAsyncioTestCase):
    """Verify that meeting summaries require an explicit opt-in."""

    def make_recorder(self, root: Path) -> SimpleRecorder:
        """Create a recorder whose files are isolated in a temporary directory."""
        recorder = SimpleRecorder.__new__(SimpleRecorder)
        recorder.transcriber = None
        recorder.summarizer = None
        recorder.recordings_dir = root / "recordings"
        recorder.transcripts_dir = root / "transcripts"
        recorder.output_dir = root / "output"
        recorder.state_file = root / "recorder_state.json"
        for directory in (
            recorder.recordings_dir,
            recorder.transcripts_dir,
            recorder.output_dir,
        ):
            directory.mkdir()
        return recorder

    def make_audio_file(self, root: Path) -> Path:
        """Create a valid one-second WAV fixture."""
        audio_path = root / "meeting.wav"
        with wave.open(str(audio_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(b"\x00\x00" * 16_000)
        return audio_path

    async def test_processing_defaults_to_transcript_only_without_calling_ollama(self):
        """Leave Ollama untouched when processing uses the default behavior."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            recorder = self.make_recorder(root)
            audio_path = self.make_audio_file(root)
            recorder.transcribe_audio = AsyncMock(
                return_value={
                    "transcript_file": str(root / "transcript.txt"),
                    "transcript_text": "A useful meeting transcript",
                }
            )
            recorder.summarize_transcript = AsyncMock()

            result = await recorder.process_recording(str(audio_path), "Planning")

            recorder.summarize_transcript.assert_not_awaited()
            self.assertFalse(result["session_info"]["summarization_enabled"])
            self.assertEqual("", result["summary"])
            self.assertEqual("A useful meeting transcript", result["transcript"])

    async def test_legacy_summary_option_does_not_start_ollama(self):
        """Old callers cannot turn summaries back on."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            recorder = self.make_recorder(root)
            audio_path = self.make_audio_file(root)
            recorder.transcribe_audio = AsyncMock(
                return_value={
                    "transcript_file": str(root / "transcript.txt"),
                    "transcript_text": "A useful meeting transcript",
                }
            )
            recorder.summarize_transcript = AsyncMock(
                return_value={
                    "summary": "The generated summary",
                    "participants": ["Akash"],
                    "discussion_areas": [],
                    "key_points": ["Ship it"],
                    "action_items": [],
                }
            )

            result = await recorder.process_recording(
                str(audio_path), "Planning", summarize=True
            )

            recorder.summarize_transcript.assert_not_awaited()
            self.assertFalse(result["session_info"]["summarization_enabled"])
            self.assertEqual("", result["summary"])


if __name__ == "__main__":
    unittest.main()
