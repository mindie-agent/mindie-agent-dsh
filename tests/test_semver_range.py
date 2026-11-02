"""Declared DSH ranges decide compatibility with npm-semver precedence."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from semver_range import SemverRangeError, satisfies

# The declared optional peer range. DSH evaluates it with prereleases included.
RANGE = '>=0.2.0-rc.2 <0.2.2-0'

ADMITTED = [
    '0.2.0-rc.2',
    '0.2.0-rc.3',
    '0.2.0',
    '0.2.1-0',
    '0.2.1-rc.0',
    '0.2.1-rc.5',
    '0.2.1',
]

REJECTED = [
    '0.1.9',
    '0.2.0-0',
    '0.2.2-0',
    '0.2.2-rc.1',
    '0.2.2',
    '0.3.0-0',
    '0.3.0-rc.1',
    '1.0.0',
]


@pytest.mark.parametrize('version', ADMITTED)
def test_declared_range_admits_020_and_021_lines(version):
    assert satisfies(version, RANGE)


@pytest.mark.parametrize('version', REJECTED)
def test_declared_range_rejects_other_lines(version):
    assert not satisfies(version, RANGE)


def test_alternatives_whitespace_and_metadata():
    assert satisfies('0.2.1-rc.5', '0.2.0-rc.2 || 0.2.1-rc.5')
    assert not satisfies('0.2.1-rc.4', '0.2.0-rc.2 || 0.2.1-rc.5')
    assert satisfies('0.2.1', '>= 0.2.0 < 0.2.2')
    assert satisfies('0.2.1+build.7', '=0.2.1')
    assert not satisfies('0.2.1+build.7', '=0.2.2')


def test_prerelease_precedence_matches_semver():
    # Numeric prerelease identifiers sort below alphanumeric ones.
    assert satisfies('0.2.2-rc.1', '>0.2.2-0')
    assert not satisfies('0.2.2-rc.1', '<0.2.2-0')
    # A final release outranks its prereleases.
    assert satisfies('0.2.1', '>0.2.1-rc.5')
    assert satisfies('0.2.1-rc.5', '>0.2.0')


@pytest.mark.parametrize('expression', ['0.2.x', '^0.2.0', '~0.2.1', '1.2', '', '>= '])
def test_unsupported_syntax_is_rejected(expression):
    with pytest.raises(SemverRangeError):
        satisfies('0.2.1', expression)
