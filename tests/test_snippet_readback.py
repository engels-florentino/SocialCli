"""Readback normalization is narrowly scoped and never mutates approval bodies."""
import copy

import pytest
from socialctl.management.snippet_readback import same_snippet_readback


@pytest.mark.parametrize('actual,expected,want', [
    ({'tags': ['a', 'b', 'a']}, {'tags': ['b', 'a', 'a']}, True),
    ({}, {'tags': [], 'description': ''}, True),
    ({'tags': ['a']}, {'tags': ['a', 'a']}, False),
    ({'tags': ['A']}, {'tags': ['a']}, False),
    ({'tags': [' a']}, {'tags': ['a']}, False),
    ({'tags': None}, {'tags': None}, False),
    ({'tags': [1]}, {'tags': [1]}, False),
    ({'tags': 'a'}, {'tags': ['a']}, False),
    ({'other': ['a', 'b']}, {'other': ['b', 'a']}, False),
    ({'defaultAudioLanguage': 'en'}, {}, False),
    (None, {}, False),
    ({'description': None}, {'description': None}, False),
])
def test_exact_readback_contract(actual, expected, want):
    before = copy.deepcopy((actual, expected))
    assert same_snippet_readback(actual, expected) is want
    assert (actual, expected) == before
