"""Pluggable speech-to-text providers.

A provider turns an audio file into a list of :class:`TranscriptSegment` on that
audio's *local* timeline (starting at 0). The pipeline is responsible for
offsetting segments by their chunk start and merging chunks into one transcript.
"""

from clipmaster.transcription.base import Transcriber, TranscriptionResult, get_transcriber

__all__ = ["Transcriber", "TranscriptionResult", "get_transcriber"]
