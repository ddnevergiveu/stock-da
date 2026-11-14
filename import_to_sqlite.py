"""Compatibility entry point; importing this module never touches a database."""

import sys

from marketlab.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["import", *sys.argv[1:]]))
