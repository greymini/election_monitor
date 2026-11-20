"""ASR_ENGINE=fake: for tests only. Returns TEXT (or raises when FAIL is set) and records each call.

Tests set the module globals: TEXT, CONFIDENCE, FAIL. Never use it in a deployment (preflight warns).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pulse.asr.base import Result, TranscriptionError

TEXT = "demo report: gaon mein chapakal kharab hai"
CONFIDENCE: Decimal | None = Decimal("0.870")
FAIL = False
calls: list[tuple[str, str, int]] = []          # (file name, lang, bytes)


class FakeEngine:
    name = "fake"

    def transcribe(self, audio_path: Path, lang: str = "hi") -> Result:
        calls.append((Path(audio_path).name, lang, Path(audio_path).stat().st_size))
        if FAIL:
            raise TranscriptionError("fake engine told to fail")
        return Result(text=TEXT, confidence=CONFIDENCE, engine=self.name)


def make() -> FakeEngine:
    return FakeEngine()
