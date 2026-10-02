"""Text-to-speech engines.

Kokoro-82M is the default engine: free, Apache-2.0 licensed (commercial use OK),
and fast on both CPU and NVIDIA GPUs. Other engines can be added later by
implementing the same ``synthesize`` method.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 24_000

KOKORO_REPO = "hexgrad/Kokoro-82M"

# English voices shipped with Kokoro-82M v1.0.
# Prefix: a = American English, b = British English; f = female, m = male.
KOKORO_VOICES: dict[str, str] = {
    "af_heart": "American female - warm (best overall)",
    "af_bella": "American female - expressive",
    "af_nicole": "American female - soft, close-mic",
    "af_aoede": "American female",
    "af_kore": "American female",
    "af_sarah": "American female",
    "af_nova": "American female",
    "af_sky": "American female",
    "af_alloy": "American female",
    "af_jessica": "American female",
    "af_river": "American female",
    "am_michael": "American male - clear narrator",
    "am_fenrir": "American male - deep",
    "am_puck": "American male - energetic",
    "am_echo": "American male",
    "am_eric": "American male",
    "am_liam": "American male",
    "am_onyx": "American male - low",
    "am_adam": "American male",
    "am_santa": "American male - jolly",
    "bf_emma": "British female - clear",
    "bf_isabella": "British female",
    "bf_alice": "British female",
    "bf_lily": "British female",
    "bm_george": "British male - narrator",
    "bm_fable": "British male - storyteller",
    "bm_lewis": "British male",
    "bm_daniel": "British male",
}

DEFAULT_VOICE = "am_michael"


class KokoroTTS:
    """Kokoro-82M wrapper. The model downloads on first use (~330 MB)."""

    def __init__(self, device: str | None = None):
        self.device = device
        self._pipelines: dict[str, object] = {}

    def _pipeline(self, lang_code: str):
        if lang_code not in self._pipelines:
            from kokoro import KModel, KPipeline

            # Share one model between the American and British pipelines.
            model = None
            if self._pipelines:
                model = next(iter(self._pipelines.values())).model
            if model is None:
                import torch

                device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
                model = KModel(repo_id=KOKORO_REPO).to(device).eval()
            self._pipelines[lang_code] = KPipeline(
                lang_code=lang_code, repo_id=KOKORO_REPO, model=model
            )
        return self._pipelines[lang_code]

    @property
    def device_name(self) -> str:
        import torch

        if self.device:
            return self.device
        return f"cuda ({torch.cuda.get_device_name(0)})" if torch.cuda.is_available() else "cpu"

    def synthesize(self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> np.ndarray:
        """Return mono float32 audio at SAMPLE_RATE."""
        lang_code = voice[0] if voice[:1] in ("a", "b") else "a"
        pipeline = self._pipeline(lang_code)
        chunks = []
        # split_pattern=None keeps the whole segment as one utterance.
        for result in pipeline(text, voice=voice, speed=speed, split_pattern=None):
            if result.audio is not None:
                chunks.append(result.audio.detach().cpu().numpy())
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32)


class FakeTTS:
    """Offline stand-in used by tests: a quiet tone whose length tracks word count."""

    words_per_second = 2.7

    def synthesize(self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> np.ndarray:
        seconds = max(0.3, len(text.split()) / (self.words_per_second * speed))
        t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
        return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def trim_silence(audio: np.ndarray, threshold: float = 0.01, pad: float = 0.03) -> np.ndarray:
    """Trim leading/trailing near-silence, keeping a short pad."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return audio[:0]
    pad_n = int(pad * SAMPLE_RATE)
    start = max(0, loud[0] - pad_n)
    end = min(len(audio), loud[-1] + pad_n)
    return audio[start:end]
