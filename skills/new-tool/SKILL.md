---
name: new-tool
description: Add a new built-in tool to this harness -- schema, summary,
  run, registration, permission gating, tests and docs. Use when asked to
  add, write, or wire up a tool that the model can call.
allowed-tools: read_file, write_file, edit_file, grep, glob, bash
---

# Adding a tool to Yantra

A tool is three things glued together (see `src/yantra/tools/base.py`):
a hand-written JSON Schema, a `summary()` the permission prompt shows a
human, and a `run()` that does the thing. Get all three right and the
loop handles the rest.

**First, is it a BUILT-IN?** These steps are for a tool that ships with
the harness. A tool that belongs to one agent goes in that agent's
package instead -- `<package>/tools/<name>.py`, same `Tool` subclass,
same hand-written schema, but no registration and no selector step: it
is discovered by being there. See `notes/32-package-tools.md` and
`examples/agents/researcher/tools/outline.py`.

A tool several agents share can ship as a pip-installable **pack** instead
(`notes/53-a-tool-that-arrives-by-pip.md`). If you publish one, declare
the Yantra it was built for in the pack's own dependencies
(`dependencies = ["yantra>=0.1"]`): Yantra reads that line at load and
refuses a mismatch before importing your code
(`notes/87-which-release-and-what-its-called.md`). Write the description
about what the tool does, not what it is called -- a manifest may rename
it with a prefix, and the description is not rewritten.

## Steps

1. **Read the neighbours first.** `src/yantra/tools/glob.py` is the
   smallest complete example; `src/yantra/tools/fs.py` shows a gated
   write. Match their comment density and voice -- this repo explains
   WHY in prose, not just what.

2. **Write the tool** in `src/yantra/tools/<name>.py`. Start from
   `template.py` in this skill's folder (read it with read_file). The
   rules that matter:
   - `parameters` is hand-written JSON Schema with a real `description`
     on every property, a `required` list, and
     `"additionalProperties": false`. Vague schemas produce vague calls.
   - `read_only = True` ONLY if the call cannot change anything outside
     the process and sends nothing over the network. It drives permission
     auto-approval, so getting it wrong removes a seatbelt silently.
   - Raise `ToolError` for anything the MODEL could plausibly fix (bad
     argument, missing file). The loop turns that into an error result
     the model reads and recovers from. Let real bugs crash.
   - Validate every argument with `require_str` / `require_int` -- args
     are whatever JSON the model produced, not what your schema asked for.

3. **Register it** in `default_registry()` in
   `src/yantra/tools/__init__.py`, add it to `__all__`, and update that
   module's docstring count ("Sixteen built-ins ...") -- it is prose that
   goes stale silently.

4. **Consider selection.** If the tool belongs to the autonomy floor
   (every task needs it), add it to `CORE_PINS` in
   `src/yantra/tools/selector.py`. Otherwise leave it retrievable.
   - **Write the description for the reader, not the ranker.** Retrieval
     indexes name + description, and it used to make a long description
     cost you: a thorough entry point ranked below its own terse
     siblings. That is fixed (`b=0.30`, and names index in pieces as
     well as whole -- [notes/60](../../notes/60-the-tool-that-explained-itself.md)),
     so explain the tool properly. Say what it returns and name the
     tools it pairs with.
   - **If the tool is a family's entry point** -- the one that must run
     before its siblings mean anything -- check it actually retrieves.
     A one-line test that asserts it lands in `select(query, k)` for a
     plausible query is cheap, and its absence is invisible: the model
     just reports it has no such tool.

5. **Test it** in `tests/test_tools.py` (or its own module if the surface
   is large). Cover: the happy path, a bad argument raising `ToolError`,
   the sandbox/permission story, and whatever the schema promises.

6. **Document it.** This repo ships docs WITH the feature, never after:
   - `README.md` -- the tool table and any usage section it touches
   - `notes/NN-<topic>.md` if the tool introduces a concept worth a
     write-up

## Before you say you are done

Run `uv run pytest -q` and report the real result. If anything is still
failing or skipped, say so plainly rather than rounding up.
