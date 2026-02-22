from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
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
        self.provider = self.settings.llm_provider.strip().lower()
        self.model = self._resolve_model()
        self.client = None

        if self.provider == "anthropic":
            self.client = self._init_anthropic_client()
        elif self.provider == "bedrock":
            self.client = self._init_bedrock_client()

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
            provider_hint = (
                "Set CLAUDE_API_KEY for Anthropic or AWS credentials/BEDROCK_REGION for Bedrock."
            )
            return UrlEnrichmentResult(
                employer_name=name,
                status="needs_setup",
                careers_url=None,
                source_url=None,
                confidence_score=None,
                reason=(
                    f"LLM client not configured for provider '{self.provider}'. " f"{provider_hint}"
                ),
            )

        prompt = (
            f"Find the URL for the page that lists actual open job positions (individual job postings) for this employer. "
            f"This is often hosted on a third-party ATS like Greenhouse, Lever, Workday, or similar — not just the employer's general careers/culture page. "
            f"Return JSON only.\n"
            f"Employer: {name}\n"
            f"JSON schema:\n"
            f"{{\"careers_url\": string|null, \"source_url\": string|null, "
            f"\"confidence_score\": number, \"reason\": string}}\n"
            f"Rules: confidence_score is between 0 and 1."
        )

        try:
            raw_text = self._request_completion(prompt)
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
                reason=f"{self.provider} request failed.",
                error_message=str(exc),
            )

    def _request_completion(self, prompt: str) -> str:
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._extract_text_from_anthropic(response)

        if self.provider == "bedrock":
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 400,
                "temperature": 0,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            }
            response = self.client.invoke_model(
                modelId=self.model,
                body=json.dumps(payload),
                contentType="application/json",
                accept="application/json",
            )
            raw_response = response["body"].read().decode("utf-8")
            parsed = json.loads(raw_response)
            return self._extract_text_from_bedrock(parsed)

        raise ValueError(
            f"Unsupported LLM provider '{self.provider}'. Use 'bedrock' or 'anthropic'."
        )

    def _resolve_model(self) -> str:
        if self.provider == "bedrock":
            return self.settings.bedrock_model_id
        return self.settings.anthropic_model

    def _init_anthropic_client(self) -> object | None:
        if not self.settings.claude_api_key:
            return None
        try:
            import anthropic

            return anthropic.Anthropic(api_key=self.settings.claude_api_key)
        except Exception:
            return None

    def _init_bedrock_client(self) -> object | None:
        try:
            import boto3

            session_kwargs: dict[str, str] = {"region_name": self.settings.bedrock_region}
            if self.settings.aws_profile:
                session_kwargs["profile_name"] = self.settings.aws_profile
                session = boto3.Session(**session_kwargs)
            else:
                if self.settings.aws_access_key_id:
                    session_kwargs["aws_access_key_id"] = self.settings.aws_access_key_id
                if self.settings.aws_secret_access_key:
                    session_kwargs["aws_secret_access_key"] = self.settings.aws_secret_access_key
                if self.settings.aws_session_token:
                    session_kwargs["aws_session_token"] = self.settings.aws_session_token
                session = boto3.Session(**session_kwargs)

            return session.client("bedrock-runtime")
        except Exception:
            return None

    @staticmethod
    def _extract_text_from_anthropic(response: object) -> str:
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
    def _extract_text_from_bedrock(response: dict[str, Any]) -> str:
        content = response.get("content")
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
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
