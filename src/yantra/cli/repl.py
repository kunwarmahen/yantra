"""The REPL: input loop, slash commands, and Ctrl-C semantics.

Ctrl-C contract (the part every interactive harness gets wrong once):
  * at the prompt      -> clear the line, keep the session
  * during a turn      -> cancel the TURN, never the session; the agent's
                          resumable-history cleanup runs via stream.close()
  * Ctrl-D / /quit     -> exit
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.status import Status

from yantra.agent import Agent, TurnEnd
from yantra.builder import BUILD_SYSTEM, BuildSpec, run_build
from yantra.cli.render import Renderer
from yantra.config import default_model, load_settings
from yantra.context import RED, estimate_history
from yantra.errors import ImageError, RateLimitError, UserUnavailable
from yantra.images import load_image_block
from yantra.memory.reflect import (Candidate, decline, keep, mark_reviewed,
                                   reflect)
from yantra.pricing import is_free, session_cost
from yantra.prompt import recompose
from yantra.trace import watch
from yantra.types import ImageBlock
from yantra.permissions import (REFUSED_USER, PermissionRequest,
                                SwitchableGate, refuse, yolo)
from yantra.providers import get_provider
from yantra.sandbox import ToolSandbox
from yantra.session import SessionStore, apply_payload
from yantra.skills.learn import SCOPES, Learner, Offer
from yantra.skills.loader import PROMOTE_AFTER, SKILL_FILE, skill_roots
from yantra.tools import default_registry

HELP = """[bold]commands[/bold]
  /help              this text
  /model [slug]      show or hot-swap the model
  /provider [name]   show or switch provider (history survives -- internal types!)
  /tools             list registered tools and their schemas ([off] = pulled)
  /tools off|on NAME pull or restore tools MID-SESSION (globs ok:
                     browser_*, mcp__slack__*); applies to the very next call
  /env [off|local|full]
                     show what the agent auto-detected about your machine/
                     location, or switch session awareness (bare = show;
                     full adds your city via one public-IP lookup)
  /skills            list skills on disk (loaded ones marked, broken ones
                     explained); /skills reload re-scans after you edit one
  /skills NAME       show one skill's instructions without spending a turn
  /skills off|on NAME
                     pull or restore skills MID-SESSION (globs ok: deploy-*)
                     — same switch as /tools, applied to the roster
  /skills tool NAME  make a learned skill's script a tool of its own (one
                     call next time; tested and shown to you first)
  /NAME ...          run a skill directly: /pr-review the auth branch
  /learn             save what the last turn did as a skill (it is tested
                     and shown to you first; --learn off stops the offers)
  /memory            what the agent remembers about you across
                     conversations (* = in this conversation's prompt)
  /memory forget ID  remove one -- a fact that stopped being true
  /memory add TEXT   tell it something to keep, in your own words
  /memory find WORDS search what is remembered
  /remember          look back over this conversation now for things
                     worth keeping about you (it also happens by itself on
                     /quit, /clear and /load; you keep or drop each)
  /mcp               list connected mcp servers
  /mcp add ...       connect a server MID-SESSION: /mcp add NAME URL, or
                     /mcp add NAME COMMAND [ARGS...] (asks whether to save)
  /mcp off|on NAME   soft-switch one server's whole toolset (stays warm)
  /mcp remove NAME   disconnect it and pull its tools (forgets saved entry)
  /build SPEC        build a project from SPEC in a scratch workspace,
                     then independently verify it (BUILD GREEN/RED)
  /history           dump the conversation so far
  /usage             session token + dollar totals (+ context pressure)
  /save [name]       checkpoint this session (SQLite, append-only versions)
  /load [name]       restore the newest checkpoint of a session
  /compact           force context compaction now (auto-fires at 80%)
  /clear             reset history (keeps provider/model)
  /yolo [on|off]     flip permission prompts: bypass everything, or ask
                     again (bare /yolo toggles; applies mid-turn)
  /image PATH...     stage image(s) onto your NEXT message ("clear" unstages)
  /quit              exit

  //text             send a message starting with a literal slash
  trailing \\        continue the same message on the next line
  ctrl-c             cancel current turn (or clear the prompt line)
  permission prompts offer y/n/e -- 'e' edits the tool call before it runs
  (--resume loads the newest checkpoint at startup)"""


#: An edit round: amend the pending arguments, or cancel. Raises ValueError
#: with a human-readable message when the edit can't be parsed.
EditFn = Callable[[dict[str, Any]], "dict[str, Any] | None"]


def terminal_editor(args: dict[str, Any]) -> dict[str, Any] | None:
    """The default EditFn: $EDITOR on a temp file when interactive,
    a single input() line otherwise (piped sessions, tests).

    Returns the amended args dict, None to cancel. Bad JSON raises
    ValueError (json.JSONDecodeError already is one).
    """
    pretty = json.dumps(args, indent=2)
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor and sys.stdin.isatty() and sys.stdout.isatty():
        fd, path = tempfile.mkstemp(suffix=".json", prefix="yantra-edit-")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(pretty + "\n")
            if subprocess.run([editor, path]).returncode != 0:
                return None  # editor died -- treat as cancel, not denial
            raw = Path(path).read_text()
        finally:
            os.unlink(path)
    else:
        raw = input(f"edited args JSON ({json.dumps(args)})> ").strip()
        if not raw:
            return None  # empty answer = cancel
    edited = json.loads(raw)
    if not isinstance(edited, dict):
        raise ValueError("edited arguments must be a JSON object")
    return edited


def confirm_gate(console: Console, editor: EditFn | None = None):
    """The CLI's PermissionFn: show exactly what will happen, default No --
    'e' amends the call before approving (approve-with-edits), and 's'
    refuses it in your own words.

    The loop: preview -> y/n/e/s. Editing swaps ``request.arguments`` for
    the amended dict, re-renders the summary through the tool's own
    ``summary()`` (pre-bound by the agent loop as ``summarize``), tags
    the panel *(edited)*, and asks again -- so what you approve is what
    runs, now literally. A cancelled or unparseable edit never denies:
    it returns to the prompt.

    's' IS THE ANSWER THAT IS NOT YES OR NO. "Not like that, like this"
    was sayable only through the editor, which asks a person to write a
    valid arguments dict by hand -- fine for a path, hopeless for "use
    the staging database instead". Saying it in English costs one line
    and hands the model something it can act on; the sentence reaches it
    verbatim, attributed, in place of "Permission denied by user."
    (notes/56).

    Read-only tools never prompt -- a tool that declared itself
    side-effect-free (the same flag allow_read_only trusts) has nothing
    to confirm, and a discovery hatch behind a permission wall would
    defeat the selection loop that pins it.
    """
    edit = editor or terminal_editor

    def gate(request: PermissionRequest) -> bool:
        if request.read_only:
            return True
        edited = False
        while True:
            console.print()
            console.print(Panel(
                request.summary,
                title=f"approve {request.tool_name}(){' (edited)' if edited else ''}?",
                border_style="yellow", title_align="left"))
            answer = Prompt.ask("run it?", choices=["y", "n", "e", "s"],
                                default="n").lower()
            if answer == "y":
                return True  # edits ride along: the loop adopts them
            if answer == "s":
                said = console.input("no, because (or why not): ").strip()
                if not said:
                    # An empty sentence is a plain no, not a refusal that
                    # says nothing: the model reading "" would be worse
                    # off than the model reading the default.
                    return refuse(request,
                                  f"{request.tool_name} was denied: you "
                                  f"said no at the approval prompt.",
                                  code=REFUSED_USER)
                # ATTRIBUTED, always. The model has to be able to tell a
                # person's instruction from the harness's own voice --
                # one is worth arguing with and the other is not.
                return refuse(request,
                              f"{request.tool_name} was denied. The person "
                              f"said: {said}",
                              code=REFUSED_USER)
            if answer == "n":
                # The one gate where the default sentence is TRUE -- a
                # person was asked and said no. It still carries a code,
                # so a caller counting refusals never has to guess which
                # of them had a human behind them.
                return refuse(request,
                              f"{request.tool_name} was denied: you said "
                              f"no at the approval prompt.",
                              code=REFUSED_USER)
            try:
                amended = edit(request.arguments)
            except ValueError as exc:
                console.print(f"[red]bad edit: {exc} -- try again[/red]")
                continue
            if amended is None:  # cancelled -- back to the prompt
                continue
            request.arguments = amended
            try:
                request.summary = (request.summarize(amended) if
                                   request.summarize else
                                   f"{request.tool_name}({json.dumps(amended)})")
            except Exception:
                # A broken summary() must not trap the approval flow.
                request.summary = f"{request.tool_name}({json.dumps(amended)})"
            edited = True

    return gate


def _chosen(answer: str, found: list[Candidate]) -> list[Candidate]:
    """``a`` keeps all; numbers keep those; anything else keeps none."""
    answer = answer.strip().lower()
    if answer in ("a", "all", "y", "yes"):
        return list(found)
    picked = []
    for token in answer.replace(",", " ").split():
        if token.isdigit() and 1 <= int(token) <= len(found):
            candidate = found[int(token) - 1]
            if candidate not in picked:
                picked.append(candidate)
    return picked


class Repl:
    def __init__(self, agent: Agent, console: Console,
                 store: SessionStore | None = None,
                 input_fn: Callable[[str], str] | None = None,
                 sandbox: ToolSandbox | None = None,
                 mcp: Any | None = None,
                 trace: Any | None = None) -> None:
        self.agent = agent
        #: A TrajectoryLog when the operator asked for one (--trace), so
        #: a turn that goes wrong leaves something a regression case can
        #: be built from (notes/57). None is the usual case and costs
        #: nothing.
        self.trace = trace
        self.console = console
        self.store = store
        # MCPManager when the entrypoint wired one (None in tests/embedders
        # -- /mcp then reports instead of crashing).
        self.mcp = mcp
        # Injectable so tests can feed lines without a real terminal.
        self._input = input_fn or input
        self.renderer = Renderer(console)
        # bash confinement for /build children (None = the tools' default).
        self.sandbox = sandbox
        # StreamEvents are PUSHED here while each model response streams;
        # ToolExecuted/TurnEnd still arrive as yielded events below. The
        # indirection also owns the per-turn spinner: deltas must drop it
        # as they arrive -- see _on_stream_event.
        agent.on_stream_event = self._on_stream_event
        # The dead-air spinner while one is installed (run_turn).
        self._spinner: Status | None = None
        # Images staged by /image, consumed by the NEXT turn (validated at
        # attach time so a bad path errors immediately, not mid-conversation).
        self._pending_images: list[ImageBlock] = []
        #: How the last turn ended -- what /learn needs when no Learner
        #: watched it (learning off). "" before the first turn.
        self._last_end = ""

    # ---- main loop ---------------------------------------------------------

    def run(self) -> None:
        self._banner()
        while True:
            try:
                line = self._read_line()
            except EOFError:
                self.console.print()
                self.end_conversation()
                return
            except KeyboardInterrupt:
                self.console.print()  # clear the line, keep the session
                continue

            if not line:
                continue
            if line.startswith("//"):  # escape hatch for literal slashes
                pass
            elif line.startswith("/"):
                if self._command(line):
                    return  # /quit
                continue

            try:
                self.run_turn(line, images=self._take_pending_images())
            except KeyboardInterrupt:
                # The generator below was interrupted mid-pull; closing it
                # runs the agent's outstanding-call synthesis.
                self.console.print("\n[yellow](cancelled)[/yellow]")
            except UserUnavailable as exc:
                # The model tried to consult its human and nobody was home
                # (piped session, or stdin hit EOF). Turn failed, history
                # resumable -- the session itself survives.
                self.console.print(f"\n[red]turn failed: {exc}[/red]")
            except RateLimitError as exc:
                wait = f" retry after {exc.retry_after:.0f}s" if exc.retry_after else ""
                self.console.print(f"\n[red]rate limited.{wait}[/red]")

    def _read_line(self) -> str:
        """One LOGICAL line of input. ``input()`` is line-oriented and we
        don't want a readline/curses dependency just for pasting code or
        multi-paragraph prompts -- so a trailing backslash simply continues
        on a ``... `` prompt. Exactly one backslash is consumed per line,
        and continuation lines keep their leading indentation (pasted
        code must survive)."""
        parts: list[str] = []
        # The prompt itself carries the permission mode -- bypassing is
        # precisely when you want a standing reminder of it. (Tests inject
        # input_fn and never see this string.)
        line = self._input(
            "yolo> " if self._gate_mode() == "yolo" else "> ").strip()
        while True:
            continues = line.endswith("\\")
            if continues:
                line = line[:-1]
            parts.append(line)
            if not continues:
                return "\n".join(parts)
            line = self._input("... ").rstrip()  # keep indentation

    def _on_stream_event(self, event) -> None:
        """Push-channel entry: drop the dead-air spinner at the FIRST
        streamed fragment, then paint.

        The spinner must stop HERE, not in run_turn's pull loop -- deltas
        never pass through that loop (they are pushed), so stopping there
        meant a tool-free reply streamed ENTIRELY inside an active rich
        Live region, whose repaints erase the partial lines they wrap.
        On a real terminal that rendered as an empty reply.
        """
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        self.renderer(event)

    def run_turn(self, user_input: str,
                 *, images: list[ImageBlock] | None = None,
                 learn: bool = True) -> None:
        """One user turn, fully rendered. Public because one-shot mode and
        the REPL share this exact path. ``images`` (loaded via
        yantra.images) ride along in the same user message -- how
        ``yantra --image shot.png "what is this?"`` works.

        ``learn=False`` counts the turn against any learned skill it used
        but never offers to save it -- a turn that ran a skill by name
        already had its recipe."""
        stream = self.agent.run_streaming(user_input, images=images)
        if self.trace is not None:
            # A TEE, not a consumer: the renderer still sees every event
            # (trace.watch). The turn is recorded when it ends, including
            # when it ends badly.
            stream = watch(user_input, stream, self.trace.record,
                           provider=getattr(self.agent.provider, "name", ""),
                           model=self.agent.model, detail=self.trace.detail,
                           spawner=getattr(self.agent, "subagents", None))
        spinner = self.console.status("[dim]… connecting[/dim]", spinner="dots")
        spinner.start()
        self._spinner = spinner  # _on_stream_event drops it at first delta
        end: TurnEnd | None = None
        try:
            for event in stream:  # ToolExecuted / TurnEnd only
                self.renderer(event)
                if isinstance(event, TurnEnd):
                    end = event
        except BaseException:
            # Deterministic cleanup on ANY abnormal exit (including
            # KeyboardInterrupt raised mid-pull): closing the generator
            # runs the agent's outstanding-call synthesis -- and, through
            # the tee, records the turn that was abandoned.
            stream.close()
            raise
        finally:
            # Died before ANY event arrived (connection refused, instant
            # provider error) -- nothing else ever got the chance to stop.
            if self._spinner is not None:
                self._spinner.stop()
                self._spinner = None
        if end is not None:
            self._last_end = end.reason
            self._after_turn(end, learn=learn)

    # ---- learning (notes/96) --------------------------------------------------

    def _after_turn(self, end: TurnEnd, *, learn: bool) -> None:
        """Count learned skills the turn used; offer to save what it solved.

        Nothing here may cost the answer that was just printed: a failure
        is one yellow line, and Ctrl-C is a no to the offer, not a
        cancelled turn.
        """
        learner = getattr(self.agent, "learner", None)
        try:
            if learner is not None:
                for name, worked in learner.after_turn(end):
                    self.console.print(self._counted_line(name, worked),
                                       markup=False, style="dim")
                    self._suggest_tool(name, worked)
                if learn and end.reason == "end_turn":
                    self._offer_skill(learner, forced=False)
        except KeyboardInterrupt:
            self.console.print("\n[yellow](not saved)[/yellow]")
        except Exception as exc:
            self.console.print(f"[yellow]learning skipped: {exc}[/yellow]")
        self._offer_pending_memories()

    def _offer_pending_memories(self) -> None:
        """Facts found mid-turn -- before compaction, or by a skill's
        write-up -- with nobody to ask then. Asked after the skill's
        question, so the two are read in the order they were found."""
        memory = getattr(self.agent, "memory", None)
        if memory is not None and memory.pending:
            found, memory.pending = list(memory.pending), []
            self._offer_memories(found)

    def _suggest_tool(self, name: str, worked: bool) -> None:
        """The one line the turn that reaches PROMOTE_AFTER in a row prints.
        A suggestion only: promoting is the person's call (notes/98)."""
        skills = getattr(self.agent, "skills", None)
        skill = skills.get(name) if skills is not None else None
        if (not worked or skill is None or not skill.suggest_tool
                or skill.learned.in_a_row != PROMOTE_AFTER):
            return
        self.console.print(
            f"skill {name} has worked {PROMOTE_AFTER} times in a row. Make "
            f"its script a tool, so next time is one call? /skills tool {name}",
            markup=False, style="cyan")

    def _counted_line(self, name: str, worked: bool) -> str:
        """What one use did to a learned skill's record, in one line --
        including how close a failing one is to being set aside."""
        if worked:
            return f"skill {name}: this use counted as worked"
        skills = getattr(self.agent, "skills", None)
        skill = skills.get(name) if skills is not None else None
        record = skill.learned if skill is not None else None
        if record is None:
            return f"skill {name}: this use counted as failed"
        if record.stale:
            return (f"skill {name}: failed {record.failing} times in a row -- "
                    f"set aside until a fresh solve repairs it")
        return (f"skill {name}: this use counted as failed "
                f"({record.failing} in a row; set aside at 3)")

    def _memory_command(self, arg: str) -> None:
        """``/memory``: read and correct what is remembered about you.

        Local and free -- no model turn. What changes here reaches the
        prompt at the next conversation (/clear), not mid-conversation:
        the layer is filled once so the cached prefix survives.
        """
        memory = getattr(self.agent, "memory", None)
        if memory is None:
            self.console.print("[yellow]memory is off for this session "
                               "(--memory off, a package that did not ask, "
                               "or no identity -- set YANTRA_USER)[/yellow]")
            return
        verb, _, rest = arg.partition(" ")
        rest = rest.strip()
        try:
            if verb == "forget":
                if not rest:
                    self.console.print("[red]usage: /memory forget ID[/red]")
                elif memory.forget(rest):
                    self.console.print(f"[green]forgot #{rest}[/green] -- "
                                       "gone from the next conversation on")
                else:
                    self.console.print(f"[red]no memory #{rest}[/red]")
                return
            if verb == "add":
                if not rest:
                    self.console.print("[red]usage: /memory add TEXT[/red]")
                    return
                memory_id = memory.remember(rest, kind="fact")
                self.console.print(f"[green]remembered #{memory_id}[/green]")
                return
            if verb == "find":
                items = memory.recall(rest, 20) if rest else []
                title = f"{len(items)} match {rest!r}"
            elif verb:
                self.console.print("[red]usage: /memory [forget ID | add TEXT "
                                   "| find WORDS][/red]")
                return
            elif "list" in memory.cannot():
                self.console.print(
                    f"memory: {memory.store.name}, for {memory.user} -- this "
                    f"store cannot list what it keeps; /memory find WORDS "
                    f"searches it", markup=False)
                return
            else:
                items = memory.list(100)
                title = f"{len(items)} remembered"
        except Exception as exc:  # a store's own failure, said plainly
            self.console.print(f"[red]memory ({memory.store.name}): "
                               f"{exc}[/red]")
            return
        shown = {item.id for item in memory.in_prompt}
        lines = [f"memory: {memory.store.name}, for {memory.user} -- {title}"]
        for item in items:
            mark = "*" if item.id in shown else " "
            where = f"  [{item.package}]" if item.package else ""
            lines.append(f" {mark} #{item.id}  {item.statement}{where}")
        if not items and not verb:
            lines.append("  nothing yet -- tell the agent about yourself, or "
                         "/memory add TEXT")
        if cannot := memory.cannot():
            lines.append(f"  (this store cannot {' or '.join(cannot)})")
        # PLAIN: a statement is the person's (or model's) words, and may
        # hold rich markup that would be swallowed.
        self.console.print("\n".join(lines), markup=False)

    # ---- the look back at a conversation's end (memory/reflect.py) ----------

    def end_conversation(self, *, forced: bool = False) -> None:
        """Look back over this conversation for facts about the person.

        On /quit, Ctrl-D, /clear, /load and the end of a one-shot run;
        ``forced`` is /remember, which looks even with --reflect off (the
        flag stops the automatic look; asking is still allowed). Nothing
        here may cost the session: a failure is one yellow line, and
        Ctrl-C is a no.
        """
        memory = getattr(self.agent, "memory", None)
        if memory is None:
            if forced:
                self.console.print("[yellow]memory is off for this session, "
                                   "so there is nothing to keep it in[/yellow]")
            return
        if memory.reflect == "off" and not forced:
            return
        found, memory.pending = list(memory.pending), []
        try:
            with self.console.status("[dim]… looking back over the "
                                     "conversation[/dim]", spinner="dots"):
                found += [c for c in reflect(self.agent) if c not in found]
        except KeyboardInterrupt:
            self.console.print("[yellow](not looked back over)[/yellow]")
            return
        except Exception as exc:
            self.console.print(f"[yellow]looking back skipped: {exc}[/yellow]")
            return
        if not found:
            if forced:
                self.console.print("[dim]nothing new worth remembering about "
                                   "you in this conversation[/dim]")
            return
        self._offer_memories(found, auto=memory.reflect == "auto" and not forced)

    def _offer_memories(self, found: list[Candidate], *, auto: bool = False) -> None:
        """Keep all, some (by number), or none -- the default is none."""
        memory = self.agent.memory
        if auto:
            chosen = found
        else:
            lines = ["worth remembering about you, for later conversations?"]
            lines += [f"  {i}. {c.kind}: {c.statement}"
                      for i, c in enumerate(found, 1)]
            self.console.print("\n".join(lines), markup=False)
            try:
                answer = self._input("keep [a]ll, numbers (1 3), or [N]one > ")
            except (EOFError, KeyboardInterrupt):
                answer = ""
            chosen = _chosen(answer, found)
            decline(memory, [c for c in found if c not in chosen])
        try:
            ids = keep(memory, chosen)
        except Exception as exc:
            self.console.print(f"[red]memory ({memory.store.name}): {exc}; "
                               f"nothing kept[/red]")
            return
        if ids:
            self.console.print(f"[green]remembered {len(ids)}[/green] "
                               f"[dim](#{', #'.join(ids)} -- from the next "
                               f"conversation on; /memory to see or "
                               f"forget)[/dim]")
        else:
            self.console.print("[dim]nothing kept[/dim]")

    def _learn_command(self) -> None:
        """``/learn``: the person asks for the last turn to be saved.

        Works with learning off (the flag stops OFFERS; asking is still
        allowed) -- a Learner is made for the one request.
        """
        if getattr(self.agent, "skills", None) is None:
            self.console.print("[yellow]skills are off for this session "
                               "(--no-skills), so there is nowhere to save "
                               "one[/yellow]")
            return
        learner = getattr(self.agent, "learner", None)
        if learner is None:
            learner = Learner(self.agent, "ask")
            learner.last_reason = self._last_end
        try:
            self._offer_skill(learner, forced=True)
        except KeyboardInterrupt:
            self.console.print("\n[yellow](not saved)[/yellow]")
        self._offer_pending_memories()

    def _offer_skill(self, learner: Learner, *, forced: bool) -> None:
        offer = learner.consider(
            forced=forced,
            progress=lambda text: self.console.print(f"[dim]· {text}[/dim]"))
        if offer is None:
            if forced and learner.last_skip:
                self.console.print(f"nothing saved: {learner.last_skip}",
                                   markup=False, style="yellow")
            return
        if learner.mode == "auto" and not forced:
            skill = learner.save(offer)
            self.console.print(f"[green]saved skill {skill.name}[/green] "
                               f"[dim]({skill.directory})[/dim]")
            return
        self._ask_to_save(learner, offer)

    def _show_offer(self, offer: Offer, scope: str) -> None:
        """The save question's body: everything that would be written.
        PLAIN text for the file contents -- a skill is full of brackets
        that rich would read as markup."""
        draft, view = offer.draft, offer.view(Path(self.agent.ctx.cwd))
        print_ = self.console.print
        print_()
        if offer.repairs is not None:
            print_("[bold]Update this skill?[/bold]")
            print_(f"  {draft.name} failed this time -- {offer.failure}",
                   markup=False)
            print_("  the task was then finished another way; this is that "
                   "way, written over the saved recipe")
        else:
            print_("[bold]Save this as a skill?[/bold]")
        print_(f"  {draft.name} -- {draft.description}", markup=False)
        if offer.renamed_from:
            print_(f"  (renamed: a skill you wrote is already called "
                   f"{offer.renamed_from})", markup=False)
        print_(f"  scope:  {SCOPES[scope]} -> {view['scopes'][scope]['path']}",
               markup=False)
        print_(f"  needs:  {draft.needs or 'nothing'}", markup=False)
        for need in offer.connections:
            print_(f"          {need.name}: " + (
                "connected" if need.connected else
                "not connected" if need.known else
                "no such connector in Setu here"), markup=False)
        print_(f"  inputs: {draft.inputs or 'none'}", markup=False)
        if offer.tested is None:
            print_("  tested: no script, so nothing to run")
        else:
            print_(f"  tested: passed ({offer.test_runs} run"
                   f"{'s' if offer.test_runs > 1 else ''}) -- $ {draft.test}",
                   markup=False)
        print_(f"  cost:   {offer.spent.input_tokens:,} in / "
               f"{offer.spent.output_tokens:,} out tokens to write and test")
        if offer.tool:
            print_(f"  tool:   {offer.tool} stays -- the fixed script takes the "
                   f"same arguments" if offer.keeps_tool else
                   f"  tool:   {offer.tool} goes -- the fixed script's "
                   f"arguments changed; it is a recipe again until it has "
                   f"worked {PROMOTE_AFTER} times in a row", markup=False)
        if offer.repairs is not None:
            # An update is read as a change: the diff, not two whole files.
            # [e]dit first still opens the whole proposed files.
            print_(f"[dim]── changes {'─' * 54}[/dim]")
            print_(offer.diff().rstrip() or "(no change to the files)",
                   markup=False, highlight=False)
            print_(f"[dim]{'─' * 64}[/dim]")
            return
        if offer.replaces is not None:
            print_(f"  replaces the learned skill at {offer.replaces}",
                   markup=False)
        for label, path in self._offer_files(offer):
            print_(f"[dim]── {label} {'─' * max(3, 60 - len(label))}[/dim]")
            print_(path.read_text(encoding="utf-8").rstrip(), markup=False,
                   highlight=False)
        print_(f"[dim]{'─' * 64}[/dim]")

    @staticmethod
    def _offer_files(offer: Offer) -> list[tuple[str, Path]]:
        """The staged files, which ARE the offer: edits land there."""
        files = [(SKILL_FILE, offer.staging / SKILL_FILE)]
        if offer.draft.script_name:
            files.append((offer.draft.script_name,
                          offer.staging / offer.draft.script_name))
        return files

    def _ask_to_save(self, learner: Learner, offer: Offer) -> None:
        """Save / edit first / change scope / no -- the default is no."""
        scope = offer.draft.scope
        other = {"user": "project", "project": "user"}
        while True:
            self._show_offer(offer, scope)
            answer = self._input(
                "[s]ave  [e]dit first  [N]o > " if offer.repairs is not None
                else f"[s]ave  [e]dit first  [c]hange scope to "
                     f"{other[scope]}  [N]o > ").strip().lower()
            if answer in ("c", "scope") and offer.repairs is not None:
                continue   # an update stays where the skill already lives
            if answer in ("c", "scope"):
                scope = other[scope]
                continue
            if answer in ("e", "edit"):
                self._edit_offer(offer)
                continue
            if answer not in ("s", "save", "y", "yes"):
                learner.discard(offer)
                self.console.print("[dim]not saved[/dim]")
                return
            files = dict(self._offer_files(offer))
            script = files.get(offer.draft.script_name)
            try:
                skill = learner.save(
                    offer, scope=scope,
                    skill_md=files[SKILL_FILE].read_text(encoding="utf-8"),
                    script=script.read_text(encoding="utf-8") if script else None)
            except Exception as exc:
                self.console.print(f"cannot save it as it is: {exc}",
                                   markup=False, style="red")
                continue
            self.console.print(f"[green]saved skill {skill.name}[/green] "
                               f"[dim]({skill.directory}) -- next time, the "
                               f"model follows it instead of rediscovering "
                               f"the way[/dim]")
            return

    def _edit_offer(self, offer: Offer) -> None:
        """$EDITOR on each staged file; without one, the paths and a pause."""
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        files = [path for _, path in self._offer_files(offer)]
        if editor and sys.stdin.isatty() and sys.stdout.isatty():
            for path in files:
                subprocess.run([editor, str(path)])
            return
        self.console.print("edit these, then press Enter:\n  "
                           + "\n  ".join(str(p) for p in files), markup=False)
        self._input("")

    # ---- slash commands ----------------------------------------------------

    def _command(self, line: str) -> bool:  # returns True when exiting
        parts = line[1:].split(maxsplit=1)
        name, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")

        match name:
            case "help":
                self.console.print(HELP)
            case "model":
                if arg:
                    self.agent.model = arg
                    self.console.print(f"[green]model -> {arg}[/green]")
                else:
                    self.console.print(f"model: {self.agent.model}")
            case "provider":
                if arg:
                    self._switch_provider(arg)
                else:
                    self.console.print(
                        f"provider: {self.agent.provider.name}, model: {self.agent.model}"
                    )
            case "tools":
                self._tools_command(arg)
            case "mcp":
                self._mcp_command(arg)
            case "build":
                if not arg:
                    self.console.print("[red]/build needs a spec: "
                                       "/build <what to build>[/red]")
                else:
                    self._build_command(arg)
            case "history":
                self._show_history()
            case "usage":
                u = self.agent.total_usage
                line = (f"session tokens: {u.input_tokens} in / {u.output_tokens} out "
                        f"(cache read {u.cache_read_tokens} / write "
                        f"{u.cache_write_tokens})")
                ratio = self.agent.utilization()
                if ratio is not None:
                    real = self.agent.last_context_tokens
                    size = (f"~{real}" if real else
                            f"~{estimate_history(self.agent.history)} estimated")
                    line += (f"\ncontext: {size} tokens "
                             f"({ratio:.0%} of usable window; "
                             f"red zone at {int(RED * 100)}%)")
                line += self._cost_line()
                if self.agent.budget is not None:
                    line += f"\nbudget: {self.agent.budget.describe()}"
                self.console.print(line)
            case "compact":
                stats = self.agent.compact()
                parts = [f"[green]compacted[/green] "
                         f"{stats['messages_before']} -> "
                         f"{stats['messages_after']} message(s)"]
                if stats["masked"]:
                    parts.append(f"elided {stats['masked']} old tool result(s)")
                if stats["summarized"]:
                    parts.append(f"summarized a {stats['segment_size']}-message "
                                 "segment")
                self.console.print(" · ".join(parts))
            case "clear":
                self.end_conversation()
                self.agent.history.clear()
                self.console.print("[green]history cleared[/green]")
            case "yolo":
                self._yolo_command(arg)
            case "env":
                self._env_command(arg)
            case "save":
                self._save_session(arg or "default")
            case "load":
                self._load_session(arg or "default")
            case "image":
                self._image_command(arg)
            case "skills":
                self._skills_command(arg)
            case "learn":
                self._learn_command()
            case "memory":
                self._memory_command(arg)
            case "remember":
                self.end_conversation(forced=True)
            case "quit" | "exit":
                self.end_conversation()
                return True
            case _:
                # A skill name is a command: /pr-review <anything> runs a
                # normal turn with that skill's instructions already in
                # hand. Built-ins win the name, which is why this lives in
                # the fallback rather than ahead of the match.
                if self._run_skill_command(name, arg):
                    return False
                self.console.print(f"[red]unknown command {line!r} — /help[/red]")
        return False

    # ---- skills ---------------------------------------------------------------

    def _skills_command(self, arg: str) -> None:
        """/skills lists what is on disk; ``/skills reload`` re-scans after
        you write one; ``/skills NAME`` prints a skill's instructions
        locally -- reading your own file should not cost a model turn."""
        skills = getattr(self.agent, "skills", None)
        if skills is None:
            self.console.print("[yellow]skills are off for this session "
                               "(--no-skills)[/yellow]")
            return
        if arg == "reload":
            found = skills.reload()
            self.console.print(f"[green]rescanned[/green] -- {len(found)} "
                               f"skill(s), {len(found.broken)} broken")
            return
        parts = arg.split()
        if parts and parts[0] in ("off", "on"):
            self._skills_toggle(skills, parts[0], parts[1:])
            return
        if parts and parts[0] == "tool":
            if len(parts) != 2:
                self.console.print("[red]usage: /skills tool NAME[/red]")
                return
            try:
                self._promote(parts[1])
            except KeyboardInterrupt:
                self.console.print("\n[yellow](not made a tool)[/yellow]")
            return
        if arg:
            skill = skills.get(arg)
            if skill is None:
                near = skills.suggestions(arg)
                hint = f" (did you mean {', '.join(near)}?)" if near else ""
                self.console.print(f"[red]no skill {arg!r}{hint}[/red]")
                return
            # PLAIN: a SKILL.md is arbitrary markdown and may contain rich
            # markup that would otherwise be swallowed or crash the render.
            self.console.print(f"{skill.name} · {skill.source} · {skill.path}",
                               markup=False)
            self.console.print(skill.body, markup=False)
            return
        self.console.print(self._skills_panel(skills), markup=False)

    def _skills_toggle(self, skills: Any, verb: str, patterns: list[str]) -> None:
        """``/skills off|on NAME|GLOB...`` -- the /tools switch, for skills.

        Same matching rule as YANTRA_DISABLED_SKILLS, and the same soft
        semantics: a pulled skill stays on disk and keeps showing up in
        /skills marked [off]; it just leaves the roster and refuses to
        load until you bring it back. Recomposing the prompt costs the
        cached prefix, which is the honest price of changing what the
        model is told mid-session.
        """
        import fnmatch

        if not patterns:
            self.console.print(f"[red]usage: /skills {verb} NAME|GLOB "
                               "[NAME|GLOB...] -- bare /skills lists[/red]")
            return
        present = skills.names()
        affected = sorted({n for pat in patterns
                           for n in present if fnmatch.fnmatch(n, pat)})
        unmatched = [pat for pat in patterns
                     if not any(fnmatch.fnmatch(n, pat) for n in present)]
        for name in affected:
            skills.disable(name) if verb == "off" else skills.enable(name)
        if affected:
            skills.reapply()  # the roster is a prompt layer; rewrite it
            word = "pulled" if verb == "off" else "restored"
            self.console.print(f"[green]{word}[/green] {', '.join(affected)} "
                               "-- applies to the next model call")
        if unmatched:
            self.console.print(f"[yellow]no skill matches: "
                               f"{', '.join(unmatched)}[/yellow]")

    def _skills_panel(self, skills: Any) -> str:
        """Bare-/skills text. PLAIN, for the same reason /env's panel is."""
        if not len(skills) and not skills.found.broken:
            roots = ", ".join(str(root) for root, _ in
                              skill_roots(Path(self.agent.ctx.cwd)))
            return f"no skills found. Put one in: {roots}"
        lines = [f"{len(skills)} skill(s):"]
        for skill in skills:
            mark = "*" if skill.name in skills.loaded else " "
            off = " [off]" if skills.is_disabled(skill.name) else ""
            off += " [stale]" if skill.is_stale else ""
            kind = " [delegated]" if skill.delegated else ""
            kind += f" [tool: {skill.tool_name}]" if skill.tool_name else ""
            lines.append(f" {mark} {skill.name} [{skill.source}]{kind}{off} "
                         f"-- {skill.description}")
            if skill.learned is not None:
                lines.append(f"     learned {skill.learned.render()}")
            if error := skills.tool_errors.get(skill.name):
                lines.append(f"     tool not offered: {error}")
            if skill.suggest_tool:
                lines.append(f"     worked {skill.learned.in_a_row} times in "
                             f"a row -- make it a tool? /skills tool "
                             f"{skill.name}")
        for broken in skills.found.broken:
            lines.append(f" ! {broken.path}: {broken.reason}")
        for name, path in skills.found.shadowed:
            lines.append(f" ~ {name} shadowed: {path}")
        if skills.loaded:
            lines.append(f"loaded this session: {', '.join(skills.loaded)}")
        return "\n".join(lines)

    def _promote(self, name: str) -> None:
        """``/skills tool NAME``: write the tool, test it once, ask.

        Works with learning off, like /learn: the flag stops OFFERS, and
        this is the person asking.
        """
        learner = getattr(self.agent, "learner", None) or Learner(self.agent, "ask")
        offer = learner.propose_tool(
            name, progress=lambda text: self.console.print(f"[dim]· {text}[/dim]"))
        if offer is None:
            self.console.print(f"not made a tool: {learner.last_skip}",
                               markup=False, style="yellow")
            return
        view = offer.view()
        print_ = self.console.print
        print_()
        print_("[bold]Make this a tool?[/bold]")
        print_(f"  {view['name']} -- {view['description']}", markup=False)
        print_(f"  from:   the skill {name}", markup=False)
        for key, spec in view["parameters"]["properties"].items():
            need = "required" if key in view["parameters"]["required"] else "optional"
            print_(f"  {key}: {spec.get('type')}, {need} -- "
                   f"{spec.get('description', '')}", markup=False)
        print_(f"  runs:   {view['command']}", markup=False)
        state = "passed" if offer.passed else "FAILED"
        print_(f"  tested: {state} -- {json.dumps(view['test']['args'])}",
               markup=False)
        if view["test"]["output"]:
            print_(view["test"]["output"], markup=False, style="dim",
                   highlight=False)
        print_(f"  cost:   {offer.spent_in:,} in / {offer.spent_out:,} out "
               f"tokens to write it")
        print_("  each call still asks first, like bash", style="dim")
        if not offer.passed:
            print_("[yellow]its test failed, so it was not made a tool[/yellow]")
            return
        answer = self._input("[m]ake it a tool  [N]o > ").strip().lower()
        if answer not in ("m", "make", "y", "yes"):
            print_("[dim]not made a tool[/dim]")
            return
        skill = learner.save_tool(offer)
        print_(f"[green]{name} is now the tool {skill.tool_name}[/green] "
               f"[dim]-- applies to the next model call[/dim]")

    def _run_skill_command(self, name: str, arg: str) -> bool:
        """``/pr-review the auth branch`` -> a normal turn, skill in hand.

        The instructions are prepended to the user's own words rather
        than injected anywhere clever: the model sees one message saying
        what to do and what to do it to, and the transcript shows exactly
        what was sent. Returns False when the name is not a skill, so the
        caller can fall through to its unknown-command error.
        """
        skills = getattr(self.agent, "skills", None)
        if skills is None or skills.get(name) is None:
            return False
        if skills.is_disabled(name):
            self.console.print(f"[yellow]skill {name!r} is off "
                               f"(/skills on {name} to restore)[/yellow]")
            return True
        skill = skills.get(name)
        if skill.delegated:
            # Its whole point is running fenced; typing /NAME must not
            # smuggle the body into the main conversation instead.
            self.console.print(
                f"[yellow]{name!r} is a delegated skill -- ask for it in "
                f"plain words and the model will run it in a sub-agent "
                f"with only {', '.join(skill.allowed_tools)}[/yellow]")
            return True
        skill = skills.load(name)
        task = arg or "Follow this skill for the current task."
        message = (f"Use the {skill.name!r} skill.\n\n"
                   f"Its instructions (from {skill.path}):\n{skill.body}\n\n"
                   f"Task: {task}")
        self.console.print(f"[dim]running skill {skill.name!r}[/dim]")
        try:
            self.run_turn(message, images=self._take_pending_images(),
                          learn=False)
        except KeyboardInterrupt:
            self.console.print("\n[yellow](cancelled)[/yellow]")
        return True

    # ---- tools ----------------------------------------------------------------

    def _tools_command(self, arg: str) -> None:
        """/tools lists everything registered (disabled marked [off]);
        ``/tools off|on NAME [NAME...]`` pulls or returns tools MID-SESSION.
        Names may be globs (browser_*, mcp__slack__*) -- same matching rule
        as the YANTRA_DISABLED_TOOLS startup kill-switch, but reversible:
        a pulled tool stays registered, it just stops being sent and its
        calls fail as readable data until /tools on brings it back."""
        import fnmatch

        parts = arg.split()
        if parts and parts[0] in ("off", "on"):
            if len(parts) < 2:
                self.console.print(f"[red]usage: /tools {parts[0]} "
                                   "NAME|GLOB [NAME|GLOB...] -- bare /tools "
                                   "lists[/red]")
                return
            patterns = parts[1:]
            present = self.agent.registry.names()
            affected = sorted({n for pat in patterns
                               for n in present
                               if fnmatch.fnmatch(n, pat)})
            unmatched = [pat for pat in patterns
                         if not any(fnmatch.fnmatch(n, pat) for n in present)]
            registry = self.agent.registry
            for name in affected:
                (registry.enable if parts[0] == "on" else registry.disable)(name)
            if affected:
                verb = "re-enabled" if parts[0] == "on" else "disabled"
                # The model-facing consequence, stated once: disabling is
                # live immediately -- the loop re-consults per call.
                self.console.print(
                    f"[green]{verb} {len(affected)} tool(s):[/green] "
                    f"{', '.join(affected)}")
            for pat in unmatched:
                self.console.print(f"[yellow]no tool matches {pat!r}[/yellow]")
            return

        disabled = set(self.agent.registry.disabled_names())
        if disabled:
            self.console.print(f"[dim]disabled this session "
                               f"({len(disabled)}): {', '.join(sorted(disabled))} "
                               f"-- /tools on NAME to restore[/dim]")
        if self.agent.tool_catalog is not None:
            pinned = [n for n in self.agent.tool_catalog.must_include
                      if n != "list_available_tools"]
            self.console.print(
                f"[dim]selection active: top {self.agent.tools_per_turn} of "
                f"{len(self.agent.tool_catalog.tools)} each turn; "
                f"always loaded: {', '.join(pinned)} + "
                f"list_available_tools[/dim]")
        # Iterate ALL registered tools -- specs() omits disabled ones, and
        # the whole point here is showing what's off as well as on.
        for tool in self.agent.registry:
            spec = tool.spec()
            # \[ escapes rich markup -- bare [off] parses as a style tag
            off = r" [red]\[off][/red]" if tool.name in disabled else ""
            self.console.print(
                f"[bold]{spec.name}[/bold]{off} — {spec.description}"
            )
            self.console.print_json(json.dumps(spec.parameters))

    # ---- mcp servers -----------------------------------------------------------

    def _mcp_command(self, arg: str) -> None:
        """/mcp lists connected servers; add|remove manage connections
        MID-SESSION; off|on is the soft switch (whole toolset, process
        stays warm). The terminal twin of the panel's servers section --
        same MCPManager underneath, so both stay in step."""
        if self.mcp is None:
            self.console.print("[red]this session has no mcp manager -- "
                               "start via yantra (not the library)[/red]")
            return

        parts = arg.split()
        verb = parts[0] if parts else ""

        if verb in ("off", "on"):
            if len(parts) != 2:
                self.console.print(f"[red]usage: /mcp {verb} NAME[/red]")
                return
            try:
                count = self.mcp.set_enabled(parts[1], verb == "on")
            except Exception as exc:
                self.console.print(f"[red]{exc}[/red]")
                return
            state = "re-enabled" if verb == "on" else "disabled"
            tail = ("" if verb == "on" else " -- its calls now fail as "
                    "data until /mcp on")
            self.console.print(f"[green]{state} {count} tool(s) on "
                               f"{parts[1]}{tail}[/green]")
        elif verb == "remove":
            if len(parts) != 2:
                self.console.print("[red]usage: /mcp remove NAME[/red]")
                return
            origin = self.mcp.origins.get(parts[1])
            try:
                removed = self.mcp.disconnect(parts[1])
            except Exception as exc:
                self.console.print(f"[red]{exc}[/red]")
                return
            # disconnect() also drops any saved entry; say so plainly. A
            # server the package or Setu brought is not yours to forget:
            # it is back next launch, and the line says where from.
            tail = {"package": "back next launch -- the package declares it",
                    "setu": "back next launch -- it's a connected account "
                            "(setu disconnect to drop it)"}.get(
                origin, "saved entry forgotten")
            self.console.print(f"[green]disconnected '{parts[1]}' -- "
                               f"{removed} tool(s) removed, {tail}[/green]")
        elif verb == "add":
            self._mcp_add_command(parts[1:])
        else:
            if not self.mcp.sessions:
                self.console.print("[dim]no mcp servers connected -- "
                                   "/mcp add NAME URL (or COMMAND) to "
                                   "connect one[/dim]")
                return
            for info in self.mcp.servers():
                health = "" if info["healthy"] else \
                    r" [red]\[down][/red]"  # \[ escapes rich markup
                remembered = " · saved" if info["remembered"] else ""
                origin = {"package": " · from the package",
                          "setu": " · from Setu"}.get(info.get("origin"), "")
                self.console.print(
                    f"[bold]{info['name']}[/bold] [dim]({info['transport']})[/"
                    f"dim]{health}{remembered}{origin} — {info['target']} — "
                    f"{info['tools']} tool(s)"
                    + (f", {info['disabled']} off" if info["disabled"] else ""))

    def _mcp_add_command(self, rest: list[str]) -> None:
        """/mcp add NAME URL   (http)
        /mcp add NAME CMD [ARGS...]  (stdio). After connecting, asks
        whether to remember the server for future launches -- the same
        question the web form's checkbox answers."""
        from yantra.mcp import MCPServerConfig, MCPError, remembered_path

        if len(rest) < 2:
            self.console.print("[red]usage: /mcp add NAME URL --or-- "
                               "/mcp add NAME COMMAND [ARGS...][/red]")
            return
        name, target, extra = rest[0], rest[1], rest[2:]
        cfg = (MCPServerConfig(name=name, url=target) if "://" in target
               else MCPServerConfig(name=name, command=target, args=extra))
        with self.console.status(f"[dim]connecting {name}…[/dim]"):
            try:
                names = self.mcp.connect(cfg)
            except (MCPError, ValueError) as exc:
                self.console.print(f"[red]could not connect {name!r}: "
                                   f"{exc}[/red]")
                return
        self.console.print(f"[green]connected {name}: {len(names)} "
                           f"tool(s)[/green] [dim]({', '.join(names)})[/dim]")
        answer = Prompt.ask("remember this server for future launches?",
                            choices=["y", "N"], default="N").strip().lower()
        if answer == "y":
            from yantra.mcp import remember_server
            path = self.mcp.memory_path or remembered_path()
            remember_server(cfg, path)
            self.mcp.pinned.add(name)
            self.console.print(f"[dim]saved to {path} -- reconnects on "
                               "future launches[/dim]")

    # ---- permission mode ------------------------------------------------------

    def _switchable_gate(self) -> SwitchableGate | None:
        """The runtime-switchable gate, if this session has one. Agents built
        with a bare PermissionFn (tests, embedders) keep the fixed-gate
        behavior: /yolo reports instead of crashing."""
        gate = self.agent.permissions
        return gate if isinstance(gate, SwitchableGate) else None

    def _gate_mode(self) -> str:
        """Current mode of ANY gate -- switchable, plain confirm, or yolo.
        What the prompt prefix and the banner display."""
        switch = self._switchable_gate()
        if switch is not None:
            return switch.mode
        return "yolo" if self.agent.permissions is yolo else "ask"

    def _yolo_command(self, arg: str) -> None:
        """/yolo [on|off]: flip between bypassing every approval and asking
        first. Effective immediately, INCLUDING the remaining tool calls of
        an in-flight turn -- the agent loop consults the gate per call."""
        switch = self._switchable_gate()
        if switch is None:
            self.console.print(
                "[red]this session's permission gate is fixed at startup -- "
                "/yolo can't switch it[/red]")
            return
        wanted = arg.strip().lower()
        if not wanted:
            new_mode = switch.toggle()
        elif wanted == "on":
            switch.set_mode("yolo")
            new_mode = "yolo"
        elif wanted == "off":
            switch.set_mode("ask")
            new_mode = "ask"
        else:
            self.console.print("[red]usage: /yolo [on|off][/red]")
            return
        if new_mode == "yolo":
            self.console.print("[red]yolo ON -- tools run WITHOUT asking; "
                               "you accept the risk[/red]")
        else:
            self.console.print("[green]permission prompts back on -- "
                               "risky calls ask first[/green]")

    # ---- session awareness ------------------------------------------------------

    def _env_command(self, arg: str) -> None:
        """/env [off|local|full]: show or switch what the agent knows about
        its surroundings ([notes/29](../notes/29-environment-awareness.md)).
        A flip recomposes agent.system immediately -- the next model call,
        even one mid-turn, sees the new level. Agents built without an
        EnvContext (builds, embedders) report instead of crashing."""
        ctx = getattr(self.agent, "env_context", None)
        if ctx is None:
            self.console.print("[red]this session has no env context[/red]")
            return
        wanted = arg.strip().lower()
        if wanted:
            try:
                ctx.flip(wanted)
            except ValueError:
                self.console.print(
                    "[red]usage: /env [off|local|full][/red]")
                return
            self.console.print(f"[green]env context -> {ctx.mode}[/green]"
                               " [dim](applies to the next model call)[/dim]")
        # bare, or after a flip: show exactly what the model now sees.
        # markup OFF -- hostnames and paths may contain rich syntax.
        self.console.print(ctx.panel(), markup=False, highlight=False)

    # ---- cost ---------------------------------------------------------------

    def _cost_line(self) -> str:
        """The /usage dollars line: '' when nothing has been spent yet.

        Honesty rules (mirroring yantra.pricing): local models are
        genuinely free; unknown slugs are NEVER rendered as $0 -- a made-up
        zero looks identical to a real one and quietly trains the wrong
        instinct about what sessions cost.
        """
        buckets = self.agent.usage_by_model
        if not buckets:
            return ""
        if all(is_free(self.agent.provider.name, m) for m in buckets):
            return "\ncost: $0.00 (local model)"
        total, complete = session_cost(buckets)
        if total == 0.0 and not complete:
            return ("\n[dim]cost: no list price known for this "
                    "session's model(s)[/dim]")
        suffix = "" if complete else " [dim](priced models only)[/dim]"
        return f"\nsession cost: ~${total:.4f}{suffix}"

    # ---- build mode ---------------------------------------------------------

    def _build_command(self, spec_text: str) -> None:
        """/build SPEC: a fresh child agent builds in a scratch workspace
        (parent history untouched -- same isolation reasoning as sub-agents),
        then run_build re-verifies independently. The child inherits the
        parent's gate, with one upgrade: confined bash is auto-approved,
        so builds don't stall on y/n for every test run."""
        from yantra.permissions import trust_sandbox

        bash_tool = self.agent.registry.get("bash")
        sandbox = self.sandbox or bash_tool.sandbox
        workspace = (self.agent.ctx.cwd / ".yantra" / "builds"
                     / time.strftime("%Y%m%d-%H%M%S"))
        parent_gate = self.agent.permissions

        def factory(ws: Path) -> Agent:
            return Agent(
                self.agent.provider,
                model=self.agent.model,
                system=BUILD_SYSTEM,
                tools=default_registry(sandbox),
                permissions=trust_sandbox(parent_gate, sandbox),
                cwd=ws,
            )

        self.console.print(f"[dim]building in {workspace} "
                           f"(gate: inherited + sandboxed-bash auto-approve)"
                           f"[/dim]")
        try:
            result = run_build(factory, BuildSpec(task=spec_text), workspace,
                               on_event=self.renderer)
        except KeyboardInterrupt:
            self.console.print("\n[yellow](build cancelled)[/yellow]")
            return
        for outcome in result.checks:
            mark = "[green]PASS[/green]" if outcome.passed else "[red]FAIL[/red]"
            shown = " ".join(Path(a).name if i == 1 else a
                             for i, a in enumerate(outcome.argv))
            self.console.print(f"  {mark}  $ {shown}")
        verdict = ("[bold green]BUILD GREEN[/bold green]" if result.ok
                   else "[bold red]BUILD RED[/bold red]")
        for name in result.tampered_tests:
            self.console.print(f"  [red]{name} was MODIFIED -- tests are the "
                               "contract[/red]")
        self.console.print(f"{verdict} · {result.elapsed_seconds:.1f}s · "
                           f"{len(result.files)} file(s) in {workspace}")

    # ---- image attachments --------------------------------------------------

    def _take_pending_images(self) -> list[ImageBlock]:
        """Hand staged images to the starting turn and unstage them."""
        pending, self._pending_images = self._pending_images, []
        return pending or []

    def _image_command(self, arg: str) -> None:
        """/image: stage attachments for the NEXT message. Validated now --
        a typo should error at attach time, not after three more turns."""
        import shlex  # stdlib; quoted paths survive intact

        if not arg:
            if self._pending_images:
                self.console.print(
                    f"[blue]{len(self._pending_images)} image(s) staged "
                    "for your next message[/blue]")
            else:
                self.console.print(
                    "usage: /image PATH [PATH...] (png/jpeg/gif/webp; "
                    "staged onto your next message)")
            return
        if arg.strip() == "clear":
            self._pending_images.clear()
            self.console.print("[green]staged images cleared[/green]")
            return
        for path in shlex.split(arg):
            try:
                block = load_image_block(Path(path))
            except ImageError as exc:
                self.console.print(f"[red]{exc} -- not attached[/red]")
                continue
            kb = len(block.data) * 3 // 4 // 1024
            self._pending_images.append(block)
            self.console.print(
                f"[blue]attached {Path(path).name} ({block.media_type}, "
                f"~{kb} KB) -- rides your next message[/blue]")

    # ---- durable sessions ---------------------------------------------------

    def _save_session(self, session_id: str) -> None:
        if self.store is None:
            self.console.print("[red]no session store configured[/red]")
            return
        version = self.store.save(
            self.agent, provider_name=self.agent.provider.name,
            session_id=session_id,
        )
        u = self.agent.total_usage
        self.console.print(
            f"[green]saved[/green] '{session_id}' v{version} · "
            f"{len(self.agent.history)} message(s) · "
            f"{u.input_tokens}in/{u.output_tokens}out "
            "[dim](append-only -- every save is a new version)[/dim]"
        )

    def _load_session(self, session_id: str) -> None:
        if self.store is None:
            self.console.print("[red]no session store configured[/red]")
            return
        payload = self.store.load_latest(session_id)
        if payload is None:
            self.console.print(f"[red]no checkpoint named '{session_id}'[/red]")
            return
        # Switching conversations ends this one (memory/reflect.py).
        self.end_conversation()
        try:
            summary = apply_payload(
                self.agent, payload,
                settings_loader=load_settings,
                provider_factory=get_provider,
            )
        except Exception as exc:  # corrupt/newer payload must not kill the REPL
            self.console.print(f"[red]restore failed: {exc}[/red]")
            return
        # Checkpoints store the COMPOSED system (stale facts included), so
        # rebuild it from the live prompt LAYERS -- a restored session gets
        # fresh context and this session's skill roster, not last Tuesday's
        # copy ([notes/29](../notes/29-environment-awareness.md)).
        recompose(self.agent)
        mark_reviewed(self.agent)
        self.console.print(f"[green]{summary}[/green]")

    def _switch_provider(self, name: str) -> None:
        outgoing = self.agent.provider
        try:
            settings = load_settings(name)
            self.agent.provider = get_provider(name, settings)
        except Exception as exc:
            self.console.print(f"[red]{exc}[/red]")
            return  # the old provider is still in use -- do NOT close it
        # The swap succeeded, so nothing points at the old connection pool
        # any more. Switching back and forth used to leave one behind each
        # time ([notes/38](../notes/38-giving-it-back.md)).
        outgoing.close()
        # Model slugs are per-provider namespaces: carrying the old slug
        # over would ask provider B for a model it may not have.
        try:
            self.agent.model = default_model(name)
            note = f"model reset to {self.agent.model}"
        except Exception:
            note = f"model left as {self.agent.model} -- set {name.upper()}_MODEL"
        self.console.print(
            f"[green]provider -> {name}; {note}. History intact: "
            "it is stored in internal types, not wire format.[/green]"
        )

    def _show_history(self) -> None:
        from yantra.types import (ImageBlock, RedactedThinkingBlock,
                                   TextBlock, ThinkingBlock, ToolCall,
                                   ToolResult)

        for i, message in enumerate(self.agent.history):
            self.console.rule(f"[{i}] {message.role}")
            for block in message.content:
                match block:
                    case ToolCall(id=cid, name=n, arguments=args):
                        self.console.print(f"[cyan]tool_call {n}({args}) id={cid}[/cyan]")
                    case ToolResult(tool_call_id=cid, content=c, is_error=e):
                        flag = " [error]" if e else ""
                        preview = c[:200] + ("..." if len(c) > 200 else "")
                        self.console.print(
                            f"[magenta]tool_result[{cid}]{flag}: "
                            f"{preview}[/magenta]")
                    case ThinkingBlock(thinking=t, signature=s):
                        sig = f" (signed, {len(s)} chars)" if s else ""
                        preview = t[:200] + ("..." if len(t) > 200 else "")
                        self.console.print(f"[dim italic]thinking{sig}: "
                                           f"{preview}[/dim italic]")
                    case RedactedThinkingBlock(data=d):
                        self.console.print(f"[dim italic]thinking (redacted, "
                                           f"{len(d)} chars of ciphertext)[/dim italic]")
                    case ImageBlock(media_type=m, data=d):
                        kb = len(d) * 3 // 4 // 1024
                        self.console.print(f"[blue]image ({m}, ~{kb} KB decoded "
                                           f"payload not displayed)[/blue]")
                    case TextBlock(text=t):
                        self.console.print(t or "[dim](empty)[/dim]")
                    case _:
                        self.console.print(f"[red](unknown block: {type(block).__name__})[/red]")

    def _banner(self) -> None:
        # warn ONLY when the gate really is bypassed (startup flag or a
        # session that flipped since)
        gated = "  [red](yolo: no permission prompts)[/red]" \
            if self._gate_mode() == "yolo" else ""
        self.console.print(
            f"[bold]yantra[/bold] · provider={self.agent.provider.name} · "
            f"model={self.agent.model} · tools={len(self.agent.registry)} · "
            f"cwd={self.agent.ctx.cwd}{gated}"
        )
        self.console.print("[dim]/help for commands, ctrl-c cancels a turn[/dim]\n")
