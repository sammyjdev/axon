"""Entrypoint for `python -m axon.pet`."""
import asyncio
import sys

from axon.pet.familiar import SHOW, main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.stdout.write(SHOW)
        sys.stdout.write("\n")
