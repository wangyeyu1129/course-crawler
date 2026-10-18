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


def _label_key(text: str) -> str:
    return " ".join(text.split()).rstrip(":").strip().casefold()


class FieldIndex:
    """Index a page's labels once; preserve first-label and field ownership rules."""

    def __init__(self, soup: BeautifulSoup) -> None:
        self._containers: dict[str, Tag] = {}
        for label in soup.select(".field__label"):
            container = label.find_parent(class_="field")
            self._containers.setdefault(
                _label_key(normalized_text(label)),
                container if container is not None else label.parent,
            )

    def get(self, field_name: str) -> Optional[str]:
        key = _label_key(field_name)
        if not key:
            raise ValueError("Field name must not be blank.")
        container = self._containers.get(key)
        return field_value(container) if container is not None else None


def get_labeled_field(soup: BeautifulSoup, field_name: str) -> Optional[str]:
    """Read one field; use FieldIndex when extracting several fields per page."""
    return FieldIndex(soup).get(field_name)
