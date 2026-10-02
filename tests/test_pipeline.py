"""Offline tests: no API key, no model download (uses FakeTTS and a stub client)."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from voicetrans import media
from voicetrans.project import Project, base_name, dub_step
from voicetrans.subtitles import format_time, parse_srt, read_srt
from voicetrans.timing import build_dub_track, english_cues
from voicetrans.translate import Segment, Translation, translate_cues
from voicetrans.tts import SAMPLE_RATE, FakeTTS

SRT = """﻿1
00:00:00,160 --> 00:00:01,720
นักเตะที่พูดได้ 6 ภาษา

2
00:00:02,160 --> 00:00:04,080
คว้าแชมป์ยูฟ่าได้ 4 สมัย

3
00:00:04,080 --> 00:00:04,760
และเป็นคนเดียว

4
00:00:04,760 --> 00:00:05,820
ในประวัติศาสตร์
"""


def test_parse_srt():
    cues = parse_srt(SRT)
    assert [c.index for c in cues] == [1, 2, 3, 4]
    assert cues[0].start == pytest.approx(0.16)
    assert cues[3].end == pytest.approx(5.82)
    assert cues[1].text == "คว้าแชมป์ยูฟ่าได้ 4 สมัย"
    assert format_time(65.8935) == "00:01:05,894"


def test_base_name():
    assert base_name("a/Z7EP70.th_TH.srt") == "Z7EP70"
    assert base_name("clip.th.srt") == "clip"
    assert base_name("clip.srt") == "clip"


class StubClient:
    """Mimics client.beta.messages.parse; first answer is invalid to test the retry."""

    def __init__(self):
        self.calls = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs):
        self.calls += 1
        assert kwargs["fallbacks"] == "default"
        if self.calls == 1:
            segs = [Segment(line_ids=[1], english="A footballer who spoke six languages.")]
        else:
            segs = [
                Segment(line_ids=[1], english="A footballer who spoke six languages."),
                Segment(line_ids=[2], english="Four-time Champions League winner."),
                Segment(line_ids=[3, 4], english="And the only one in history"),
            ]
        return SimpleNamespace(stop_reason="end_turn", parsed_output=Translation(segments=segs))


def test_translate_groups_and_retries():
    client = StubClient()
    segments = translate_cues(parse_srt(SRT), client=client)
    assert client.calls == 2
    assert [s["line_ids"] for s in segments] == [[1], [2], [3, 4]]
    assert segments[2]["start"] == pytest.approx(4.08)
    assert segments[2]["end"] == pytest.approx(5.82)
    assert segments[2]["thai"] == "และเป็นคนเดียว ในประวัติศาสตร์"


def test_track_never_overlaps_and_speeds_up():
    segments = [
        {"start": 0.0, "end": 1.0, "english": "one two three four five six seven eight"},
        {"start": 1.0, "end": 3.0, "english": "short line"},
    ]
    track, placed = build_dub_track(segments, FakeTTS(), voice="am_michael")
    assert placed[0].speed > 1.0  # first line was too long for 1 s, so it was sped up
    assert placed[1].start >= placed[0].end  # no overlap
    assert len(track) >= int(placed[-1].end * SAMPLE_RATE)
    cues = english_cues(placed)
    assert cues[0].start == pytest.approx(placed[0].start)
    assert cues[-1].end == pytest.approx(placed[-1].end)


def test_end_to_end_with_video(tmp_path: Path):
    srt = tmp_path / "clip.th.srt"
    srt.write_text(SRT, encoding="utf-8")
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [media.ffmpeg_exe(), "-hide_banner", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:d=7",
         "-f", "lavfi", "-i", "sine=f=440:d=7",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True,
    )
    project = Project(
        source_srt=str(srt),
        video=str(video),
        segments=[
            {"line_ids": [1], "start": 0.16, "end": 1.72, "thai": "", "english": "A footballer who spoke six languages."},
            {"line_ids": [2, 3, 4], "start": 2.16, "end": 5.82, "thai": "", "english": "Four Champions League titles, and the only one in history."},
        ],
    )
    for keep, burn in ((0.0, False), (0.15, True)):
        result = dub_step(project, tmp_path / "out", tts=FakeTTS(), original_volume=keep, burn_subtitles=burn)
        assert result.mp3.exists() and result.srt.exists() and result.video.exists()
        assert media.media_duration(result.video) == pytest.approx(7.0, abs=0.2)
    assert len(read_srt(result.srt)) >= 2


def test_auto_fit_shortens_long_lines(tmp_path: Path):
    srt = tmp_path / "clip.th.srt"
    srt.write_text(SRT, encoding="utf-8")
    project = Project(
        source_srt=str(srt),
        segments=[
            {"line_ids": [1], "start": 0.16, "end": 1.72, "thai": "x",
             "english": "A footballer who could speak six different languages fluently and well."},
            {"line_ids": [2], "start": 2.16, "end": 4.08, "thai": "y", "english": "Four titles."},
        ],
    )
    seen = []

    def shortener(items):
        seen.extend(items)
        return {it["id"]: " ".join(["word"] * it["max_words"]) for it in items}

    result = dub_step(project, tmp_path / "out", tts=FakeTTS(), auto_fit=True, shortener=shortener)
    assert [it["id"] for it in seen] == [0]
    assert result.shortened == 1
    assert project.segments[0]["english"].split()[0] == "word"
    assert not result.warnings


def test_audio_only_original_gives_no_mp4(tmp_path: Path):
    srt = tmp_path / "clip.th.srt"
    srt.write_text(SRT, encoding="utf-8")
    audio = tmp_path / "clip.mp3"
    subprocess.run(
        [media.ffmpeg_exe(), "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=f=440:d=7", str(audio)],
        check=True, capture_output=True,
    )
    project = Project(
        source_srt=str(srt), video=str(audio),
        segments=[{"line_ids": [1, 2, 3, 4], "start": 0.16, "end": 5.82, "thai": "", "english": "Hello there."}],
    )
    result = dub_step(project, tmp_path / "out", tts=FakeTTS())
    assert result.video is None and result.mp3.exists()
    assert media.media_duration(result.mp3) == pytest.approx(7.0, abs=0.2)
