"""
Speaker diarization module using pyannote.audio.

Identifies and labels individual speakers in audio recordings,
enabling the app to distinguish between multiple remote participants
instead of labeling all system audio as "Other".
"""

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DiarizationSegment:
    speaker_label: str
    start_time: float
    end_time: float


class SpeakerDiarizer:
    """Wraps pyannote.audio speaker diarization pipeline."""

    def __init__(
        self,
        hf_token: str,
        num_speakers: Optional[int] = None,
        min_speakers: int = 2,
        max_speakers: int = 10,
        use_mps: bool = True,
    ):
        self.hf_token = hf_token
        self.num_speakers = num_speakers
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.use_mps = use_mps
        self._pipeline = None
        self._lock = threading.Lock()

    def _load_pipeline(self):
        """Lazy-load the pyannote diarization pipeline."""
        if self._pipeline is not None:
            return

        with self._lock:
            if self._pipeline is not None:
                return

            try:
                import torch
                from pyannote.audio import Pipeline

                logger.info("Loading pyannote speaker-diarization-3.1 pipeline...")
                self._pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=self.hf_token,
                )

                if self.use_mps and torch.backends.mps.is_available():
                    import torch
                    self._pipeline.to(torch.device("mps"))
                    logger.info("Using MPS (Metal) GPU acceleration for diarization")
                else:
                    logger.info("Using CPU for diarization")

            except Exception as e:
                logger.error(f"Failed to load diarization pipeline: {e}")
                raise

    def diarize_audio(self, audio_path: Path) -> List[DiarizationSegment]:
        """
        Run speaker diarization on a WAV file.

        Returns a list of DiarizationSegment with speaker labels and time boundaries.
        """
        self._load_pipeline()

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info(f"Running diarization on {audio_path.name}...")

        kwargs: Dict[str, Any] = {}
        if self.num_speakers is not None:
            kwargs["num_speakers"] = self.num_speakers
        else:
            kwargs["min_speakers"] = self.min_speakers
            kwargs["max_speakers"] = self.max_speakers

        diarization = self._pipeline(str(audio_path), **kwargs)

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(DiarizationSegment(
                speaker_label=speaker,
                start_time=turn.start,
                end_time=turn.end,
            ))

        logger.info(f"Diarization complete: {len(segments)} segments, "
                    f"{len(set(s.speaker_label for s in segments))} speakers detected")
        return segments

    def diarize_buffer(self, audio_array: np.ndarray, sample_rate: int) -> List[DiarizationSegment]:
        """
        Run diarization on in-memory audio (numpy array).

        Used for near-real-time incremental diarization on accumulated audio.
        """
        self._load_pipeline()

        import torch

        if audio_array.ndim == 1:
            audio_array = audio_array[np.newaxis, :]

        waveform = torch.from_numpy(audio_array).float()
        audio_input = {"waveform": waveform, "sample_rate": sample_rate}

        kwargs: Dict[str, Any] = {}
        if self.num_speakers is not None:
            kwargs["num_speakers"] = self.num_speakers
        else:
            kwargs["min_speakers"] = self.min_speakers
            kwargs["max_speakers"] = self.max_speakers

        diarization = self._pipeline(audio_input, **kwargs)

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(DiarizationSegment(
                speaker_label=speaker,
                start_time=turn.start,
                end_time=turn.end,
            ))

        return segments

    def assign_speakers_to_segments(
        self,
        transcript_segments: List[Any],
        diarization_segments: List[DiarizationSegment],
        speaker_names: Optional[Dict[str, str]] = None,
    ) -> List[Any]:
        """
        Align transcript segments with diarization results using maximum time-overlap.

        Only relabels segments where source == "system". Preserves "You" for mic segments.
        Maps pyannote labels (SPEAKER_00) to friendly names (Speaker 1).
        Applies custom speaker_names mapping if provided.
        """
        if not diarization_segments:
            return transcript_segments

        label_map = self._build_label_map(diarization_segments, speaker_names)

        for segment in transcript_segments:
            source = getattr(segment, 'source', None) or segment.get('source', None) if isinstance(segment, dict) else getattr(segment, 'source', None)
            if source == "microphone":
                continue

            seg_start = self._get_attr(segment, 'start_time')
            seg_end = self._get_attr(segment, 'end_time')
            if seg_start is None or seg_end is None:
                continue

            best_speaker = self._find_best_overlap(seg_start, seg_end, diarization_segments)
            if best_speaker:
                friendly_name = label_map.get(best_speaker, best_speaker)
                self._set_attr(segment, 'speaker', friendly_name)

        return transcript_segments

    def _find_best_overlap(
        self, start: float, end: float, diarization_segments: List[DiarizationSegment]
    ) -> Optional[str]:
        """Find the diarization speaker with maximum overlap for a given time range."""
        best_overlap = 0.0
        best_speaker = None

        for d_seg in diarization_segments:
            overlap_start = max(start, d_seg.start_time)
            overlap_end = min(end, d_seg.end_time)
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = d_seg.speaker_label

        return best_speaker

    def _build_label_map(
        self,
        diarization_segments: List[DiarizationSegment],
        speaker_names: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Build mapping from pyannote labels (SPEAKER_00) to friendly names (Speaker 1).
        Applies custom name overrides from speaker_names config.
        """
        unique_labels = sorted(set(s.speaker_label for s in diarization_segments))
        label_map = {}
        for i, label in enumerate(unique_labels):
            friendly = f"Speaker {i + 1}"
            label_map[label] = friendly

        if speaker_names:
            for friendly, custom_name in speaker_names.items():
                for raw_label, mapped_name in list(label_map.items()):
                    if mapped_name == friendly:
                        label_map[raw_label] = custom_name
                        break

        return label_map

    @staticmethod
    def _get_attr(segment, attr: str):
        """Get attribute from either a dataclass or dict."""
        if isinstance(segment, dict):
            return segment.get(attr)
        return getattr(segment, attr, None)

    @staticmethod
    def _set_attr(segment, attr: str, value):
        """Set attribute on either a dataclass or dict."""
        if isinstance(segment, dict):
            segment[attr] = value
        else:
            setattr(segment, attr, value)


class IncrementalDiarizer:
    """
    Runs speaker diarization periodically on accumulated audio during live recording.
    Updates transcript segment labels as new diarization results come in.
    """

    def __init__(
        self,
        diarizer: SpeakerDiarizer,
        audio_path: str,
        segments_list: list,
        segments_lock: threading.Lock,
        interval_seconds: float = 30.0,
        speaker_names: Optional[Dict[str, str]] = None,
        on_labels_updated: Optional[callable] = None,
    ):
        self._diarizer = diarizer
        self._audio_path = Path(audio_path)
        self._segments = segments_list
        self._segments_lock = segments_lock
        self._interval = interval_seconds
        self._speaker_names = speaker_names or {}
        self._on_labels_updated = on_labels_updated
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_processed_size = 0

    def start(self):
        """Start the incremental diarization background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Incremental diarization started (interval: %.0fs)", self._interval)

    def stop(self):
        """Stop the background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run_loop(self):
        """Periodically run diarization on accumulated audio."""
        import time
        import wave

        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break

            if not self._audio_path.exists():
                continue

            try:
                current_size = self._audio_path.stat().st_size
            except OSError:
                continue

            # Only re-run if audio has grown significantly (at least 5 seconds of 48kHz mono 16-bit)
            min_growth = 48000 * 2 * 5  # 5 seconds
            if current_size - self._last_processed_size < min_growth:
                continue

            self._last_processed_size = current_size

            try:
                self._run_diarization()
            except Exception as e:
                logger.warning("Incremental diarization failed: %s", e)

    def _run_diarization(self):
        """Run diarization on the current audio file and update segments."""
        diarization_segments = self._diarizer.diarize_audio(self._audio_path)
        if not diarization_segments:
            return

        with self._segments_lock:
            self._diarizer.assign_speakers_to_segments(
                self._segments, diarization_segments, self._speaker_names
            )

        num_speakers = len(set(s.speaker_label for s in diarization_segments))
        logger.info("Incremental diarization: %d speakers identified", num_speakers)

        if self._on_labels_updated:
            try:
                self._on_labels_updated(num_speakers)
            except Exception:
                pass
