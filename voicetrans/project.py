"""The dubbing project file and the end-to-end steps.

A project is a small JSON file next to the outputs. It holds the translated
segments, so you can edit the English text by hand and re-run only the voice step.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import media
from .subtitles import read_srt, write_srt
from .timing import MAX_SPEED, build_dub_track, english_cues, speech_rate, timing_report
from .tts import DEFAULT_VOICE


@dataclass
class Project:
    source_srt: str
    segments: list[dict]
    model: str = ""
    video: str | None = None
    voice: str = DEFAULT_VOICE
    speed: float = 1.0
    notes: dict = field(default_factory=dict)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def base_name(srt_path: str | Path) -> str:
    """'clip.th_TH.srt' -> 'clip'."""
    name = Path(srt_path).stem
    return re.sub(r"[._-](th|tha|th_TH|th-TH)$", "", name, flags=re.IGNORECASE)


def output_dir_for(srt_path: str | Path) -> Path:
    p = Path(srt_path).resolve()
    return p.parent / f"{base_name(p)}_en"


def translate_step(
    srt_path: str | Path,
    out_dir: str | Path | None = None,
    model: str | None = None,
    style: str = "",
    glossary: str = "",
    video: str | None = None,
) -> tuple[Project, Path]:
    from .translate import DEFAULT_MODEL, translate_cues

    cues = read_srt(srt_path)
    if not cues:
        raise ValueError(f"No subtitles found in {srt_path}")
    model = model or DEFAULT_MODEL
    segments = translate_cues(cues, model=model, style=style, glossary=glossary)
    out_dir = Path(out_dir) if out_dir else output_dir_for(srt_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    project = Project(
        source_srt=str(Path(srt_path).resolve()),
        segments=segments,
        model=model,
        video=str(Path(video).resolve()) if video else None,
    )
    project_path = out_dir / f"{base_name(srt_path)}.project.json"
    project.save(project_path)
    return project, project_path


class CachedTTS:
    """Remember synthesized lines so re-runs only render text that changed."""

    def __init__(self, tts):
        self.tts = tts
        self._cache: dict[tuple, object] = {}

    def synthesize(self, text, voice, speed=1.0):
        key = (text, voice, round(speed, 3))
        if key not in self._cache:
            self._cache[key] = self.tts.synthesize(text, voice=voice, speed=speed)
        return self._cache[key]


def _too_long(placed, base_speed: float) -> list[tuple[int, int]]:
    """(segment index, max words) for segments that can't fit their own slot."""
    rate = speech_rate(placed)
    target_speed = min(MAX_SPEED, base_speed * 1.15)
    out = []
    for i, p in enumerate(placed):
        own_slot = p.slot_end - p.slot_start
        if (p.end - p.start) > own_slot + 0.15:
            out.append((i, max(2, int(own_slot * rate * target_speed))))
    return out


@dataclass
class DubResult:
    wav: Path
    mp3: Path
    srt: Path
    video: Path | None
    warnings: list[str]
    shortened: int = 0


def dub_step(
    project: Project,
    out_dir: str | Path,
    tts=None,
    original_volume: float = 0.0,
    burn_subtitles: bool = False,
    on_progress=None,
    auto_fit: bool = False,
    shortener=None,
    on_status=None,
) -> DubResult:
    """Render the dub. With auto_fit, lines too long for their slot are sent back
    to Claude to be shortened (up to 2 rounds); project.segments is updated in place."""
    if tts is None:
        from .tts import KokoroTTS

        tts = KokoroTTS()
    tts = CachedTTS(tts)
    if auto_fit and shortener is None:
        from .translate import DEFAULT_MODEL, shorten_segments

        def shortener(items):
            return shorten_segments(items, model=project.model or DEFAULT_MODEL)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = base_name(project.source_srt)

    total = media.media_duration(project.video) if project.video else None

    def render():
        return build_dub_track(
            project.segments,
            tts,
            voice=project.voice,
            base_speed=project.speed,
            total_duration=total,
            on_progress=on_progress,
        )

    track, placed = render()
    shortened = 0
    for _round in range(2 if auto_fit else 0):
        long = _too_long(placed, project.speed)
        if not long:
            break
        if on_status:
            on_status(f"Shortening {len(long)} line(s) that are too long for their time slot ...")
        items = [
            {
                "id": i,
                "thai": project.segments[i].get("thai", ""),
                "english": project.segments[i]["english"],
                "max_words": max_words,
            }
            for i, max_words in long
        ]
        rewrites = shortener(items)
        if not rewrites:
            break
        for i, english in rewrites.items():
            project.segments[i]["english"] = english
        shortened += len(rewrites)
        track, placed = render()

    wav = out_dir / f"{name}.en.wav"
    mp3 = out_dir / f"{name}.en.mp3"
    srt = out_dir / f"{name}.en.srt"
    media.write_wav(track, wav)
    media.wav_to_mp3(wav, mp3)
    write_srt(english_cues(placed), srt)

    video_out = None
    if project.video and media.has_video(project.video):
        video_out = out_dir / f"{name}.en.mp4"
        media.mux_video(
            project.video,
            wav,
            video_out,
            original_volume=original_volume,
            burn_srt=srt if burn_subtitles else None,
        )
    return DubResult(
        wav=wav,
        mp3=mp3,
        srt=srt,
        video=video_out,
        warnings=timing_report(placed),
        shortened=shortened,
    )
