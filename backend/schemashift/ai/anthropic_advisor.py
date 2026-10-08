"""Claude-backed advisor (Anthropic SDK). Strict JSON, one retry, 15 s timeout."""

from __future__ import annotations

from typing import Any

import anthropic

from schemashift.ai.base import AdvisorError
from schemashift.ai.prompts import SYSTEM, build_prompt, parse_suggestion
from schemashift.models.placement import AISuggestion
from schemashift.optimizer.features import RelationshipFeatures

DEFAULT_MODEL = "claude-opus-5-5"
TIMEOUT_SECONDS = 15.0


class AnthropicAdvisor:
    name = "anthropic"
    available = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = TIMEOUT_SECONDS,
        client: Any = None,
    ) -> None:
        self.model = model
        # the SDK retries transient network errors once; invalid JSON is retried once below
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=1
        )

    def suggest_placement(self, rel: RelationshipFeatures, schema_excerpt: str) -> AISuggestion:
        prompt = build_prompt(rel, schema_excerpt)
        last: AdvisorError | None = None
        for attempt in range(2):  # invalid JSON: retry once, then give up
            text = self._ask(
                prompt if attempt == 0 else prompt + "\n\nReturn ONLY the JSON object."
            )
            try:
                return parse_suggestion(text)
            except AdvisorError as exc:
                last = exc
        raise AdvisorError(f"invalid JSON after a retry: {last}")

    def _ask(self, prompt: str) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APITimeoutError as exc:
            raise AdvisorError("the AI advisor timed out") from exc
        except anthropic.APIError as exc:
            raise AdvisorError(f"the AI advisor request failed: {type(exc).__name__}") from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise AdvisorError("the model declined to answer")
        parts = [
            getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"
        ]
        return "".join(str(p) for p in parts)
