"""Flatten skill values from the master Excel grid."""

from typing import Iterable, List


def flatten_skill_values(values: Iterable) -> List[str]:
    """Deduplicate skill names from a flat or nested iterable, preserving order."""
    skills: List[str] = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        skill = value.strip()
        if not skill:
            continue
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        skills.append(skill)
    return skills
