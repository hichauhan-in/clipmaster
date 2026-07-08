"""Per-segment audio (DSP) features — the *audio* signal of the analysis.

The transcript tells us *what* was said; the audio waveform tells us *how* it was
delivered. Loud, energetic, fast-paced speech tends to mark the important, quotable
moments; long quiet stretches tend to be low-value. We compute cheap, deterministic
metrics for every transcript segment straight from the waveform (no model download):

* **rms_db / peak_db** — loudness of the segment and its loudest moment,
* **speech_rate_wps** — words per second (delivery pace),
* **pause_ratio** — fraction of the segment that is near-silent,
* **energy_score** — the segment's loudness rank across the whole file (0..1),
  which is robust to the absolute recording level.

Requires numpy (already pulled in by faster-whisper). If numpy is unavailable the
analysis degrades gracefully: audio features are skipped and the transcript/visual
signals are re-weighted to compensate.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

from clipmaster.config import Settings
from clipmaster.logging_setup import get_logger
from clipmaster.media import extract_audio
from clipmaster.models import AudioFeatures, SegmentAudio, Transcript

logger = get_logger("analysis.audio")

_FRAME_MS = 50  # length of one loudness-envelope frame


def analyze_audio(
    source: str | Path,
    transcript: Transcript,
    settings: Settings,
    *,
    project_dir: str | Path,
    sample_rate: int = 8000,
) -> AudioFeatures | None:
    """Compute per-segment audio metrics, or ``None`` if it can't run."""
    if not transcript.segments:
        return None
    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy not available; skipping audio feature analysis.")
        return None

    # A small mono WAV is enough for loudness; 8 kHz halves the size vs. Whisper's.
    wav_path = Path(project_dir) / "audio" / "features.wav"
    try:
        extract_audio(
            source,
            wav_path,
            ffmpeg_bin=settings.media.ffmpeg_bin,
            sample_rate=sample_rate,
        )
        samples, sr = _read_wav_mono(wav_path, np)
    except Exception as exc:  # noqa: BLE001 - never break the pipeline on audio DSP
        logger.warning("Audio feature analysis failed: %s", exc)
        return None
    finally:
        try:
            wav_path.unlink()
        except OSError:
            pass

    if samples.size == 0:
        return None

    frame = max(1, int(sr * _FRAME_MS / 1000))
    n_frames = samples.size // frame
    if n_frames == 0:
        return None

    trimmed = samples[: n_frames * frame].reshape(n_frames, frame).astype(np.float64)
    envelope = np.sqrt(np.mean(trimmed**2, axis=1)) + 1e-9  # per-frame RMS (linear)
    frame_dur = frame / sr

    global_rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)) + 1e-9)
    silence_thresh = global_rms * 0.15  # ~ -16 dB relative to the file average

    means: list[float] = []
    records: list[tuple[int, float, float, float, float]] = []
    for seg in transcript.segments:
        i0 = max(0, min(int(seg.start / frame_dur), n_frames))
        i1 = max(i0 + 1, min(int(seg.end / frame_dur), n_frames))
        chunk = envelope[i0:i1]
        if chunk.size == 0:
            mean_lin = global_rms * 0.1
            peak_lin = mean_lin
            pause = 1.0
        else:
            mean_lin = float(chunk.mean())
            peak_lin = float(chunk.max())
            pause = float(np.mean(chunk < silence_thresh))
        n_words = len(seg.words) or len(seg.text.split())
        rate = n_words / max(0.1, seg.duration)
        means.append(mean_lin)
        records.append((seg.id, mean_lin, peak_lin, rate, pause))

    # Rank-based energy: the loudest segment -> 1.0, the quietest -> 0.0.
    means_arr = np.asarray(means)
    if means_arr.size > 1:
        order = means_arr.argsort()
        ranks = np.empty_like(order, dtype=np.float64)
        ranks[order] = np.arange(means_arr.size)
        energy = ranks / (means_arr.size - 1)
    else:
        energy = np.full(means_arr.size, 0.5)

    seg_audio = [
        SegmentAudio(
            segment_id=sid,
            rms_db=round(_to_db(mean_lin), 2),
            peak_db=round(_to_db(peak_lin), 2),
            speech_rate_wps=round(rate, 2),
            pause_ratio=round(pause, 3),
            energy_score=round(float(e), 3),
        )
        for (sid, mean_lin, peak_lin, rate, pause), e in zip(records, energy)
    ]
    return AudioFeatures(
        sample_rate=sr,
        global_rms_db=round(_to_db(global_rms), 2),
        segments=seg_audio,
    )


def _to_db(linear: float) -> float:
    return 20.0 * math.log10(max(linear, 1e-9))


def _read_wav_mono(path: Path, np):  # type: ignore[no-untyped-def]
    """Read a WAV file into a mono float32 array in [-1, 1] and its sample rate."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:  # 24/32-bit fall back to int32 interpretation
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / float(2**31)

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr
