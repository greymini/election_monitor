"""Speech-to-text interface (LLD §6).

    transcribe(audio_path, lang="hi", engine=...) -> Result(text, confidence, engine)

One module per engine in pulse/asr/, chosen by ASR_ENGINE. Each module exposes `make() -> Engine
| None`; `none` returns None (voice notes stay audio-only). Adding a real engine means adding one
module, e.g. pulse/asr/whisper_local.py, and setting ASR_ENGINE=whisper_local.

Audio is converted to 16 kHz mono first when ffmpeg is on PATH; otherwise the original is passed
and the caller records that it was not converted. The original file is never changed.
"""
from __future__ import annotations

import importlib
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CONVERT_TIMEOUT_S = 120
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Result:
    text: str
    confidence: Decimal | None        # 0..1, None when the engine gives none
    engine: str


class TranscriptionError(RuntimeError):
    """The engine could not produce a transcript (the job retries, then marks it failed)."""


class Engine(Protocol):
    name: str

    def transcribe(self, audio_path: Path, lang: str = "hi") -> Result: ...


def get_engine(name: str) -> Engine | None:
    """The engine module pulse/asr/<name>.py; None for 'none'. Unknown names raise ValueError."""
    if not _NAME_RE.match(name or "") or name in ("base",):
        raise ValueError(f"not an ASR engine name: {name!r}")
    try:
        module = importlib.import_module(f"pulse.asr.{name}")
    except ModuleNotFoundError:
        raise ValueError(f"no ASR engine module pulse/asr/{name}.py") from None
    return module.make()


def transcribe(audio_path: Path, lang: str = "hi", *, engine: Engine) -> Result:
    result = engine.transcribe(Path(audio_path), lang)
    if result.confidence is not None and not (Decimal(0) <= result.confidence <= Decimal(1)):
        raise TranscriptionError("engine returned a confidence outside 0..1")
    return result


def converter_available() -> bool:
    return shutil.which("ffmpeg") is not None


def convert(src: Path, workdir: Path) -> tuple[Path, bool]:
    """(path to give the engine, converted?). 16 kHz mono WAV when ffmpeg works; else the original."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return src, False
    out = Path(workdir) / "audio-16k-mono.wav"
    try:
        subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
                        "-ac", "1", "-ar", str(SAMPLE_RATE), str(out)],
                       check=True, capture_output=True, timeout=CONVERT_TIMEOUT_S)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("audio conversion failed (%s); passing the original", type(e).__name__)
        return src, False
    return out, True
