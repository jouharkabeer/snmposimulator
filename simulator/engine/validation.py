"""Input validation for simulation size and traffic-category counts."""

from __future__ import annotations

from dataclasses import dataclass


class ValidationError(ValueError):
    """Raised when the operator supplies inconsistent device counts."""


@dataclass(frozen=True)
class CategoryCounts:
    total: int
    high: int
    medium: int
    low: int
    unreachable: int

    @property
    def reachable(self) -> int:
        return self.high + self.medium + self.low

    def as_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
            "unreachable": self.unreachable,
        }


def parse_non_negative_int(raw: str, field: str) -> int:
    text = str(raw).strip()
    if text == "":
        raise ValidationError(f"{field} cannot be empty.")
    if not text.isdigit():
        raise ValidationError(f"{field} must be a whole number >= 0, got '{raw}'.")
    return int(text)


def validate_counts(
    total: int,
    high: int,
    medium: int,
    low: int,
    unreachable: int,
) -> CategoryCounts:
    fields = {
        "total devices": total,
        "HIGH traffic devices": high,
        "MEDIUM traffic devices": medium,
        "LOW traffic devices": low,
        "UNREACHABLE devices": unreachable,
    }
    for label, value in fields.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"{label} must be an integer.")
        if value < 0:
            raise ValidationError(f"{label} cannot be negative.")

    if total < 1:
        raise ValidationError("Total devices must be at least 1.")

    categorized = high + medium + low + unreachable
    if categorized != total:
        raise ValidationError(
            "Traffic/reachability categories must add up to the total number of "
            f"devices. Got high({high}) + medium({medium}) + low({low}) + "
            f"unreachable({unreachable}) = {categorized}, expected {total}."
        )

    if high + medium + low == 0 and unreachable == total:
        # Allowed: a lab of only unreachable devices is useful for alert demos.
        pass

    return CategoryCounts(
        total=total,
        high=high,
        medium=medium,
        low=low,
        unreachable=unreachable,
    )
