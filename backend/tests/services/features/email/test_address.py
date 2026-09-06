"""Parsing a sender into a name and an address, and never confusing the two.

The whole module exists because of one production defect: a display string was
used where an address belonged. These tests name that case explicitly so a
future change that reintroduces it fails here rather than at a mail provider.
"""

import pytest

from app.services.features.email.address import (
    normalise_recipient_values,
    parse_address,
    parse_address_list,
)

ROBERT_DISPLAY = "Robert Keenan <Robert.Keenan@sunradia.com>"
ROBERT_ADDRESS = "Robert.Keenan@sunradia.com"


def test_the_robert_keenan_case_splits_into_a_name_and_an_address() -> None:
    """The exact string that caused the defect, asserted field by field."""

    parsed = parse_address(ROBERT_DISPLAY)

    assert parsed is not None
    assert parsed.name == "Robert Keenan"
    assert parsed.address == ROBERT_ADDRESS
    # The point of the whole change: the address is never the display string.
    assert parsed.address != ROBERT_DISPLAY
    assert "<" not in parsed.address


def test_the_display_form_round_trips_back_to_the_original() -> None:
    """Splitting loses nothing — `str()` rebuilds exactly what came in."""

    assert str(parse_address(ROBERT_DISPLAY)) == ROBERT_DISPLAY


def test_a_bare_address_parses_with_no_invented_name() -> None:
    parsed = parse_address(ROBERT_ADDRESS)

    assert parsed is not None
    assert parsed.address == ROBERT_ADDRESS
    assert parsed.name is None


@pytest.mark.parametrize(
    "raw",
    [
        "first.last@example.com",
        "first.last+tag@example.com",
        "UPPERCASE@Example.COM",
        "someone@subdomain.example.co.uk",
    ],
)
def test_valid_address_forms_survive_parsing_unchanged(raw: str) -> None:
    """Parsing must not be a second, stricter validator.

    These are the forms the milestone named as must-accept. Case is preserved
    rather than lowered: the local part of an address is case-sensitive by
    specification, and normalising it here would be this layer quietly
    rewriting somebody's address.
    """

    parsed = parse_address(raw)

    assert parsed is not None
    assert parsed.address == raw


def test_a_quoted_display_name_is_unquoted() -> None:
    parsed = parse_address('"Keenan, Robert" <Robert.Keenan@sunradia.com>')

    assert parsed is not None
    assert parsed.name == "Keenan, Robert"
    assert parsed.address == ROBERT_ADDRESS


def test_a_name_with_no_address_yields_no_address() -> None:
    """A name alone must not become something the system can send to."""

    parsed = parse_address("Robert Keenan")

    assert parsed is not None
    assert parsed.name == "Robert Keenan"
    assert parsed.address == ""


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_nothing_usable_parses_to_none(raw: str | None) -> None:
    assert parse_address(raw) is None


# --- recipients, at the boundary they are written through ----------------


def test_a_display_name_is_stripped_from_a_recipient() -> None:
    """The defect, at the write boundary rather than the read one."""

    assert normalise_recipient_values(
        ["Robert Keenan <Robert.Keenan@sunradia.com>"]
    ) == ["Robert.Keenan@sunradia.com"]


def test_an_already_normalised_address_is_untouched() -> None:
    assert normalise_recipient_values(["Robert.Keenan@sunradia.com"]) == [
        "Robert.Keenan@sunradia.com"
    ]


def test_one_field_may_hold_several_recipients() -> None:
    """Pasting a list into one box is ordinary, and reading it as a single
    malformed address would reject something the person plainly meant."""

    assert normalise_recipient_values(["a@x.com, b@y.com; c@z.com"]) == [
        "a@x.com",
        "b@y.com",
        "c@z.com",
    ]


def test_a_comma_inside_a_quoted_name_is_not_a_separator() -> None:
    assert normalise_recipient_values(['"Keenan, Robert" <r@x.com>']) == ["r@x.com"]


def test_several_named_recipients_split_and_strip_together() -> None:
    assert normalise_recipient_values(
        ["Robert Keenan <r@x.com>, Sudha P <s@x.com>"]
    ) == ["r@x.com", "s@x.com"]


@pytest.mark.parametrize(
    "value",
    [
        # Each of these is malformed in a way `email.utils.getaddresses` would
        # either silently repair or silently erase. Passing them through
        # unchanged is what lets validation refuse them by name.
        "a@x .com",
        "a@b@c.com",
        "a@x.com b@x.com",
        "a@",
        "john smith@x.com",
    ],
)
def test_a_malformed_address_is_passed_through_not_repaired(value: str) -> None:
    """The rule this function is built around.

    Repairing `"a@x .com"` into `"a@x.com"` invents an address nobody typed and
    sends mail to it. Erasing it makes a recipient disappear from a draft the
    person believed they had addressed. Both are worse than being refused.
    """

    assert normalise_recipient_values([value]) == [value]


def test_a_name_where_an_address_belongs_survives_to_be_refused() -> None:
    """Dropping it would lose a recipient silently; keeping it means the
    validation error can name what was wrong."""

    assert normalise_recipient_values(["Robert Keenan"]) == ["Robert Keenan"]


def test_empty_and_blank_entries_disappear() -> None:
    assert normalise_recipient_values(["", "   ", None]) == []  # type: ignore[list-item]


def test_angle_brackets_with_nothing_usable_inside_are_kept_verbatim() -> None:
    assert normalise_recipient_values(["Someone <not-an-address>"]) == [
        "Someone <not-an-address>"
    ]


# --- the same entries, for display ---------------------------------------


def test_recipients_can_be_read_back_as_names_and_addresses() -> None:
    parsed = parse_address_list(["Robert Keenan <r@x.com>, s@x.com"])

    assert [(item.name, item.address) for item in parsed] == [
        ("Robert Keenan", "r@x.com"),
        (None, "s@x.com"),
    ]
