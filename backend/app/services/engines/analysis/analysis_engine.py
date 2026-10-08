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


_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_VALID_ESCAPES = frozenset('"\\/bfnrtu')


def _repair_escapes(raw: str) -> str:
    """Make a stray backslash literal, so `Invalid \\escape` does not lose a batch.

    Models quoting mail write `\\d`, Windows paths and the like inside JSON
    strings. Each escape is read as a pair, so a valid `\\\\` is never split;
    only a backslash before a character JSON does not allow is doubled. The
    text is kept exactly as the model wrote it — nothing is dropped or guessed.
    """

    return _ESCAPE.sub(
        lambda m: m.group(0) if m.group(1) in _VALID_ESCAPES else "\\" + m.group(0),
        raw,
    )


_UNPARSED = object()


def _parse(body: str) -> object:
    """The JSON in a reply: as written, then its outermost object, each with
    stray backslashes repaired as a last resort. `_UNPARSED` when nothing
    parses."""

    for candidate in (body, _embedded_object(body)):
        if not candidate:
            continue

        for text in (candidate, _repair_escapes(candidate)):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue

    return _UNPARSED


def _embedded_object(raw: str) -> str | None:
    """The outermost `{...}` in a reply that also contains prose.

    A second attempt, not a first one: bare JSON and a fenced block are both
    handled above. This covers the model that answers "Here is the analysis:"
    and then the object, which is a formatting habit rather than a refusal —
    and which otherwise loses a whole batch of conversations to a fallback.
    """

    start = raw.find("{")
    end = raw.rfind("}")

    return raw[start : end + 1] if 0 <= start < end else None


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
        *,
        structured_output: bool = False,
        **variables: object,
    ) -> ResponseModelT:
        template = PromptRegistry.get(prompt_name)
        messages = PromptBuilder.build(template, **variables)
        if structured_output:
            # The schema goes to the provider, which constrains the reply to
            # it. Not strict: Pydantic defaults make fields optional, and the
            # model below still validates whatever comes back.
            raw_output = llm_service.complete(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__,
                        "strict": False,
                        "schema": response_model.model_json_schema(),
                    },
                },
            )
        else:
            raw_output = llm_service.complete(messages)

        if not raw_output.strip():
            # Distinguished from malformed JSON because the remedy is
            # different and the diagnosis is otherwise impossible from a log:
            # a model that puts its answer in a reasoning channel returns a
            # successful response whose content is empty.
            raise AnalysisValidationError(
                f"The model returned an empty response for prompt "
                f"'{prompt_name}'. Nothing was parsed because nothing was said."
            )

        body = _strip_code_fence(raw_output)

        data = _parse(body)

        if data is _UNPARSED:
            # The length, never the content: a model's reply quotes the
            # correspondence it was given, and a log is not the place for
            # somebody else's mail.
            raise AnalysisValidationError(
                f"LLM response for prompt '{prompt_name}' was not valid "
                f"JSON ({len(raw_output)} characters returned)."
            )

        try:
            return response_model.model_validate(data)
        except ValidationError as exc:
            raise AnalysisValidationError(
                f"LLM response for prompt '{prompt_name}' did not match the "
                "expected schema."
            ) from exc


analysis_engine = AnalysisEngine()
