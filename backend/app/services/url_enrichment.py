from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from backend.app.core.config import Settings, get_settings


@dataclass
class UrlEnrichmentResult:
    employer_name: str
    status: str
    careers_url: str | None
    source_url: str | None
    confidence_score: float | None
    reason: str
    error_message: str | None = None


class UrlEnrichmentService:
    """Resolve likely careers URLs using Claude.

    Note: this scaffold does not yet include independent web search/URL verification.
    Add that before production use.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.anthropic_model
        self.client = None

        if self.settings.claude_api_key:
            try:
                import anthropic

                self.client = anthropic.Anthropic(api_key=self.settings.claude_api_key)
            except Exception:
                self.client = None

    def enrich_employer(self, employer_name: str) -> UrlEnrichmentResult:
        name = employer_name.strip()
        if not name:
            return UrlEnrichmentResult(
                employer_name=employer_name,
                status="skipped",
                careers_url=None,
                source_url=None,
                confidence_score=None,
                reason="Employer name was blank.",
            )

        if self.client is None:
            return UrlEnrichmentResult(
                employer_name=name,
                status="needs_setup",
                careers_url=None,
                source_url=None,
                confidence_score=None,
                reason="Claude client not configured. Set CLAUDE_API_KEY and install dependencies.",
            )

        prompt = (
            "Find the most likely careers/jobs page URL for this employer and return JSON only.\n"
            "Employer: "
            f"{name}\n"
            "JSON schema:\n"
            "{\"careers_url\": string|null, \"source_url\": string|null, "
            "\"confidence_score\": number, \"reason\": string}\n"
            "Rules: confidence_score is between 0 and 1."
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = self._extract_text(response)
            payload = self._extract_json(raw_text)

            careers_url = self._validate_url(payload.get("careers_url"))
            source_url = self._validate_url(payload.get("source_url"))
            confidence = self._normalize_confidence(payload.get("confidence_score"))
            reason = str(payload.get("reason") or "No reason provided.").strip()
            status = "success" if careers_url else "failed"

            return UrlEnrichmentResult(
                employer_name=name,
                status=status,
                careers_url=careers_url,
                source_url=source_url,
                confidence_score=confidence,
                reason=reason,
            )
        except Exception as exc:
            return UrlEnrichmentResult(
                employer_name=name,
                status="failed",
                careers_url=None,
                source_url=None,
                confidence_score=None,
                reason="Claude request failed.",
                error_message=str(exc),
            )

    @staticmethod
    def _extract_text(response: object) -> str:
        content = getattr(response, "content", None)
        if not content:
            return ""

        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _extract_json(text: str) -> dict[str, object]:
        direct = text.strip()
        if direct.startswith("{") and direct.endswith("}"):
            return json.loads(direct)

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in Claude response.")

        return json.loads(match.group(0))

    @staticmethod
    def _validate_url(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None

        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return candidate
        return None

    @staticmethod
    def _normalize_confidence(value: object) -> float | None:
        if value is None:
            return None

        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None

        if confidence < 0:
            return 0.0
        if confidence > 1:
            return 1.0
        return confidence
