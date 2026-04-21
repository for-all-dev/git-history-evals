"""Splice LLM-produced tactics into a Coq file in place of an `Admitted.` placeholder.

The function preserves the indentation of the `Admitted.` line it replaces so the
resulting proof body lines up with the rest of the file. It is a pure string
transformation — no filesystem or Coq interaction.
"""

from __future__ import annotations

import re


def patch_admitted(content: str, decl: str, tactics: str) -> str:
    """Replace the first standalone `Admitted.` line after `decl` with `tactics`.

    Search starts at the first occurrence of `decl` in `content`. If `decl` is
    not found, or no `Admitted.` line appears after it, `content` is returned
    unchanged. The indentation of the `Admitted.` line is applied to every
    non-empty line of `tactics`.
    """
    decl_pos = content.find(decl)
    if decl_pos == -1:
        return content

    search_region = content[decl_pos:]
    m = re.search(r'(?m)^\s*Admitted\.\s*$', search_region)
    if m is None:
        return content

    abs_start = decl_pos + m.start()
    abs_end   = decl_pos + m.end()

    indent_match = re.match(r'(\s*)', content[abs_start:])
    indent = indent_match.group(1) if indent_match else ""
    replacement = "\n".join(
        indent + line if line.strip() else line
        for line in tactics.strip().splitlines()
    )
    return content[:abs_start] + replacement + content[abs_end:]
