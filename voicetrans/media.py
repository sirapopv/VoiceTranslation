"""FFmpeg helpers: write audio, read clip length, mux the dub into a video."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .tts import SAMPLE_RATE


def ffmpeg_exe() -> str:
    """FFmpeg on PATH, else the copy bundled with the imageio-ffmpeg package."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover - depends on the machine
        raise RuntimeError(
            "FFmpeg not found. Install it with:  winget install Gyan.FFmpeg"
        ) from e


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-y", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"FFmpeg failed:\n{tail}")
    return proc


def media_duration(path: str | Path) -> float | None:
    """Duration in seconds, read from FFmpeg's banner (no ffprobe needed)."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", proc.stderr)
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def has_video(path: str | Path) -> bool:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # Cover-art images in audio files show up as video streams; ignore those.
    return any(
        "Video:" in line and "attached pic" not in line for line in proc.stderr.splitlines()
    )


def write_wav(track: np.ndarray, path: str | Path) -> None:
    sf.write(str(path), track, SAMPLE_RATE, subtype="PCM_16")


def wav_to_mp3(wav: str | Path, mp3: str | Path) -> None:
    _run(["-i", str(wav), "-ar", "44100", "-ac", "2", "-b:a", "192k", str(mp3)])


def mux_video(
    video: str | Path,
    dub_wav: str | Path,
    out: str | Path,
    original_volume: float = 0.0,
    burn_srt: str | Path | None = None,
) -> None:
    """Put the dub track on the video.

    original_volume: 0 replaces the original audio; e.g. 0.15 keeps it quietly underneath
    (useful when the clip has background music).
    burn_srt: optional subtitle file to burn into the picture (re-encodes the video).
    """
    video, dub_wav, out = Path(video).resolve(), Path(dub_wav).resolve(), Path(out).resolve()
    args = ["-i", str(video), "-i", str(dub_wav)]
    # Cut the output at the clip length. (-shortest is imprecise when re-encoding.)
    duration = media_duration(video)
    length_args = ["-t", f"{duration:.3f}"] if duration else ["-shortest"]

    if original_volume > 0:
        audio_filter = (
            f"[0:a]volume={original_volume}[bg];"
            "[bg][1:a]amix=inputs=2:duration=longest:normalize=0[aout]"
        )
        audio_map = "[aout]"
    else:
        # The dub track is already padded to the clip length by build_dub_track.
        audio_filter = "[1:a]anull[aout]"
        audio_map = "[aout]"

    with tempfile.TemporaryDirectory() as tmp:
        if burn_srt:
            # Copy the subtitles next to the working dir so the filter path has no
            # drive letter or backslashes (FFmpeg filter escaping on Windows is painful).
            shutil.copy(burn_srt, Path(tmp) / "subs.srt")
            style = "FontName=Arial,FontSize=16,Bold=1,Outline=2,Shadow=0,MarginV=60"
            filter_complex = f"[0:v]subtitles=subs.srt:force_style='{style}'[vout];{audio_filter}"
            video_args = ["-map", "[vout]", "-c:v", "libx264", "-crf", "18", "-preset", "medium"]
        else:
            filter_complex = audio_filter
            video_args = ["-map", "0:v", "-c:v", "copy"]

        args += [
            "-filter_complex", filter_complex,
            *video_args,
            "-map", audio_map,
            "-c:a", "aac", "-b:a", "192k",
            *length_args,
            "-movflags", "+faststart",
            str(out),
        ]
        _run(args, cwd=tmp)
