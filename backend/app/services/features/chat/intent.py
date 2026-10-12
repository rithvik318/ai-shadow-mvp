"""Deciding whether a chat message is a question for the documents at all.

`/chat` used to send every message to retrieval, and answer with the fixed
"nothing in your documents" reply whenever retrieval came back empty. That is
right for a question about the knowledge base and wrong for "Hi" or "Introduce
yourself": neither is about the documents, so an empty knowledge base — or one
that happens to contain nothing about greetings — produced a no-match reply to a
message that never asked for a match.

Two narrow kinds of message are recognised here and everything else is treated
as a document question. The patterns are deliberately conservative: a message
misread as a greeting would skip retrieval and lose its grounding, while one
misread as a document question only gets the honest no-match reply it would
have got before. When in doubt, it is a document question.

Pure: a string in, an `Intent` out. No model call — classifying with the model
would put a paid, non-deterministic call in front of every question.
"""

import enum
import re


class Intent(enum.StrEnum):
    GREETING = "greeting"
    ABOUT_TWIN = "about_twin"
    DOCUMENTS = "documents"


# The whole message must be one of these, optionally followed by up to two
# words ("Hi Azmat", "hello there", "good morning team"). Anything longer is a
# real request that happens to open politely: "Hi, what is our holiday policy?"
_GREETING = re.compile(
    r"^(?:hi|hello|hey|hiya|howdy|greetings|yo"
    r"|good (?:morning|afternoon|evening|day)"
    r"|thanks|thank you|thank you very much|thx|cheers"
    r"|how are you|how are you doing|how is it going|hows it going"
    r"|whats up|nice to meet you|bye|goodbye)"
    r"(?: \w+){0,2}$"
)

# Questions addressed to the twin about itself. Matched anywhere, but each
# pattern names the twin ("you", "your", "yourself") together with something the
# profile actually records — so "what is your view on the leave policy" is not
# caught, because "view" is not a profile field.
_PROFILE_FIELDS = (
    r"name|role|job|title|position|organi[sz]ation|company|team|background"
    r"|expertise|experience|skills|responsibilities|priorities|focus"
    r"|current focus|communication style|style|preferences"
    r"|decision preferences"
)
_ABOUT_TWIN = re.compile(
    r"\bintroduce yourself\b"
    r"|\b(?:tell me|tell us) (?:a (?:bit|little) )?about (?:yourself|you)\b"
    r"|\bdescribe yourself\b"
    r"|\bwho are you\b"
    r"|\bwho am i (?:talking|speaking|chatting) (?:to|with)\b"
    r"|\bwhat do you do\b"
    r"|\bwhat are you (?:working on|focused on|focusing on)\b"
    r"|\bwhat (?:do you )?(?:remember|know) about (?:me|yourself)\b"
    rf"|\bwhat(?: is| are|s) your (?:main |top |key |current )?(?:{_PROFILE_FIELDS})\b"
)


def _normalise(message: str) -> str:
    """Lower-case, drop punctuation and apostrophes, collapse whitespace."""

    text = message.lower().replace("’", "").replace("'", "")
    text = re.sub(r"[^\w\s]", " ", text)

    return " ".join(text.split())


def classify(message: str) -> Intent:
    """Classify `message`. Anything not clearly conversational is DOCUMENTS."""

    text = _normalise(message)

    if not text:
        return Intent.DOCUMENTS

    if _ABOUT_TWIN.search(text):
        return Intent.ABOUT_TWIN

    if _GREETING.match(text):
        return Intent.GREETING

    return Intent.DOCUMENTS
