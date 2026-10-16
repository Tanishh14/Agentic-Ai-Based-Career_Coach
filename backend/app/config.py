"""Application settings loaded from environment / .env.

Every integration is optional. Missing values disable the matching feature and the
UI shows exactly which variable (or Settings-page field) is missing. Nothing is
replaced with sample data.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    database_url: str = f"sqlite:///{(ROOT_DIR / 'data' / 'career_coach.db').as_posix()}"
    app_secret_key: str = ""
    app_base_url: str = "http://127.0.0.1:8000"
    frontend_base_url: str = "http://127.0.0.1:5173"
    # Public HTTPS URL that GitHub can reach (e.g. an ngrok/smee tunnel). Empty = polling only.
    app_public_url: str = ""

    data_dir: str = str(ROOT_DIR / "data")
    media_storage_dir: str = str(ROOT_DIR / "data" / "media")
    blob_cache_dir: str = str(ROOT_DIR / "data" / "blob_cache")
    keys_dir: str = str(ROOT_DIR / "data" / "keys")

    # GitHub
    github_token: str = ""  # optional PAT; can also be entered in Settings UI
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"
    poll_interval_seconds: int = 60
    max_files_per_scan: int = 600
    max_file_bytes: int = 200_000

    # LLM (OpenAI-compatible: OpenCode Zen, OpenRouter, Groq, Gemini, Ollama, OpenAI...)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2

    # Transcription
    transcription_provider: str = "local_whisper"  # local_whisper | openai_compatible | none
    whisper_model: str = "base"
    transcription_base_url: str = ""
    transcription_api_key: str = ""
    transcription_model: str = "whisper-1"

    # Job providers
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "in"
    serpapi_api_key: str = ""

    # Agent bounds
    agent_max_tool_calls: int = 400
    agent_max_seconds: int = 600
    agent_max_llm_calls: int = 30

    # Media retention
    keep_interview_audio: bool = False
    max_upload_bytes: int = 25_000_000

    # Sandbox
    sandbox_enabled: bool = True
    sandbox_image_python: str = "python:3.12-slim"
    sandbox_image_node: str = "node:20-slim"
    sandbox_timeout_seconds: int = 300

    passport_validity_days: int = 180


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.media_storage_dir, s.blob_cache_dir, s.keys_dir):
        Path(d).mkdir(parents=True, exist_ok=True)
    if not s.app_secret_key:
        # Persist a locally generated secret so encrypted credentials survive restarts.
        key_file = Path(s.keys_dir) / "app_secret.key"
        if key_file.exists():
            s.app_secret_key = key_file.read_text().strip()
        else:
            s.app_secret_key = secrets.token_urlsafe(48)
            key_file.write_text(s.app_secret_key)
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
    return s
