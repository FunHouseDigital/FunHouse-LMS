"""Anthropic API provider for the LLM abstraction (post-credits target).

This is the provider the business switches to once AWS credits expire: setting
``LLM_PROVIDER=anthropic`` routes calls here instead of Bedrock Batch, with the
**same** ``task``/``context`` contract and the **same** normalized
:class:`~funhouse_pipeline.llm.base.LLMResult` output shape (Req 6.2). No caller
code changes.

The real transport is a thin wrapper over the Anthropic Messages API. The
``anthropic`` SDK is an optional, later dependency, so the client is
**injectable** and lazily constructed only when actually needed -- which keeps
this module importable (and fully testable with a fake client) without the SDK
installed.
"""

from __future__ import annotations

from typing import Any, Mapping

from funhouse_pipeline.llm.base import (
    LLMResult,
    LLMResultItem,
    extract_records_from_context,
)

DEFAULT_MODEL_ID = "claude-3-5-sonnet-20240620"


class AnthropicProvider:
    """LLM provider backed by the Anthropic Messages API.

    Args:
        client: Injectable Anthropic client exposing ``messages.create(...)``.
            When omitted, a real ``anthropic.Anthropic`` client is lazily built
            (requires the optional ``anthropic`` package + API key).
        model_id: Default model id when the context does not specify one.
        max_tokens: Default completion cap per request.
        api_key: Optional key forwarded to the lazily-built real client.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        client: Any | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        max_tokens: int = 4096,
        api_key: str | None = None,
    ) -> None:
        self._client = client
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._api_key = api_key

    @property
    def client(self) -> Any:
        """Return the (possibly lazily-constructed) Anthropic client."""
        if self._client is None:
            try:
                import anthropic
            except ModuleNotFoundError as exc:  # pragma: no cover - optional dep
                raise ModuleNotFoundError(
                    "The 'anthropic' package is required to use AnthropicProvider "
                    "without an injected client. Install it or inject a client."
                ) from exc
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def generate(self, task: str, context: Mapping[str, Any]) -> LLMResult:
        """Run ``task`` over ``context`` via the Anthropic Messages API."""
        records = extract_records_from_context(context)
        system_prompt = context.get("system_prompt", "")
        model_id = context.get("model_id") or self._model_id

        items: list[LLMResultItem] = []
        for index, record in enumerate(records):
            custom_id = str(record.get("custom_id") or f"record-{index}")
            messages = [{"role": "user", "content": _build_user_content(record)}]

            kwargs: dict[str, Any] = {
                "model": model_id,
                "max_tokens": int(record.get("max_tokens", self._max_tokens)),
                "messages": messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = self.client.messages.create(**kwargs)
            content, stop_reason = _parse_response(response)
            items.append(
                LLMResultItem(
                    custom_id=custom_id,
                    content=content,
                    stop_reason=stop_reason,
                    raw=_response_to_dict(response),
                )
            )

        return LLMResult(
            task=task,
            provider=self.name,
            items=tuple(items),
            model_id=model_id,
            metadata={"record_count": len(items)},
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_user_content(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the Messages API user-content blocks for one record."""
    blocks: list[dict[str, Any]] = []
    text = record.get("text")
    if text:
        blocks.append({"type": "text", "text": str(text)})
    for image in record.get("images", []) or []:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.get("media_type", "image/png"),
                    "data": image.get("data", ""),
                },
            }
        )
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks


def _parse_response(response: Any) -> tuple[str, str | None]:
    """Normalize an Anthropic Messages response to (text, stop_reason).

    Works with both SDK objects (attribute access) and plain-dict fakes.
    """
    if isinstance(response, Mapping):
        content = response.get("content")
        stop_reason = response.get("stop_reason")
    else:
        content = getattr(response, "content", None)
        stop_reason = getattr(response, "stop_reason", None)

    texts: list[str] = []
    for block in content or []:
        if isinstance(block, Mapping):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
        else:
            if getattr(block, "type", None) == "text":
                texts.append(getattr(block, "text", ""))
    return "".join(texts), stop_reason


def _response_to_dict(response: Any) -> dict[str, Any]:
    """Best-effort capture of the raw response for audit/debug."""
    if isinstance(response, Mapping):
        return dict(response)
    for attr in ("model_dump", "to_dict", "dict"):
        method = getattr(response, attr, None)
        if callable(method):
            try:
                return dict(method())
            except Exception:  # pragma: no cover - defensive
                pass
    return {"repr": repr(response)}
