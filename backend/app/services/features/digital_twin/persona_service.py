"""Rendering the profile and memories into the block the model is shown.

A pure function over data, like `context_service`: no database, no provider, no
configuration read at import time. That is what lets the exact text the model
receives be asserted in a test rather than inferred.

The output is deliberately not interleaved with retrieved passages. Profile and
memory say who is asking and what they care about; sources say what the
documents contain. The model is told to cite only the latter, and the only way
that instruction can hold is if the two are visibly different things on the
page.
"""

from dataclasses import dataclass

from app.models.digital_twin import DigitalTwinMemory, DigitalTwinProfile

PROFILE_HEADING = "[DIGITAL TWIN PROFILE]"
MEMORY_HEADING = "[MEMORY]"
KNOWLEDGE_HEADING = "[KNOWLEDGE SOURCES]"


@dataclass(frozen=True)
class PersonaContext:
    """The rendered persona block, and what actually made it in."""

    text: str
    memories: list[DigitalTwinMemory]

    @property
    def is_empty(self) -> bool:
        return not self.text


def _describe_profile(profile: DigitalTwinProfile) -> str:
    lines = [
        PROFILE_HEADING,
        f"Name: {profile.name}",
        f"Role: {profile.role}",
        f"Organization: {profile.organization}",
    ]

    for label, values in (
        ("Responsibilities", profile.responsibilities),
        ("Expertise", profile.expertise),
        ("Priorities", profile.priorities),
        ("Decision preferences", profile.decision_preferences),
        ("Current focus", profile.current_focus),
    ):
        if values:
            lines.append(f"{label}: {'; '.join(values)}")

    if profile.communication_style:
        lines.append(f"Communication style: {profile.communication_style}")

    return "\n".join(lines)


def _describe_memory(memory: DigitalTwinMemory) -> str:
    return f"[{memory.type.value.upper()}] {memory.content}"


def build_persona_context(
    profile: DigitalTwinProfile | None,
    memories: list[DigitalTwinMemory],
    *,
    max_chars: int,
) -> PersonaContext:
    """Render the persona block within `max_chars`.

    Memories are dropped from the least important end until the block fits,
    and `memories` reports the ones that survived — so a caller can tell what
    the model was actually shown rather than what it was offered.

    With no profile and no memories the text is empty, and the caller composes
    exactly the prompt it composed before this feature existed.
    """

    if profile is None and not memories:
        return PersonaContext(text="", memories=[])

    blocks: list[str] = []

    if profile is not None:
        blocks.append(_describe_profile(profile))

    kept: list[DigitalTwinMemory] = []
    if memories:
        # Built up one memory at a time so the budget is spent on the most
        # important ones. `memories` arrives ordered by importance.
        for memory in memories:
            candidate = kept + [memory]
            rendered = "\n".join(
                [MEMORY_HEADING, *(_describe_memory(item) for item in candidate)]
            )
            if len("\n\n".join([*blocks, rendered])) > max_chars and kept:
                break

            kept = candidate

        if kept:
            blocks.append(
                "\n".join([MEMORY_HEADING, *(_describe_memory(item) for item in kept)])
            )

    full_text = "\n\n".join(blocks)
    text = full_text

    # A profile long enough to blow the budget on its own is still worth
    # showing in part: the model needs to know who it is answering for even if
    # it does not get every priority. Truncation is the last resort, and it is
    # what guarantees the bound this function promises.
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        # Truncation can cut the memory block short. A memory whose line begins
        # inside what survives *is* represented — partially, and deliberately,
        # because a single oversized memory shown in part beats showing none at
        # all. One whose line begins past the cut is not in the text, and
        # reporting it would make `memories` a claim the text cannot support.
        #
        # Matched by position rather than by substring: two memories may hold
        # the same words, and `in` would credit the later one for the earlier
        # one's line.
        cursor = 0
        surviving: list[DigitalTwinMemory] = []

        for item in kept:
            start = full_text.find(_describe_memory(item), cursor)
            if start == -1 or start >= len(text):
                continue

            cursor = start + 1
            surviving.append(item)

        kept = surviving

    return PersonaContext(text=text, memories=kept)
