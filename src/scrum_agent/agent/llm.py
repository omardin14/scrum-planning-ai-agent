"""LLM instance factory for the Scrum Agent.

# See README: "Architecture" — Model layer
# See README: "Agentic Blueprint Reference" — Core Graph Setup

Provider-agnostic LLM factory. The active provider is selected at runtime via
the LLM_PROVIDER env var (default: "anthropic"). This means the agent works
with Anthropic Claude, OpenAI GPT, or Google Gemini — swap by changing one
env var, no code changes required.

Why lazy imports?
Each provider requires its own langchain integration package. Lazy imports
(inside the if-branches) mean importing this module never fails even if one
of the optional packages isn't installed — the error is surfaced only when
get_llm() is called with that provider.
"""

import logging

from langchain_core.language_models import BaseChatModel

from scrum_agent.config import get_llm_model, get_llm_provider

logger = logging.getLogger(__name__)

# Default models per provider — chosen for best quality/cost balance.
# Override any of these with the LLM_MODEL env var.
_PROVIDER_DEFAULTS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "google": "gemini-2.0-flash",
    "bedrock": "us.anthropic.claude-sonnet-4-20250514-v1:0",
}

# Kept for backward compatibility — callers that imported DEFAULT_MODEL still work.
DEFAULT_MODEL = _PROVIDER_DEFAULTS["anthropic"]


def get_llm(model: str | None = None, temperature: float = 0.0) -> BaseChatModel:
    """Create an LLM instance for the configured provider.

    # See README: "Agentic Blueprint Reference" — Core Graph Setup
    # BaseChatModel is LangChain's common interface for all chat LLMs.
    # Every provider wrapper (ChatAnthropic, ChatOpenAI, ChatGoogleGenerativeAI)
    # implements BaseChatModel, so the rest of the agent — nodes, bind_tools(),
    # streaming — works identically regardless of which LLM is active.
    #
    # Provider selection:
    #   LLM_PROVIDER=anthropic  →  ChatAnthropic  (default)
    #   LLM_PROVIDER=openai     →  ChatOpenAI
    #   LLM_PROVIDER=google     →  ChatGoogleGenerativeAI
    #
    # Model selection (highest priority wins):
    #   1. `model` argument passed directly to get_llm()
    #   2. LLM_MODEL env var
    #   3. Provider default from _PROVIDER_DEFAULTS

    Args:
        model: Model ID override. None means use LLM_MODEL env var or provider default.
        temperature: Sampling temperature. 0.0 = deterministic (default for structured
            artifact generation). Use 0.2–0.5 for tools that benefit from variety.

    Returns:
        A configured BaseChatModel ready for use in LangGraph nodes.

    Raises:
        OSError: If the required API key for the selected provider is not set.
        ValueError: If LLM_PROVIDER is set to an unknown value.
    """
    provider = get_llm_provider()
    resolved_model = model or get_llm_model() or _PROVIDER_DEFAULTS.get(provider, "")
    logger.debug("get_llm: provider=%s, model=%s, temperature=%s", provider, resolved_model, temperature)

    if provider == "anthropic":
        # langchain-anthropic is a required dependency — always available.
        from langchain_anthropic import ChatAnthropic

        from scrum_agent.config import get_anthropic_api_key

        llm = ChatAnthropic(
            model=resolved_model,
            api_key=get_anthropic_api_key(),
            temperature=temperature,
        )
        logger.info("LLM ready: provider=anthropic, model=%s", resolved_model)
        return llm

    if provider == "openai":
        # langchain-openai is an optional dependency (install with: uv add langchain-openai)
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise ImportError("langchain-openai is not installed. Run: uv add langchain-openai") from e

        from scrum_agent.config import get_openai_api_key

        api_key = get_openai_api_key()
        if not api_key:
            raise OSError("OPENAI_API_KEY is not set. Add it to your .env file.")
        logger.info("LLM ready: provider=openai, model=%s", resolved_model)
        return ChatOpenAI(model=resolved_model, api_key=api_key, temperature=temperature)

    if provider == "google":
        # langchain-google-genai is an optional dependency (install with: uv add langchain-google-genai)
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as e:
            raise ImportError("langchain-google-genai is not installed. Run: uv add langchain-google-genai") from e

        from scrum_agent.config import get_google_api_key

        api_key = get_google_api_key()
        if not api_key:
            raise OSError("GOOGLE_API_KEY is not set. Add it to your .env file.")
        logger.info("LLM ready: provider=google, model=%s", resolved_model)
        return ChatGoogleGenerativeAI(model=resolved_model, google_api_key=api_key, temperature=temperature)

    if provider == "bedrock":
        # langchain-aws is an optional dependency (install with: uv sync --extra bedrock)
        # # See README: "Deploy on AWS Lightsail (OpenClaw)" — Bedrock uses IAM credentials
        # from the instance role, ~/.aws/credentials, or AWS_ACCESS_KEY_ID env vars.
        # No API key needed on Lightsail — the IAM role is attached automatically.
        try:
            from langchain_aws import ChatBedrockConverse
        except ImportError as e:
            raise ImportError("langchain-aws is not installed. Run: uv sync --extra bedrock") from e

        from scrum_agent.config import get_bedrock_region

        region = get_bedrock_region()
        logger.info("LLM ready: provider=bedrock, model=%s, region=%s", resolved_model, region)
        return ChatBedrockConverse(
            model=resolved_model,
            region_name=region,
            temperature=temperature,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r}. Valid options are: anthropic (default), openai, google, bedrock."
    )
