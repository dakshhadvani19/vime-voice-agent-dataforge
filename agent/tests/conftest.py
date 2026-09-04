"""Test path setup.

The kernel is a set of flat modules (``from events import ...``) because the brief's
repository layout in §16 is ``agent/*.py`` without a package wrapper. Keeping that layout
means tests and scripts add ``agent/`` (and ``evaluation/``) to ``sys.path`` rather than
forcing every contributor to install the package editable first.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "agent", ROOT / "evaluation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
