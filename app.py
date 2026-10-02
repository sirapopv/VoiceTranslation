"""VoiceTranslation web UI (runs locally): Thai SRT -> English voice-over.

Start with:  python app.py   (or double-click start.bat on Windows)
"""

from __future__ import annotations

import os
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv, set_key

from voicetrans.project import Project, base_name, dub_step, translate_step
from voicetrans.tts import DEFAULT_VOICE, KOKORO_VOICES, KokoroTTS

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"
OUTPUT_ROOT = APP_DIR / "outputs"

MODELS = ["claude-sonnet-5-5", "claude-opus-5-5"]
PREVIEW_TEXT = "He won the Champions League with three different clubs. Who should I cover next?"

load_dotenv(ENV_FILE)
_tts: KokoroTTS | None = None


def tts() -> KokoroTTS:
    global _tts
    if _tts is None:
        _tts = KokoroTTS()
    return _tts


def voice_choices() -> list[tuple[str, str]]:
    return [(f"{name} — {desc}", name) for name, desc in KOKORO_VOICES.items()]


def save_api_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return "⚠️ กรุณาใส่ API Key"
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), "ANTHROPIC_API_KEY", key)
    os.environ["ANTHROPIC_API_KEY"] = key
    return "✅ บันทึก API Key แล้ว (เก็บไว้ในไฟล์ .env บนเครื่องนี้เท่านั้น)"


def segments_to_rows(segments: list[dict]) -> list[list]:
    return [
        [i + 1, f"{s['start']:.2f}–{s['end']:.2f}", s.get("thai", ""), s["english"]]
        for i, s in enumerate(segments)
    ]


def apply_rows(project: Project, rows) -> None:
    """Copy edited English text from the table back into the project."""
    if rows is None:
        return
    rows = rows.values.tolist() if hasattr(rows, "values") else rows
    for row, seg in zip(rows, project.segments):
        text = str(row[3]).strip()
        if text:
            seg["english"] = text


def do_translate(srt_file, video_file, model, style, glossary, progress=gr.Progress()):
    if not srt_file:
        raise gr.Error("กรุณาอัปโหลดไฟล์ SRT ภาษาไทย")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise gr.Error("ยังไม่ได้ใส่ Anthropic API Key (แท็บ ตั้งค่า)")
    progress(0.1, desc="กำลังแปลด้วย Claude ...")
    out_dir = OUTPUT_ROOT / f"{base_name(srt_file)}_en"
    try:
        project, path = translate_step(
            srt_file, out_dir=out_dir, model=model, style=style, glossary=glossary, video=video_file
        )
    except Exception as e:
        raise gr.Error(f"แปลไม่สำเร็จ: {e}") from e
    status = f"✅ แปลแล้ว {len(project.segments)} ช่วง — แก้คำแปลในตารางได้ แล้วกด 'สร้างเสียงพากย์'"
    return project, str(path), segments_to_rows(project.segments), status


def do_load_project(project_file, video_file):
    if not project_file:
        raise gr.Error("กรุณาเลือกไฟล์ .project.json")
    project = Project.load(project_file)
    if video_file:
        project.video = video_file
    status = f"✅ เปิดโปรเจกต์แล้ว {len(project.segments)} ช่วง"
    return project, project_file, segments_to_rows(project.segments), status


def do_preview(voice, speed):
    try:
        audio = tts().synthesize(PREVIEW_TEXT, voice=voice, speed=speed)
    except Exception as e:
        raise gr.Error(f"สร้างเสียงตัวอย่างไม่สำเร็จ: {e}") from e
    return (24_000, audio)


