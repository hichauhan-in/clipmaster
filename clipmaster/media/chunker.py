"""Chunk planning and extraction.

The project operates on processing units of at most ``max_chunk_seconds`` (default
20 minutes). Longer videos are divided into ``N = ceil(duration / max)`` **evenly
sized** chunks so the workload is balanced:

    30 min -> 2 chunks of 15:00
    40 min -> 2 chunks of 20:00
    50 min -> 3 chunks of 16:40

A small ``overlap_seconds`` is added to the *end* of each non-final chunk so a
sentence straddling a boundary is transcribed intact; overlapping transcript
segments are de-duplicated when chunks are merged.
"""

from __future__ import annotations

import math
from pathlib import Path

from clipmaster.media.ffmpeg import run_ffmpeg
from clipmaster.models import Chunk, ChunkPlan


def plan_chunks(
    total_duration_s: float,
    *,
    max_chunk_seconds: float = 1200.0,
    overlap_seconds: float = 2.0,
) -> ChunkPlan:
    """Compute an evenly divided :class:`ChunkPlan` for ``total_duration_s``."""
    if total_duration_s <= 0:
        return ChunkPlan(
            total_duration_s=0.0,
            max_chunk_seconds=max_chunk_seconds,
            overlap_seconds=overlap_seconds,
            chunks=[],
        )

    n_chunks = max(1, math.ceil(total_duration_s / max_chunk_seconds))
    base_len = total_duration_s / n_chunks

    chunks: list[Chunk] = []
    for i in range(n_chunks):
        start = i * base_len
        end = total_duration_s if i == n_chunks - 1 else (i + 1) * base_len
        # Extend non-final chunks by the overlap, clamped to the video end.
        if i < n_chunks - 1:
            end = min(total_duration_s, end + overlap_seconds)
        chunks.append(Chunk(index=i, start_s=round(start, 3), end_s=round(end, 3)))

    return ChunkPlan(
        total_duration_s=total_duration_s,
        max_chunk_seconds=max_chunk_seconds,
        overlap_seconds=overlap_seconds,
        chunks=chunks,
    )


def extract_chunk(
    source: str | Path,
    chunk: Chunk,
    dest: str | Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    reencode: bool = False,
) -> Path:
    """Extract ``chunk`` from ``source`` into ``dest``.

    By default a fast stream copy is used (no re-encode). Set ``reencode=True`` for
    frame-accurate cuts when the container's keyframes are sparse.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = ["-ss", f"{chunk.start_s:.3f}", "-i", str(source), "-t", f"{chunk.duration_s:.3f}"]
    if reencode:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]
    else:
        args += ["-c", "copy"]
    args += [str(dest)]

    run_ffmpeg(ffmpeg_bin, args)
    return dest
