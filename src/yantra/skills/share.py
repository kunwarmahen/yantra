"""Sharing a learned skill: the checks before anything leaves, and installing one.

A recipe the agent saved for you ([notes/96](../../notes/96-solve-it-once.md))
is a folder: a SKILL.md and maybe a script. Handing it to somebody else
is copying that folder, and the danger is in what the copy carries.
The write-up was told to keep your values out (learn.py), and the person
read it before saving, but a recipe gets repaired, edited and promoted
after that, and nobody re-reads it with sharing in mind. So sharing
reads it again, every file, for what must not leave:

    secrets          tokens, KEY=value lines, Authorization headers   (learn.scrub)
    your redaction   the trace's own --trace-redact patterns and words
    addresses        an email address anywhere
    what you told it a value from memory: an entity id, an address
    where you live   your home folder's path, your login name
    needs            a setu:<id> Setu does not know

ANY FINDING STOPS IT. A recipe with one problem is not written at all,
and each problem names the file, the line and the reason. The fix is
the person's -- edit the file, or replace the value with an input
(``inputs:``) -- because a scrubber that silently cut a value out would
hand somebody else a recipe with a hole in it.

WHAT IS LEFT OUT ON PURPOSE. The ``learned:`` counters are how it did
for you, and ``tool:`` is a promotion you chose; neither is the
recipe's. The copy keeps the steps, the inputs, the needs and the
script, nothing else.

WHERE IT GOES. ``recipes/<name>/`` under the current folder -- the
layout of the Setu catalog, so run from a checkout of it and the recipe
is where a pull request wants it. A hash over the files is printed; a
catalog pins a recipe by it.

INSTALLING ONE is the reverse, and its danger is the reverse: a script
somebody else wrote. The host shows every file and asks before
``install`` writes anything. It lands in your own learned folder with
fresh counters and a ``shared:`` line naming the hash, so ``/skills``
tells it from one the agent learned for you, and it is repaired, set
aside when it goes stale, and deleted exactly like one.
"""

from __future__ import annotations

import getpass
import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from yantra.skills.loader import SKILL_FILE, Skill, SkillError, learned_root, load_skill
from yantra.trace import REDACT_PRESETS

#: Where a shared recipe is written, under the current folder: the Setu
#: catalog keeps recipes there.
RECIPES_DIR = "recipes"
#: Frontmatter lines that are this person's, not the recipe's.
PERSONAL_KEYS = ("learned", "tool", "shared")
#: A file bigger than this is not a recipe's script; it is something else.
MAX_FILE_BYTES = 200_000

_EMAIL = re.compile(REDACT_PRESETS["email"])
#: A token worth looking for: an entity id, an address, a number with a
#: unit, a path -- anything with a digit or a joiner in it.
_VALUE = re.compile(r"[A-Za-z0-9][\w.@:/+-]*\w")


@dataclass(frozen=True, slots=True)
class Finding:
    """One reason a recipe cannot leave as it is."""

    file: str
    line: int
    why: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.why}"


@dataclass(slots=True)
class Prepared:
    """A recipe read for sharing: what would be written, and what stops it."""

    name: str
    files: dict[str, str] = field(default_factory=dict)
    problems: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def digest(self) -> str:
        return recipe_hash(self.files)


def recipe_hash(files: dict[str, str]) -> str:
    """sha256 over every file, in path order: what a catalog pins."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + files[path].encode() + b"\0")
    return "sha256:" + h.hexdigest()


def without_keys(text: str, keys: tuple[str, ...] = PERSONAL_KEYS) -> str:
    """SKILL.md with the named frontmatter keys (and their folded
    continuation lines) removed; the body is untouched."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    out, dropping, in_header = [lines[0]], False, True
    for line in lines[1:]:
        if in_header and line.strip() == "---":
            in_header, dropping = False, False
            out.append(line)
            continue
        if in_header:
            if line.startswith((" ", "\t")) and dropping:
                continue
            key = line.split(":", 1)[0].strip().lower()
            dropping = key in keys
            if dropping:
                continue
        out.append(line)
    return "".join(out)


def memory_values(statements: list[str]) -> dict[str, str]:
    """Distinctive tokens in what memory holds -> the statement they came
    from. An entity id, an address, a number: things a recipe should take
    as an input, never carry. Words are left alone, capitalised or not --
    "Home Assistant" is in every fan recipe -- and a name is what the
    trace's own name list and reader are for (``redact``)."""
    found: dict[str, str] = {}
    for statement in statements:
        for token in _VALUE.findall(statement):
            valued = any(c.isdigit() for c in token) or any(c in token for c in "._@/:")
            if valued and len(token) >= 4:
                found.setdefault(token, statement)
    return found


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _scan(rel: str, text: str, *, redact: re.Pattern[str] | None,
          values: dict[str, str], home: str, user: str) -> list[Finding]:
    from yantra.skills.learn import REDACTED, scrub

    out: list[Finding] = []
    for n, (before, after) in enumerate(zip(text.splitlines(),
                                            scrub(text).splitlines(), strict=False), 1):
        if before != after and REDACTED in after and REDACTED not in before:
            out.append(Finding(rel, n, "a secret-looking value (a token, a key, "
                                       "an Authorization header)"))
    for match in _EMAIL.finditer(text):
        out.append(Finding(rel, _line_of(text, match.start()),
                           f"an email address ({match.group(0)})"))
    if redact is not None:
        for match in redact.finditer(text):
            out.append(Finding(rel, _line_of(text, match.start()),
                               f"matches your trace redaction ({match.group(0)!r})"))
    for token, statement in values.items():
        pattern = re.compile(rf"(?<![\w.]){re.escape(token)}(?![\w])",
                             0 if any(c.isdigit() for c in token) else re.IGNORECASE)
        for match in pattern.finditer(text):
            out.append(Finding(rel, _line_of(text, match.start()),
                               f"a value you told it: {token!r} (from memory: "
                               f"\"{statement}\") -- make it an input"))
    if home and home not in ("/", "") and home in text:
        out.append(Finding(rel, _line_of(text, text.index(home)),
                           f"your home folder ({home})"))
    if user and len(user) >= 3:
        for match in re.finditer(rf"(?<![\w]){re.escape(user)}(?![\w])", text):
            out.append(Finding(rel, _line_of(text, match.start()),
                               f"your login name ({user})"))
    return out


