"""Speech-to-text providers for recorded interview answers.

- local_whisper: openai-whisper running on this machine (audio never leaves the device).
- openai_compatible: any /audio/transcriptions endpoint (Groq, OpenAI...) — audio is uploaded
  to that third party, so the UI asks for explicit consent first.
- none: transcription disabled; typed answers remain available.
Never invents a transcript: failures are reported and the user can type/edit instead.
"""
from __future__ import annotations

import asyncio
import importlib.util
import shutil
import threading
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import get_settings
from ..database import session_scope
from ..errors import AppError, NotConfigured, ProviderError
from ..services.integrations_store import provider_config, provider_secret

_model_lock = threading.Lock()
_models: dict[str, Any] = {}


@dataclass
class Transcript:
    text: str
    provider: str
    language: str | None
    duration: float | None
    segments: list[dict[str, Any]]


def transcription_status(user_id: str) -> dict[str, Any]:
    s = get_settings()
    with session_scope() as db:
        cfg = provider_config(db, user_id, "transcription")
        key = provider_secret(db, user_id, "transcription", s.transcription_api_key)
    provider = cfg.get("provider") or s.transcription_provider
    if provider == "local_whisper":
        has_whisper = importlib.util.find_spec("whisper") is not None
        has_ffmpeg = shutil.which("ffmpeg") is not None
        ok = has_whisper and has_ffmpeg
        missing = [x for x, present in (("openai-whisper package", has_whisper), ("ffmpeg on PATH", has_ffmpeg)) if not present]
        return {"provider": "local_whisper", "available": ok, "model": cfg.get("model") or s.whisper_model, "third_party_upload": False,
                "missing": missing}
    if provider == "openai_compatible":
        base = cfg.get("base_url") or s.transcription_base_url
        ok = bool(base and key)
        return {"provider": "openai_compatible", "available": ok, "base_url": base, "model": cfg.get("model") or s.transcription_model,
                "third_party_upload": True, "missing": [] if ok else ["TRANSCRIPTION_BASE_URL / API key"]}
    return {"provider": "none", "available": False, "third_party_upload": False, "missing": ["transcription provider"]}


def _load_whisper(name: str):
    with _model_lock:
        if name not in _models:
            import whisper  # type: ignore

            _models[name] = whisper.load_model(name)
        return _models[name]


def _run_whisper(path: str, model_name: str) -> Transcript:
    model = _load_whisper(model_name)
    res = model.transcribe(path, fp16=False, language=None, condition_on_previous_text=False)
    segs = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()} for s in res.get("segments", [])]
    dur = segs[-1]["end"] if segs else None
    return Transcript(res.get("text", "").strip(), f"local_whisper:{model_name}", res.get("language"), dur, segs)


async def transcribe(user_id: str, path: str, mime: str) -> Transcript:
    st = transcription_status(user_id)
    if not st["available"]:
        raise NotConfigured("Speech-to-text", st.get("missing") or ["provider"], action="Settings → Transcription, or type your answer instead.")
    if st["provider"] == "local_whisper":
        try:
            return await asyncio.to_thread(_run_whisper, path, st["model"])
        except Exception as e:  # noqa: BLE001
            raise AppError("transcription_failed", f"Local Whisper failed: {type(e).__name__}: {str(e)[:200]}", status=500,
                           action="Edit or type the transcript manually.")
    s = get_settings()
    with session_scope() as db:
        key = provider_secret(db, user_id, "transcription", s.transcription_api_key)
    with open(path, "rb") as fh:
        data = fh.read()
    ext = "webm" if "webm" in mime else "ogg" if "ogg" in mime else "mp4" if "mp4" in mime else "wav"
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{st['base_url'].rstrip('/')}/audio/transcriptions", headers={"Authorization": f"Bearer {key}"},
                         files={"file": (f"answer.{ext}", data, mime)}, data={"model": st["model"], "response_format": "verbose_json"})
    if r.status_code == 429:
        raise ProviderError("Transcription", "rate limited", transient=True, retry_after=int(r.headers.get("retry-after", "30") or 30))
    if r.status_code >= 400:
        raise ProviderError("Transcription", f"HTTP {r.status_code}: {r.text[:200]}", transient=r.status_code >= 500)
    j = r.json()
    segs = [{"start": float(x.get("start", 0)), "end": float(x.get("end", 0)), "text": str(x.get("text", "")).strip()} for x in j.get("segments") or []]
    return Transcript(str(j.get("text", "")).strip(), f"openai_compatible:{st['model']}", j.get("language"), j.get("duration"), segs)
