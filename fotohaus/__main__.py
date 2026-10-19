"""``python -m fotohaus [photos or folders…]`` opens the app; ``python -m fotohaus <command>`` runs the CLI."""
import os
import sys

CLI_COMMANDS = {"add", "scan", "analyze", "search", "info", "star", "unstar", "caption", "albums",
                "album-add", "people", "dupes", "export", "stats", "mcp", "-h", "--help"}

_args = [a for a in sys.argv[1:] if not a.startswith("-psn")]
if _args and _args[0] in CLI_COMMANDS:
    from .cli import main as cli_main
    sys.exit(cli_main(_args))

paths = []
for a in _args:
    if a == "--gui":
        continue
    if os.path.exists(a):
        paths.append(a)
    else:
        print(f"fotohaus: no such file or folder: {a}", file=sys.stderr)
from .app import main  # noqa: E402
sys.exit(main([sys.argv[0]] + paths))
