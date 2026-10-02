"""Lay synthesized English segments onto the original subtitle timeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .subtitles import Cue
from .tts import SAMPLE_RATE, trim_silence

# Kokoro still sounds natural up to about this speed-up.
MAX_SPEED = 1.3
# Gap kept after the last segment when the clip length is unknown.
TAIL_SECONDS = 1.0


@dataclass
class PlacedSegment:
    english: str
    start: float  # where the speech actually starts in the dub track
    end: float
    slot_start: float  # where the original subtitle lines start
    slot_end: float  # the latest the speech may end without overlapping the next segment
    speed: float
    audio: np.ndarray = field(repr=False)

    @property
    def overflow(self) -> float:
        return max(0.0, self.end - self.slot_end)


def synthesize_segment(tts, text: str, voice: str, base_speed: float, available: float):
    """Synthesize text, speeding up (up to MAX_SPEED) if it doesn't fit ``available`` seconds."""
    speed = base_speed
    audio = trim_silence(tts.synthesize(text, voice=voice, speed=speed))
    duration = len(audio) / SAMPLE_RATE
    if available > 0 and duration > available and speed < MAX_SPEED:
        # Speech length scales roughly with 1/speed; aim slightly under the slot.
        speed = min(MAX_SPEED, speed * duration / available * 1.03)
        audio = trim_silence(tts.synthesize(text, voice=voice, speed=speed))
    return audio, speed


def build_dub_track(
    segments: list[dict],
    tts,
    voice: str,
    base_speed: float = 1.0,
    total_duration: float | None = None,
    on_progress=None,
) -> tuple[np.ndarray, list[PlacedSegment]]:
    """Return (mono float32 track, placed segments).

    Each segment starts at its subtitle start time. If the previous segment ran
    long, it starts right after it instead, so speech never overlaps.
    """
    placed: list[PlacedSegment] = []
    cursor = 0.0
    for i, seg in enumerate(segments):
        if i + 1 < len(segments):
            slot_end = segments[i + 1]["start"]
        elif total_duration:
            slot_end = max(seg["end"], total_duration)
        else:
            slot_end = seg["end"] + TAIL_SECONDS
        start = max(seg["start"], cursor)
        audio, speed = synthesize_segment(
            tts, seg["english"], voice, base_speed, available=slot_end - start
        )
        end = start + len(audio) / SAMPLE_RATE
        placed.append(
            PlacedSegment(
                english=seg["english"],
                start=start,
                end=end,
                slot_start=seg["start"],
                slot_end=slot_end,
                speed=speed,
                audio=audio,
            )
        )
        cursor = end
        if on_progress:
            on_progress(i + 1, len(segments))

    length = max([p.end for p in placed] + [total_duration or 0.0])
    track = np.zeros(int(np.ceil(length * SAMPLE_RATE)) + 1, dtype=np.float32)
    for p in placed:
        i0 = int(round(p.start * SAMPLE_RATE))
        track[i0 : i0 + len(p.audio)] += p.audio[: len(track) - i0]
    np.clip(track, -1.0, 1.0, out=track)
    return track, placed


def _split_caption(text: str, max_chars: int = 42) -> list[str]:
    """Split text into evenly sized caption chunks of at most ~max_chars."""
    words = text.split()
    if not words:
        return []
    n = max(1, -(-len(text) // max_chars))  # ceil
    target = len(text) / n
    chunks: list[str] = []
    current: list[str] = []
    for i, word in enumerate(words):
        current.append(word)
        remaining_words = len(words) - i - 1
        length = len(" ".join(current))
        if len(chunks) < n - 1 and remaining_words and (
            length >= target or (re.search(r"[,.!?;:]$", word) and length >= target * 0.6)
        ):
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def english_cues(placed: list[PlacedSegment], max_chars: int = 42) -> list[Cue]:
    """English subtitles timed to the actual dubbed speech."""
    cues: list[Cue] = []
    for p in placed:
        chunks = _split_caption(p.english, max_chars)
        total_chars = sum(len(c) for c in chunks) or 1
        t = p.start
        for chunk in chunks:
            d = (p.end - p.start) * len(chunk) / total_chars
            cues.append(Cue(index=len(cues) + 1, start=t, end=t + d, text=chunk))
            t += d
    return cues


def speech_rate(placed: list[PlacedSegment]) -> float:
    """Measured words per second of this voice at speed 1.0."""
    words = sum(len(p.english.split()) for p in placed)
    seconds = sum((p.end - p.start) * p.speed for p in placed)
    return words / seconds if seconds > 0 else 2.7


def timing_report(placed: list[PlacedSegment]) -> list[str]:
    """Human-readable warnings about segments that ran over their time slot."""
    warnings = []
    for i, p in enumerate(placed, start=1):
        if p.overflow > 0.15:
            warnings.append(
                f"segment {i} ({p.slot_start:.2f}s) is {p.overflow:.2f}s too long "
                f"even at speed {p.speed:.2f}: \"{p.english}\""
            )
    return warnings
