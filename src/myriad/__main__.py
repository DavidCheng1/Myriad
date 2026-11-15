"""Entry point: python -m myriad [--provider mock|ollama]"""

import sys

from myriad.interface.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
