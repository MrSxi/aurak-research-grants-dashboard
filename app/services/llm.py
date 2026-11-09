"""Language-model providers for the research assistant.

The retrieval (RAG) part is identical for every provider: records come from our database, and the model only
writes the answer. The default provider is the local Ollama model (Qwen). Claude (Anthropic API or Microsoft
Foundry) and Microsoft Azure OpenAI can be switched on with LLM_PROVIDER plus their keys in .env.
"""
import json
import logging

import httpx

from app import config

log = logging.getLogger("llm")

CLOUD_PROVIDERS = {"claude", "claude_azure", "azure_openai"}


class ProviderUnavailable(Exception):
    """The provider is not configured or not reachable (no key, local model not running)."""


def ollama_running() -> bool:
    try:
        return httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def cloud_configured(provider: str) -> bool:
    if provider == "claude":
        return bool(config.ANTHROPIC_API_KEY)
    if provider == "claude_azure":
        return bool(config.ANTHROPIC_FOUNDRY_API_KEY and config.ANTHROPIC_FOUNDRY_RESOURCE)
    if provider == "azure_openai":
        return bool(config.AZURE_OPENAI_ENDPOINT and config.AZURE_OPENAI_API_KEY and config.AZURE_OPENAI_DEPLOYMENT)
    return False


def active_provider() -> str | None:
    """The provider that will answer right now, or None when none is usable."""
    p = (config.LLM_PROVIDER or "ollama").lower()
    if p in CLOUD_PROVIDERS:
        if cloud_configured(p):
            return p
        if not config.LLM_FALLBACK_TO_OLLAMA:
            return None
    return "ollama" if ollama_running() else None


def model_name(provider: str | None) -> str:
    if provider in ("claude", "claude_azure"):
        return config.CLAUDE_MODEL
    if provider == "azure_openai":
        return config.AZURE_OPENAI_DEPLOYMENT or "azure-openai"
    return config.LLM_MODEL


def describe(provider: str | None) -> str:
    """Sentence fragment for the chat page. The default (Ollama) text is unchanged from the original page."""
    if provider == "claude":
        return f"Claude ({config.CLAUDE_MODEL}, Anthropic API)"
    if provider == "claude_azure":
        return f"Claude ({config.CLAUDE_MODEL}) through Microsoft Foundry"
    if provider == "azure_openai":
        return f"Microsoft Azure OpenAI ({config.AZURE_OPENAI_DEPLOYMENT})"
    return f"a local open-source model ({config.LLM_MODEL} via Ollama)"


# ---------------------------------------------------------------- streaming
def stream(provider: str, system: str, messages: list[dict]):
    """Yield text chunks. `messages` holds only user/assistant turns; `system` carries the rules + RECORDS."""
    if provider == "claude" or provider == "claude_azure":
        yield from _stream_claude(provider, system, messages)
    elif provider == "azure_openai":
        yield from _stream_azure_openai(system, messages)
    else:
        yield from _stream_ollama(system, messages)


def _stream_ollama(system, messages):
    with httpx.stream("POST", f"{config.OLLAMA_URL}/api/chat",
                      timeout=httpx.Timeout(10.0, read=config.LLM_TIMEOUT),
                      json={"model": config.LLM_MODEL, "messages": [{"role": "system", "content": system}] + messages,
                            "stream": True, "keep_alive": config.LLM_KEEP_ALIVE,
                            "options": {"temperature": 0.2, "num_ctx": 4096}}) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
        for line in r.iter_lines():
            if not line:
                continue
            part = json.loads(line)
            if part.get("error"):
                raise RuntimeError(part["error"])
            delta = (part.get("message") or {}).get("content") or ""
            if delta:
                yield delta
            if part.get("done"):
                break


def _claude_client(provider):
    import anthropic
    if provider == "claude_azure":
        return anthropic.AnthropicFoundry(api_key=config.ANTHROPIC_FOUNDRY_API_KEY,
                                          resource=config.ANTHROPIC_FOUNDRY_RESOURCE, timeout=120.0)
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=120.0)


def _stream_claude(provider, system, messages):
    import anthropic
    client = _claude_client(provider)
    kwargs = dict(model=config.CLAUDE_MODEL, max_tokens=4000, system=system, messages=messages)
    if config.CLAUDE_EFFORT:
        kwargs["output_config"] = {"effort": config.CLAUDE_EFFORT}
    try:
        if provider == "claude":
            # Server-side fallback: if a safety classifier declines, the API retries on a fallback model.
            ctx = client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            ctx = client.messages.stream(**kwargs)
        with ctx as s:
            for text in s.text_stream:
                yield text
            final = s.get_final_message()
        if final.stop_reason == "refusal":
            yield "\n\nThe model declined to answer this question."
    except anthropic.AuthenticationError as e:
        raise ProviderUnavailable("the Claude API key was rejected") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Claude rate limit reached, please try again shortly") from e
    except anthropic.APIConnectionError as e:
        raise ProviderUnavailable("Claude could not be reached") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Claude API error {e.status_code}") from e


def _stream_azure_openai(system, messages):
    url = (f"{config.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/{config.AZURE_OPENAI_DEPLOYMENT}"
           f"/chat/completions?api-version={config.AZURE_OPENAI_API_VERSION}")
    body = {"messages": [{"role": "system", "content": system}] + messages, "stream": True, "max_tokens": 1500}
    with httpx.stream("POST", url, headers={"api-key": config.AZURE_OPENAI_API_KEY},
                      timeout=httpx.Timeout(15.0, read=120.0), json=body) as r:
        if r.status_code in (401, 403):
            raise ProviderUnavailable("the Azure OpenAI key was rejected")
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"Azure OpenAI HTTP {r.status_code}: {r.text[:200]}")
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            part = json.loads(data)
            for ch in part.get("choices") or []:
                delta = (ch.get("delta") or {}).get("content")
                if delta:
                    yield delta
