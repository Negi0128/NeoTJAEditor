"""Experimental audio-driven chart draft generator ("AI譜面生成"). Not a
trained model - just onset detection (see audio_engine._onset_envelope,
shared with detect_bpm_offset) quantized to a beat-subdivision grid, with a
crude strength-based don/ka split. Meant to produce a rough starting draft
for one course, never to replace hand-charting. Pure functions (no Qt), so
they're testable without a QApplication - the Qt-facing decode/threading
wrapper lives in audio_engine.ChartGenWorker.

OFFSET convention (matches the rest of this app): audio_time =
chart_time - OFFSET, i.e. chart_time = audio_time + OFFSET, so chart_time 0
(the first beat) sits at audio_time = -OFFSET.
"""

import math

import numpy as np

from neotja.audio_engine import _onset_envelope


def generate_notes(mono: np.ndarray, sample_rate: int, bpm: float, offset: float,
                    subdivision: int = 16, density: float = 0.5):
    """Returns [(chart_time_seconds, char)] with char in "1"/"2" (don/ka),
    quantized to a `subdivision`-per-beat grid (16 = 16th notes) starting at
    chart_time 0. For each grid slot, the onset envelope's peak within a
    half-slot-wide window is that slot's "strength"; `density` (0-1) keeps
    roughly that fraction of the slots with any onset activity (by
    percentile threshold, not a hard count). Kept slots above their own
    group's median strength become don ("1"), the rest ka ("2") - a rough
    relative-strength split, not real frequency analysis. Returns [] if the
    audio is too short/quiet or bpm is invalid."""
    if not bpm or bpm <= 0:
        return []
    onset, frame_rate = _onset_envelope(mono, sample_rate)
    if onset is None:
        return []

    duration = mono.size / sample_rate
    slot_interval = 240.0 / (bpm * subdivision)
    if slot_interval <= 0:
        return []

    t0 = -offset  # audio_time at chart_time 0
    half_window_frames = max(1, int(round(slot_interval / 2.0 * frame_rate)))
    n_frames = onset.size

    scores = []
    chart_times = []
    n = 0
    while True:
        audio_time = t0 + n * slot_interval
        if audio_time > duration:
            break
        n += 1
        if audio_time < 0:
            continue
        center = int(round(audio_time * frame_rate))
        lo = max(0, center - half_window_frames)
        hi = min(n_frames, center + half_window_frames + 1)
        score = float(onset[lo:hi].max()) if hi > lo else 0.0
        scores.append(score)
        chart_times.append((n - 1) * slot_interval)

    if not scores:
        return []
    scores_arr = np.array(scores)
    nonzero = scores_arr[scores_arr > 0]
    if nonzero.size == 0:
        return []

    density = max(0.01, min(1.0, density))
    threshold = np.percentile(nonzero, max(0.0, (1.0 - density) * 100.0))
    keep = (scores_arr > 0) & (scores_arr >= threshold)
    if not keep.any():
        return []

    kept_scores = scores_arr[keep]
    kept_times = [t for t, k in zip(chart_times, keep) if k]
    median = float(np.median(kept_scores))

    return [(t, "1" if s > median else "2") for t, s in zip(kept_times, kept_scores)]


def format_tja_body(notes, bpm: float, subdivision: int = 16,
                     measure_num: int = 4, measure_den: int = 4,
                     duration_seconds: float = 0.0) -> str:
    """Lays `notes` ([(chart_time, char)], as returned by generate_notes())
    out into TJA measure lines - one measure per line, `subdivision *
    measure_num/measure_den` characters (0/1/2) followed by a comma, filling
    empty slots with "0". Generates enough trailing empty measures to cover
    `duration_seconds` (or just past the last note if that's not given), the
    same way a real chart has empty measures over instrumental/outro
    sections rather than stopping abruptly at the last note."""
    measure_val = measure_num / measure_den
    chars_per_measure = max(1, round(subdivision * measure_val))
    measure_duration = 240.0 * measure_val / bpm if bpm and bpm > 0 else 2.0

    if duration_seconds <= 0:
        duration_seconds = (notes[-1][0] + measure_duration) if notes else measure_duration
    n_measures = max(1, math.ceil(duration_seconds / measure_duration))

    grids = [["0"] * chars_per_measure for _ in range(n_measures)]
    for chart_time, char in notes:
        m = int(chart_time // measure_duration)
        if m < 0 or m >= n_measures:
            continue
        local = chart_time - m * measure_duration
        idx = int(round(local / measure_duration * chars_per_measure))
        idx = max(0, min(chars_per_measure - 1, idx))
        grids[m][idx] = char

    return "\n".join("".join(row) + "," for row in grids) + "\n"


def build_ai_variant_content(content: str, course_range, new_body: str) -> str:
    """Splices `new_body` in place of the course body spanning `course_range`
    - (start_line, end_line), the #START/#END line numbers themselves as
    returned by TJACourseAnalyzer.course_line_range() - into a *copy* of
    `content`, and appends "(AI譜面生成)" to the TITLE: line. Pure string
    manipulation - never touches the live editor, since the AI-generated
    variant is always written out as a separate file, not applied in place.
    """
    start_line, end_line = course_range
    lines = content.split("\n")
    new_body_lines = new_body.rstrip("\n").split("\n")
    spliced = lines[:start_line] + new_body_lines + lines[end_line - 1:]

    for i, line in enumerate(spliced):
        if line.startswith("TITLE:"):
            title = line[6:].strip()
            spliced[i] = f"TITLE:{title}(AI譜面生成)"
            break

    return "\n".join(spliced)
