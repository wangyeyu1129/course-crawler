"""DOM field extraction shared by calendar parsers; no network dependencies."""

from typing import Optional

from bs4 import BeautifulSoup, Tag


def normalized_text(node: Tag) -> str:
    """Collapse HTML whitespace, including non-breaking spaces."""
    return " ".join(node.get_text(" ", strip=True).split())


def field_value(container: Tag) -> Optional[str]:
    """Read all items owned by this field without borrowing nested fields."""
    values = []
    for item in container.select(".field__item"):
        owner = item.find_parent(class_="field")
        if owner is not None and owner is not container:
            continue
        ancestor_item = item.find_parent(class_="field__item")
        if (
            ancestor_item is not None
            and ancestor_item.find_parent(class_="field") is owner
        ):
            continue
        # Each text fragment belongs to its nearest field, not every ancestor.
        # Read without mutating the soup so nested fields remain queryable.
        fragments = (
            str(text).strip()
            for text in item.strings
            if text.find_parent(class_="field") is owner
        )
        value = " ".join(" ".join(fragments).split())
        if value:
            values.append(value)
    return "; ".join(values) or None


def get_labeled_field(soup: BeautifulSoup, field_name: str) -> Optional[str]:
    """Match a complete label, ignoring case, whitespace and trailing colons.

    Missing/empty fields return None. Multiple items retain document order and
    are separated by semicolons. Values never come from neighboring fields.
    """
    target = " ".join(field_name.split()).rstrip(":").strip().casefold()
    if not target:
        raise ValueError("Field name must not be blank.")
    for label in soup.select(".field__label"):
        name = normalized_text(label).rstrip(":").strip().casefold()
        if name == target:
            container = label.find_parent(class_="field")
            return field_value(container if container is not None else label.parent)
    return None