def do_dub(project, project_path, rows, video_file, voice, speed, keep_original, burn, auto_fit,
           progress=gr.Progress()):
    if project is None:
        raise gr.Error("ยังไม่มีคำแปล — กด 'แปล' ก่อน")
    apply_rows(project, rows)
    project.voice, project.speed = voice, speed
    if video_file:
        project.video = video_file
    if auto_fit and not os.environ.get("ANTHROPIC_API_KEY"):
        auto_fit = False

    def on_progress(done, total):
        progress(done / total, desc=f"กำลังสร้างเสียง {done}/{total}")

    out_dir = Path(project_path).parent if project_path else OUTPUT_ROOT / "project"
    try:
        result = dub_step(
            project,
            out_dir,
            tts=tts(),
            original_volume=keep_original,
            burn_subtitles=burn,
            on_progress=on_progress,
            auto_fit=auto_fit,
        )
    except Exception as e:
        raise gr.Error(f"สร้างเสียงไม่สำเร็จ: {e}") from e
    if project_path:
        project.save(project_path)

    lines = [f"✅ เสร็จแล้ว — ไฟล์อยู่ที่ {out_dir}"]
    if result.shortened:
        lines.append(f"✂️ ย่อคำแปลอัตโนมัติ {result.shortened} ช่วงให้พูดทันเวลา")
    if result.warnings:
        lines.append("⚠️ ช่วงที่ยังยาวเกินเวลา (ลองย่อคำแปลในตารางแล้วกดสร้างใหม่):")
        lines += [f"  • {w}" for w in result.warnings]
    files = [str(p) for p in (result.mp3, result.srt, result.video) if p]
    return (
        str(result.mp3),
        str(result.video) if result.video else None,
        files,
        "\n".join(lines),
        segments_to_rows(project.segments),
    )


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="VoiceTranslation") as demo:
        gr.Markdown("# 🎙️ VoiceTranslation\nพากย์คลิปจากซับไทย (.srt) เป็นเสียงภาษาอังกฤษ")
        project_state = gr.State(None)
        project_path = gr.State(None)

        with gr.Tab("1. แปล"):
            with gr.Row():
                srt_file = gr.File(label="ไฟล์ซับไทย (.srt)", file_types=[".srt"], type="filepath")
                video_file = gr.File(
                    label="คลิปต้นฉบับ (ไม่บังคับ — ถ้าใส่จะได้ไฟล์ .mp4 ที่พากย์แล้ว)",
                    file_types=["video", "audio"],
                    type="filepath",
                )
            with gr.Accordion("ตัวเลือกการแปล", open=False):
                model = gr.Dropdown(MODELS, value=MODELS[0], label="โมเดลแปล (Opus แม่นกว่า แพงกว่า 2 เท่า)")
                style = gr.Textbox(
                    label="สไตล์การแปล",
                    placeholder="เช่น เล่าแบบตื่นเต้น เป็นกันเอง เหมือนช่อง YouTube กีฬา",
                    lines=2,
                )
                glossary = gr.Textbox(
                    label="คำเฉพาะ (บรรทัดละคำ)",
                    placeholder="Ziak United = Ziak United\nซีดอร์ฟ = Seedorf",
                    lines=3,
                )
            with gr.Row():
                translate_btn = gr.Button("แปล", variant="primary")
            with gr.Accordion("หรือเปิดโปรเจกต์เดิม (.project.json)", open=False):
                project_file = gr.File(label="ไฟล์โปรเจกต์", file_types=[".json"], type="filepath")
                load_btn = gr.Button("เปิดโปรเจกต์")
            status = gr.Markdown()
            table = gr.Dataframe(
                headers=["#", "เวลา (วินาที)", "ไทย", "English (แก้ไขได้)"],
                datatype=["number", "str", "str", "str"],
                interactive=True,
                wrap=True,
                type="array",
                label="คำแปล",
            )

        with gr.Tab("2. เสียงพากย์"):
            with gr.Row():
                voice = gr.Dropdown(voice_choices(), value=DEFAULT_VOICE, label="เสียง")
                speed = gr.Slider(0.8, 1.3, value=1.0, step=0.05, label="ความเร็วพูดพื้นฐาน")
            with gr.Row():
                preview_btn = gr.Button("🔊 ฟังตัวอย่างเสียง")
                preview_audio = gr.Audio(label="ตัวอย่าง", interactive=False)
            with gr.Row():
                keep_original = gr.Slider(
                    0.0, 0.5, value=0.0, step=0.05,
                    label="เก็บเสียงต้นฉบับไว้ข้างหลัง (0 = ตัดทิ้ง, 0.1–0.2 = ได้ยินเพลงพื้นหลังเบา ๆ)",
                )
                burn = gr.Checkbox(label="ฝังซับอังกฤษลงในวิดีโอ", value=False)
                auto_fit = gr.Checkbox(label="ให้ Claude ย่อประโยคที่พูดไม่ทันเวลาอัตโนมัติ", value=True)
            dub_btn = gr.Button("สร้างเสียงพากย์", variant="primary")
            dub_status = gr.Markdown()
            with gr.Row():
                out_audio = gr.Audio(label="เสียงพากย์ (.mp3)", interactive=False)
                out_video = gr.Video(label="วิดีโอที่พากย์แล้ว (.mp4)")
            out_files = gr.File(label="ดาวน์โหลดไฟล์", file_count="multiple")

        with gr.Tab("ตั้งค่า"):
            gr.Markdown(
                "ใส่ **Anthropic API Key** (สมัครที่ console.anthropic.com → API Keys) "
                "ใช้สำหรับการแปลเท่านั้น ส่วนการสร้างเสียงทำในเครื่องนี้ฟรี"
            )
            api_key = gr.Textbox(
                label="Anthropic API Key",
                type="password",
                value=os.environ.get("ANTHROPIC_API_KEY", ""),
            )
            save_btn = gr.Button("บันทึก")
            key_status = gr.Markdown()

        translate_btn.click(
            do_translate,
            [srt_file, video_file, model, style, glossary],
            [project_state, project_path, table, status],
        )
        load_btn.click(
            do_load_project, [project_file, video_file], [project_state, project_path, table, status]
        )
        preview_btn.click(do_preview, [voice, speed], preview_audio)
        dub_btn.click(
            do_dub,
            [project_state, project_path, table, video_file, voice, speed, keep_original, burn, auto_fit],
            [out_audio, out_video, out_files, dub_status, table],
        )
        save_btn.click(save_api_key, api_key, key_status)
    return demo


if __name__ == "__main__":
    OUTPUT_ROOT.mkdir(exist_ok=True)
    build_ui().queue().launch(inbrowser=True, allowed_paths=[str(OUTPUT_ROOT)])
