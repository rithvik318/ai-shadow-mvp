from typing import Literal, TypedDict

from app.prompts.base import PromptTemplate


class ChatMessage(TypedDict):
    """Generic chat message built from a prompt template."""

    role: Literal["system", "user"]
    content: str


class PromptBuilder:
    """Build provider-agnostic chat messages from prompt templates."""

    @classmethod
    def build(
        cls,
        template: PromptTemplate,
        **variables: object,
    ) -> list[ChatMessage]:
        """Render a template as ordered, non-empty chat messages."""

        rendered = template.render(**variables)
        messages: list[ChatMessage] = []

        if rendered["system"].strip():
            messages.append({"role": "system", "content": rendered["system"]})

        if rendered["user"].strip():
            messages.append({"role": "user", "content": rendered["user"]})

        return messages
