"""UTM calendar adapter: fetch HTML separately from deterministic parsing."""

import re
from typing import Optional

from bs4 import BeautifulSoup

from crawler.fields import field_value, get_labeled_field, normalized_text
from crawler.http import HttpClient, HttpPolicy

BASE_URL = "https://utm.calendar.utoronto.ca"
FIELD_LABELS = {
    "prerequisites": "Prerequisites",
    "exclusions": "Exclusions",
    "enrolment_limits": "Enrolment Limits",
    "distribution_requirement": "Distribution Requirement",
    "instruction_hours": "Total Instructional Hours",
    "delivery_mode": "Mode of Delivery",
    "program_area": "Program Area",
}


def course_url(course_code: str) -> str:
    """Build a UTM course URL from a validated calendar course code."""
    code = course_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}\d{3}[HY]5", code):
        raise ValueError("Invalid UTM course code.")
    return f"{BASE_URL}/course/{code.lower()}"


def fetch_course_page(course_code: str, *, policy: HttpPolicy = HttpPolicy()) -> str:
    with HttpClient(policy) as client:
        return client.get_text(course_url(course_code))


def get_description(soup: BeautifulSoup) -> Optional[str]:
    """Use the description's semantic field class, independent of its label."""
    container = soup.select_one(".field--name-field-desc")
    return field_value(container) if container is not None else None


def parse_course_page(html: str) -> dict[str, Optional[str]]:
    """Parse one course. Optional absent fields are None; invalid titles fail."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1.page-title")
    if title is None:
        raise ValueError("Course title not found.")
    parts = normalized_text(title).split("•", 1)
    if len(parts) != 2 or not parts[1].strip():
        raise ValueError("Course title must contain a course code and name.")
    code, name = (part.strip() for part in parts)
    url = course_url(code)
    return {
        "code": code.upper(),
        "name": name,
        "campus": "UTM",
        "description": get_description(soup),
        **{key: get_labeled_field(soup, label) for key, label in FIELD_LABELS.items()},
        "url": url,
    }
