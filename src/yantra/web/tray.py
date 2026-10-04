"""The to-keep tray, kept on disk: a restart no longer loses what waited.

What a look behind the answer finds (notes/113) waits in the tray until
the person answers it. The tray lived in the server's memory, so
restarting ``--web`` dropped every waiting offer -- a recipe tested a
minute before, facts about the person, a site's new guide -- while the
recipes' staged files stayed behind in ``.yantra/learning/`` with
nothing pointing at them.

ONE FILE PER WORKSPACE, IN THE STATE DIRECTORY. Facts about the person
are among what waits, so the file lives beside memory
(``~/.local/state/yantra/tray/``), never in the project folder a person
may share. Its name is a hash of the workspace's path: a tray belongs to
the folder it was found in, as the staged drafts do.

WRITTEN ON EVERY CHANGE, READ ONCE. The server writes the whole tray
whenever it changes (atomically, 0600) and reads it when a session
attaches. An offer whose staged folder is gone is dropped on the way in
-- its files are what saving writes. A recipe that repairs a learned
skill finds the skill again by its folder; one whose skill is gone is
dropped too. A recipe's Setu needs are asked of Setu again: what is
connected may have changed while the server was down.

A FILE THAT CANNOT BE READ IS SET ASIDE, not trusted and not fatal: it
is renamed ``.bad`` and the tray starts empty.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

FORMAT = "yantra.tray.v1"


def path_for(cwd: Path) -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    key = hashlib.sha256(str(Path(cwd).resolve()).encode()).hexdigest()[:16]
    return Path(state) / "yantra" / "tray" / f"{key}.json"


def _offer_data(offer: Any) -> dict[str, Any]:
    return {
        "draft": dataclasses.asdict(offer.draft),
        "staging": str(offer.staging),
        "tested": offer.tested,
        "test_output": offer.test_output,
        "test_runs": offer.test_runs,
        "spent": dataclasses.asdict(offer.spent),
        "replaces": str(offer.replaces) if offer.replaces else None,
        "renamed_from": offer.renamed_from,
        "repairs": str(offer.repairs.directory) if offer.repairs is not None else None,
        "failure": offer.failure,
        "tool": offer.tool,
        "keeps_tool": offer.keeps_tool,
        "waiting": offer.waiting,
    }


def save(cwd: Path, recipes: dict[str, tuple[Any, Any]], memories: list[Any],
         guides: dict[str, Any]) -> None:
    """The whole tray, written atomically; an empty tray removes the file."""
    target = path_for(cwd)
    if not recipes and not memories and not guides:
        target.unlink(missing_ok=True)
        return
    data = {
        "format": FORMAT,
        "cwd": str(Path(cwd).resolve()),
        "recipes": [{"id": key, **_offer_data(offer)} for key, (_, offer) in recipes.items()],
        "memories": [{"statement": c.statement, "kind": c.kind} for c in memories],
        "guides": [{"id": key, **dataclasses.asdict(g)} for key, g in guides.items()],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    handle, staged = tempfile.mkstemp(dir=target.parent, prefix=".tray-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out)
        os.chmod(staged, 0o600)
        os.replace(staged, target)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise


def load(cwd: Path, agent: Any, learner: Any) -> tuple[dict[str, tuple[Any, Any]],
                                                       list[Any], dict[str, Any], list[str]]:
    """(recipes, memories, guides, notes) as they were left. ``learner``
    is the one recipes are saved through; notes say what was dropped."""
    from yantra.memory.reflect import Candidate
    from yantra.site_guide import GuideOffer

    target = path_for(cwd)
    notes: list[str] = []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise ValueError(f"format {data.get('format')!r}")
    except FileNotFoundError:
        return {}, [], {}, notes
    except (ValueError, OSError) as exc:
        target.replace(target.with_suffix(".json.bad"))
        return {}, [], {}, [f"the tray file could not be read ({exc}); set aside as .bad"]
    recipes: dict[str, tuple[Any, Any]] = {}
    for item in data.get("recipes") or []:
        try:
            offer = _offer(item, agent, learner)
        except (KeyError, TypeError, ValueError) as exc:
            notes.append(f"a waiting recipe could not be restored ({exc})")
            continue
        if offer is None:
            notes.append(f"{item.get('draft', {}).get('name', '?')}: its staged files "
                         "or the skill it updated are gone")
            continue
        recipes[str(item["id"])] = (learner, offer)
    memories = [Candidate(str(m["statement"]), str(m.get("kind") or "fact"))
                for m in data.get("memories") or [] if m.get("statement")]
    guides = {}
    for g in data.get("guides") or []:
        try:
            guides[str(g["id"])] = GuideOffer(site=g["site"], name=g["name"], old=g["old"],
                                              new=g["new"], calls=int(g["calls"]))
        except (KeyError, TypeError, ValueError):
            continue
    return recipes, memories, guides, notes


def _offer(item: dict[str, Any], agent: Any, learner: Any) -> Any:
    from yantra.setu_link import resolve_needs
    from yantra.skills.learn import Draft, Offer
    from yantra.types import Usage

    staging = Path(item["staging"])
    if not staging.is_dir():
        return None
    repairs = None
    if item.get("repairs"):
        skills = getattr(agent, "skills", None)
        folder = Path(item["repairs"]).resolve()
        repairs = next((s for s in (list(skills) if skills is not None else [])
                        if Path(s.directory).resolve() == folder), None)
        if repairs is None:
            return None
    draft = Draft(**item["draft"])
    offer = Offer(draft=draft, staging=staging, tested=item.get("tested"),
                  test_output=item.get("test_output") or "",
                  test_runs=int(item.get("test_runs") or 0),
                  spent=Usage(**(item.get("spent") or {})),
                  replaces=Path(item["replaces"]) if item.get("replaces") else None,
                  renamed_from=item.get("renamed_from") or "", repairs=repairs,
                  failure=item.get("failure") or "", tool=item.get("tool") or "",
                  keeps_tool=bool(item.get("keeps_tool")),
                  waiting=bool(item.get("waiting")))
    offer.connections = resolve_needs(draft.needs, learner._setu_link())
    return offer
