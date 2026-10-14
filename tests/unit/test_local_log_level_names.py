"""A local log write maps only the documented level names; anything else logs at INFO."""

from __future__ import annotations

import logging

import pytest

from perceivo.local.adapters.logging import LocalLoggingService


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.levels: list[int] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.levels.append(record.levelno)


@pytest.fixture
def capture():
    target = logging.getLogger("perceivo")
    handler = _Capture()
    previous = target.level
    target.setLevel(logging.DEBUG)
    target.addHandler(handler)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("debug", logging.DEBUG),
        ("info", logging.INFO),
        ("warning", logging.WARNING),
        ("error", logging.ERROR),
        ("ERROR", logging.ERROR),
    ],
)
async def test_a_documented_level_logs_at_that_level(capture, level, expected):
    await LocalLoggingService(level="DEBUG").write(level, "m")
    assert capture.levels == [expected]


@pytest.mark.parametrize("level", ["critical", "fatal", "warn", "verbose", "basic_format"])
async def test_an_unrecognised_level_logs_at_info(capture, level):
    await LocalLoggingService(level="DEBUG").write(level, "m")
    assert capture.levels == [logging.INFO]
