from typing import ClassVar

from app.core.exceptions import DuplicatePromptError, PromptNotFoundError
from app.prompts.base import PromptTemplate


class PromptRegistry:
    """Centralized registry for prompt templates."""

    _templates: ClassVar[dict[str, PromptTemplate]] = {}

    @classmethod
    def register(cls, template: PromptTemplate) -> None:
        if template.name in cls._templates:
            raise DuplicatePromptError(
                f"Prompt template already registered: {template.name}"
            )

        cls._templates[template.name] = template

    @classmethod
    def get(cls, name: str) -> PromptTemplate:
        try:
            return cls._templates[name]
        except KeyError as exc:
            raise PromptNotFoundError(f"Prompt template not found: {name}") from exc

    @classmethod
    def exists(cls, name: str) -> bool:
        return name in cls._templates

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._templates.keys())

    @classmethod
    def clear(cls) -> None:
        cls._templates.clear()
