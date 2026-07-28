from app.prompts.base import PromptTemplate

# Only prompts with a current or imminent caller live here. The reference
# repository carried seven, four of which no code path used; see
# docs/DECISIONS.md.

ASSISTANT_PROMPT = PromptTemplate(
    name="assistant",
    system_prompt="You are a helpful, accurate, and concise assistant.",
    user_prompt="{input}",
)

AI_SHADOW_PROMPT = PromptTemplate(
    name="ai_shadow",
    system_prompt=(
        "You are an AI Shadow that answers strictly from the provided context. "
        "If the context does not contain the answer, say so plainly rather than "
        "relying on prior knowledge."
    ),
    user_prompt="Context:\n{context}\n\nQuestion:\n{input}",
)


__all__ = ["ASSISTANT_PROMPT", "AI_SHADOW_PROMPT"]
