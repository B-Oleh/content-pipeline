"""Atomic text-file writes.

Writing directly to a file's final path (e.g. Path.write_text) is not
atomic: if the process is killed, crashes, or loses power between opening
the file and finishing the write, the destination can be left truncated or
otherwise unparsable. This writes to a temporary file in the SAME directory
as the destination (required -- atomic replace only works within one
filesystem/volume) and then atomically replaces the destination with it, so
any read of the destination always sees either the complete previous
content or the complete new content, never a partial write.

This does not make concurrent writers from separate processes/threads safe
together -- two writers racing still means the last one to finish wins (a
lost update), not a corrupted file. Callers with more than one writer need
their own coordination (e.g. a lock file); none of this project's current
callers run concurrently.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write content to path, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Best-effort cleanup of the temp file. The destination is
        # untouched either way -- os.replace either fully ran or didn't.
        Path(tmp_name).unlink(missing_ok=True)
        raise
