"""Skills: procedural knowledge the agent loads only when it applies.

Discovery and the on-disk format live in ``loader``; see
[notes/30](../../notes/30-skills.md) for the why. Skills the agent writes
down itself, after solving a task, come from ``learn``
([notes/96](../../notes/96-solve-it-once.md)).
"""

from __future__ import annotations

from yantra.skills.registry import (
    ROSTER_LIMIT,
    SkillRegistry,
    enable_skills,
)
from yantra.skills.tools import ListSkills, LoadSkill, RunSkill
from yantra.skills.learn import Learner, Offer, enable_learning, learn_mode
from yantra.skills.loader import (
    MIN_DESCRIPTION,
    SKILL_FILE,
    BrokenSkill,
    LearnedRecord,
    Skill,
    SkillError,
    SkillSet,
    discover,
    load_skill,
    parse_frontmatter,
    render_skill_md,
    skill_roots,
    validate_text,
)

__all__ = [
    "BrokenSkill",
    "enable_learning",
    "enable_skills",
    "LearnedRecord",
    "learn_mode",
    "Learner",
    "ListSkills",
    "LoadSkill",
    "RunSkill",
    "ROSTER_LIMIT",
    "SkillRegistry",
    "discover",
    "load_skill",
    "MIN_DESCRIPTION",
    "Offer",
    "parse_frontmatter",
    "render_skill_md",
    "validate_text",
    "Skill",
    "SKILL_FILE",
    "SkillError",
    "SkillSet",
    "skill_roots",
]
