from dataclasses import dataclass
from typing import TypedDict

from app.core.exceptions import PromptTemplateError


class RenderedPrompt(TypedDict):
    """Rendered system and user prompts."""

    system: str
    user: str


@dataclass(frozen=True)
class PromptTemplate:
    """Reusable template containing system and user prompts."""

    name: str
    system_prompt: str
    user_prompt: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Prompt template name cannot be empty.")

        if not self.system_prompt.strip():
            raise ValueError("System prompt cannot be empty.")

        if not self.user_prompt.strip():
            raise ValueError("User prompt cannot be empty.")

    def render(self, **kwargs: object) -> RenderedPrompt:
        """Render both prompts with the provided template variables."""

        try:
            return {
                "system": self.system_prompt.format(**kwargs),
                "user": self.user_prompt.format(**kwargs),
            }
        except KeyError as exc:
            raise PromptTemplateError(
                f"Missing template variable: {exc.args[0]}"
            ) from exc
        except IndexError as exc:
            raise PromptTemplateError("Invalid positional template variable.") from exc
        except ValueError as exc:
            raise PromptTemplateError("Invalid prompt template format.") from exc
