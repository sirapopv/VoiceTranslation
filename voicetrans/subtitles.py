"""Read and write SubRip (.srt) subtitle files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TIME_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


@dataclass
class Cue:
    index: int
    start: float  # seconds
    end: float  # seconds
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def format_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[Cue]:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        time_idx = next((i for i, line in enumerate(lines) if _TIME_RE.search(line)), None)
        if time_idx is None:
            continue
        g = _TIME_RE.search(lines[time_idx]).groups()
        body = " ".join(line.strip() for line in lines[time_idx + 1 :]).strip()
        if not body:
            continue
        cues.append(
            Cue(
                index=len(cues) + 1,
                start=_to_seconds(*g[:4]),
                end=_to_seconds(*g[4:]),
                text=body,
            )
        )
    return cues


def read_srt(path: str | Path) -> list[Cue]:
    return parse_srt(Path(path).read_text(encoding="utf-8-sig"))


def write_srt(cues: list[Cue], path: str | Path) -> None:
    blocks = [
        f"{i}\n{format_time(c.start)} --> {format_time(c.end)}\n{c.text}\n"
        for i, c in enumerate(cues, start=1)
    ]
    Path(path).write_text("\n".join(blocks), encoding="utf-8")
