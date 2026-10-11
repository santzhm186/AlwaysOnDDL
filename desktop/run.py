"""Desktop entry point. Opening the UI never enables monitoring by itself."""
from ironwill.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
