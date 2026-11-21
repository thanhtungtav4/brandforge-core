#!/usr/bin/env python3
"""Run the golden set: .venv/bin/python run_golden.py (exit code non-zero on any FAIL)."""
import sys

from content_core.runner import main

if __name__ == "__main__":
    sys.exit(main())
