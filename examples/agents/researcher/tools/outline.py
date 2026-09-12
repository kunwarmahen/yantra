"""outline -- the shape of a Markdown file, without reading the whole thing.

A worked example of a PACKAGE TOOL: a tool that ships with an agent
instead of with the harness (notes/32). Nothing here is special. It is
the same ``Tool`` subclass every built-in is, it writes its own JSON
Schema by hand, and it may use the harness's own helpers -- the sandbox
resolver below is the one read_file uses, so this tool cannot be talked
into reading /etc/shadow either.

Why this tool exists rather than a toy one: the researcher's whole job is
finding which of forty files answers a question, and reading all forty
costs more context than the answer is worth. Headings are the cheap
version of that scan.
"""

from __future__ import annotations

from typing import Any, ClassVar

from yantra.errors import ToolError
from yantra.tools.base import Tool, ToolContext, require_str
from yantra.tools.fs import resolve_in_sandbox

MAX_HEADINGS = 200


class Outline(Tool):
    name = "outline"
    description = (
        "List the Markdown headings of a file with their line numbers, as "
        "an indented outline. Use to decide WHETHER a document answers a "
        "question, and which part of it to read, before spending a "
        "read_file on the whole thing. Markdown only."
    )
    # Hand-written, like every schema in this project: the 'description'
    # strings here are what stop the model calling this on a .png.
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to a Markdown file, relative to the "
                               "working directory.",
            },
            "depth": {
                "type": "integer",
                "description": "Deepest heading level to include, 1-6. "
                               "Defaults to 3; use 2 for a long document.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    # THE AUTHOR'S DECLARATION, and nothing can check it: this tool only
    # reads, so it auto-approves. Writing True here on a tool that deletes
    # files would be lying to the permission gate.
    read_only = True

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"outline: {args.get('path', '?')}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = resolve_in_sandbox(ctx, require_str(args, "path"))
        depth = args.get("depth", 3)
        if not isinstance(depth, int) or isinstance(depth, bool):
            raise ToolError("argument 'depth' must be an integer 1-6")
        depth = max(1, min(6, depth))
        if not path.is_file():
            raise ToolError(f"not a file: {path}")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"cannot read {path}: {exc}") from None

        lines = []
        fenced = False
        for number, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("```"):
                fenced = not fenced          # '### ' inside a code block is code
                continue
            if fenced or not line.startswith("#"):
                continue
            hashes = len(line) - len(line.lstrip("#"))
            title = line[hashes:].strip()
            if not title or hashes > depth:
                continue
            lines.append(f"{'  ' * (hashes - 1)}{title}  (line {number})")
            if len(lines) >= MAX_HEADINGS:
                lines.append(f"... truncated at {MAX_HEADINGS} headings")
                break

        if not lines:
            return f"{path.name}: no Markdown headings at depth <= {depth}"
        return "\n".join(lines)