def prepare(skill: Skill, *, memories: list[str] | None = None,
            link: Any = None, redact: re.Pattern[str] | None = None,
            home: str | None = None, user: str | None = None) -> Prepared:
    """Read a learned skill's folder for sharing. Writes nothing.

    ``memories`` are the person's remembered statements (None: memory not
    reachable, said in a note); ``link`` is Setu's last report, for the
    needs check; ``redact`` the session's trace redaction.
    """
    from yantra.setu_link import resolve_needs

    out = Prepared(name=skill.name)
    if not skill.is_learned:
        out.problems.append(Finding(SKILL_FILE, 1, "not a learned skill: one a person "
                                                   "wrote is already a file to share"))
        return out
    folder = skill.path.parent
    home = str(Path.home()) if home is None else home
    if user is None:
        try:
            user = getpass.getuser()
        except (OSError, KeyError):
            user = ""
    values = memory_values(memories or [])
    if memories is None:
        out.notes.append("memory was not reachable, so values you told it were not "
                         "checked")
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if "__pycache__" in path.parts or rel.startswith("."):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            out.problems.append(Finding(rel, 1, f"{path.stat().st_size} bytes is not a "
                                                f"recipe's file; leave it out"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            out.problems.append(Finding(rel, 1, "not a text file; a recipe is steps "
                                                "and a script"))
            continue
        if rel == SKILL_FILE:
            text = without_keys(text)
        out.files[rel] = text
        out.problems += _scan(rel, text, redact=redact, values=values,
                              home=home, user=user or "")
    if skill.tool:
        out.notes.append(f"left out tool: {skill.tool_name} -- whoever installs it "
                         f"decides whether it becomes a tool")
    for need in resolve_needs(skill.needs, link):
        if link is None:
            out.notes.append(f"Setu was not found, so setu:{need.connector} was not "
                             f"checked against its connectors")
        elif not need.known:
            out.problems.append(Finding(SKILL_FILE, 1, f"needs setu:{need.connector}, "
                                                       f"which Setu has no connector for"))
    return out


def write(prepared: Prepared, root: Path) -> Path:
    """Write a recipe that passed every check to ``root/recipes/<name>/``."""
    if not prepared.ok:
        raise SkillError(f"{prepared.name} has {len(prepared.problems)} problem(s); "
                         f"nothing written")
    target = root / RECIPES_DIR / prepared.name
    if target.exists():
        shutil.rmtree(target)
    for rel, text in prepared.files.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return target


def read_recipe(source: Path) -> tuple[Skill, dict[str, str]]:
    """A shared recipe's skill and every text file in it, for the host to
    show before asking. SkillError when it is not one."""
    source = source.expanduser().resolve()
    if source.is_file() and source.name == SKILL_FILE:
        source = source.parent
    if not (source / SKILL_FILE).is_file():
        raise SkillError(f"no {SKILL_FILE} in {source}")
    skill = load_skill(source / SKILL_FILE, source="shared")
    if skill.origin != "learned":
        raise SkillError(f"{skill.name} is not a recipe (origin: learned); copy a "
                         f"hand-written skill into a skills folder instead")
    files = {}
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = path.relative_to(source).as_posix()
        if "__pycache__" in path.parts or rel.startswith("."):
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise SkillError(f"{rel} is not a text file; a recipe is steps and a "
                             f"script") from None
    files[SKILL_FILE] = without_keys(files[SKILL_FILE])
    return skill, files


def install(source: Path, *, home: Path | None = None,
            today: str | None = None) -> Skill:
    """Install a shared recipe into the person's own learned folder.

    The host has shown every file and the person said yes. Same name
    already there: the same recipe is a no-op, a different one an error
    -- installing never overwrites something you had.
    """
    skill, files = read_recipe(source)
    digest = recipe_hash(files)
    target = learned_root("user", home=home) / skill.name
    if target.exists():
        try:
            mine = load_skill(target / SKILL_FILE, source="learned-user")
        except (OSError, SkillError):
            mine = None
        if mine is not None and _shared_hash(target) == digest:
            return mine
        raise SkillError(f"you already have a skill named {skill.name} "
                         f"({target}); remove or rename it first")
    today = today or date.today().isoformat()
    for rel, text in files.items():
        if rel == SKILL_FILE:
            text = _with_lines(text, [f"shared: {digest}", f"learned: {today}"])
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return load_skill(target / SKILL_FILE, source="learned-user")


def _with_lines(text: str, lines: list[str]) -> str:
    """Add frontmatter lines just before the closing fence."""
    head, sep, rest = text.partition("\n---")
    return head + "\n" + "\n".join(lines) + sep + rest


def _shared_hash(folder: Path) -> str:
    for line in (folder / SKILL_FILE).read_text(encoding="utf-8").splitlines():
        if line.startswith("shared:"):
            return line.split(":", 1)[1].strip()
    return ""


__all__ = ["Finding", "Prepared", "RECIPES_DIR", "install", "memory_values",
           "prepare", "read_recipe", "recipe_hash", "without_keys", "write"]
