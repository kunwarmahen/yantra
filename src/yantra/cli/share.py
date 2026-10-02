"""Sharing and installing a recipe, for the terminal (skills/share.py).

Two ways in, one path: ``/skills share NAME`` inside a session, and
``yantra --skill-share NAME`` without one -- no model, no key, like
``--mcp-login``. Either way the checks see the person's memories, Setu's
connectors and the trace's redaction, when they can be reached, and say
so when they cannot.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from yantra.skills.loader import SkillError


def share_skill(name: str, skills: Any, agent: Any, console: Console, cwd: Path, *,
                memories: list[str] | None = None, link: Any = None,
                redact: re.Pattern[str] | None = None) -> int:
    """Check one learned skill and write it to ``cwd/recipes/NAME/``.
    0 when written, 1 when a check stopped it or it was not found."""
    from yantra.skills.share import prepare, write

    skill = skills.get(name)
    if skill is None:
        console.print(f"[red]no skill {name!r}[/red]")
        return 1
    if agent is not None:
        memory = getattr(agent, "memory", None)
        if memories is None and memory is not None:
            try:
                memories = [item.statement for item in memory.list(500)]
            except Exception:  # a store that is down: said in a note
                memories = None
        redact = redact if redact is not None else getattr(memory, "redact", None)
        setu = getattr(agent, "setu", None)
        link = link if link is not None else getattr(setu, "link", None)
    prepared = prepare(skill, memories=memories, link=link, redact=redact)
    files = ", ".join(prepared.files) or "nothing"
    console.print(f"{escape(skill.name)}: {files}", markup=True)
    for note in prepared.notes:
        console.print(f"[dim]  {escape(note)}[/dim]")
    if not prepared.ok:
        console.print(f"[red]not shared -- {len(prepared.problems)} problem(s):[/red]")
        for finding in prepared.problems:
            console.print(f"  {escape(str(finding))}", markup=True)
        console.print("[dim]  fix each in the skill's own folder (a value of yours "
                      "becomes an input), then share again[/dim]")
        return 1
    target = write(prepared, cwd)
    console.print(f"[green]written to {escape(str(target))}[/green]\n"
                  f"[dim]  {prepared.digest}\n"
                  f"  from a checkout of the Setu catalog, that is where a pull "
                  f"request wants it; or hand the folder to someone, who runs "
                  f"yantra --skill-install {escape(str(target))}[/dim]")
    return 0


def install_recipe(source: Path, console: Console,
                   ask: Callable[[str], bool] | None) -> int:
    """Show every file of a shared recipe, ask, and install it."""
    from yantra.skills.share import install, read_recipe, recipe_hash

    try:
        skill, files = read_recipe(source)
    except (OSError, SkillError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return 1
    console.print(f"[bold]{escape(skill.name)}[/bold] -- {escape(skill.description)}")
    if skill.needs:
        console.print(f"needs: {escape(skill.needs)}")
    for rel, text in files.items():
        console.print(f"\n[bold]── {escape(rel)}[/bold]")
        console.print(text, markup=False, highlight=False)
    console.print(f"[dim]{recipe_hash(files)}[/dim]")
    question = (f"install {skill.name}? Its script runs in your sessions, in the "
                f"sandbox, when a task calls for it")
    if ask is None:
        console.print("[yellow]not installed: run it in a terminal to answer[/yellow]")
        return 1
    if not ask(question):
        console.print("[dim]not installed[/dim]")
        return 1
    try:
        installed = install(source)
    except (OSError, SkillError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return 1
    console.print(f"[green]installed {escape(installed.name)}[/green] -- "
                  f"{escape(str(installed.path.parent))}")
    return 0


def person_context(args: Any, console: Console) -> tuple[list[str] | None, Any,
                                                          re.Pattern[str] | None]:
    """What the checks look at when no session is running: memory on the
    built-in store, Setu's report, the trace redaction flags."""
    from yantra import setu_link
    from yantra.memory import memory_mode, resolve_user
    from yantra.trace import _compile_redactions, read_word_list

    memories: list[str] | None = None
    try:
        mode, user = memory_mode(getattr(args, "memory", None)), resolve_user()
        if mode == "local" and user:
            from yantra.memory.local import LocalStore
            memories = [i.statement for i in LocalStore().list(user, 500)]
        elif mode not in ("off", "local"):
            console.print(f"[dim]memory lives behind the {mode} server; start a "
                          f"session and use /skills share to check against it[/dim]")
    except Exception as exc:  # unreadable memory: said, never fatal
        console.print(f"[yellow]memory not read: {escape(str(exc))}[/yellow]")
    link = None
    try:
        mode, path = setu_link.resolve_mode(getattr(args, "setu", None))
        link = setu_link.load(mode, path)
    except setu_link.SetuLinkError as exc:
        console.print(f"[yellow]setu: {escape(str(exc))}[/yellow]")
    words = [w for p in (getattr(args, "trace_redact_words", None) or [])
             for w in read_word_list(p)]
    redact = _compile_redactions(list(getattr(args, "trace_redact", None) or []), words)
    return memories, link, redact
