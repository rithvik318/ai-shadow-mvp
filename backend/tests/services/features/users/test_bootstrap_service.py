"""Escaping the administrator deadlock, exactly once and no further.

`is_admin` is granted by no endpoint, so without this a deployment with users
and no administrator could never gain one. The tests that matter are the ones
that check the bootstrap does not overreach: it must not create people, must
not promote a second one, and must not read `role` as permission.
"""

import pytest
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.features.users import bootstrap_service, user_service


def make(db: Session, *, name: str, email: str, role: str = "CEO") -> User:
    return user_service.create_user(db, name=name, email=email, role=role)


class TestPromotion:
    def test_the_first_user_in_the_list_becomes_the_administrator(
        self, db_session: Session
    ):
        # Asserted against `list_users` rather than against insertion order,
        # because that is what the module promises and what the twin switcher
        # shows. The two differ on SQLite, where `created_at` is
        # second-resolution and users made in the same second tie — see the
        # note in `bootstrap_service`.
        make(db_session, name="Sudha", email="sudha@sunradia.com")
        make(db_session, name="Second", email="second@sunradia.com", role="CRM Manager")

        promoted = bootstrap_service.ensure_admin(db_session)
        first_in_list = user_service.list_users(db_session)[0]

        assert promoted is not None
        assert promoted.id == first_in_list.id
        assert first_in_list.is_admin is True

    def test_only_one_person_is_promoted(self, db_session: Session):
        make(db_session, name="Sudha", email="sudha@sunradia.com")
        make(db_session, name="Second", email="second@sunradia.com")

        bootstrap_service.ensure_admin(db_session)

        assert len(bootstrap_service.find_admins(db_session)) == 1

    def test_a_configured_email_wins_over_the_first_user(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ):
        make(db_session, name="First", email="first@sunradia.com")
        wanted = make(db_session, name="Chosen", email="chosen@sunradia.com")

        monkeypatch.setattr(
            bootstrap_service.settings, "BOOTSTRAP_ADMIN_EMAIL", "chosen@sunradia.com"
        )

        promoted = bootstrap_service.ensure_admin(db_session)

        assert promoted is not None
        assert promoted.id == wanted.id

    def test_a_configured_email_naming_nobody_falls_back_to_the_first_user(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ):
        # A typo in configuration must not leave the deployment unadministrable.
        first = make(db_session, name="First", email="first@sunradia.com")

        monkeypatch.setattr(
            bootstrap_service.settings, "BOOTSTRAP_ADMIN_EMAIL", "nobody@sunradia.com"
        )

        promoted = bootstrap_service.ensure_admin(db_session)

        assert promoted is not None
        assert promoted.id == first.id


class TestRestraint:
    def test_nothing_happens_when_an_administrator_already_exists(
        self, db_session: Session
    ):
        first = make(db_session, name="First", email="first@sunradia.com")
        second = make(db_session, name="Second", email="second@sunradia.com")
        second.is_admin = True
        db_session.commit()

        assert bootstrap_service.ensure_admin(db_session) is None
        assert first.is_admin is False

    def test_nobody_is_ever_demoted(self, db_session: Session):
        user = make(db_session, name="Admin", email="admin@sunradia.com")
        user.is_admin = True
        db_session.commit()

        bootstrap_service.ensure_admin(db_session)

        assert user.is_admin is True

    def test_no_user_is_invented_on_an_empty_deployment(self, db_session: Session):
        # An identity that appears on first boot is an identity nobody chose.
        assert bootstrap_service.ensure_admin(db_session) is None
        assert user_service.list_users(db_session) == []

    def test_running_it_twice_changes_nothing_the_second_time(
        self, db_session: Session
    ):
        make(db_session, name="Sudha", email="sudha@sunradia.com")

        assert bootstrap_service.ensure_admin(db_session) is not None
        assert bootstrap_service.ensure_admin(db_session) is None

    def test_the_persona_label_is_not_read_as_permission(self, db_session: Session):
        # `role` is typed by a person. Reading "Admin" there as authorisation
        # would make anybody who writes the word an administrator — the exact
        # mistake `is_admin` exists to prevent.
        make(db_session, name="First", email="first@sunradia.com", role="Intern")
        make(db_session, name="Claims", email="claims@sunradia.com", role="Admin")

        promoted = bootstrap_service.ensure_admin(db_session)

        # Whoever was promoted, it was not decided by the word in `role`: the
        # choice is the list order, and a persona label never enters it.
        assert promoted is not None
        assert promoted.id == user_service.list_users(db_session)[0].id
