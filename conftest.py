"""conftest.py — RC3 test configuration."""

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (model loading, training, etc.)",
    )
