import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from src.apple_speech_transcriber import AppleSpeechTranscriber
from src.realtime_transcriber import create_realtime_transcriber


class _EmptyStream:
    def __iter__(self):
        return iter(())


class _FakeProcess:
    stdout = ['{"event":"status","message":"Apple Speech capture started."}\n']
    stderr = _EmptyStream()

    def poll(self):
        return 0


class AppleSpeechDeviceSelectionTests(unittest.TestCase):
    def test_selected_device_does_not_use_a_possibly_stale_bundled_helper(self):
        transcriber = AppleSpeechTranscriber(
            system_audio_device="CANDIDATE_ONLY",
            context_terms=[],
        )

        self.assertIsNone(transcriber._find_bundled_helper())

    def test_helper_receives_the_exact_requested_system_audio_device(self):
        transcriber = AppleSpeechTranscriber(
            enable_system_audio=True,
            enable_microphone=False,
            system_audio_device="CANDIDATE_ONLY",
            context_terms=[],
        )

        with patch.object(
            transcriber,
            "_ensure_helper_binary",
            return_value=Path("/tmp/mac_native_speech_transcriber"),
        ), patch(
            "src.apple_speech_transcriber.subprocess.Popen",
            return_value=_FakeProcess(),
        ) as popen:
            self.assertTrue(transcriber.start())

        command = popen.call_args.args[0]
        self.assertIn("--system-audio-device", command)
        device_index = command.index("--system-audio-device")
        self.assertEqual("CANDIDATE_ONLY", command[device_index + 1])

    def test_factory_preserves_system_audio_device_for_apple_speech(self):
        with patch("src.realtime_transcriber.RealtimeTranscriber") as transcriber_class:
            transcriber, _ = create_realtime_transcriber(
                enable_system_audio=True,
                enable_microphone=False,
                system_audio_device="CANDIDATE_ONLY",
                transcription_backend="apple-speech",
                enable_live_logging=False,
            )

        self.assertIs(transcriber, transcriber_class.return_value)
        self.assertEqual(
            "CANDIDATE_ONLY",
            transcriber_class.call_args.kwargs["system_audio_device"],
        )


if __name__ == "__main__":
    unittest.main()
