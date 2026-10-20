"""Run the spiral simulator as ``python -m spiral_sim`` or as the Windows executable."""

from __future__ import annotations

import multiprocessing
import sys
import traceback

from spiral_sim.cli import hold_frozen_window, main

if __name__ == "__main__":
    # Required so a frozen Windows executable does not re-run this file
    # if a library starts a helper process.
    multiprocessing.freeze_support()
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        hold_frozen_window()
        code = 1
    sys.exit(code)
