"""faster-whisper backed transcription.

Runs on CPU everywhere and on NVIDIA GPUs via CUDA. On the AMD 7900 XT under
Windows there is no CUDA, so this provider defaults to CPU (the 7800X3D handles
``small``/``base`` comfortably). For GPU-accelerated transcription on AMD, see the
README section "AMD GPU acceleration" — a whisper.cpp/Vulkan provider drops in
behind the same :class:`Transcriber` interface.

The ``faster_whisper`` import is deliberately lazy so the rest of ClipMaster (CLI
help, tests, the report tooling) works without the heavy ML dependency installed.
"""

from __future__ import annotations

import os
from pathlib import Path

from clipmaster.logging_setup import get_logger
from clipmaster.models import TranscriptSegment, Word
from clipmaster.transcription.base import Transcriber, TranscriptionResult

logger = get_logger("transcription.faster_whisper")


class FasterWhisperTranscriber(Transcriber):
    """Local Whisper transcription using the CTranslate2 ``faster-whisper`` engine."""

    def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config)
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        # Keep transcription fully local and quiet. These must be set BEFORE
        # huggingface_hub is imported (by faster_whisper) to take effect:
        #   * no anonymous usage telemetry leaves the machine;
        #   * silence the harmless Windows symlink-cache warning.
        # The model weights are still downloaded ONCE into the local HF cache;
        # set HF_HUB_OFFLINE=1 yourself to forbid all network access after that.
        # Every value uses setdefault so a user's own env vars win.
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "faster-whisper is not installed. Install the transcription extra:\n"
                "    pip install -e .[transcribe]"
            ) from exc

        logger.info(
            "Loading Whisper model '%s' (device=%s, compute=%s)",
            self.config.model,
            self.config.device,
            self.config.compute_type,
        )
        self._model = WhisperModel(
            self.config.model,
            device=self.config.device,
            compute_type=self.config.compute_type,
        )
        return self._model

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        model = self._ensure_model()
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=self.config.language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            word_timestamps=self.config.word_timestamps,
        )

        segments: list[TranscriptSegment] = []
        for idx, seg in enumerate(segments_iter):
            words: list[Word] = []
            for w in (seg.words or []):
                words.append(
                    Word(
                        text=w.word,
                        start=float(w.start),
                        end=float(w.end),
                        probability=getattr(w, "probability", None),
                    )
                )
            segments.append(
                TranscriptSegment(
                    id=idx,
                    start=float(seg.start),
                    end=float(seg.end),
                    text=seg.text.strip(),
                    words=words,
                    avg_logprob=getattr(seg, "avg_logprob", None),
                    no_speech_prob=getattr(seg, "no_speech_prob", None),
                )
            )

        language = getattr(info, "language", None) or self.config.language
        return TranscriptionResult(language=language, segments=segments)

    def close(self) -> None:
        self._model = None
