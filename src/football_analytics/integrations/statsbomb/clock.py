import re


def timestamp_to_milliseconds(value: str) -> int:
    """Parse a StatsBomb period timestamp without floating-point rounding."""
    match = re.fullmatch(
        r"(?P<hours>[0-9]{2}):"
        r"(?P<minutes>[0-5][0-9]):"
        r"(?P<seconds>[0-5][0-9])\."
        r"(?P<milliseconds>[0-9]{3})",
        value,
    )

    if match is None:
        raise ValueError(f"Invalid StatsBomb timestamp: {value!r}")

    hours = int(match["hours"])
    minutes = int(match["minutes"])
    seconds = int(match["seconds"])
    milliseconds = int(match["milliseconds"])

    return hours * 3_600_000 + minutes * 60_000 + seconds * 1_000 + milliseconds


def format_duration_ms(value: int) -> str:
    if value < 0:
        raise ValueError("Duration must not be negative")

    minutes, remainder = divmod(value, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)

    return f"{minutes} min {seconds:02d},{milliseconds:03d} s"
