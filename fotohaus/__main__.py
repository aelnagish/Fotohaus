import os
import sys

_args = sys.argv[1:]
if _args and _args[0] not in ("--gui",) and not _args[0].startswith("-psn") and not os.path.exists(_args[0]):
    from .cli import main as cli_main
    sys.exit(cli_main(_args))
else:
    from .app import main
    sys.exit(main([sys.argv[0]] + [a for a in _args if a != "--gui"]))
