"""DOM field extraction shared by calendar parsers; no network dependencies."""

from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup, Tag

from domain.field_state import FieldState, FieldStatus


def normalized_text(node: Tag) -> str:
    """Collapse HTML whitespace, including non-breaking spaces."""
    return " ".join(node.get_text(" ", strip=True).split())


def _owned_items(container: Tag) -> list[Tag]:
    items = []
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
        items.append(item)
    return items


def _items_value(items: list[Tag]) -> Optional[str]:
    values = []
    for item in items:
        owner = item.find_parent(class_="field")
        fragments = (
            str(text).strip()
            for text in item.strings
            if text.find_parent(class_="field") is owner
        )
        value = " ".join(" ".join(fragments).split())
        if value:
            values.append(value)
    return "; ".join(values) or None


def field_value(container: Tag) -> Optional[str]:
    """Legacy value projection, retaining nested-field ownership rules."""
    return _items_value(_owned_items(container))


@dataclass(frozen=True)
class FieldReading:
    value: Optional[str]
    state: FieldState

    def __post_init__(self):
        self.state.check_value(self.value)


def read_field(container: Optional[Tag]) -> FieldReading:
    """Observe source structure before interpreting an empty value."""
    if container is None:
        return FieldReading(None, FieldState(status=FieldStatus.NOT_PROVIDED))
    items = _owned_items(container)
    if not items:
        return FieldReading(
            None,
            FieldState(
                status=FieldStatus.PARSE_FAILED,
                error="Field container has no owned .field__item.",
            ),
        )
    owned_ids = {id(item) for item in items}
    for text in container.strings:
        if not str(text).strip() or text.find_parent(class_="field__label") is not None:
            continue
        owner = text.find_parent(class_="field")
        if owner is not None and owner is not container:
            continue
        if not any(id(parent) in owned_ids for parent in text.parents):
            return FieldReading(
                None,
                FieldState(
                    status=FieldStatus.PARSE_FAILED,
                    error="Field contains text outside its owned value items.",
                ),
            )
    value = _items_value(items)
    status = FieldStatus.PRESENT if value is not None else FieldStatus.EMPTY
    return FieldReading(value, FieldState(status=status))


def _label_key(text: str) -> str:
    return " ".join(text.split()).rstrip(":").strip().casefold()


class FieldIndex:
    """Index a page's labels once; preserve first-label and field ownership rules."""

    def __init__(self, soup: BeautifulSoup) -> None:
        self._containers: dict[str, Tag] = {}
        self._candidates: dict[str, list[Tag]] = {}
        self._unowned_labels: set[str] = set()
        for label in soup.select(".field__label"):
            container = label.find_parent(class_="field")
            key = _label_key(normalized_text(label))
            if container is None:
                self._unowned_labels.add(key)
            candidate = container if container is not None else label.parent
            self._containers.setdefault(key, candidate)
            candidates = self._candidates.setdefault(key, [])
            if not any(item is candidate for item in candidates):
                candidates.append(candidate)

    def get(self, field_name: str) -> Optional[str]:
        key = _label_key(field_name)
        if not key:
            raise ValueError("Field name must not be blank.")
        container = self._containers.get(key)
        return field_value(container) if container is not None else None

    def observe(
        self, field_name: str, *, expected_container: Optional[Tag] = None
    ) -> FieldReading:
        key = _label_key(field_name)
        if not key:
            raise ValueError("Field name must not be blank.")
        candidates = self._candidates.get(key, [])
        if key in self._unowned_labels or (
            expected_container is not None
            and not any(candidate is expected_container for candidate in candidates)
        ):
            return FieldReading(
                None,
                FieldState(
                    status=FieldStatus.PARSE_FAILED,
                    error="Expected field container and label do not match.",
                ),
            )
        if len(candidates) > 1:
            return FieldReading(
                None,
                FieldState(
                    status=FieldStatus.PARSE_FAILED,
                    error="Multiple field containers share this label.",
                ),
            )
        return read_field(candidates[0] if candidates else None)


def get_labeled_field(soup: BeautifulSoup, field_name: str) -> Optional[str]:
    """Read one field; use FieldIndex when extracting several fields per page."""
    return FieldIndex(soup).get(field_name)
