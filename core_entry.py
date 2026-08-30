"""Entry point for the frozen core.

Freezing `ableton_ai/server.py` directly makes it `__main__` with no package,
so its relative imports fail. This wrapper keeps the package intact.
"""

from ableton_ai.server import main

if __name__ == "__main__":
    main()
