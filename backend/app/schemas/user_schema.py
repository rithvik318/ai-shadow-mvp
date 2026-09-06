import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserCreateRequest(BaseModel):
    """A person the Shadow can answer for."""

    name: str = Field(min_length=1, max_length=255)
    # Checked for shape, not deliverability, and typed `str` rather than
    # `EmailStr` so this does not pull in `email-validator` — a dependency the
    # project does not declare, for a field nothing sends mail to. It is a
    # unique handle on a row, and that is all it has to be.
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=255)

    @field_validator("name", "role")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """`min_length` alone would accept a string of spaces."""

        if not value.strip():
            raise ValueError("cannot be blank")

        return value

    @field_validator("email")
    @classmethod
    def looks_like_an_address(cls, value: str) -> str:
        candidate = value.strip()
        local, separator, domain = candidate.partition("@")

        if not separator or not local or not domain:
            raise ValueError("email must look like name@example.com")

        return candidate


class UserResponse(BaseModel):
    """A stored user.

    No secret to omit, because there is none: this is an identity, not an
    account. `id` is what goes in the `X-User-ID` header.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    role: str
    # The persona label above is what the Twin writes as; this is the only
    # field the server treats as permission.
    is_admin: bool = False
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """Every user, oldest first."""

    items: list[UserResponse]
    total: int


class UserDeletionPreview(BaseModel):
    """What deleting this user would destroy, counted per table.

    Backs the confirmation dialog. Counts rather than a generic warning,
    because "this will delete 14 drafts and 60 memories" is a decision somebody
    can actually make, and "this cannot be undone" is not.

    `shared_knowledge_documents` is listed to say what is *safe*: the company
    knowledge base is shared, is not owned by this person, and is not part of
    the deletion.
    """

    user_id: uuid.UUID
    name: str
    email: str
    owned: dict[str, int]
    owned_total: int
    shared_knowledge_documents: int
    shared_knowledge_note: str


class UserDeletionResponse(BaseModel):
    """What a completed deletion actually removed."""

    user_id: uuid.UUID
    deleted: dict[str, int]
    total: int
    shared_knowledge_documents: int
