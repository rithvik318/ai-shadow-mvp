from app.prompts.base import PromptTemplate

# Only prompts with a current or imminent caller live here. The reference
# repository carried seven, four of which no code path used; see
# docs/DECISIONS.md.

ASSISTANT_PROMPT = PromptTemplate(
    name="assistant",
    system_prompt="You are a helpful, accurate, and concise assistant.",
    user_prompt="{input}",
)

RAG_ANSWER_PROMPT = PromptTemplate(
    name="rag_answer",
    system_prompt=(
        "You answer questions about the user's own documents, using only the "
        "numbered passages supplied as context.\n"
        "\n"
        "Rules, in order of importance:\n"
        "1. Treat the context as the only source of truth. Do not use anything "
        "you know from outside it, however confident you are.\n"
        "2. Never introduce a fact, figure, name or date that does not appear "
        "in the context.\n"
        "3. If the context does not answer the question, say so plainly and "
        "stop. Do not guess, and do not present a partial answer as a complete "
        "one.\n"
        "4. Where several passages bear on the question, synthesise them into "
        "one coherent answer rather than summarising each in turn.\n"
        "5. Be concise, but never at the cost of a condition, exception or "
        "qualification the context attaches to the answer."
    ),
    user_prompt="Context:\n{context}\n\nQuestion:\n{question}",
)


__all__ = ["ASSISTANT_PROMPT", "RAG_ANSWER_PROMPT"]
