"""Translate Thai subtitle lines into English voice-over segments with Claude.

Thai subtitles for short-form video are usually split into tiny fragments
("และเป็นคนเดียว" / "ในประวัติศาสตร์" / ...). Translating each fragment on its own
gives broken English, so Claude first merges consecutive lines into natural
spoken segments, then translates each segment so it fits the time those lines
covered.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, Field

from .subtitles import Cue

DEFAULT_MODEL = "claude-sonnet-5-5"

# Comfortable English voice-over pace, used to tell Claude how long a line may be.
WORDS_PER_SECOND = 2.7

SYSTEM_PROMPT = """\
You are a professional subtitle translator and voice-over script writer.
You turn Thai subtitles from short social-media videos (TikTok, YouTube, Facebook)
into an English voice-over script that will be read aloud by a text-to-speech voice
and laid back over the original video.

How to work:
1. The Thai subtitle lines are short fragments of longer sentences. Group consecutive
   lines into segments, where each segment is one natural spoken unit (a sentence or a
   clause that can stand on its own). A segment may be a single line. Every line id
   must appear in exactly one segment, in the original order, with no gaps.
   Prefer segments of roughly 2-6 seconds; avoid merging across long pauses.
2. Translate each segment into natural, engaging, conversational English, the way a
   native English-speaking creator would narrate it. Do not translate word for word.
3. Fit the time: each segment's English must be speakable within the segment's
   duration (from the start of its first line to the end of its last line) at about
   {wps} words per second. If the literal meaning is too long, shorten it while
   keeping the key facts.
4. Keep names, clubs, places, numbers and dates accurate. Use the standard English
   spelling of people, clubs and cities (e.g. ซีดอร์ฟ -> Seedorf, อาแจ็กซ์ -> Ajax).
   Text already written in Latin letters (such as a channel or brand name) must be kept
   exactly as written.
5. Write numbers the way they should be spoken naturally in English
   (e.g. "in 1995", "four times", "six languages").
6. Output only the script text in "english": no stage directions, no quotes, no notes.
"""


class Segment(BaseModel):
    line_ids: list[int] = Field(description="Consecutive Thai subtitle line ids in this segment")
    english: str = Field(description="English voice-over text for this segment")


class Translation(BaseModel):
    segments: list[Segment]


def _format_lines(cues: list[Cue]) -> str:
    rows = [
        f"[{c.index}] {c.start:.2f}-{c.end:.2f}s ({c.duration:.2f}s): {c.text}" for c in cues
    ]
    return "\n".join(rows)


def _validate(segments: list[Segment], cues: list[Cue]) -> None:
    ids = [i for seg in segments for i in seg.line_ids]
    expected = [c.index for c in cues]
    if ids != expected:
        raise ValueError(
            "segments must cover every line id exactly once and in order; "
            f"expected {expected[:5]}..{expected[-1]}, got {ids[:5]}..{ids[-1] if ids else None}"
        )
    for seg in segments:
        if not seg.english.strip():
            raise ValueError(f"empty translation for lines {seg.line_ids}")


def translate_cues(
    cues: list[Cue],
    model: str = DEFAULT_MODEL,
    style: str = "",
    glossary: str = "",
    client: anthropic.Anthropic | None = None,
) -> list[dict]:
    """Return segments as dicts: {line_ids, start, end, thai, english}."""
    client = client or anthropic.Anthropic()

    user_parts = [
        "Thai subtitle lines (format: [id] start-end (duration): text):",
        _format_lines(cues),
    ]
    if style.strip():
        user_parts.append(f"Style notes from the creator:\n{style.strip()}")
    if glossary.strip():
        user_parts.append(f"Glossary (always use these translations):\n{glossary.strip()}")
    user_message = "\n\n".join(user_parts)

    messages = [{"role": "user", "content": user_message}]
    last_error: Exception | None = None
    for _attempt in range(2):
        response = client.beta.messages.parse(
            model=model,
            max_tokens=32000,
            system=SYSTEM_PROMPT.format(wps=WORDS_PER_SECOND),
            messages=messages,
            output_format=Translation,
            # If a request is declined by a safety classifier, let the API retry it
            # on a suitable fallback model instead of failing.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to translate this subtitle file.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Subtitle file is too long for one request; split it into parts.")
        result = response.parsed_output
        try:
            _validate(result.segments, cues)
            break
        except ValueError as e:
            last_error = e
            messages = [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": result.model_dump_json()},
                {"role": "user", "content": f"That output is invalid: {e}. Please fix it."},
            ]
    else:
        raise RuntimeError(f"Translation output was invalid after retrying: {last_error}")

    by_id = {c.index: c for c in cues}
    out = []
    for seg in result.segments:
        lines = [by_id[i] for i in seg.line_ids]
        out.append(
            {
                "line_ids": seg.line_ids,
                "start": lines[0].start,
                "end": lines[-1].end,
                "thai": " ".join(c.text for c in lines),
                "english": seg.english.strip(),
            }
        )
    return out


SHORTEN_PROMPT = """\
You are tightening an English voice-over script so each line fits its time slot.
For each item, rewrite the English so it has at most max_words words, keeping the
meaning of the Thai original, the key facts (names, numbers, places), and a natural
spoken style. Keep text written in Latin letters in the Thai (channel/brand names)
exactly as written. Return one rewrite per item, in the same order, with the same id.
"""


class Rewrite(BaseModel):
    id: int
    english: str


class Rewrites(BaseModel):
    items: list[Rewrite]


def shorten_segments(
    items: list[dict],
    model: str = DEFAULT_MODEL,
    client: anthropic.Anthropic | None = None,
) -> dict[int, str]:
    """items: [{id, thai, english, max_words}] -> {id: shorter english}."""
    client = client or anthropic.Anthropic()
    rows = "\n".join(
        f"[{it['id']}] max_words={it['max_words']}\n  Thai: {it['thai']}\n  English: {it['english']}"
        for it in items
    )
    response = client.beta.messages.parse(
        model=model,
        max_tokens=16000,
        system=SHORTEN_PROMPT,
        messages=[{"role": "user", "content": rows}],
        output_format=Rewrites,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        return {}
    wanted = {it["id"] for it in items}
    return {
        r.id: r.english.strip()
        for r in response.parsed_output.items
        if r.id in wanted and r.english.strip()
    }
