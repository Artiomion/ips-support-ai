"""Opt-in OpenAI Responses adapter; callers explicitly provide credentials."""

import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from ips.ai.prompts import INSTRUCTIONS, prepare_customer_prompt
from ips.ai.provider import AnswerInput, AnswerResult, Citation, ProviderFailure

_URL = "https://api.openai.com/v1/responses"
_MAX_RESPONSE_BYTES = 1_048_576


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _transport(url, headers, payload, timeout):
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as result:
            data = result.read(_MAX_RESPONSE_BYTES + 1)
            if len(data) > _MAX_RESPONSE_BYTES:
                raise ProviderFailure("invalid_response")
            return json.loads(data)
    except HTTPError as exc:
        return exc.code, None
    except (URLError, TimeoutError, OSError):
        raise ProviderFailure("provider_unavailable") from None
    except (UnicodeError, ValueError):
        raise ProviderFailure("invalid_response") from None


class OpenAIProvider:
    def __init__(self, *, api_key: str, model: str, transport=None):
        if not api_key or not model:
            raise ProviderFailure("not_configured")
        self._api_key = api_key
        self._model = model
        self._transport = transport or _transport

    @classmethod
    def from_env(cls):
        if os.environ.get("AI_PROVIDER", "fake") != "openai":
            raise ProviderFailure("not_configured")
        return cls(api_key=os.environ.get("AI_API_KEY", ""), model=os.environ.get("AI_MODEL", ""))

    def answer(self, data: AnswerInput, timeout_seconds: float) -> AnswerResult:
        prompt, sources = prepare_customer_prompt(data)
        if not sources:
            return AnswerResult(
                text="Не удалось найти ответ. Обратитесь к специалисту.",
                citations=[],
                needs_human=True,
                category=None,
                priority="normal",
                reason="no_public_evidence",
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProviderFailure("invalid_timeout")
        body = {
            "model": self._model,
            "store": False,
            "instructions": INSTRUCTIONS,
            "input": prompt,
            "max_output_tokens": 700,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "support_answer",
                    "strict": True,
                    "schema": AnswerResult.model_json_schema(),
                }
            },
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        try:
            result = self._transport(_URL, headers, body, min(timeout_seconds, 20.0))
        except ProviderFailure as exc:
            code = (
                exc.code
                if exc.code in {"provider_unavailable", "invalid_response"}
                else "provider_unavailable"
            )
            raise ProviderFailure(code) from None
        except Exception:  # noqa: BLE001 - redact arbitrary transport errors at the provider boundary.
            raise ProviderFailure("provider_unavailable") from None
        if isinstance(result, tuple):
            raise ProviderFailure("rate_limited" if result[0] == 429 else "provider_unavailable")
        try:
            if result.get("status") != "completed":
                raise ValueError
            contents = [
                part["text"]
                for item in result["output"]
                if item.get("type") == "message"
                for part in item["content"]
                if part.get("type") == "output_text"
            ]
            if len(contents) != 1 or len(contents[0]) > _MAX_RESPONSE_BYTES:
                raise ValueError
            answer = AnswerResult.model_validate_json(contents[0], strict=True)
        except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
            raise ProviderFailure("invalid_response") from None
        allowed = {(str(item["article_id"]), item["revision"]): item for item in sources}
        citations = []
        for citation in answer.citations:
            item = allowed.get((str(citation.article_id), citation.revision))
            if item is not None:
                canonical = Citation.model_validate(
                    {key: item[key] for key in ("article_id", "revision", "title")}
                )
                if canonical not in citations:
                    citations.append(canonical)
        if not citations and not answer.needs_human:
            return AnswerResult(
                text="Не удалось подтвердить источник. Обратитесь к специалисту.",
                citations=[],
                needs_human=True,
                category=None,
                priority="normal",
                reason="no_verified_citations",
            )
        return answer.model_copy(update={"citations": citations})
