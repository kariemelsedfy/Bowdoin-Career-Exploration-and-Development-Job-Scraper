from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from backend.app.core.config import Settings, get_settings

ATS_DOMAIN_HINTS = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "icims.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "jobvite.com",
    "workable.com",
    "bamboohr.com",
    "ultipro.com",
)

JOB_HINTS = (
    "job",
    "jobs",
    "career",
    "careers",
    "open positions",
    "openings",
    "search jobs",
    "apply",
)


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
    """Resolve likely careers URLs using Anthropic Claude on Amazon Bedrock.

    Note: this scaffold does not yet include independent web search/URL verification.
    Add that before production use.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.bedrock_model_id
        self.client = self._init_bedrock_client()
        self.http_client = self._init_http_client()

    def enrich_employer(self, employer_name: str, seed_url: str | None = None) -> UrlEnrichmentResult:
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
                reason=(
                    "Bedrock client not configured. Set AWS_BEARER_TOKEN_BEDROCK "
                    "(API key auth) or AWS credentials plus BEDROCK_REGION."
                ),
            )

        prompt = (
            f"Find the URL for the page that lists actual open job positions (individual job postings) for this employer. "
            f"This is often hosted on a third-party ATS like Greenhouse, Lever, Workday, or similar — not just the employer's general careers/culture page. "
            f"Return JSON only.\n"
            f"Employer: {name}\n"
            f"Known careers page URL (may be general and not direct listings): {seed_url or 'unknown'}\n"
            f"JSON schema:\n"
            f"{{\"careers_url\": string|null, \"source_url\": string|null, "
            f"\"confidence_score\": number, \"reason\": string}}\n"
            f"Rules: confidence_score is between 0 and 1."
        )

        try:
            raw_text = self._request_completion(prompt)
            payload = self._parse_payload(raw_text)

            candidate_url = self._validate_url(payload.get("careers_url"))
            source_url = self._validate_url(payload.get("source_url"))
            confidence = self._normalize_confidence(payload.get("confidence_score"))
            reason = str(payload.get("reason") or "No reason provided.").strip()
            careers_url, validation_note = self._verify_job_listings_url(
                candidate_url, seed_url=seed_url
            )
            if validation_note:
                reason = f"{reason} Validation: {validation_note}".strip()
            status = "success" if careers_url else "failed"
            if careers_url and confidence is None:
                confidence = 0.6

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
                reason=self._reason_for_exception(exc),
                error_message=str(exc),
            )

    def _request_completion(self, prompt: str) -> str:
        system_text = (
            "Return only valid JSON with keys: careers_url, source_url, "
            "confidence_score, reason. No markdown."
        )

        # Use Bedrock Converse first.
        try:
            response = self.client.converse(
                modelId=self.model,
                system=[{"text": system_text}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 400, "temperature": 0},
            )
            text = self._extract_text_from_bedrock_converse(response)
            if not text:
                raise ValueError("Bedrock converse returned empty content.")
            # If converse output isn't machine-parseable JSON, retry with invoke_model.
            self._parse_payload(text)
            return text
        except Exception as converse_exc:
            anthropic_request = {
                "anthropic_version": "bedrock-2023-05-31",
                "system": system_text,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                "max_tokens": 400,
                "temperature": 0,
            }
            try:
                text = self._invoke_anthropic_native(anthropic_request)
                self._parse_payload(text)
                return text
            except Exception as invoke_exc:
                converse_message = self._format_bedrock_exception(converse_exc)
                invoke_message = self._format_bedrock_exception(invoke_exc)
                raise RuntimeError(
                    "Bedrock request failed in both paths. "
                    f"converse={converse_message}; invoke_model={invoke_message}"
                ) from invoke_exc

    def _init_http_client(self) -> object | None:
        try:
            import httpx

            timeout = max(5, min(self.settings.request_timeout_seconds, 20))
            return httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; Bowdoin-CED-JobTracker/1.0; +https://bowdoin.edu)"
                    )
                },
            )
        except Exception:
            return None

    def _invoke_anthropic_native(self, request_body: dict[str, object]) -> str:
        response = self.client.invoke_model(
            modelId=self.model,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )
        raw_response = response["body"].read().decode("utf-8")
        parsed = json.loads(raw_response)
        text = self._extract_text_from_bedrock_invoke_model(parsed)
        if not text:
            raise ValueError("Bedrock invoke_model returned empty content.")
        return text

    def _verify_job_listings_url(
        self, candidate_url: str | None, seed_url: str | None = None
    ) -> tuple[str | None, str]:
        if not candidate_url:
            return None, "Model did not provide a URL."

        resolved_url, note = self._probe_url(candidate_url)
        if resolved_url:
            return resolved_url, note

        # If candidate fails, try a deterministic fallback by scanning the known careers page.
        if seed_url:
            discovered = self._discover_jobs_url_from_seed(seed_url)
            if discovered:
                return discovered, "Discovered verified job URL from seed careers page."

        return None, note

    def _discover_jobs_url_from_seed(self, seed_url: str) -> str | None:
        verified_seed, _ = self._probe_url(seed_url)
        if not verified_seed:
            return None

        if self.http_client is None:
            return None

        try:
            response = self.http_client.get(verified_seed)
        except Exception:
            return None

        if response.status_code >= 400:
            return None

        content_type = (response.headers.get("content-type") or "").lower()
        if "text/html" not in content_type:
            return None

        html = response.text or ""
        links = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        if not links:
            return None

        candidates: list[tuple[int, str]] = []
        for href in links:
            absolute = urljoin(str(response.url), href)
            normalized = self._validate_url(absolute)
            if not normalized:
                continue
            score = self._score_job_url_candidate(normalized)
            if score > 0:
                candidates.append((score, normalized))

        for _, url in sorted(candidates, key=lambda item: item[0], reverse=True):
            verified_url, _ = self._probe_url(url)
            if verified_url:
                return verified_url

        return None

    def _score_job_url_candidate(self, url: str) -> int:
        parsed = urlparse(url)
        haystack = f"{parsed.netloc.lower()} {parsed.path.lower()}"

        score = 0
        if any(domain in parsed.netloc.lower() for domain in ATS_DOMAIN_HINTS):
            score += 5
        if any(hint in haystack for hint in JOB_HINTS):
            score += 2
        if "privacy" in haystack or "terms" in haystack or "cookie" in haystack:
            score -= 4
        return score

    def _probe_url(self, url: str) -> tuple[str | None, str]:
        if self.http_client is None:
            return None, "HTTP validator unavailable (httpx not initialized)."

        try:
            response = self.http_client.get(url)
        except Exception as exc:
            return None, f"URL not reachable: {exc}"

        if response.status_code >= 400:
            return None, f"URL returned HTTP {response.status_code}."

        final_url = str(response.url)
        content_type = (response.headers.get("content-type") or "").lower()
        body = (response.text or "")[:15000].lower()
        final_lower = final_url.lower()

        hint_hits = 0
        if any(domain in final_lower for domain in ATS_DOMAIN_HINTS):
            hint_hits += 2
        if any(hint in final_lower for hint in JOB_HINTS):
            hint_hits += 1
        if any(hint in body for hint in ("job", "jobs", "career", "careers", "open positions")):
            hint_hits += 1

        if "text/html" in content_type and hint_hits > 0:
            return final_url, "Verified reachable jobs/careers-like page."

        if hint_hits > 1:
            return final_url, "Verified reachable URL with strong jobs/careers indicators."

        return None, "URL reachable but does not look like a jobs listings page."

    def _init_bedrock_client(self) -> object | None:
        try:
            import boto3

            if self.settings.aws_bearer_token_bedrock:
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self.settings.aws_bearer_token_bedrock
                return boto3.client("bedrock-runtime", region_name=self.settings.bedrock_region)

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
    def _extract_text_from_bedrock_converse(response: dict[str, Any]) -> str:
        output = response.get("output")
        if not isinstance(output, dict):
            return ""
        message = output.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _extract_text_from_bedrock_invoke_model(response: dict[str, Any]) -> str:
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
    def _reason_for_exception(exc: Exception) -> str:
        message = str(exc).lower()
        if "use case details" in message or "ftuformnotfilled" in message:
            return (
                "Bedrock Anthropic model access is not enabled for this account. "
                "Ask your admin to submit Anthropic use-case details in Bedrock Model access, "
                "then retry."
            )
        if "unable to locate credentials" in message:
            return "AWS credentials or AWS_BEARER_TOKEN_BEDROCK are missing."
        if "accessdeniedexception" in message:
            return (
                "Access denied calling Bedrock. Check IAM/API key permissions for "
                "bedrock-runtime invocation and model access."
            )
        if "validationexception" in message:
            return (
                "Bedrock request validation failed. Check BEDROCK_MODEL_ID, region, and request body schema."
            )
        if "resource not found" in message or "resourcenotfoundexception" in message:
            return "Bedrock model ID not found or not available in this region/account."
        if "endpointconnectionerror" in message or "could not connect to the endpoint url" in message:
            return "Cannot reach Bedrock endpoint. Check network/VPN/firewall and BEDROCK_REGION."
        if "throttl" in message or "rate" in message:
            return "Bedrock rate limit reached. Retry with lower concurrency."
        return "bedrock request failed."

    @staticmethod
    def _format_bedrock_exception(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error = response.get("Error", {})
            metadata = response.get("ResponseMetadata", {})
            code = error.get("Code", type(exc).__name__)
            message = error.get("Message", str(exc))
            http_status = metadata.get("HTTPStatusCode")
            request_id = metadata.get("RequestId")
            return f"{code}(http={http_status}, request_id={request_id}, message={message})"
        return f"{type(exc).__name__}({exc})"

    @staticmethod
    def _extract_json(text: str) -> dict[str, object]:
        direct = text.strip()
        if direct.startswith("{") and direct.endswith("}"):
            return json.loads(direct)

        fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
        if fence_match:
            return json.loads(fence_match.group(1))

        match = re.search(r"\{[\s\S]*?\}", text)
        if not match:
            raise ValueError("No JSON object found in model response.")

        return json.loads(match.group(0))

    @classmethod
    def _parse_payload(cls, text: str) -> dict[str, object]:
        # Path 1: strict JSON extraction
        try:
            payload = cls._extract_json(text)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

        # Path 2: Python-literal dict fallback (single quotes, True/None)
        literal = text.strip()
        if literal.startswith("{") and literal.endswith("}"):
            try:
                payload = ast.literal_eval(literal)
                if isinstance(payload, dict):
                    return {str(k): v for k, v in payload.items()}
            except Exception:
                pass

        # Path 2b: fenced Python-literal dict fallback.
        fence_literal_match = re.search(
            r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
            text,
            flags=re.IGNORECASE,
        )
        if fence_literal_match:
            fenced_literal = fence_literal_match.group(1).strip()
            try:
                payload = ast.literal_eval(fenced_literal)
                if isinstance(payload, dict):
                    return {str(k): v for k, v in payload.items()}
            except Exception:
                pass

        # Path 3: key-value and URL heuristics
        url_matches = re.findall(r"https?://[^\s)\"']+", text)
        careers_url = url_matches[0] if url_matches else None
        source_url = url_matches[1] if len(url_matches) > 1 else careers_url

        confidence_match = re.search(
            r"(?:confidence[_\s-]*score|confidence)\s*[\"']?\s*[:=]\s*([01](?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        confidence_score: float | None = None
        if confidence_match:
            try:
                confidence_score = float(confidence_match.group(1))
            except Exception:
                confidence_score = None

        if careers_url or source_url:
            return {
                "careers_url": careers_url,
                "source_url": source_url,
                "confidence_score": confidence_score if confidence_score is not None else 0.5,
                "reason": text.strip()[:1000] or "Parsed from non-JSON model response.",
            }

        raise ValueError("No JSON object found in model response.")

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
