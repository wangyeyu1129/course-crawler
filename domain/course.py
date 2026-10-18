"""Pydantic course model and explicit bridges for historical twelve-field records."""

import json
from collections.abc import Mapping
from typing import Annotated, Literal, Optional, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from domain.field_state import CourseFieldStates, FieldState, FieldStatus

CourseRecord = dict[str, str | None]


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Required course text must not be blank.")
    return value


NonblankText = Annotated[str, AfterValidator(nonblank)]
OPTIONAL_TEXT_FIELDS = tuple(
    name for name in CourseFieldStates.model_fields if name != "description"
)


class CourseData(BaseModel):
    """Stable legacy projection; every key remains required, including nullable ones."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    code: NonblankText
    name: NonblankText
    campus: NonblankText
    description: NonblankText
    prerequisites: Optional[str]
    exclusions: Optional[str]
    enrolment_limits: Optional[str]
    distribution_requirement: Optional[str]
    instruction_hours: Optional[str]
    delivery_mode: Optional[str]
    program_area: Optional[str]
    url: NonblankText

    @field_validator(*OPTIONAL_TEXT_FIELDS)
    @classmethod
    def normalize_empty_text(cls, value: Optional[str]) -> Optional[str]:
        return None if value is not None and not value.strip() else value


class Course(CourseData):
    """Source-neutral values with validated, immutable field observations."""

    schema_version: Literal[1] = 1
    field_states: CourseFieldStates

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer.")
        return value

    @model_validator(mode="after")
    def validate_states(self) -> Self:
        for name in CourseFieldStates.model_fields:
            state = getattr(self.field_states, name)
            try:
                state.check_value(getattr(self, name))
            except ValueError as error:
                raise ValueError(f"{name}: {error}") from error
        return self

    @classmethod
    def from_legacy(cls, value: Mapping) -> Self:
        """Never infer source absence from historical nulls without DOM evidence."""
        original = dict(value)
        data = CourseData.model_validate(original)
        states = {}
        for name in CourseFieldStates.model_fields:
            raw = original[name]
            status = (
                FieldStatus.UNKNOWN
                if raw is None
                else FieldStatus.EMPTY
                if not raw.strip()
                else FieldStatus.PRESENT
            )
            states[name] = FieldState(status=status)
        return cls(**data.model_dump(), field_states=CourseFieldStates(**states))

    def to_legacy_dict(self) -> CourseRecord:
        return {name: getattr(self, name) for name in CourseData.model_fields}

    def require_parse_success(self) -> None:
        failures = {
            name: getattr(self.field_states, name)
            for name in CourseFieldStates.model_fields
            if getattr(self.field_states, name).status is FieldStatus.PARSE_FAILED
        }
        if failures:
            raise CourseFieldError(failures)


class CourseFieldError(ValueError):
    """A page diagnostic that can be persisted without confusing failure with null."""

    def __init__(self, failures: Mapping[str, FieldState]):
        self.field_states = {
            name: state.model_dump(mode="json") for name, state in failures.items()
        }
        super().__init__(
            "Course field parsing failed: "
            + json.dumps(self.field_states, ensure_ascii=False, sort_keys=True)
        )


def validate_course_model(value: object, expected_url: Optional[str] = None) -> Course:
    if isinstance(value, Course):
        course = Course.model_validate(value)
    elif isinstance(value, Mapping):
        course = (
            Course.model_validate(dict(value))
            if "field_states" in value
            else Course.from_legacy(value)
        )
    else:
        raise ValueError("Course must be a model or mapping.")
    if expected_url is not None and course.url != expected_url:
        raise ValueError("Parsed course URL does not match the queued URL.")
    course.require_parse_success()
    return course


def validate_course(value: object, expected_url: Optional[str] = None) -> CourseRecord:
    """Legacy dictionary boundary, validated by the canonical Pydantic model."""
    return validate_course_model(value, expected_url).to_legacy_dict()
