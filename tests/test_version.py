"""Tests for the norns package version metadata."""

import norns


def test_version_is_non_empty_string():
    assert isinstance(norns.__version__, str)
    assert norns.__version__ != ""
