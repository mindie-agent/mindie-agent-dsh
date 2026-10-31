"""[Role] Evaluate npm-style version ranges for a candidate plugin's DSH requirement.
[Invariants] Comparison follows Semantic Versioning 2.0.0 precedence, and every
comparator is a plain precedence test because DSH itself evaluates peer ranges
with `includePrerelease: true`. Only comparator sets (>=, <=, >, <, =, joined by
whitespace, alternatives joined by `||`) are accepted; any other syntax is
rejected instead of guessed, so an author error cannot silently admit a
candidate the running DSH would refuse.
[Timeouts & Locks] Pure in-memory parsing and comparison; no I/O, children,
locks, or network.
[Side-Effect Policy] Read-only: returns a boolean or raises a typed error.
"""
from __future__ import annotations

import re

_NUMERIC = re.compile(r'^(?:0|[1-9]\d*)$')
_VERSION = re.compile(
    r'^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)'
    r'(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
    r'(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
)
_COMPARATOR = re.compile(r'^(?P<operator><=|>=|<|>|=)?\s*(?P<version>\S+)$')
_OPERATORS = ('<=', '>=', '<', '>', '=')


class SemverRangeError(ValueError):
    """A version or range falls outside the supported npm-semver subset."""


def _prerelease_identifier(part: str) -> tuple[int, object]:
    """Order identifiers per SemVer: numeric identifiers sort below alphanumeric."""
    if _NUMERIC.match(part):
        return (0, int(part))
    return (1, part)


def parse_version(text: str) -> tuple[int, int, int, tuple[tuple[int, object], ...]]:
    """Parse one exact semantic version, ignoring build metadata for precedence."""
    match = _VERSION.match(text.strip())
    if match is None:
        raise SemverRangeError(f'unsupported semantic version: {text!r}')
    prerelease = match.group('pre')
    return (
        int(match.group('major')),
        int(match.group('minor')),
        int(match.group('patch')),
        tuple(_prerelease_identifier(part) for part in prerelease.split('.'))
        if prerelease
        else (),
    )


def _compare(
    left: tuple[int, int, int, tuple[tuple[int, object], ...]],
    right: tuple[int, int, int, tuple[tuple[int, object], ...]],
) -> int:
    """Return -1, 0, or 1 for Semantic Versioning precedence."""
    for left_part, right_part in zip(left[:3], right[:3]):
        if left_part != right_part:
            return -1 if left_part < right_part else 1
    if not left[3] and not right[3]:
        return 0
    if not left[3]:
        return 1
    if not right[3]:
        return -1
    for left_part, right_part in zip(left[3], right[3]):
        if left_part != right_part:
            return -1 if left_part < right_part else 1
    if len(left[3]) == len(right[3]):
        return 0
    return -1 if len(left[3]) < len(right[3]) else 1


def _comparators(alternative: str) -> list[tuple[str, tuple[int, int, int, tuple[tuple[int, object], ...]]]]:
    tokens = alternative.split()
    if not tokens:
        raise SemverRangeError('empty comparator set in range')
    comparators: list[tuple[str, tuple[int, int, int, tuple[tuple[int, object], ...]]]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        # A bare operator token belongs to the next version (`>= 0.2.0`).
        if token in _OPERATORS:
            if index + 1 >= len(tokens):
                raise SemverRangeError(f'operator {token!r} has no version')
            comparators.append((token, parse_version(tokens[index + 1])))
            index += 2
            continue
        match = _COMPARATOR.match(token)
        if match is not None:
            comparators.append(
                (match.group('operator') or '=', parse_version(match.group('version')))
            )
            index += 1
            continue
        raise SemverRangeError(f'unsupported range syntax near {token!r}')
    return comparators


def _holds(
    target: tuple[int, int, int, tuple[tuple[int, object], ...]],
    operator: str,
    bound: tuple[int, int, int, tuple[tuple[int, object], ...]],
) -> bool:
    order = _compare(target, bound)
    if operator == '=':
        return order == 0
    if operator == '>':
        return order > 0
    if operator == '>=':
        return order >= 0
    if operator == '<':
        return order < 0
    return order <= 0


def satisfies(version: str, range_expression: str) -> bool:
    """Whether an exact version satisfies an npm-style range under DSH semantics."""
    if not isinstance(range_expression, str) or not range_expression.strip():
        raise SemverRangeError('range must be a non-empty string')
    target = parse_version(version)
    for alternative in range_expression.split('||'):
        if all(_holds(target, operator, bound) for operator, bound in _comparators(alternative)):
            return True
    return False
