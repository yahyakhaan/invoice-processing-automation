from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult

from invoice_agent.config import Settings


class MockChatModel(BaseChatModel):
    """Deterministic stand-in. Agents must not treat this as LLM reasoning."""

    @property
    def _llm_type(self) -> str:
        return "mock-deterministic"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        content = (
            "DETERMINISTIC FALLBACK — NO LLM. "
            "Use tool results and local rules; do not claim model reasoning."
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def get_chat_model(settings: Settings):
    if settings.require_llm and settings.provider == "mock":
        raise RuntimeError("--require-llm was set but no live LLM provider is configured")
    if settings.provider == "mock":
        return MockChatModel()
    if settings.provider == "xai":
        from langchain_openai import ChatOpenAI

        if not settings.xai_api_key:
            raise RuntimeError("XAI_API_KEY is required for provider=xai")
        return ChatOpenAI(
            model=settings.model_name,
            api_key=settings.xai_api_key,
            base_url="https://api.x.ai/v1",
            temperature=0,
        )
    if settings.provider == "openai":
        from langchain_openai import ChatOpenAI

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for provider=openai")
        return ChatOpenAI(model=settings.model_name, api_key=settings.openai_api_key, temperature=0)
    if settings.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for provider=anthropic")
        return ChatAnthropic(model=settings.model_name, api_key=settings.anthropic_api_key, temperature=0)
    if settings.provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=settings.model_name, base_url=settings.ollama_base_url, temperature=0)
    raise RuntimeError(f"Unknown provider: {settings.provider}")
