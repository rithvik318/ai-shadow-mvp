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
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """Every user, oldest first."""

    items: list[UserResponse]
    total: int
