"""Stub resource for the commentaries_fragments table — real rows come from
commentaries.py.

The fragments table is populated by ``commentaries.fetch_fragments_data``
during the ``commentaries`` resource's fragments phase (zeeker.toml:
``[resource.commentaries] fragments = true``). This file exists only because
zeeker's builder treats every ``[resource.X]`` section in zeeker.toml as a
buildable resource and fails the build when ``resources/X.py`` is missing —
and the ``[resource.commentaries_fragments]`` section must stay, because it
carries the Datasette column descriptions for the fragments table.

Returning an empty list makes zeeker record this resource as
``[SKIP] no data returned`` without touching the table.
"""

from typing import Any, Dict, List, Optional

from sqlite_utils.db import Table


def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    """No-op: rows are written by commentaries.fetch_fragments_data."""
    return []
