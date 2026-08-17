"""
Search and download stock photos from the Pexels REST API.

Reads PEXELS_API_KEY from a .env file (searched upward from this script's
location) or from the environment. No browser automation needed.

List candidates before downloading (skim id/photographer/dimensions to
judge which has a plain/separated background rembg will cut out cleanly):

    python fetch_pexels.py list "scales of justice" --orientation portrait

Download a specific candidate (index from the `list` output, default 0):

    python fetch_pexels.py get "scales of justice" public/raw_scales.jpg \
        --orientation portrait --index 0

Options:
    --orientation {landscape,portrait,square}   default: portrait (this project is 9:16)
    --per-page N                                default: 8
    --index N                                   which result to download, default: 0
"""

import argparse
import os
import pathlib
import sys

import requests

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"


def load_api_key():
    key = os.environ.get("PEXELS_API_KEY")
    if key:
        return key
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        env_path = parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("PEXELS_API_KEY="):
                    return line.split("=", 1)[1].strip()
    sys.exit("PEXELS_API_KEY not found in environment or any .env file above this script.")


def search(query, orientation, per_page):
    headers = {"Authorization": load_api_key()}
    params = {"query": query, "orientation": orientation, "per_page": per_page}
    resp = requests.get(PEXELS_SEARCH_URL, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json().get("photos", [])


def cmd_list(args):
    photos = search(args.query, args.orientation, args.per_page)
    if not photos:
        print(f"No results for '{args.query}' (orientation={args.orientation}).")
        return
    for i, p in enumerate(photos):
        print(
            f"[{i}] id={p['id']} {p['width']}x{p['height']} "
            f"by {p['photographer']} avg_color={p.get('avg_color')} "
            f"page={p['url']}"
        )
        print(f"     preview: {p['src']['medium']}")


def cmd_get(args):
    photos = search(args.query, args.orientation, args.per_page)
    if not photos:
        sys.exit(f"No results for '{args.query}' (orientation={args.orientation}).")
    if args.index >= len(photos):
        sys.exit(f"Only {len(photos)} results, index {args.index} out of range.")
    p = photos[args.index]
    src_url = p["src"]["original"]
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = requests.get(src_url, timeout=30)
    img.raise_for_status()
    out_path.write_bytes(img.content)
    print(f"Saved {out_path} ({p['width']}x{p['height']}, photo by {p['photographer']}, {p['url']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List candidate photos for a query")
    p_list.add_argument("query")
    p_list.add_argument("--orientation", default="portrait", choices=["landscape", "portrait", "square"])
    p_list.add_argument("--per-page", type=int, default=8)
    p_list.set_defaults(func=cmd_list)

    p_get = sub.add_parser("get", help="Download one candidate photo")
    p_get.add_argument("query")
    p_get.add_argument("out")
    p_get.add_argument("--orientation", default="portrait", choices=["landscape", "portrait", "square"])
    p_get.add_argument("--per-page", type=int, default=8)
    p_get.add_argument("--index", type=int, default=0)
    p_get.set_defaults(func=cmd_get)

    args = parser.parse_args()
    args.func(args)
