"""Course data contract with lightweight validation at application boundaries."""

from collections.abc import Mapping
from typing import Optional, TypedDict, cast

from crawler.urls import course_url


class Course(TypedDict):
    code: str
    name: str
    campus: str
    description: str
    prerequisites: Optional[str]
    exclusions: Optional[str]
    enrolment_limits: Optional[str]
    distribution_requirement: Optional[str]
    instruction_hours: Optional[str]
    delivery_mode: Optional[str]
    program_area: Optional[str]
    url: str


def validate_course(value, expected_url: Optional[str] = None) -> Course:
    if not isinstance(value, Mapping) or set(value) != set(Course.__annotations__):
        raise ValueError("Course must contain exactly the twelve defined fields.")
    for name, item in value.items():
        if item is not None and not isinstance(item, str):
            raise ValueError(f"Course field {name} must be text or None.")
    for name in ("code", "name", "campus", "description", "url"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError(f"Course field {name} must contain text.")
    if value["campus"] != "UTM" or value["code"] != value["code"].strip().upper():
        raise ValueError("Course campus/code are not canonical UTM values.")
    if course_url(value["code"]) != value["url"] or (
        expected_url is not None and value["url"] != expected_url
    ):
        raise ValueError(
            "Parsed course URL does not match the queued URL or course code."
        )
    return cast(Course, dict(value))
