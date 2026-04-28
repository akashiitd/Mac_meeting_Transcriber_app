"""
Liquid AI LFM2-Audio-1.5B transcription backend.

This wraps Liquid's specialized llama.cpp audio runner for local ASR. The model
and platform runner are downloaded from LiquidAI/LFM2-Audio-1.5B-GGUF on first
use when this backend is selected.
"""

import logging
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import wave
import zipfile
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


class LFM2AudioTranscriber:
    """Minimal local ASR wrapper around Liquid's llama-lfm2-audio runner."""

    REPO_ID = "LiquidAI/LFM2-Audio-1.5B-GGUF"
    SUPPORTED_PLATFORMS = {"android-arm64", "macos-arm64", "ubuntu-arm64", "ubuntu-x64"}

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        quantization: str = "Q8_0",
        timeout_seconds: int = 45
    ):
        self.cache_dir = cache_dir or self._default_cache_dir()
        self.quantization = quantization
        self.timeout_seconds = timeout_seconds
        self.platform_id = self._platform_id()

        if self.platform_id not in self.SUPPORTED_PLATFORMS:
            raise ValueError(
                f"LFM2-Audio is not available for {self.platform_id}. "
                f"Supported platforms: {', '.join(sorted(self.SUPPORTED_PLATFORMS))}"
            )

        self.model_filename = f"LFM2-Audio-1.5B-{quantization}.gguf"
        self.mmproj_filename = f"mmproj-audioencoder-LFM2-Audio-1.5B-{quantization}.gguf"
        self.audiodecoder_filename = f"audiodecoder-LFM2-Audio-1.5B-{quantization}.gguf"
        self.runner_name = "llama-lfm2-audio"
        self.asr_prompt = "Perform ASR."

    @staticmethod
    def _default_cache_dir() -> Path:
        if "Mac Meeting Transcriber.app" in str(Path(__file__)) or "Applications" in str(Path(__file__)):
            base_dir = Path.home() / "Library" / "Application Support" / "mac-meeting-transcriber"
        else:
            base_dir = Path(__file__).parent.parent
        return base_dir / "models" / "LFM2-Audio-1.5B-GGUF"

    @staticmethod
    def _platform_id() -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine in {"x86_64", "amd64"}:
            arch = "x64"
        elif machine in {"aarch64", "arm64"} or machine.startswith("arm"):
            arch = "arm64"
        else:
            arch = machine

        if system == "darwin":
            os_name = "macos"
        elif system == "linux":
            os_name = "ubuntu"
        else:
            os_name = system

        return f"{os_name}-{arch}"

    @property
    def model_path(self) -> Path:
        return self.cache_dir / self.model_filename

    @property
    def mmproj_path(self) -> Path:
        return self.cache_dir / self.mmproj_filename

    @property
    def audiodecoder_path(self) -> Path:
        return self.cache_dir / self.audiodecoder_filename

    @property
    def runner_dir(self) -> Path:
        return self.cache_dir / "runners" / self.platform_id / f"lfm2-audio-{self.platform_id}"

    @property
    def runner_path(self) -> Path:
        return self.runner_dir / self.runner_name

    def ensure_available(self) -> None:
        """Download model files and runner if needed."""
        if self._has_required_files():
            self._make_runner_executable()
            return

        logger.info("Downloading LFM2-Audio model and llama.cpp runner from Hugging Face")
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError(
                "huggingface_hub is required for LFM2-Audio. "
                "Run: pip install huggingface_hub"
            ) from exc

        tmp_dir = Path(tempfile.mkdtemp(prefix="lfm2_audio_download_"))
        try:
            snapshot_path = Path(snapshot_download(
                repo_id=self.REPO_ID,
                local_dir=str(tmp_dir),
                local_dir_use_symlinks=False,
            ))

            if self.cache_dir.exists():
                shutil.rmtree(self.cache_dir)
            self.cache_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(snapshot_path, self.cache_dir)
            self._extract_runner()
            self._make_runner_executable()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if not self._has_required_files():
            raise RuntimeError(f"Incomplete LFM2-Audio download at {self.cache_dir}")

    def transcribe(self, audio, sample_rate: int) -> str:
        """Transcribe mono float32 audio."""
        if not NUMPY_AVAILABLE:
            raise RuntimeError("NumPy is required for LFM2-Audio transcription")

        self.ensure_available()
        temp_path = self._write_temp_wav(audio, sample_rate)
        try:
            result = subprocess.run(
                self._command(temp_path),
                capture_output=True,
                text=False,
                timeout=self.timeout_seconds,
                check=False,
            )
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            raise RuntimeError(f"LFM2-Audio failed with code {result.returncode}: {stderr}")

        return self._parse_output(result.stdout)

    def _has_required_files(self) -> bool:
        return all(path.exists() for path in [
            self.model_path,
            self.mmproj_path,
            self.audiodecoder_path,
            self.runner_path,
        ])

    def _extract_runner(self) -> None:
        zip_path = self.cache_dir / "runners" / self.platform_id / f"lfm2-audio-{self.platform_id}.zip"
        if not zip_path.exists():
            raise RuntimeError(f"LFM2-Audio runner zip not found: {zip_path}")

        platform_dir = self.cache_dir / "runners" / self.platform_id
        platform_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(platform_dir)

    def _make_runner_executable(self) -> None:
        if not self.runner_dir.exists():
            return

        for file_path in self.runner_dir.iterdir():
            if file_path.is_file():
                file_path.chmod(file_path.stat().st_mode | stat.S_IEXEC)

    def _command(self, audio_file_path: str) -> list[str]:
        return [
            str(self.runner_path),
            "-m",
            str(self.model_path),
            "--mmproj",
            str(self.mmproj_path),
            "-mv",
            str(self.audiodecoder_path),
            "-sys",
            self.asr_prompt,
            "--audio",
            audio_file_path,
        ]

    def _write_temp_wav(self, audio, sample_rate: int) -> str:
        samples = np.asarray(audio, dtype=np.float32).flatten()
        if samples.size == 0:
            raise ValueError("Cannot transcribe empty audio")

        samples = np.clip(samples, -1.0, 1.0)
        pcm16 = (samples * 32767.0).astype(np.int16)

        fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="lfm2_live_chunk_")
        os.close(fd)

        with wave.open(temp_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm16.tobytes())

        return temp_path

    @staticmethod
    def _parse_output(output: bytes) -> str:
        output_str = output.decode("utf-8", errors="replace")
        transcription_lines = []

        ignored_keywords = [
            "loading",
            "load_gguf",
            "loaded",
            "tensors",
            "gguf",
            "encoding",
            "slice",
            "tokens",
            "speed",
            "ms",
        ]

        for line in output_str.splitlines():
            line = line.strip()
            if not line:
                continue
            if any(keyword in line.lower() for keyword in ignored_keywords):
                continue
            transcription_lines.append(line)

        text = " ".join(transcription_lines)
        for artifact in [
            "Perform ASR.",
            "[INST]",
            "[/INST]",
            "<s>",
            "</s>",
            "System:",
            "User:",
            "Assistant:",
        ]:
            text = text.replace(artifact, "")

        return " ".join(text.split()).strip()
