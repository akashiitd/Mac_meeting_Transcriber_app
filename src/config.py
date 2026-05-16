"""
Configuration management for Mac Meeting Transcriber.

Handles storing and loading user preferences like model selection.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class Config:
    """Manages application configuration with file persistence."""

    DEFAULT_MODEL = "llama3.2:3b"
    DEFAULT_REALTIME_TRANSCRIPTION_MODEL = "apple-speech"
    DEFAULT_TRANSCRIPTION_QUALITY_MODE = "fast"

    # Supported models with metadata (organized by parameter size, ascending)
    SUPPORTED_MODELS = {
        "llama3.2:3b": {
            "name": "Llama 3.2 3B",
            "size": "2GB",
            "params": "3B",
            "description": "Fastest option for quick meetings (default)",
            "speed": "very fast",
            "quality": "good"
        },
        "gemma3:4b": {
            "name": "Gemma 3 4B",
            "size": "2.5GB",
            "params": "4B",
            "description": "Lightweight and efficient",
            "speed": "fast",
            "quality": "good"
        },
        "qwen3:8b": {
            "name": "Qwen 3 8B",
            "size": "4.7GB",
            "params": "8B",
            "description": "Excellent at structured output and action items",
            "speed": "fast",
            "quality": "excellent"
        },
        "deepseek-r1:8b": {
            "name": "DeepSeek R1 8B",
            "size": "4.7GB",
            "params": "8B",
            "description": "Strong reasoning and analysis capabilities",
            "speed": "medium",
            "quality": "excellent"
        }
    }

    SUPPORTED_REALTIME_TRANSCRIPTION_MODELS = {
        "apple-speech": {
            "name": "Apple Speech (macOS Built-in)",
            "backend": "apple-speech",
            "size": "system",
            "description": "Native macOS SpeechAnalyzer with built-in microphone and system audio capture",
            "speed": "live",
            "quality": "system"
        },
        "whisper-small": {
            "name": "Whisper Small MLX",
            "backend": "whisper",
            "size": "small",
            "description": "Current Apple Silicon accelerated Whisper backend",
            "speed": "fast",
            "quality": "good"
        },
        "lfm2-audio-1.5b-q8": {
            "name": "LFM2-Audio 1.5B Q8",
            "backend": "lfm2-audio",
            "size": "1.5B",
            "description": "Liquid AI local ASR through specialized llama.cpp runner",
            "speed": "experimental",
            "quality": "experimental"
        }
    }

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to config file. If None, uses default location.
        """
        if config_path is None:
            # Use same directory logic as recorder state
            if "Mac Meeting Transcriber.app" in str(Path(__file__)) or "Applications" in str(Path(__file__)):
                # Production: ~/Library/Application Support/mac-meeting-transcriber
                base_dir = Path.home() / "Library" / "Application Support" / "mac-meeting-transcriber"
            else:
                # Development: project root
                base_dir = Path(__file__).parent.parent

            base_dir.mkdir(parents=True, exist_ok=True)
            self.config_path = base_dir / "config.json"
        else:
            self.config_path = config_path

        self._config: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        """Load configuration from file."""
        if not self.config_path.exists():
            logger.info(f"Config file not found, creating default at {self.config_path}")
            return self._get_default_config()

        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
                logger.info(f"Loaded config from {self.config_path}")
                return config
        except Exception as e:
            logger.error(f"Error loading config: {e}, using defaults")
            return self._get_default_config()

    def _save(self) -> bool:
        """Save configuration to file."""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self._config, f, indent=2)
            logger.info(f"Saved config to {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "model": self.DEFAULT_MODEL,
            "realtime_transcription_model": self.DEFAULT_REALTIME_TRANSCRIPTION_MODEL,
            "notifications_enabled": True,
            "transcription_context": {
                "domain_terms": [],
                "participant_names": [],
                "locale": "en_US",
                "quality_mode": self.DEFAULT_TRANSCRIPTION_QUALITY_MODE
            },
            "version": "1.0",
            "diarization": {
                "enabled": False,
                "hf_token": "",
                "num_speakers": None,
                "min_speakers": 2,
                "max_speakers": 10,
                "use_mps": True,
                "mode": "batch",
                "speaker_names": {}
            }
        }

    def get_model(self) -> str:
        """Get the configured model name."""
        return self._config.get("model", self.DEFAULT_MODEL)

    def set_model(self, model_name: str) -> bool:
        """
        Set the model to use for summarization.

        Args:
            model_name: Name of the model (e.g., "llama3.1:8b")

        Returns:
            True if saved successfully, False otherwise
        """
        # Validate model name
        if model_name not in self.SUPPORTED_MODELS:
            logger.warning(f"Model {model_name} not in supported list, but allowing anyway")

        self._config["model"] = model_name
        return self._save()

    def get_model_info(self, model_name: str) -> Optional[Dict[str, str]]:
        """
        Get metadata about a specific model.

        Args:
            model_name: Name of the model

        Returns:
            Dictionary with model metadata or None if not found
        """
        return self.SUPPORTED_MODELS.get(model_name)

    def list_supported_models(self) -> Dict[str, Dict[str, str]]:
        """Get all supported models with their metadata."""
        return self.SUPPORTED_MODELS.copy()

    def get_realtime_transcription_model(self) -> str:
        """Get the configured real-time transcription model."""
        return self._config.get(
            "realtime_transcription_model",
            self.DEFAULT_REALTIME_TRANSCRIPTION_MODEL
        )

    def set_realtime_transcription_model(self, model_name: str) -> bool:
        """Set the model to use for real-time transcription."""
        if model_name not in self.SUPPORTED_REALTIME_TRANSCRIPTION_MODELS:
            logger.warning(f"Real-time transcription model {model_name} not in supported list")
            return False

        self._config["realtime_transcription_model"] = model_name
        return self._save()

    def get_realtime_transcription_model_info(self, model_name: str) -> Optional[Dict[str, str]]:
        """Get metadata about a specific real-time transcription model."""
        return self.SUPPORTED_REALTIME_TRANSCRIPTION_MODELS.get(model_name)

    def list_supported_realtime_transcription_models(self) -> Dict[str, Dict[str, str]]:
        """Get all supported real-time transcription models with metadata."""
        return self.SUPPORTED_REALTIME_TRANSCRIPTION_MODELS.copy()

    def get_notifications_enabled(self) -> bool:
        """Get whether desktop notifications are enabled."""
        return self._config.get("notifications_enabled", True)

    def set_notifications_enabled(self, enabled: bool) -> bool:
        """
        Set whether desktop notifications are enabled.

        Args:
            enabled: True to enable notifications, False to disable

        Returns:
            True if saved successfully, False otherwise
        """
        self._config["notifications_enabled"] = enabled
        return self._save()

    def get_transcription_context(self) -> Dict[str, Any]:
        """Get transcription context config (domain terms, names, locale, quality)."""
        return self._config.get("transcription_context", {})

    def get_transcription_locale(self) -> str:
        """Get the configured locale for Apple Speech transcription."""
        ctx = self.get_transcription_context()
        return ctx.get("locale", "en_US")

    def get_transcription_quality_mode(self) -> str:
        """Get quality mode: 'fast' or 'balanced'."""
        ctx = self.get_transcription_context()
        return ctx.get("quality_mode", self.DEFAULT_TRANSCRIPTION_QUALITY_MODE)

    def get_context_terms(self) -> list:
        """Get combined domain terms and participant names for vocabulary hints."""
        ctx = self.get_transcription_context()
        terms = list(ctx.get("domain_terms", []))
        terms.extend(ctx.get("participant_names", []))
        return terms

    def set_transcription_context(self, context: Dict[str, Any]) -> bool:
        """Set transcription context config."""
        self._config["transcription_context"] = context
        return self._save()

    # --- Diarization config ---

    def get_diarization_config(self) -> Dict[str, Any]:
        """Get the full diarization configuration."""
        defaults = {
            "enabled": False,
            "hf_token": "",
            "num_speakers": None,
            "min_speakers": 2,
            "max_speakers": 10,
            "use_mps": True,
            "mode": "batch",
            "speaker_names": {}
        }
        config = self._config.get("diarization", {})
        defaults.update(config)
        return defaults

    def is_diarization_enabled(self) -> bool:
        """Check if speaker diarization is enabled."""
        return self.get_diarization_config().get("enabled", False)

    def set_diarization_enabled(self, enabled: bool) -> bool:
        """Enable or disable speaker diarization."""
        if "diarization" not in self._config:
            self._config["diarization"] = {}
        self._config["diarization"]["enabled"] = enabled
        return self._save()

    def get_hf_token(self) -> str:
        """Get HuggingFace auth token (from config or HF_TOKEN env var)."""
        import os
        token = self.get_diarization_config().get("hf_token", "")
        if not token:
            token = os.environ.get("HF_TOKEN", "")
        return token

    def set_hf_token(self, token: str) -> bool:
        """Store HuggingFace auth token in config."""
        if "diarization" not in self._config:
            self._config["diarization"] = {}
        self._config["diarization"]["hf_token"] = token
        return self._save()

    def get_speaker_names(self) -> Dict[str, str]:
        """Get speaker name mappings (e.g. {'Speaker 1': 'Alice'})."""
        return self.get_diarization_config().get("speaker_names", {})

    def set_speaker_names(self, mapping: Dict[str, str]) -> bool:
        """Set speaker name mappings."""
        if "diarization" not in self._config:
            self._config["diarization"] = {}
        self._config["diarization"]["speaker_names"] = mapping
        return self._save()

    def set_diarization_config(self, config: Dict[str, Any]) -> bool:
        """Set the full diarization configuration."""
        self._config["diarization"] = config
        return self._save()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        """Set a configuration value and save."""
        self._config[key] = value
        return self._save()


# Global config instance
_config_instance: Optional[Config] = None


def get_config() -> Config:
    """Get the global config instance (singleton pattern)."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
