"""Assign conservative serial exceptions during every collection."""
import pytest
from scripts.test_lanes import SERIAL_FILES


def pytest_collection_modifyitems(items):
    for item in items:
        if item.nodeid.split('::', 1)[0] in SERIAL_FILES:
            item.add_marker(pytest.mark.serial_required)
