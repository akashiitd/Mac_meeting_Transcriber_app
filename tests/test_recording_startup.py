import io
import json
import signal
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

from click.testing import CliRunner
from simple_recorder import cli

from src.apple_speech_transcriber import AppleSpeechTranscriber


class RecordingStartupTests(unittest.TestCase):
    def test_record_command_saves_only_transcript_and_announces_readiness(self):
        from src.realtime_transcriber import TranscriptSegment

        runner = CliRunner()
        segment = TranscriptSegment(text='The meeting begins now.', start_time=0, end_time=1, speaker='You', source='microphone')
        transcriber = Mock()
        transcriber.start.return_value = True
        transcriber.stop.return_value = [segment]
        transcriber.get_full_transcript.return_value = segment.text
        previous_signals = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            with runner.isolated_filesystem(), patch('simple_recorder.create_realtime_transcriber', return_value=(transcriber, None)) as factory:
                result = runner.invoke(cli, ['record', '0', 'startup-check', '--summarize'])
                self.assertEqual(0, result.exit_code, result.output)
                self.assertIn('{"event": "recording-ready"}', result.output)
                self.assertEqual('apple-speech', factory.call_args.kwargs['transcription_backend'])
                meeting = json.loads(next(Path('output').glob('*.json')).read_text())
                self.assertEqual('[You]: The meeting begins now.', meeting['transcript'])
                self.assertEqual('', meeting['summary'])
                self.assertFalse(meeting['session_info']['summarization_enabled'])
        finally:
            for sig, handler in previous_signals.items():
                signal.signal(sig, handler)

    def test_entry_point_does_not_import_unused_ai_backends(self):
        subprocess.run([
            sys.executable, "-c",
            "import simple_recorder, sys; "
            "assert not {'whisper', 'torch', 'ollama'} & sys.modules.keys()",
        ], cwd=Path(__file__).resolve().parents[1], check=True)

    def test_start_waits_for_capture_and_rejects_native_failure(self):
        for event, expected in [
            ('{"event":"status","message":"Apple Speech capture started."}\n', True),
            ('{"event":"error","message":"Permission denied"}\n', False),
            ('', False),
        ]:
            with self.subTest(event=event):
                release = threading.Event()
                reading = threading.Event()

                class Output:
                    def __iter__(self):
                        reading.set()
                        release.wait(2)
                        if event:
                            yield event

                process = Mock(stdout=Output(), stderr=io.StringIO())
                process.poll.return_value = None
                transcriber = AppleSpeechTranscriber(context_terms=[])
                result = []
                with patch.object(transcriber, '_ensure_helper_binary', return_value=Path('/tmp/helper')), patch(
                    'src.apple_speech_transcriber.subprocess.Popen', return_value=process
                ):
                    worker = threading.Thread(target=lambda: result.append(transcriber.start()))
                    worker.start()
                    try:
                        self.assertTrue(reading.wait(1))
                        self.assertEqual([], result, 'Process spawn is not capture readiness')
                    finally:
                        release.set()
                        worker.join(3)
                        transcriber.stop()
                    self.assertEqual([expected], result)


if __name__ == '__main__':
    unittest.main()
