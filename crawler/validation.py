"""UTM adapter validation layered over the shared domain contract."""

from typing import Optional

from crawler.urls import course_url
from domain.course import Course
from domain.course import validate_course as validate_course_shape


def validate_utm_course(value: object, expected_url: Optional[str] = None) -> Course:
    course = validate_course_shape(value, expected_url=expected_url)
    if course["campus"] != "UTM" or course["code"] != course["code"].strip().upper():
        raise ValueError("Course campus/code are not canonical UTM values.")
    if course_url(course["code"]) != course["url"]:
        raise ValueError("Parsed course URL does not match the course code.")
    return course
