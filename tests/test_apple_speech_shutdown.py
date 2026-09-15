import subprocess
import threading
import time
import unittest

from src import apple_speech_transcriber
from src.apple_speech_transcriber import (
    READER_JOIN_SECONDS,
    SHUTDOWN_GRACE_SECONDS,
    SHUTDOWN_KILL_WAIT_SECONDS,
    AppleSpeechTranscriber,
)


class FakeProcess:
    """Stands in for the Swift helper subprocess."""

    def __init__(self, exit_delay: float = 0.0, ignores_terminate: bool = False):
        self.exit_delay = exit_delay
        self.ignores_terminate = ignores_terminate
        self.terminated = False
        self.killed = False
        self._exit_at = None

    def poll(self):
        if self._exit_at is not None and time.monotonic() >= self._exit_at:
            return 0
        return None

    def terminate(self):
        self.terminated = True
        if not self.ignores_terminate:
            self._exit_at = time.monotonic() + self.exit_delay

    def kill(self):
        self.killed = True
        self._exit_at = time.monotonic()

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while time.monotonic() <= deadline:
            if self.poll() is not None:
                return 0
            time.sleep(0.01)
        if self.poll() is not None:
            return 0
        raise subprocess.TimeoutExpired(cmd="helper", timeout=timeout)


def build_transcriber(process, callback=None):
    transcriber = AppleSpeechTranscriber(
        callback=callback,
        context_terms=[],
        quality_mode="fast",
    )
    transcriber.process = process
    transcriber.running = True
    return transcriber


class AppleSpeechShutdownTests(unittest.TestCase):
    def test_cooperative_helper_stops_without_waiting_out_the_grace_period(self):
        process = FakeProcess(exit_delay=0.05)
        transcriber = build_transcriber(process)

        started = time.monotonic()
        transcriber.stop()
        elapsed = time.monotonic() - started

        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)
        self.assertLess(elapsed, 0.5)
        self.assertIsNone(transcriber.process)

    def test_wedged_helper_is_killed_within_the_shutdown_budget(self):
        process = FakeProcess(ignores_terminate=True)
        transcriber = build_transcriber(process)

        started = time.monotonic()
        transcriber.stop()
        elapsed = time.monotonic() - started

        budget = SHUTDOWN_GRACE_SECONDS + SHUTDOWN_KILL_WAIT_SECONDS + (2 * READER_JOIN_SECONDS)
        self.assertTrue(process.killed)
        self.assertLess(elapsed, budget + 0.5)

    def test_stop_does_not_wait_on_stalled_reader_threads(self):
        keep_running = threading.Event()
        process = FakeProcess(exit_delay=0.0)
        transcriber = build_transcriber(process)
        stalled = threading.Thread(target=keep_running.wait, daemon=True)
        stalled.start()
        transcriber.stdout_thread = stalled
        transcriber.stderr_thread = stalled

        started = time.monotonic()
        try:
            transcriber.stop()
        finally:
            keep_running.set()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, (2 * READER_JOIN_SECONDS) + 0.5)

    def test_pending_partial_text_survives_a_forced_shutdown(self):
        captured = []
        process = FakeProcess(ignores_terminate=True)
        transcriber = build_transcriber(process, callback=captured.append)
        transcriber._handle_transcript_payload({
            "event": "transcript",
            "source": "microphone",
            "speaker": "You",
            "text": "the last thing anyone said before stopping",
            "start_time": 10.0,
            "end_time": 12.0,
            "confidence": 0.8,
            "is_final": False,
        })

        transcriber.stop()

        self.assertEqual(1, len(captured))
        self.assertEqual("the last thing anyone said before stopping", captured[0].text)

    def test_shutdown_budget_leaves_room_for_the_helper_to_exit_on_its_own(self):
        self.assertGreater(SHUTDOWN_GRACE_SECONDS, apple_speech_transcriber.PARTIAL_FLUSH_DELAY_SECONDS / 2)
        self.assertLessEqual(
            SHUTDOWN_GRACE_SECONDS + SHUTDOWN_KILL_WAIT_SECONDS + (2 * READER_JOIN_SECONDS),
            5.0,
        )


if __name__ == "__main__":
    unittest.main()
