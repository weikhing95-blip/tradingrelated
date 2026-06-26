"""Root entrypoint for platform build auto-detection.

Railway's Railpack (and Nixpacks) look for a top-level ``main.py``/``app.py`` to
derive a start command. This thin shim delegates to the real package entrypoint
so the bot runs identically to ``python -m mag7bot.app``. The Dockerfile and
``railway.json`` are the preferred build paths; this is a fallback.
"""

from mag7bot.app import main

if __name__ == "__main__":
    main()
