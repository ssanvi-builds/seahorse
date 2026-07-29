"""``python -m seahorse.mcp`` launcher — delegates to ``seahorse.mcp.profile.main``.

Kept as a thin module so ``main()`` lives in ``profile`` (the canonical server
module) and is shared by the ``seahorse-mcp`` console script and ``python -m``.
This module is loaded only on ``python -m seahorse.mcp``; a bare
``import seahorse.mcp`` does not load it (and so does not pull ``seahorse.cli``).
"""

from __future__ import annotations

import sys

from seahorse.mcp.profile import main

if __name__ == "__main__":
    sys.exit(main())