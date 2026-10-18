"""Source observations; absence and extraction failure are not interchangeable."""

from enum import Enum
from typing import Optional, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class FieldStatus(str, Enum):
    PRESENT = "present"
    EMPTY = "empty"
    NOT_PROVIDED = "not_provided"
    PARSE_FAILED = "parse_failed"
    UNKNOWN = "unknown"


class FieldState(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    status: FieldStatus

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, value: object) -> FieldStatus:
        if not isinstance(value, str):
            raise ValueError("Field status must be an enum or text value.")
        return FieldStatus(value)

    error: Optional[str] = None

    @model_validator(mode="after")
    def validate_error(self) -> Self:
        if self.status is FieldStatus.PARSE_FAILED:
            if self.error is None or not self.error.strip():
                raise ValueError("parse_failed requires a nonblank diagnostic.")
        elif self.error is not None:
            raise ValueError("Only parse_failed may contain an error.")
        return self

    def check_value(self, value: Optional[str]) -> None:
        if self.status is FieldStatus.PRESENT:
            if value is None or not value.strip():
                raise ValueError("present requires nonblank text.")
        elif value is not None:
            raise ValueError(f"{self.status.value} requires a null value.")


class CourseFieldStates(BaseModel):
    """Evidence for content fields; identity fields are required by the course schema."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    description: FieldState
    prerequisites: FieldState
    exclusions: FieldState
    enrolment_limits: FieldState
    distribution_requirement: FieldState
    instruction_hours: FieldState
    delivery_mode: FieldState
    program_area: FieldState
