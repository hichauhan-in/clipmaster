"""ClipMaster — a local-first video analysis and editing pipeline.

The package is organized in layers so that the CLI, the HTTP server, and the
desktop UI all share the exact same core logic:

    clipmaster.config       -> typed configuration loaded from YAML
    clipmaster.events       -> progress/event bus for live UI + CLI updates
    clipmaster.models       -> pydantic data models (the shared vocabulary)
    clipmaster.media        -> ffmpeg/ffprobe wrappers, silence, chunk planning
    clipmaster.transcription-> pluggable speech-to-text providers
    clipmaster.analysis     -> Ollama LLM client + transcript analysis
    clipmaster.pipeline     -> orchestration (ingest -> analyze -> report)
    clipmaster.report       -> render the analysis artifact (JSON / Markdown)
"""

from clipmaster.version import __version__

__all__ = ["__version__"]
