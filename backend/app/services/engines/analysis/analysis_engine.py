import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.exceptions import AnalysisValidationError
from app.prompts.builder import PromptBuilder
from app.prompts.registry import PromptRegistry
from app.services.llm.llm_service import llm_service

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)

_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def _strip_code_fence(raw: str) -> str:
    """Remove a surrounding markdown code fence, if the model added one.

    Prompts instruct the model to return bare JSON, but models routinely wrap
    it in a fence anyway. Stripping it here turns a whole class of avoidable
    validation failures into successful responses.
    """

    match = _FENCE_PATTERN.match(raw)
    return match.group("body") if match else raw


class AnalysisEngine:
    """Run a registered prompt and validate its JSON output against a
    caller-supplied Pydantic model.

    Domain-agnostic: callers supply the prompt name, the expected response
    model, and the template variables.
    """

    def run(
        self,
        prompt_name: str,
        response_model: type[ResponseModelT],
        **variables: object,
    ) -> ResponseModelT:
        template = PromptRegistry.get(prompt_name)
        messages = PromptBuilder.build(template, **variables)
        raw_output = llm_service.complete(messages)

        try:
            data = json.loads(_strip_code_fence(raw_output))
        except json.JSONDecodeError as exc:
            raise AnalysisValidationError(
                f"LLM response for prompt '{prompt_name}' was not valid JSON."
            ) from exc

        try:
            return response_model.model_validate(data)
        except ValidationError as exc:
            raise AnalysisValidationError(
                f"LLM response for prompt '{prompt_name}' did not match the "
                "expected schema."
            ) from exc


analysis_engine = AnalysisEngine()
