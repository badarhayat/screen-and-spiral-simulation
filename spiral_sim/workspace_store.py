"""Persist the browser workspace on disk.

The page autosaves here so a refresh or a restarted Windows executable
comes back to the same feed, settings, and results. The file lives in the
user's application data folder, not in the temporary folder a one-file
executable unpacks into.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE_VERSION = 1
STEPS = {
    "welcome",
    "sizes",
    "minerals",
    "grades",
    "circuit",
    "data",
    "plant",
    "splitters",
    "results",
}


class WorkspaceError(ValueError):
    """The saved file is unreadable or the requested write is unsafe."""


def workspace_directory() -> Path:
    """Directory that survives closing the program."""
    override = os.environ.get("SPIRAL_WORKSPACE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / "SpiralConcentrator"


def workspace_file() -> Path:
    return workspace_directory() / "workspace.json"


def read_workspace() -> dict | None:
    """Return the saved document, or None when no file exists.

    A damaged file is left on disk and reported. It is not replaced here.
    """
    path = workspace_file()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"The saved session could not be read ({exc}). It was left unchanged.") from exc
    if not isinstance(data, dict) or data.get("version") != WORKSPACE_VERSION:
        raise WorkspaceError("The saved session file is not a version this program can read. It was left unchanged.")
    if not isinstance(data.get("draft"), dict):
        raise WorkspaceError("The saved session is missing its working copy. It was left unchanged.")
    if data.get("checkpoint") is not None and not isinstance(data.get("checkpoint"), dict):
        raise WorkspaceError("The saved checkpoint is not valid. It was left unchanged.")
    return data


def draft_is_meaningful(draft: dict) -> bool:
    """True when the draft holds a feed, a result, or a step past the start page."""
    if not isinstance(draft, dict):
        return False
    if isinstance(draft.get("result"), dict):
        return True
    if draft.get("step") not in (None, "", "welcome"):
        return True
    state = draft.get("state") if isinstance(draft.get("state"), dict) else {}
    for size in state.get("sizes") or []:
        if not isinstance(size, dict):
            continue
        if any(str(size.get(key) or "").strip() for key in ("d_low", "d_high", "mass_percent")):
            return True
    for mineral in state.get("minerals") or []:
        if not isinstance(mineral, dict):
            continue
        if str(mineral.get("name") or "").strip() or str(mineral.get("density") or "").strip():
            return True
    for bucket in ("overall", "yields"):
        values = state.get(bucket) or {}
        if isinstance(values, dict) and any(str(value or "").strip() for value in values.values()):
            return True
    return False


def _clean_draft(draft: dict) -> dict:
    if not isinstance(draft, dict):
        raise WorkspaceError("The working copy needs to be an object.")
    cleaned = dict(draft)
    if cleaned.get("precision") not in (4, 5):
        cleaned["precision"] = 4
    if cleaned.get("step") not in STEPS:
        cleaned["step"] = "welcome"
    if "state" in cleaned and cleaned["state"] is not None and not isinstance(cleaned["state"], dict):
        raise WorkspaceError("The saved inputs are not valid.")
    if cleaned.get("result") is not None and not isinstance(cleaned.get("result"), dict):
        raise WorkspaceError("The saved results are not valid.")
    return cleaned


def _write_document(document: dict) -> None:
    path = workspace_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def save_workspace(draft: dict, *, commit: bool = False, replace: bool = False) -> dict:
    """Store the working copy.

    An empty page is not written over a session that already has data unless
    ``replace`` is set. A file that cannot be read is not overwritten either.
    ``commit`` records the current copy as the checkpoint Discard returns to.
    """
    cleaned = _clean_draft(draft)
    try:
        existing = read_workspace()
    except WorkspaceError:
        if not replace:
            raise
        existing = None
    if (
        existing
        and draft_is_meaningful(existing.get("draft") or {})
        and not draft_is_meaningful(cleaned)
        and not replace
    ):
        raise WorkspaceError("An empty page was not saved over the existing session.")
    checkpoint = None if existing is None else existing.get("checkpoint")
    if commit:
        checkpoint = copy.deepcopy(cleaned)
    document = {
        "version": WORKSPACE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "draft": cleaned,
        "checkpoint": checkpoint,
    }
    _write_document(document)
    return document


def discard_to_checkpoint() -> dict:
    """Replace the working copy with the last simulation checkpoint."""
    existing = read_workspace()
    if not existing or not isinstance(existing.get("checkpoint"), dict):
        raise WorkspaceError("There is no earlier checkpoint to restore. Run a simulation first, or reset the page.")
    document = {
        "version": WORKSPACE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "draft": copy.deepcopy(existing["checkpoint"]),
        "checkpoint": existing["checkpoint"],
    }
    _write_document(document)
    return document


def clear_workspace() -> None:
    """Delete the saved session. Used only after the user confirms a reset."""
    path = workspace_file()
    if path.exists():
        path.unlink()
    temporary = path.with_suffix(".json.tmp")
    if temporary.exists():
        temporary.unlink()


def import_workspace(document: dict) -> dict:
    """Replace the saved session with a backup file the user chose."""
    if not isinstance(document, dict):
        raise WorkspaceError("That backup is not a session file.")
    if document.get("version") != WORKSPACE_VERSION or not isinstance(document.get("draft"), dict):
        raise WorkspaceError("That backup is not a spiral workspace file.")
    draft = _clean_draft(document["draft"])
    checkpoint = document.get("checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, dict):
            raise WorkspaceError("That backup has a checkpoint this program cannot read.")
        checkpoint = _clean_draft(checkpoint)
    stored = {
        "version": WORKSPACE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "draft": draft,
        "checkpoint": checkpoint,
    }
    _write_document(stored)
    return stored
