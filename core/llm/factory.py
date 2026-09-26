from __future__ import annotations

from core.config import get_settings


def get_llm(provider: str | None = None, model: str | None = None):
    s = get_settings()
    provider = (provider or s.llm_provider).lower()
    model = model or s.llm_model or None
    if provider == "openai":
        from core.llm.openai_llm import OpenAILLM
        return OpenAILLM(model, s.llm_temperature)
    if provider == "anthropic":
        from core.llm.anthropic_llm import AnthropicLLM
        return AnthropicLLM(model, s.llm_temperature)
    from core.llm.simulated import SimulatedLLM
    return SimulatedLLM()
