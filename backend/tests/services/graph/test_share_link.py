"""Encoding a sharing URL, and knowing what is behind one.

Pure and offline. The encoding half is checked against the shape Graph
documents; the classification half exists because "this failed" and "no
permission can ever make this work" are different answers, and telling them
apart is what stops a week of granting permissions that cannot help.
"""

import base64

import pytest

from app.services.graph.share_link import (
    SharedResourceKind,
    classify,
    explain,
    host_of,
    is_short_link,
    reachable_app_only,
    sharing_token,
)

# --- encoding ------------------------------------------------------------


def test_a_url_is_encoded_whole_behind_the_marker() -> None:
    """`/shares` takes the entire URL, encoded — not a fragment of it."""

    url = "https://contoso.sharepoint.com/:f:/s/Marketing/Ab12Cd"
    token = sharing_token(url)

    assert token.startswith("u!")

    body = token[2:].replace("_", "/").replace("-", "+")
    padded = body + "=" * (-len(body) % 4)

    assert base64.b64decode(padded).decode() == url


def test_the_encoding_is_url_safe_and_unpadded() -> None:
    """Graph's documented form: no `=`, `/` becomes `_`, `+` becomes `-`.

    A token carrying any of those raw is rejected or, worse, silently
    truncated by whatever routes it.
    """

    # Chosen so the base64 of it contains both '+' and '/' before substitution.
    token = sharing_token("https://example.sharepoint.com/" + "\u00ff\u00fe" * 8)

    assert "=" not in token
    assert "/" not in token[2:]
    assert "+" not in token


def test_nothing_inside_the_url_is_interpreted() -> None:
    """The string in a sharing URL that looks like an item id is not one.

    This is the whole safety property: the encoder is reversible and total, so
    it cannot have picked the wrong substring.
    """

    url = "https://contoso.sharepoint.com/:f:/g/personal/x/EaBc123_-XYZ?e=abcd"
    body = sharing_token(url)[2:].replace("_", "/").replace("-", "+")

    assert base64.b64decode(body + "=" * (-len(body) % 4)).decode() == url


def test_an_empty_url_is_refused() -> None:
    with pytest.raises(ValueError):
        sharing_token("   ")


# --- classification ------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://1drv.ms/f/s!AbCdEf", SharedResourceKind.SHORT_LINK),
        (
            "https://contoso.sharepoint.com/:f:/s/Marketing/Ab12",
            SharedResourceKind.SHAREPOINT_LIBRARY,
        ),
        (
            "https://contoso-my.sharepoint.com/:f:/g/personal/sudha_contoso_com/Ab12",
            SharedResourceKind.ONEDRIVE_FOR_BUSINESS,
        ),
        (
            "https://onedrive.live.com/?id=root&cid=ABC",
            SharedResourceKind.CONSUMER_ONEDRIVE,
        ),
        ("https://dropbox.com/s/whatever", SharedResourceKind.UNKNOWN),
        ("not a url at all", SharedResourceKind.UNKNOWN),
    ],
)
def test_a_link_is_classified_by_its_host(
    url: str, expected: SharedResourceKind
) -> None:
    assert classify(url) is expected


def test_a_personal_site_is_not_mistaken_for_a_team_site() -> None:
    """Both end in `.sharepoint.com`, and the longer suffix has to win or
    every OneDrive for Business link reads as a document library."""

    assert (
        classify("https://contoso-my.sharepoint.com/:f:/g/personal/x/Ab")
        is SharedResourceKind.ONEDRIVE_FOR_BUSINESS
    )


def test_a_short_link_is_reported_as_short_rather_than_guessed_at() -> None:
    """The point of a short link is that it hides its destination. Guessing
    what is behind one is how the wrong tenant gets blamed."""

    assert is_short_link("https://1drv.ms/f/s!AbCdEf")
    assert not is_short_link("https://contoso.sharepoint.com/:f:/s/M/Ab")


def test_only_work_and_school_content_is_reachable_by_an_app_token() -> None:
    """An application token is issued by a tenant and has authority only
    inside it. Consumer OneDrive is a different identity system entirely."""

    assert reachable_app_only(SharedResourceKind.SHAREPOINT_LIBRARY)
    assert reachable_app_only(SharedResourceKind.ONEDRIVE_FOR_BUSINESS)
    assert not reachable_app_only(SharedResourceKind.CONSUMER_ONEDRIVE)
    assert not reachable_app_only(SharedResourceKind.SHORT_LINK)
    assert not reachable_app_only(SharedResourceKind.UNKNOWN)


def test_the_explanation_of_a_consumer_link_says_no_permission_helps() -> None:
    """The sentence that ends the permission-granting loop."""

    message = explain(SharedResourceKind.CONSUMER_ONEDRIVE, host="onedrive.live.com")

    assert "personal Microsoft account" in message
    assert "can ever read" in message


def test_the_explanation_of_a_library_names_the_permission_that_covers_it() -> None:
    assert "Files.Read.All" in explain(SharedResourceKind.SHAREPOINT_LIBRARY)


def test_the_host_is_read_case_insensitively() -> None:
    assert host_of("https://CONTOSO.SharePoint.com/x") == "contoso.sharepoint.com"
