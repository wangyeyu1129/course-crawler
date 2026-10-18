"""Valid course records for persistence and orchestration behavior tests."""

from urllib.parse import urlsplit


def course_record(source_url, **changes):
    record = dict.fromkeys(
        (
            "prerequisites",
            "exclusions",
            "enrolment_limits",
            "distribution_requirement",
            "instruction_hours",
            "delivery_mode",
            "program_area",
        )
    )
    record.update(
        code=urlsplit(source_url).path.rsplit("/", 1)[-1].upper(),
        name="Test course",
        campus="UTM",
        description="Test description",
        url=source_url,
    )
    record.update(changes)
    return record
