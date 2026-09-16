#!/usr/bin/env python3
"""Rewrites router/nginx.conf from router/nginx.conf.template with weighted
blue/green upstream servers.

nginx does not accept a literal weight=0, so a color with weight 0 is
omitted from the upstream entirely rather than written with weight=0.

Usage: render_upstream.py <blue_weight 0-100> <green_weight 0-100>
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main(blue_weight: int, green_weight: int) -> None:
    if blue_weight == 0 and green_weight == 0:
        print("refusing to render an upstream with zero total weight", file=sys.stderr)
        sys.exit(1)

    lines = []
    if blue_weight > 0:
        lines.append(f"        server app-blue:8000 weight={blue_weight};")
    if green_weight > 0:
        lines.append(f"        server app-green:8000 weight={green_weight};")
    servers_block = "\n".join(lines)

    template = (ROOT / "router" / "nginx.conf.template").read_text()
    rendered = template.replace("__UPSTREAM_SERVERS__", servers_block)
    (ROOT / "router" / "nginx.conf").write_text(rendered)
    print(f"rendered upstream: blue={blue_weight} green={green_weight}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: render_upstream.py <blue_weight> <green_weight>", file=sys.stderr)
        sys.exit(2)
    main(int(sys.argv[1]), int(sys.argv[2]))
