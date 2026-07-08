"""Media processing layer: ffprobe/ffmpeg wrappers, silence detection, chunking."""

from clipmaster.media.chunker import extract_chunk, plan_chunks
from clipmaster.media.ffmpeg import FFmpegError, run_ffmpeg, run_ffprobe
from clipmaster.media.probe import probe_media
from clipmaster.media.silence import detect_silence, extract_audio

__all__ = [
    "FFmpegError",
    "run_ffmpeg",
    "run_ffprobe",
    "probe_media",
    "detect_silence",
    "extract_audio",
    "plan_chunks",
    "extract_chunk",
]
