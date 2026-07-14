"""Provider-agnostic LLM abstraction (Req 6.1, 6.2).

All large-language-model calls in the pipeline go through the single public
entry point :func:`llm_generate`, which selects a provider from the
``LLM_PROVIDER`` environment variable (``bedrock`` | ``anthropic``) and delegates
to it. Every provider returns the same normalized :class:`LLMResult`, so the
Extractor imports **only** ``llm_generate`` -- never a provider SDK -- and swapping
providers requires no Extractor code change (Req 6.2).

Banned services (Amazon Pinpoint, DynamoDB, Cognito, and Lambda-as-application-
architecture) are never referenced here; PostgreSQL remains the only database
(Req 6.3, 6.4).

Typical use::

    from funhouse_pipeline.llm import llm_generate
    result = llm_generate("extract_records", context)   # provider from env
    for item in result.items:
        parse(item.content)

Provider construction is isolated behind a :class:`ProviderRegistry` of
factories, so real providers are built lazily (only when selected) and tests can
register fake providers without any network access.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from funhouse_pipeline.llm.anthropic import AnthropicProvider
from funhouse_pipeline.llm.base import (
    LLMProvider,
    LLMResult,
    LLMResultItem,
    extract_records_from_context,
)
from funhouse_pipeline.llm.bedrock import BedrockBatchError, BedrockBatchProvider

__all__ = [
    "LLMProvider",
    "LLMResult",
    "LLMResultItem",
    "BedrockBatchProvider",
    "BedrockBatchError",
    "AnthropicProvider",
    "ProviderRegistry",
    "ProviderNotConfiguredError",
    "default_registry",
    "get_provider",
    "llm_generate",
]

# The provider values recognized by the abstraction (mirrors config.VALID_LLM_PROVIDERS).
SUPPORTED_PROVIDERS = ("bedrock", "anthropic")

ProviderFactory = Callable[[Mapping[str, Any]], LLMProvider]


class ProviderNotConfiguredError(KeyError):
    """Raised when an unknown/unregistered provider name is requested."""


class ProviderRegistry:
    """Maps provider names to factories that build :class:`LLMProvider`s.

    Factories receive an ``options`` mapping (e.g. the ``context`` or config
    values) so a provider can be constructed with the right bucket/region. The
    registry never builds a provider until it is actually selected, so importing
    this package never requires boto3 credentials or the anthropic SDK.
    """

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        """Register (or override) the factory for ``name``."""
        self._factories[name.lower()] = factory

    def unregister(self, name: str) -> None:
        self._factories.pop(name.lower(), None)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def create(self, name: str, options: Mapping[str, Any] | None = None) -> LLMProvider:
        """Build the provider registered under ``name``.

        Raises:
            ProviderNotConfiguredError: If no factory is registered for ``name``.
        """
        key = (name or "").lower()
        try:
            factory = self._factories[key]
        except KeyError as exc:
            raise ProviderNotConfiguredError(
                f"No LLM provider registered for {name!r}. "
                f"Known providers: {self.names() or '(none)'}."
            ) from exc
        return factory(options or {})


def _bedrock_factory(options: Mapping[str, Any]) -> LLMProvider:
    """Default factory for the Bedrock Batch provider.

    Pulls the S3 bucket / region / role from the options mapping (which callers
    populate from :class:`~funhouse_pipeline.config.Config`). The bucket is
    required because Bedrock Batch performs JSONL I/O through S3.
    """
    s3_bucket = options.get("s3_bucket")
    if not s3_bucket:
        raise ValueError(
            "The bedrock provider requires 's3_bucket' in the call options "
            "(set config.s3_bucket) for Batch JSONL input/output."
        )
    kwargs: dict[str, Any] = {
        "s3_bucket": s3_bucket,
        "role_arn": options.get("role_arn"),
        "region": options.get("region", "af-south-1"),
    }
    if options.get("model_id"):
        kwargs["model_id"] = options["model_id"]
    return BedrockBatchProvider(**kwargs)


def _anthropic_factory(options: Mapping[str, Any]) -> LLMProvider:
    """Default factory for the Anthropic provider."""
    kwargs: dict[str, Any] = {}
    if options.get("model_id"):
        kwargs["model_id"] = options["model_id"]
    if options.get("api_key"):
        kwargs["api_key"] = options["api_key"]
    return AnthropicProvider(**kwargs)


def _build_default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("bedrock", _bedrock_factory)
    registry.register("anthropic", _anthropic_factory)
    return registry


# Module-level default registry used by llm_generate when none is injected.
_DEFAULT_REGISTRY = _build_default_registry()


def default_registry() -> ProviderRegistry:
    """Return the process-wide default provider registry."""
    return _DEFAULT_REGISTRY


def get_provider(
    name: str,
    *,
    options: Mapping[str, Any] | None = None,
    registry: ProviderRegistry | None = None,
) -> LLMProvider:
    """Resolve and construct the provider registered under ``name``."""
    reg = registry or _DEFAULT_REGISTRY
    return reg.create(name, options)


def llm_generate(
    task: str,
    context: Mapping[str, Any],
    *,
    provider: str | None = None,
    options: Mapping[str, Any] | None = None,
    registry: ProviderRegistry | None = None,
    env: Mapping[str, str] | None = None,
) -> LLMResult:
    """Single public entry point for every model call (Req 6.1, 6.2).

    Selects the provider from the ``LLM_PROVIDER`` environment variable
    (``bedrock`` | ``anthropic``), delegates the call, and returns a normalized
    :class:`LLMResult` the Extractor parses identically regardless of provider.

    Args:
        task: Stable task identifier (e.g. ``extract_records``).
        context: Images/text plus the business-rules system prompt.
        provider: Explicit provider name; overrides the environment when given
            (primarily for tests). Defaults to ``env['LLM_PROVIDER']``.
        options: Provider construction options (e.g. ``s3_bucket``, ``region``).
            Callers typically build this from :class:`~funhouse_pipeline.config.Config`.
        registry: Provider registry to resolve against (defaults to the
            process-wide registry). Injectable so tests can register fakes.
        env: Environment mapping (defaults to ``os.environ``). Injectable.

    Returns:
        A normalized :class:`LLMResult`.

    Raises:
        ProviderNotConfiguredError: If the selected provider is not registered.
    """
    environment = os.environ if env is None else env
    name = provider or environment.get("LLM_PROVIDER", "bedrock")
    selected = get_provider(name, options=options, registry=registry)
    return selected.generate(task, context)
