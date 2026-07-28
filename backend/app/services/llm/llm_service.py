from app.config.settings import settings
from app.prompts.builder import ChatMessage
from app.services.llm.client import get_llm_client


class LLMService:
    """Provider-agnostic access to chat completions.

    All provider selection happens in `get_llm_client()`; nothing here branches
    on `LLM_PROVIDER`. The Chat Completions surface is used rather than the
    Responses API because it is the interface every OpenAI-compatible provider
    implements — see docs/DECISIONS.md.
    """

    def complete(self, messages: list[ChatMessage]) -> str:
        """Send pre-built chat messages to the configured model."""

        response = get_llm_client().chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
        )

        return response.choices[0].message.content or ""


llm_service = LLMService()
