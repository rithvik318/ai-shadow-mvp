import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.digital_twin import MemoryType

# Long enough for a real answer, short enough that no single field can
# dominate the prompt budget on its own.
MAX_TEXT = 2000
MAX_LIST_ITEMS = 25
MAX_LIST_ITEM = 500


class ProfileRequest(BaseModel):
    """The person the Shadow answers for.

    Every field is optional so that correcting one does not mean resending the
    profile — but a profile that does not name anybody is not a profile, so the
    first write has to carry at least a name, and the service rejects the
    insert otherwise.
    """

    name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    organization: str | None = Field(default=None, max_length=255)
    communication_style: str | None = Field(default=None, max_length=MAX_TEXT)

    responsibilities: list[str] | None = Field(default=None, max_length=MAX_LIST_ITEMS)
    expertise: list[str] | None = Field(default=None, max_length=MAX_LIST_ITEMS)
    priorities: list[str] | None = Field(default=None, max_length=MAX_LIST_ITEMS)
    decision_preferences: list[str] | None = Field(
        default=None, max_length=MAX_LIST_ITEMS
    )
    current_focus: list[str] | None = Field(default=None, max_length=MAX_LIST_ITEMS)

    @field_validator("name", "role", "organization")
    @classmethod
    def reject_blank(cls, value: str | None) -> str | None:
        """`max_length` alone would accept a string of spaces."""

        if value is not None and not value.strip():
            raise ValueError("cannot be blank")

        return value

    @field_validator(
        "responsibilities",
        "expertise",
        "priorities",
        "decision_preferences",
        "current_focus",
    )
    @classmethod
    def reject_blank_entries(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None

        cleaned = [item.strip() for item in value if item.strip()]

        for item in cleaned:
            if len(item) > MAX_LIST_ITEM:
                raise ValueError(f"entries must be {MAX_LIST_ITEM} characters or fewer")

        return cleaned

    def supplied(self) -> dict[str, object]:
        """Only the fields the caller actually sent."""

        return self.model_dump(exclude_unset=True, exclude_none=True)


class ProfileResponse(BaseModel):
    """The stored profile."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: str
    organization: str
    communication_style: str | None
    responsibilities: list[str]
    expertise: list[str]
    priorities: list[str]
    decision_preferences: list[str]
    current_focus: list[str]
    created_at: datetime
    updated_at: datetime


class MemoryCreateRequest(BaseModel):
    """One durable thing worth remembering."""

    type: MemoryType
    content: str = Field(min_length=1, max_length=MAX_TEXT)
    importance: int = Field(default=3, ge=1, le=5)
    source: str = Field(default="user", max_length=255)
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content cannot be blank")

        return value


class MemoryUpdateRequest(BaseModel):
    """A partial change to a stored memory."""

    type: MemoryType | None = None
    content: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    importance: int | None = Field(default=None, ge=1, le=5)
    source: str | None = Field(default=None, max_length=255)
    active: bool | None = None
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def reject_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("content cannot be blank")

        return value

    def supplied(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class MemoryResponse(BaseModel):
    """A stored memory."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: MemoryType
    content: str
    importance: int
    source: str
    active: bool
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    """Every memory matching the filters, most important first."""

    items: list[MemoryResponse]
    total: int
