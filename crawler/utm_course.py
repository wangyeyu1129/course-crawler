"""UTM calendar adapter: fetch HTML separately from deterministic parsing."""

from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

from crawler.fields import (
    FieldIndex,
    FieldReading,
    field_value,
    normalized_text,
    read_field,
)
from crawler.fields import get_labeled_field as get_labeled_field
from crawler.http import HttpClient, HttpPolicy
from crawler.urls import BASE_URL as BASE_URL
from crawler.urls import course_url
from crawler.validation import validate_utm_course as validate_course
from domain.course import Course, CourseFieldError, CourseRecord
from domain.field_state import CourseFieldStates, FieldState, FieldStatus


@dataclass(frozen=True)
class FieldSpec:
    label: str
    selector: str


FIELD_SPECS = {
    "prerequisites": FieldSpec("Prerequisites", ".field--name-field-prerequisite"),
    "exclusions": FieldSpec("Exclusions", ".field--name-field-exclusion"),
    "enrolment_limits": FieldSpec(
        "Enrolment Limits", ".field--name-field-enrolment-limits"
    ),
    "distribution_requirement": FieldSpec(
        "Distribution Requirement", ".field--name-field-distribution-requirements"
    ),
    "instruction_hours": FieldSpec(
        "Total Instructional Hours", ".field--name-field-hours"
    ),
    "delivery_mode": FieldSpec(
        "Mode of Delivery", ".field--name-field-mode-of-delivery"
    ),
    "program_area": FieldSpec("Program Area", ".field--name-field-sections"),
}
FIELD_LABELS = {name: spec.label for name, spec in FIELD_SPECS.items()}


def _observe(soup: BeautifulSoup, fields: FieldIndex, spec: FieldSpec) -> FieldReading:
    containers = soup.select(spec.selector)
    if len(containers) > 1:
        return FieldReading(
            None,
            FieldState(
                status=FieldStatus.PARSE_FAILED,
                error="Multiple source containers for this field.",
            ),
        )
    return fields.observe(
        spec.label, expected_container=containers[0] if containers else None
    )


def fetch_course_page(course_code: str, *, policy: HttpPolicy = HttpPolicy()) -> str:
    with HttpClient(policy) as client:
        return client.get_text(course_url(course_code))


def get_description(soup: BeautifulSoup) -> Optional[str]:
    """Use the description's semantic field class, independent of its label."""
    container = soup.select_one(".field--name-field-desc")
    return field_value(container) if container is not None else None


def parse_course_document(html: str) -> Course:
    """Return observed field evidence; malformed optional fields remain diagnosable."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1.page-title")
    if title is None:
        raise ValueError("Course title not found.")
    parts = normalized_text(title).split("•", 1)
    if len(parts) != 2 or not parts[1].strip():
        raise ValueError("Course title must contain a course code and name.")
    code, name = (part.strip() for part in parts)
    url = course_url(code)
    containers = soup.select(".field--name-field-desc")
    if len(containers) > 1:
        description = FieldReading(
            None,
            FieldState(
                status=FieldStatus.PARSE_FAILED,
                error="Multiple description containers.",
            ),
        )
    else:
        description = read_field(containers[0] if containers else None)
    if description.value is None:
        raise CourseFieldError({"description": description.state})
    fields = FieldIndex(soup)
    readings = {key: _observe(soup, fields, spec) for key, spec in FIELD_SPECS.items()}
    return Course(
        code=code.upper(),
        name=name,
        campus="UTM",
        url=url,
        description=description.value,
        **{key: reading.value for key, reading in readings.items()},
        field_states=CourseFieldStates(
            description=description.state,
            **{key: reading.state for key, reading in readings.items()},
        ),
    )


def parse_course_page(html: str) -> CourseRecord:
    """Legacy twelve-field projection; field extraction failures remain failures."""
    return validate_course(parse_course_document(html))
