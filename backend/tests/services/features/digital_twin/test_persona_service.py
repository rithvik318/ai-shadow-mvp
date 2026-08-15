"""Rendering the persona block is a pure function, so it is tested as one.

These assertions are about the exact text the model is shown — which is where
the line between "who is asking" and "what the documents say" is actually
drawn.
"""

import uuid

from app.models.digital_twin import DigitalTwinMemory, DigitalTwinProfile, MemoryType
from app.services.features.digital_twin.persona_service import build_persona_context

WIDE = 100_000


def _profile(**overrides) -> DigitalTwinProfile:
    values = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "Test Executive",
        "role": "CEO",
        "organization": "SunRadia",
        "communication_style": "Concise and executive-friendly",
        "responsibilities": ["Business development"],
        "expertise": ["Data modernization"],
        "priorities": ["Government opportunities"],
        "decision_preferences": ["Prefer evidence-backed recommendations"],
        "current_focus": [],
    }
    values.update(overrides)

    return DigitalTwinProfile(**values)


def _memory(content: str, memory_type: MemoryType = MemoryType.PREFERENCE):
    return DigitalTwinMemory(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        type=memory_type,
        content=content,
        importance=3,
        source="user",
        active=True,
    )


def test_nothing_to_say_renders_nothing() -> None:
    """With no Digital Twin, chat must build exactly the prompt it built
    before this feature existed."""

    context = build_persona_context(None, [], max_chars=WIDE)

    assert context.text == ""
    assert context.is_empty


def test_the_profile_is_rendered_under_its_own_heading() -> None:
    context = build_persona_context(_profile(), [], max_chars=WIDE)

    assert context.text.startswith("[DIGITAL TWIN PROFILE]")
    assert "Role: CEO" in context.text
    assert "Organization: SunRadia" in context.text
    assert "Priorities: Government opportunities" in context.text
    assert "Communication style: Concise and executive-friendly" in context.text


def test_empty_profile_lists_are_omitted() -> None:
    """A label with nothing after it invites the model to fill the gap."""

    context = build_persona_context(_profile(current_focus=[]), [], max_chars=WIDE)

    assert "Current focus" not in context.text


def test_memories_are_labelled_by_type() -> None:
    """The type is what tells the model whether it is reading a preference to
    honour or a commitment already made."""

    context = build_persona_context(
        None,
        [
            _memory("Prefers concise executive-facing emails."),
            _memory("Prioritize government-sector opportunities.", MemoryType.DECISION),
        ],
        max_chars=WIDE,
    )

    assert "[MEMORY]" in context.text
    assert "[PREFERENCE] Prefers concise executive-facing emails." in context.text
    assert "[DECISION] Prioritize government-sector opportunities." in context.text


def test_nothing_rendered_looks_like_a_citable_source() -> None:
    """`[SOURCE n]` is the citation contract. If persona text could produce
    one, the model could cite the person as evidence for a claim about the
    documents — which is the failure this whole separation exists to prevent."""

    context = build_persona_context(
        _profile(),
        [_memory("Prefers concise executive-facing emails.")],
        max_chars=WIDE,
    )

    assert "[SOURCE" not in context.text
    assert "Document:" not in context.text


def test_the_profile_comes_before_the_memories() -> None:
    context = build_persona_context(
        _profile(), [_memory("A preference.")], max_chars=WIDE
    )

    assert context.text.index("[DIGITAL TWIN PROFILE]") < context.text.index("[MEMORY]")


def test_the_budget_drops_the_least_important_memories() -> None:
    """Memories arrive ordered by importance, so dropping from the end is
    dropping the least important."""

    memories = [_memory("A" * 100), _memory("B" * 100), _memory("C" * 100)]

    context = build_persona_context(None, memories, max_chars=250)

    assert len(context.memories) == 2
    assert "C" * 100 not in context.text


def test_the_memories_reported_are_the_ones_shown() -> None:
    """A caller that logs what the model saw must not be told about a memory
    the budget dropped."""

    memories = [_memory("A" * 200), _memory("B" * 200)]

    context = build_persona_context(None, memories, max_chars=260)

    assert [memory.content for memory in context.memories] == ["A" * 200]


def test_a_single_oversized_memory_is_still_included() -> None:
    """Rendering an empty memory section would be worse than a truncated one."""

    context = build_persona_context(None, [_memory("A" * 5000)], max_chars=100)

    assert context.memories
    assert len(context.text) <= 100


def test_the_block_never_exceeds_its_budget() -> None:
    """The bound is what lets chat subtract it from the context budget and
    still promise a prompt that fits."""

    profile = _profile(priorities=[f"priority {index}" for index in range(25)])
    memories = [_memory(f"memory {index}") for index in range(20)]

    context = build_persona_context(profile, memories, max_chars=300)

    assert len(context.text) <= 300
