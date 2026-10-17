"""Loading user-supplied images into ImageBlocks.

Providers take base64 payloads inline -- never file paths -- so the CLI
reads and encodes the bytes up front, before any request exists. The
constraints here mirror Anthropic's documented image limits (5 MB per
image; png/jpeg/gif/webp); the OpenAI dialect accepts the same four
types carried as ``data:`` URLs instead.

Validation is deliberately shallow: extension -> MIME mapping and a
size cap. We do NOT sniff magic bytes or decode pixels -- a wrong
extension produces a provider-side 400 the user sees verbatim, which
teaches more than silently second-guessing them.
"""

from __future__ import annotations

import base64
from pathlib import Path

from yantra.errors import ImageError
from yantra.types import ImageBlock

#: extension -> MIME type. Case-insensitive via .lower() at lookup.
MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

#: Anthropic's per-image cap (the stricter of the two dialects). Enforced
#: on RAW bytes -- base64 inflates by 4/3 on the wire, but providers size
#: their limits in decoded bytes.
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _media_type_for(name: str) -> str:
    """Extension -> MIME lookup shared by both loaders."""
    media_type = MEDIA_TYPES.get(Path(name).suffix.lower())
    if media_type is None:
        supported = ", ".join(sorted(MEDIA_TYPES))
        suffix = Path(name).suffix or "(none)"
        raise ImageError(
            f"unsupported image type '{suffix}' "
            f"for {Path(name).name} -- supported: {supported}"
        )
    return media_type


def _capped(raw: bytes, name: str) -> None:
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError(
            f"{name} is {len(raw) / 1e6:.1f} MB -- over the "
            f"{MAX_IMAGE_BYTES / 1e6:.0f} MB per-image cap"
        )


def load_image_block(path: Path) -> ImageBlock:
    """Read an image file into a request-ready ImageBlock.

    Raises ImageError (a ValueError) on missing files, unsupported
    extensions, or oversize payloads -- before any turn starts, so bad
    input never pollutes history.
    """
    path = Path(path)
    if not path.is_file():
        raise ImageError(f"image not found: {path}")

    media_type = _media_type_for(str(path))
    raw = path.read_bytes()
    _capped(raw, path.name)

    return ImageBlock(media_type=media_type,
                      data=base64.b64encode(raw).decode("ascii"))


def image_block_from_bytes(filename: str, raw: bytes) -> ImageBlock:
    """Same contract, for bytes that never touched a filesystem (web UI
    uploads arrive base64-decoded over HTTP). Same shallow philosophy:
    the extension decides the MIME type, size is capped, pixels un-sniffed.
    """
    media_type = _media_type_for(filename)
    _capped(raw, filename)
    return ImageBlock(media_type=media_type,
                      data=base64.b64encode(raw).decode("ascii"))


#: How long asking a local server whether a model can see may take.
SEE_TIMEOUT = 5.0


def is_local(provider: str, base_url: str) -> bool:
    """Ollama, or any server on this machine."""
    from urllib.parse import urlparse

    host = (urlparse(base_url or "").hostname or "").lower()
    return provider == "ollama" or host in ("localhost", "127.0.0.1", "::1")


def sees_pictures(provider: str, base_url: str, model: str) -> bool:
    """Whether a picture may go to this model. A cloud model takes them.
    A local one only when Ollama lists "vision" among its capabilities;
    unknown (another server, no answer) is no, because a picture sent to
    a model that can't take one fails the whole turn."""
    import httpx

    if not is_local(provider, base_url):
        return True
    root = (base_url or "http://localhost:11434").rstrip("/").removesuffix("/v1")
    try:
        response = httpx.post(f"{root}/api/show", json={"model": model},
                              timeout=SEE_TIMEOUT)
        return "vision" in (response.json().get("capabilities") or [])
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return False
