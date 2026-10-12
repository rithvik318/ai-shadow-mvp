import logging
from typing import Any

from openai import BadRequestError, NotFoundError, OpenAIError

from app.config.settings import settings
from app.core.exceptions import LLMServiceError
from app.prompts.builder import ChatMessage
from app.services.llm.client import get_llm_client, structured_output_options

logger = logging.getLogger(__name__)


class LLMService:
    """Provider-agnostic access to chat completions.

    All provider selection happens in `get_llm_client()`; nothing here branches
    on `LLM_PROVIDER`. The Chat Completions surface is used rather than the
    Responses API because it is the interface every OpenAI-compatible provider
    implements — see docs/DECISIONS.md.
    """

    def complete(
        self,
        messages: list[ChatMessage],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Send pre-built chat messages to the configured model.

        `response_format` asks the provider to constrain the reply to a JSON
        schema rather than trusting a prompt instruction to produce valid JSON.
        A provider that refuses the parameter (400/404) gets the same request
        without it, so asking for structure can never make a call fail that
        would otherwise have succeeded.

        Any other provider failure — authentication, timeout, rate limit,
        connection — is logged by class and status and raised as
        `LLMServiceError`, which the API maps to 502. Left raw, it escaped every
        handler as an unlogged 500. The provider's message is not logged: it can
        echo request details, and the class and status are enough to diagnose.
        """

        try:
            return self._complete(messages, response_format)
        except OpenAIError as exc:
            logger.warning(
                "llm_call_failed error=%s status=%s model=%s",
                type(exc).__name__,
                getattr(exc, "status_code", None),
                settings.LLM_MODEL,
            )
            raise LLMServiceError(
                f"The language model call failed ({type(exc).__name__})."
            ) from exc

    def _complete(
        self,
        messages: list[ChatMessage],
        response_format: dict[str, Any] | None,
    ) -> str:
        client = get_llm_client()

        if response_format is not None:
            try:
                response = client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=messages,
                    response_format=response_format,
                    **structured_output_options(),
                )

                return response.choices[0].message.content or ""
            except (BadRequestError, NotFoundError) as exc:
                logger.warning(
                    "llm_structured_output_unsupported status=%s; retrying "
                    "without response_format",
                    getattr(exc, "status_code", None),
                )

        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
        )

        return response.choices[0].message.content or ""


llm_service = LLMService()
