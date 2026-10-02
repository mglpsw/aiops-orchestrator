"""Pytest plugin recording selected IDs, including actual xdist collections."""
import json
import os
from pathlib import Path
import pytest


def record(ids):
    path = os.environ.get('AIOPS_TEST_COLLECTION_REPORT')
    if path:
        Path(path).write_text(json.dumps(sorted(ids), indent=2) + '\n')


def pytest_collection_finish(session):
    if not hasattr(session.config, 'workerinput'):
        record([item.nodeid for item in session.items])


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    # xdist independently rejects mismatched worker collections. Record the
    # actual worker IDs in the controller, never race workers writing a file.
    record(ids)
