#!/usr/bin/env python3
"""Crop a PNG to an exact top-left physical size for the browser renderer."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("width", type=int)
    parser.add_argument("height", type=int)
    args = parser.parse_args()
    image = Image.open(args.input)
    if image.width < args.width or image.height < args.height:
        raise SystemExit(f"Cannot crop {image.size} to {(args.width, args.height)}")
    image.crop((0, 0, args.width, args.height)).save(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
