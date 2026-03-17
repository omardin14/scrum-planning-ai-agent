"""Auto-apply the 'eval' and 'slow' markers to all tests in the golden/ directory."""

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "/golden/" in str(item.fspath):
            item.add_marker(pytest.mark.eval)
            item.add_marker(pytest.mark.slow)
