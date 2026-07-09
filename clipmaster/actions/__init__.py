"""Post-analysis actions: turn an ``AnalysisReport`` into deliverables.

Each action is UI-agnostic and publishes progress to an :class:`~clipmaster.events.EventBus`
so the CLI, the HTTP server and the desktop app can all render the same status.

* :func:`build_cleanup` — a trimmed cut with silence / filler / off-topic removed.
* :func:`build_shorts`  — vertical short-form clips from the best moments.
* :func:`build_notes`   — written Markdown study notes grouped into a few files.
"""

from clipmaster.actions.cleanup import CleanupResult, build_cleanup
from clipmaster.actions.notes import NotesResult, build_notes
from clipmaster.actions.shorts import ShortsResult, build_shorts
from clipmaster.actions.transcript import TranscriptResult, build_transcript

__all__ = [
    "build_cleanup",
    "CleanupResult",
    "build_shorts",
    "ShortsResult",
    "build_notes",
    "NotesResult",
    "build_transcript",
    "TranscriptResult",
]
