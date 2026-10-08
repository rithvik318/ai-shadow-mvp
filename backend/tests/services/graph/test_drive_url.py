"""Reading a OneDrive address without inventing anything from it.

Pure, offline, and the whole point is what it refuses to do. Two failures this
module exists to prevent, both of which look like a working configuration until
Graph is actually asked:

- keeping the `Documents` prefix, which produces a 404 for a folder that is
  plainly there
- guessing a user principal name out of a personal site name, which is not
  reversible and would silently address somebody else's drive
"""

import pytest

from app.services.graph.drive_url import UnreadableDriveUrl, describe, parse

# The Knowledge Base address SunRadia supplied, exactly as copied from a
# browser: the viewer page in the path, the folder in the `id` parameter,
# everything percent-encoded including the underscores.
KNOWLEDGE_BASE = (
    "https://sunradia-my.sharepoint.com/personal/"
    "sudha_gummuluru_sunradia_com/_layouts/15/onedrive.aspx"
    "?id=%2Fpersonal%2Fsudha%5Fgummuluru%5Fsunradia%5Fcom%2FDocuments"
    "%2FKnowledge%20Base&ga=1"
)


class TestTheSuppliedAddress:
    def test_it_reads_the_folder_relative_to_the_drive_root(self):
        # The single most important line in this file. The URL says
        # `/personal/<site>/Documents/Knowledge Base`, but Graph's drive root
        # *is* `Documents` — so the path to configure is `Knowledge Base`.
        # Configure the other and every call 404s on a folder that exists.
        assert parse(KNOWLEDGE_BASE).path == "Knowledge Base"

    def test_it_recognises_a_personal_site(self):
        location = parse(KNOWLEDGE_BASE)

        assert location.is_personal is True
        assert location.host == "sunradia-my.sharepoint.com"
        assert location.site == "sudha_gummuluru_sunradia_com"

    def test_it_does_not_guess_a_user_principal_name(self):
        # `sudha_gummuluru_sunradia_com` could be sudha.gummuluru@sunradia.com
        # or sudha@gummuluru.sunradia.com. The transformation replaces both `@`
        # and `.` with `_`, so it cannot be reversed — and a guess would
        # address a different person's drive, or none.
        location = parse(KNOWLEDGE_BASE)

        assert location.needs_user_principal_name is True
        assert "@" not in location.site

    def test_the_configuration_it_prints_leaves_the_address_to_a_person(self):
        printed = describe(parse(KNOWLEDGE_BASE))

        assert '"path": "Knowledge Base"' in printed
        assert "<user>@example.com" in printed
        # Never a drive id: the whole point is that Graph is asked for one.
        assert "drive_id" not in printed

    def test_the_viewer_page_never_becomes_the_folder(self):
        # `_layouts/15/onedrive.aspx` is in the URL *path* while the folder is
        # in the query. Reading the path would configure a source pointed at
        # the site's own machinery.
        assert "_layouts" not in parse(KNOWLEDGE_BASE).path


class TestOtherShapes:
    def test_a_direct_folder_link_is_read_from_its_path(self):
        location = parse(
            "https://sunradia-my.sharepoint.com/personal/"
            "sudha_gummuluru_sunradia_com/Documents/Knowledge%20Base/Case%20Study"
        )

        assert location.path == "Knowledge Base/Case Study"

    def test_the_library_root_itself_has_an_empty_path(self):
        # Not an error: a source may name a whole library. `resolve_folder`
        # addresses an empty path as the drive root.
        location = parse(
            "https://sunradia-my.sharepoint.com/personal/"
            "sudha_gummuluru_sunradia_com/Documents"
        )

        assert location.path == ""
        assert "(the library root)" in describe(location)

    def test_a_team_site_drops_its_own_library_prefix(self):
        location = parse(
            "https://sunradia.sharepoint.com/sites/Engineering/"
            "Shared%20Documents/Capabilities"
        )

        assert location.is_personal is False
        assert location.path == "Capabilities"
        # A team site is addressed by drive id, so that is what it asks for.
        assert "drive_id" in describe(location)

    def test_the_root_folder_parameter_is_read_as_well_as_id(self):
        # Older SharePoint views use `RootFolder` instead of `id`.
        location = parse(
            "https://sunradia.sharepoint.com/sites/Engineering/Forms/AllItems.aspx"
            "?RootFolder=%2Fsites%2FEngineering%2FShared%20Documents%2FCapabilities"
        )

        assert location.path == "Capabilities"


class TestRefusals:
    @pytest.mark.parametrize(
        "url",
        [
            "https://1drv.ms/f/s!AbCdEf",
            "https://onedrive.live.com/?id=root&cid=57C6D5F079856CA8",
        ],
    )
    def test_a_consumer_onedrive_link_is_refused_with_the_reason(self, url: str):
        # Not a tenant address. An application token is issued by a tenant and
        # has no standing over a personal Microsoft account, so no permission
        # grant makes this readable — saying so here is more useful than
        # parsing it into something that looks configurable.
        with pytest.raises(UnreadableDriveUrl, match="consumer OneDrive"):
            parse(url)

    def test_an_unrelated_address_is_refused(self):
        with pytest.raises(UnreadableDriveUrl):
            parse("https://example.com/some/folder")

    def test_nothing_is_refused_rather_than_returning_an_empty_location(self):
        with pytest.raises(UnreadableDriveUrl):
            parse("   ")
