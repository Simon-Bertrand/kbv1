"""Allow python -m pdfparse.cachecheck in an editable source checkout."""
from .cli import main

if __name__ == '__main__':
    raise SystemExit(main())
