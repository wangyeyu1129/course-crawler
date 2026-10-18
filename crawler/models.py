"""Compatibility imports; new code uses domain.course and crawler.validation."""

from crawler.validation import validate_utm_course as validate_course
from domain.course import Course as Course

__all__ = ["Course", "validate_course"]
