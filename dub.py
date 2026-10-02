"""Command-line dubbing: Thai SRT -> English voice-over (Kokoro) -> mp3 / srt / mp4.

Examples:
  python dub.py run clip.th.srt --video clip.mp4 --voice am_michael
  python dub.py translate clip.th.srt                # only translate, then edit the JSON
  python dub.py voice clip_en/clip.project.json      # re-make the audio after editing
  python dub.py voices                                # list voices
  python dub.py preview --voice bf_emma               # hear a voice
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from voicetrans.tts import DEFAULT_VOICE, KOKORO_VOICES


def _progress(done: int, total: int) -> None:
    print(f"\r  voice: {done}/{total} segments", end="" if done < total else "\n", flush=True)


def _apply_voice_args(project, args) -> None:
    if args.voice:
        project.voice = args.voice
    if args.speed:
        project.speed = args.speed
    if getattr(args, "video", None):
        project.video = str(Path(args.video).resolve())


def _check_voice(name: str | None) -> None:
    if name and name not in KOKORO_VOICES:
        sys.exit(f"Unknown voice '{name}'. Run:  python dub.py voices")


def _dub(project, out_dir: Path, args) -> None:
    from voicetrans.project import dub_step

    print(f"Generating English voice ({project.voice}, speed {project.speed}) ...")
    result = dub_step(
        project,
        out_dir,
        original_volume=args.keep_original,
        burn_subtitles=args.burn_subs,
        on_progress=_progress,
        auto_fit=not args.no_fit,
        on_status=print,
    )
    if result.shortened:
        print(f"Shortened {result.shortened} line(s) to fit; saved in the project file.")
    print("Done:")
    for p in (result.mp3, result.srt, result.video):
        if p:
            print(f"  {p}")
    if result.warnings:
        print("\nTiming warnings (shorten these lines in the project file and run 'voice' again):")
        for w in result.warnings:
            print(f"  - {w}")


def cmd_translate(args) -> tuple:
    from voicetrans.project import translate_step

    print(f"Translating {args.srt} with {args.model or 'default model'} ...")
    style = Path(args.style).read_text(encoding="utf-8") if args.style else ""
    glossary = Path(args.glossary).read_text(encoding="utf-8") if args.glossary else ""
    project, path = translate_step(
        args.srt,
        out_dir=args.out,
        model=args.model,
        style=style,
        glossary=glossary,
        video=getattr(args, "video", None),
    )
    print(f"  {len(project.segments)} segments -> {path}")
    for seg in project.segments:
        print(f"  [{seg['start']:6.2f}s] {seg['english']}")
    return project, path


def cmd_run(args) -> None:
    _check_voice(args.voice)
    project, path = cmd_translate(args)
    _apply_voice_args(project, args)
    project.save(path)
    _dub(project, path.parent, args)
    project.save(path)


def cmd_voice(args) -> None:
    from voicetrans.project import Project

    _check_voice(args.voice)
    path = Path(args.project)
    project = Project.load(path)
    _apply_voice_args(project, args)
    project.save(path)
    _dub(project, path.parent, args)
    project.save(path)


def cmd_voices(_args) -> None:
    for name, desc in KOKORO_VOICES.items():
        print(f"  {name:12s} {desc}")


def cmd_preview(args) -> None:
    from voicetrans import media
    from voicetrans.tts import KokoroTTS

    _check_voice(args.voice)
    tts = KokoroTTS()
    print(f"Device: {tts.device_name}")
    audio = tts.synthesize(args.text, voice=args.voice, speed=args.speed or 1.0)
    out = Path(args.out or f"preview_{args.voice}.wav")
    media.write_wav(audio, out)
    print(f"Saved {out}")


def _add_voice_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--voice", help=f"Kokoro voice (default {DEFAULT_VOICE}); see 'voices'")
    p.add_argument("--speed", type=float, help="base speaking speed, e.g. 0.9-1.2 (default 1.0)")
    p.add_argument("--video", help="original clip; produces a dubbed .mp4")
    p.add_argument(
        "--keep-original",
        type=float,
        default=0.0,
        metavar="VOL",
        help="keep the original audio under the dub at this volume, e.g. 0.15 (default: replace)",
    )
    p.add_argument("--burn-subs", action="store_true", help="burn English subtitles into the video")
    p.add_argument(
        "--no-fit",
        action="store_true",
        help="don't ask Claude to shorten lines that are too long for their time slot",
    )


def _add_translate_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("srt", help="Thai .srt file")
    p.add_argument("--out", help="output folder (default: <name>_en next to the srt)")
    p.add_argument("--model", help="Claude model (default claude-sonnet-5-5)")
    p.add_argument("--style", help="text file with style notes for the translation")
    p.add_argument("--glossary", help="text file with fixed translations, one per line")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="translate + voice in one go")
    _add_translate_options(p)
    _add_voice_options(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("translate", help="translate only (writes an editable project .json)")
    _add_translate_options(p)
    p.add_argument("--video", help="remember the original clip in the project")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("voice", help="(re)generate audio/video from a project .json")
    p.add_argument("project", help="path to <name>.project.json")
    _add_voice_options(p)
    p.set_defaults(func=cmd_voice)

    p = sub.add_parser("voices", help="list available voices")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("preview", help="save a short sample of a voice")
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--speed", type=float)
    p.add_argument("--text", default="He won the Champions League with three different clubs.")
    p.add_argument("--out")
    p.set_defaults(func=cmd_preview)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
