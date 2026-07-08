"""Tests for the even chunk-planning logic."""

from clipmaster.media.chunker import plan_chunks


def _durations(total, max_chunk, overlap=0.0):
    plan = plan_chunks(total, max_chunk_seconds=max_chunk, overlap_seconds=overlap)
    return plan.chunks


def test_short_video_single_chunk():
    chunks = _durations(120, 1200)
    assert len(chunks) == 1
    assert chunks[0].start_s == 0
    assert chunks[0].end_s == 120


def test_30_min_splits_into_two_15_min():
    chunks = _durations(30 * 60, 20 * 60)  # 1800s, max 1200s -> 2 chunks
    assert len(chunks) == 2
    assert abs(chunks[0].duration_s - 900) < 1e-6
    assert abs(chunks[1].duration_s - 900) < 1e-6


def test_40_min_splits_into_two_20_min():
    chunks = _durations(40 * 60, 20 * 60)  # 2400s -> 2 chunks of 1200
    assert len(chunks) == 2
    assert abs(chunks[0].duration_s - 1200) < 1e-6
    assert abs(chunks[1].duration_s - 1200) < 1e-6


def test_50_min_splits_into_three():
    chunks = _durations(50 * 60, 20 * 60)  # 3000s -> ceil(2.5)=3 chunks of 1000
    assert len(chunks) == 3
    for c in chunks:
        assert abs(c.duration_s - 1000) < 1e-6


def test_chunks_cover_whole_timeline():
    total = 3725.0
    chunks = _durations(total, 1200)
    assert chunks[0].start_s == 0
    assert abs(chunks[-1].end_s - total) < 1e-6
    # Chunks are contiguous (starts line up with previous nominal boundary).
    for i in range(1, len(chunks)):
        assert chunks[i].start_s <= chunks[i - 1].end_s


def test_overlap_extends_non_final_chunks():
    chunks = _durations(2400, 1200, overlap=2.0)
    # First chunk end is extended by the overlap; last chunk is not.
    assert chunks[0].end_s > 1200
    assert abs(chunks[-1].end_s - 2400) < 1e-6


def test_zero_duration_returns_no_chunks():
    assert _durations(0, 1200) == []
