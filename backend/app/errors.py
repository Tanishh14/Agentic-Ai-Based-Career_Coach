"""Structured application errors returned to the UI as {error: {code, message, action, retry_after}}."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        action: str | None = None,
        retry_after: int | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.action = action
        self.retry_after = retry_after
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "action": self.action,
                "retry_after": self.retry_after,
                "detail": self.detail,
            }
        }


class NotConfigured(AppError):
    def __init__(self, feature: str, missing: list[str], action: str | None = None) -> None:
        super().__init__(
            "not_configured",
            f"{feature} is not configured. Missing: {', '.join(missing)}",
            status=412,
            action=action or "Open Settings → Integrations and provide the missing values.",
            detail={"feature": feature, "missing": missing},
        )


class ProviderError(AppError):
    """Transient or permanent failure of an external provider."""

    def __init__(self, provider: str, message: str, transient: bool = True, retry_after: int | None = None, status: int = 502):
        super().__init__(
            "provider_rate_limited" if retry_after else ("provider_unavailable" if transient else "provider_error"),
            f"{provider}: {message}",
            status=429 if retry_after else status,
            retry_after=retry_after,
            action="Retry later." if transient else None,
            detail={"provider": provider, "transient": transient},
        )
        self.provider = provider
        self.transient = transient
