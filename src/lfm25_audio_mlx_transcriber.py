"""
Liquid AI LFM2.5-Audio MLX transcription backend.

This backend uses the MLX Community conversion of LiquidAI/LFM2.5-Audio-1.5B
through mlx-audio. It is intended for Apple Silicon and expects mono float32
audio, preferably at 24 kHz.
"""

import logging
import platform
import re
import threading
from typing import Optional

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


class LFM25AudioMLXTranscriber:
    """Local ASR wrapper around mlx-audio's LFM2.5-Audio model."""

    MODEL_ID = "mlx-community/LFM2.5-Audio-1.5B-bf16"
    REQUIRED_SAMPLE_RATE = 24000

    def __init__(
        self,
        model_id: str = MODEL_ID,
        max_new_tokens: int = 512,
        temperature: float = 0.0,
    ):
        self.model_id = model_id
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.model = None
        self.processor = None
        self.mx = None
        self.ChatState = None
        self.LFMModality = None
        self._lock = threading.Lock()

        if platform.system().lower() != "darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
            raise ValueError("LFM2.5-Audio MLX requires Apple Silicon macOS")

    def ensure_available(self) -> None:
        """Load the model and processor, downloading from Hugging Face if needed."""
        if self.model is not None and self.processor is not None:
            return

        try:
            import mlx.core as mx
            from mlx_audio.sts.models.lfm_audio import (
                LFM2AudioModel,
                LFM2AudioProcessor,
                ChatState,
                LFMModality,
            )
        except ImportError as exc:
            raise RuntimeError(
                "mlx-audio is required for Liquid AI LFM2.5-Audio MLX. "
                "Run: pip install -U mlx-audio"
            ) from exc

        logger.info("Loading LFM2.5-Audio MLX model: %s", self.model_id)
        self.mx = mx
        self.ChatState = ChatState
        self.LFMModality = LFMModality
        self.model = LFM2AudioModel.from_pretrained(self.model_id)
        self.processor = LFM2AudioProcessor.from_pretrained(self.model_id)
        logger.info("LFM2.5-Audio MLX model loaded")

    def transcribe(self, audio, sample_rate: int = REQUIRED_SAMPLE_RATE) -> str:
        """Transcribe a mono float32 audio chunk."""
        if not NUMPY_AVAILABLE:
            raise RuntimeError("NumPy is required for LFM2.5-Audio MLX transcription")

        self.ensure_available()

        samples = np.asarray(audio, dtype=np.float32).flatten()
        if samples.size == 0:
            return ""

        samples = np.clip(samples, -1.0, 1.0)
        audio_array = self.mx.array(samples)

        # The underlying MLX model object is shared between microphone/system chunks.
        with self._lock:
            chat = self.ChatState(self.processor)
            chat.new_turn("user")
            chat.add_audio(audio_array, sample_rate=sample_rate)
            chat.add_text("Transcribe the audio.")
            chat.end_turn()
            chat.new_turn("assistant")

            text_parts = []
            generate_from_chat_state = getattr(self.model, "generate_from_chat_state", None)
            if generate_from_chat_state:
                iterator = generate_from_chat_state(
                    chat,
                    mode="interleaved",
                    max_new_tokens=self.max_new_tokens,
                    temperature=self.temperature,
                )
            else:
                iterator = self.model.generate_interleaved(
                    **dict(chat),
                    max_new_tokens=self.max_new_tokens,
                    temperature=self.temperature,
                )

            for token, modality in iterator:
                self.mx.eval(token)
                if modality == self.LFMModality.TEXT:
                    text_parts.append(self.processor.decode_text(token[None]))

        return self._clean_text("".join(text_parts))

    @staticmethod
    def _clean_text(text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        text = re.sub(r"<\|[^|]+?\|>", " ", text)
        text = text.replace("Transcribe the audio.", " ")
        text = text.replace("Perform ASR.", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()
