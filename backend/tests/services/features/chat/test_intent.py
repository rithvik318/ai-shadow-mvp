"""Which messages skip retrieval. A false positive costs a grounded answer, so
the document-question cases matter as much as the conversational ones."""

import pytest

from app.services.features.chat.intent import Intent, classify


@pytest.mark.parametrize(
    "message",
    [
        "Hi",
        "hi!",
        "Hello there",
        "Hey Azmat",
        "Good morning",
        "Good afternoon team",
        "Thanks!",
        "Thank you",
        "How are you?",
        "How’s it going?",
    ],
)
def test_greetings(message: str) -> None:
    assert classify(message) is Intent.GREETING


@pytest.mark.parametrize(
    "message",
    [
        "Introduce yourself",
        "Can you introduce yourself?",
        "Please introduce yourself to the team.",
        "Who are you?",
        "Tell me about yourself",
        "Tell me a bit about yourself",
        "What do you do?",
        "What is your role?",
        "What's your current focus?",
        "What are your priorities?",
        "What are your main responsibilities?",
        "Hi, who are you?",
        "What are you working on?",
    ],
)
def test_questions_about_the_twin(message: str) -> None:
    assert classify(message) is Intent.ABOUT_TWIN


@pytest.mark.parametrize(
    "message",
    [
        "q?",
        "How does holiday accrue?",
        "Hi, what is our holiday policy?",
        "Hello, can you summarise the Freddie Mac contract terms?",
        "What is your view on the leave policy?",
        "Who are our clients?",
        "Thanks — and what does the handbook say about expenses?",
        "",
        "???",
    ],
)
def test_everything_else_is_a_document_question(message: str) -> None:
    assert classify(message) is Intent.DOCUMENTS
