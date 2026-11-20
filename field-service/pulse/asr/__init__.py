"""Speech-to-text engines (LLD §6). See pulse/asr/base.py."""
from pulse.asr.base import Engine, Result, TranscriptionError, convert, get_engine, transcribe

__all__ = ["Engine", "Result", "TranscriptionError", "convert", "get_engine", "transcribe"]
