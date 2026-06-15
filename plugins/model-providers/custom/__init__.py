"""Custom / Ollama (local) provider profile.

Covers any endpoint registered as provider="custom", including local
Ollama instances. Key quirks:
  - ollama_num_ctx → extra_body.options.num_ctx (local context window)
  - reasoning_config disabled → extra_body.think = False
"""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class CustomProfile(ProviderProfile):
    """Custom/Ollama local provider — think=false and num_ctx support."""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        ollama_num_ctx: int | None = None,
        **ctx: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}

        # Ollama context window
        if ollama_num_ctx:
            options = extra_body.get("options", {})
            options["num_ctx"] = ollama_num_ctx
            extra_body["options"] = options

        # Reasoning handling.
        top_level: dict[str, Any] = {}
        if reasoning_config and isinstance(reasoning_config, dict):
            _effort = (reasoning_config.get("effort") or "").strip().lower()
            _enabled = reasoning_config.get("enabled", True)
            if _effort == "none" or _enabled is False:
                # Ollama-style off switch (ignored by remote OpenAI-compat routers).
                extra_body["think"] = False
            elif ctx.get("supports_reasoning") and "haiku" not in (
                ctx.get("model") or ""
            ).lower():
                # Remote OpenAI-compatible routers (e.g. the OpenComputer router,
                # which proxies Anthropic/xAI) honour the OpenAI-style top-level
                # ``reasoning_effort`` and return ``reasoning_content`` — they
                # ignore the OpenRouter-style ``extra_body.reasoning`` object.
                # This branch only fires when the route is known reasoning-capable
                # (see run_agent._supports_reasoning_extra_body), so Ollama/local
                # endpoints — which report no reasoning support — are unaffected.
                #
                # Claude Haiku is excluded above: the router advertises it as
                # reasoning-capable but rejects the ``reasoning_effort`` parameter
                # for it with HTTP 400 ("This model does not support the effort
                # parameter"), which silently fails every Haiku request when a
                # reasoning effort is set (the default). Skipping reasoning for
                # Haiku makes it answer normally instead.
                _clamp = {
                    "minimal": "low",
                    "low": "low",
                    "medium": "medium",
                    "high": "high",
                    "xhigh": "high",
                }
                top_level["reasoning_effort"] = _clamp.get(_effort, "medium")
                # Anthropic models require temperature == 1 whenever extended
                # thinking is enabled (they reject any other value with HTTP
                # 400), so force it for Claude only. Other backends behind the
                # same router (e.g. xAI/Grok) have no such constraint and prefer
                # the caller's temperature, so don't override it for them.
                # ``top_level`` merges after the base temperature in
                # _build_kwargs_from_profile, so this wins for Claude. (Haiku
                # never reaches here, so it keeps the caller's temperature.)
                _model = (ctx.get("model") or "").lower()
                if any(p in _model for p in ("claude", "opus", "sonnet")):
                    top_level["temperature"] = 1

        return extra_body, top_level

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Custom/Ollama: base_url is user-configured; fetch if set."""
        if not self.base_url:
            return None
        return super().fetch_models(api_key=api_key, timeout=timeout)


custom = CustomProfile(
    name="custom",
    aliases=(
        "ollama",
        "local",
        "vllm",
        "llamacpp",
        "llama.cpp",
        "llama-cpp",
    ),
    env_vars=(),  # No fixed key — custom endpoint
    base_url="",  # User-configured
    # Without this, no max_tokens is sent and Ollama falls back to its internal
    # num_predict=128, truncating responses after a few tokens (#39281). This is
    # only a floor used when the user hasn't set model.max_tokens — they can
    # override per-model — so we set it generously rather than lowballing it.
    default_max_tokens=65536,
)

register_provider(custom)
