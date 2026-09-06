"""Turning what somebody typed into a name and an address, separately.

One pure function, and it exists because of one specific defect. A message
whose sender is displayed as::

    Robert Keenan <Robert.Keenan@sunradia.com>

was being stored, returned and then re-sent as though the whole string were an
address. Everything downstream that built a reply inherited that, and the
failure only showed up at the provider — the worst place to discover it.

`email.utils.parseaddr` is the standard library's answer to exactly this and is
used rather than a regular expression: it already understands quoted display
names and angle-addr, and a hand-rolled pattern here would be a second, worse
implementation of a solved problem.

Pure by design — no database, no provider, no settings — so the parsing rules
can be tested directly and reused by the API layer without dragging anything
else in.
"""

from email.utils import parseaddr

from app.services.email.provider.base import EmailAddress


def parse_address(value: str | None) -> EmailAddress | None:
    """Split `"Name <addr>"` into its parts, or return None for nothing usable.

    Three shapes reach this function and each has one right answer:

    - ``"Robert Keenan <Robert.Keenan@sunradia.com>"`` → name and address split.
    - ``"Robert.Keenan@sunradia.com"`` → address only, no name invented.
    - ``"Robert Keenan"`` → **not** an address. Returned as a name with no
      address, so a caller can display it and still cannot send to it.

    That last case is the important one. Returning it as an address would
    reintroduce the defect this module exists to remove, and returning None
    would silently discard the only sender information the caller had.
    """

    raw = (value or "").strip()

    if not raw:
        return None

    name, address = parseaddr(raw)
    name = name.strip()
    address = address.strip()

    # A value with no '@' anywhere is not an address, whichever position
    # parseaddr put it in. It is also where parseaddr is least useful: given
    # "Robert Keenan" it returns ('', 'Robert') — the surname is dropped
    # because the space is read as a separator. So the *original* text is kept
    # as the name here rather than parseaddr's truncated view of it.
    if address and "@" not in address:
        name = name or raw
        address = ""

    if not address:
        return EmailAddress(address="", name=name or None) if name else None

    return EmailAddress(address=address, name=name or None)


_SEPARATORS = (",", ";")


def _split_entries(value: str) -> list[str]:
    """Split one field into the recipients it holds, without interpreting them.

    A comma inside a quoted display name — ``"Keenan, Robert" <r@x.com>`` — is
    not a separator, so quoted runs and angle-addr runs are skipped over.
    Everything between separators is returned **verbatim**: this function
    decides where the boundaries are and nothing else.
    """

    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    in_angles = False

    for character in value:
        if character == '"':
            in_quotes = not in_quotes
        elif character == "<" and not in_quotes:
            in_angles = True
        elif character == ">" and not in_quotes:
            in_angles = False
        elif character in _SEPARATORS and not in_quotes and not in_angles:
            parts.append("".join(current))
            current = []
            continue

        current.append(character)

    parts.append("".join(current))

    return [part.strip() for part in parts if part.strip()]


def normalise_recipient_values(values: list[str] | None) -> list[str]:
    """Reduce recipient input to the strings that should be stored and sent.

    Two transformations, and **no others**:

    - one field holding several recipients is split into them;
    - ``Name <addr>`` becomes ``addr``.

    Anything else is passed through byte-for-byte so that validation, not this
    function, decides whether it is deliverable. That restraint is the whole
    design. `email.utils.getaddresses` is not used for the general case because
    it is too helpful to be safe here: it turns ``"a@x .com"`` into
    ``"a@x.com"`` — inventing an address nobody typed — and reduces
    ``"a@b@c.com"`` and ``"a@x.com b@x.com"`` to nothing at all, which would
    make a malformed recipient silently vanish from a draft instead of being
    refused. Repairing and disappearing are both worse than rejecting.

    A display name is only separated where the value actually carries angle
    brackets, because that is the one form whose address part is unambiguous.
    """

    normalised: list[str] = []

    for value in values or []:
        for part in _split_entries(value or ""):
            if "<" in part and ">" in part:
                parsed = parse_address(part)
                # `parse_address` returns the address alone when it found one.
                # With no address inside the brackets, the original text is
                # kept so validation can name it.
                normalised.append(parsed.address if parsed and parsed.address else part)
            else:
                normalised.append(part)

    return normalised


def parse_address_list(values: list[str] | None) -> list[EmailAddress]:
    """The same entries, as name/address pairs, for display rather than sending.

    Built on `normalise_recipient_values` so the two can never disagree about
    where one recipient ends and the next begins.
    """

    parsed: list[EmailAddress] = []

    for value in values or []:
        for part in _split_entries(value or ""):
            entry = parse_address(part)

            if entry is not None:
                parsed.append(entry)

    return parsed
