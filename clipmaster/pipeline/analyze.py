"""The analysis pipeline: ingest -> chunk -> transcribe -> analyze -> report.

This is the foundation every other feature (cleanup, shorts, editing) builds on.
It is UI-agnostic: progress is published to an :class:`EventBus` so the CLI, the
HTTP server and the desktop editor can all render the same live status.

Workflow
--------
1. **Probe** the input with ffprobe -> :class:`MediaInfo`.
2. **Plan chunks** so no processing unit exceeds ``chunking.max_chunk_seconds``.
3. For each chunk: extract mono 16 kHz audio, transcribe locally, and offset the
   segments back onto the absolute video timeline. Overlap is de-duplicated.
4. **Detect silence** across the whole file (for the report and cleanup).
5. **Analyze** the merged transcript (LLM + heuristics).
6. Assemble and persist an :class:`AnalysisReport` (``analysis.json``).
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from clipmaster.analysis.transcript_analyzer import analyze_transcript
from clipmaster.config import Settings
from clipmaster.events import EventBus, Stage, default_bus
from clipmaster.logging_setup import get_logger
from clipmaster.media import detect_silence, extract_audio, plan_chunks, probe_media
from clipmaster.models import AnalysisReport, Transcript, TranscriptSegment
from clipmaster.transcription import get_transcriber

logger = get_logger("pipeline.analyze")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "video"


def project_id_for(source: Path) -> str:
    """Stable, human-readable project id: ``<slug>-<hash8>``."""
    digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{_slugify(source.stem)}-{digest}"


def project_dir_for(settings: Settings, source: Path) -> Path:
    path = settings.workspace_path / project_id_for(source)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _merge_segments(
    existing: list[TranscriptSegment],
    new_segments: list[TranscriptSegment],
    offset: float,
    accepted_until: float,
) -> float:
    """Append offset ``new_segments`` to ``existing``, skipping overlap dupes.

    Returns the updated ``accepted_until`` (max end time accepted so far).
    """
    for seg in new_segments:
        start = seg.start + offset
        end = seg.end + offset
        midpoint = (start + end) / 2
        if midpoint < accepted_until:  # already covered by the previous chunk
            continue
        words = [
            w.model_copy(update={"start": w.start + offset, "end": w.end + offset})
            for w in seg.words
        ]
        existing.append(
            seg.model_copy(
                update={
                    "id": len(existing),
                    "start": start,
                    "end": end,
                    "words": words,
                }
            )
        )
        accepted_until = max(accepted_until, end)
    return accepted_until


def analyze_video(
    source: str | Path,
    settings: Settings,
    *,
    bus: EventBus | None = None,
    skip_analysis: bool = False,
) -> AnalysisReport:
    """Run the full analysis pipeline and return a persisted :class:`AnalysisReport`.

    Parameters
    ----------
    source:
        Path to the input video.
    settings:
        Validated application configuration.
    bus:
        Event bus for live progress. Defaults to the process-wide bus.
    skip_analysis:
        When True, produce transcript + silence only and skip the LLM analysis
        (useful for fast iteration or when Ollama is offline).
    """
    bus = bus or default_bus
    source = Path(source)
    started = time.time()

    bus.stage_start(Stage.INGEST, f"Ingesting {source.name}")
    project_dir = project_dir_for(settings, source)
    warnings: list[str] = []

    # 1) Probe -----------------------------------------------------------------
    bus.stage_start(Stage.PROBE, "Reading media metadata")
    media = probe_media(source, settings.media.ffprobe_bin)
    bus.stage_end(
        Stage.PROBE,
        f"{media.duration_s:.0f}s, "
        f"{media.video.width if media.video else '?'}x"
        f"{media.video.height if media.video else '?'}",
        duration_s=media.duration_s,
    )

    # 2) Chunk plan ------------------------------------------------------------
    bus.stage_start(Stage.CHUNK, "Planning chunks")
    plan = plan_chunks(
        media.duration_s,
        max_chunk_seconds=settings.chunking.max_chunk_seconds,
        overlap_seconds=settings.chunking.overlap_seconds,
    )
    bus.stage_end(Stage.CHUNK, f"{len(plan.chunks)} chunk(s)", chunks=len(plan.chunks))

    # 3) Transcribe per chunk --------------------------------------------------
    transcript = Transcript(duration_s=media.duration_s)
    if not media.has_audio:
        warnings.append("Input has no audio stream; transcript is empty.")
        logger.warning(warnings[-1])
    else:
        transcriber = get_transcriber(settings.transcription)
        audio_dir = project_dir / "audio"
        accepted_until = 0.0
        detected_language: str | None = settings.transcription.language
        try:
            for chunk in plan.chunks:
                bus.stage_start(
                    Stage.EXTRACT_AUDIO,
                    f"Extracting audio for chunk {chunk.index + 1}/{len(plan.chunks)}",
                )
                audio_path = extract_audio(
                    source,
                    audio_dir / f"chunk_{chunk.index:03d}.wav",
                    ffmpeg_bin=settings.media.ffmpeg_bin,
                    start_s=chunk.start_s,
                    duration_s=chunk.duration_s,
                )
                bus.stage_start(
                    Stage.TRANSCRIBE,
                    f"Transcribing chunk {chunk.index + 1}/{len(plan.chunks)}",
                )
                result = transcriber.transcribe(audio_path)
                detected_language = detected_language or result.language
                accepted_until = _merge_segments(
                    transcript.segments, result.segments, chunk.start_s, accepted_until
                )
                bus.progress(
                    Stage.TRANSCRIBE,
                    (chunk.index + 1) / max(1, len(plan.chunks)),
                    f"{len(transcript.segments)} segments so far",
                )
        finally:
            transcriber.close()
        transcript.language = detected_language

    # 4) Silence detection -----------------------------------------------------
    bus.stage_start(Stage.SILENCE, "Detecting silence")
    silences = []
    if media.has_audio:
        silences = detect_silence(
            source,
            ffmpeg_bin=settings.media.ffmpeg_bin,
            noise_db=settings.silence.noise_db,
            min_silence_seconds=settings.silence.min_silence_seconds,
        )
    bus.stage_end(Stage.SILENCE, f"{len(silences)} silent span(s)")

    # 5) Analysis --------------------------------------------------------------
    analysis: dict = {}
    if not skip_analysis and transcript.segments:
        bus.stage_start(Stage.ANALYZE, "Analyzing transcript")

        def _on_progress(fraction: float, message: str) -> None:
            bus.progress(Stage.ANALYZE, fraction, message)

        analysis = analyze_transcript(
            transcript,
            silences,
            settings.llm,
            settings.analysis,
            progress=_on_progress,
        )
        warnings.extend(analysis.pop("warnings", []))
        bus.stage_end(Stage.ANALYZE, f"{len(analysis.get('chapters', []))} chapter(s)")
    elif skip_analysis:
        warnings.append("Analysis skipped (--skip-analysis).")

    # 6) Assemble report -------------------------------------------------------
    bus.stage_start(Stage.REPORT, "Writing analysis report")
    report = AnalysisReport(
        project_id=project_dir.name,
        source_path=str(source.resolve()),
        media=media,
        chunk_plan=plan,
        transcript=transcript,
        silences=silences,
        transcription_model=f"{settings.transcription.provider}:{settings.transcription.model}",
        llm_model=settings.llm.model if analysis else "",
        warnings=warnings,
        **analysis,
    )
    report_path = report.save(project_dir / "analysis.json")
    bus.stage_end(Stage.REPORT, f"Saved {report_path.name}", path=str(report_path))

    bus.stage_start(
        Stage.DONE,
        f"Analysis complete in {time.time() - started:.1f}s -> {project_dir}",
        project_dir=str(project_dir),
    )
    return report
