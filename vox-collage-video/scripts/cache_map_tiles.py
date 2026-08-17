"""
Download the raster tiles a MapGraphic scene needs into
`public/map_tiles/{z}/{x}/{y}.png`, so the render reads them off disk.

Why this exists
---------------
`MapGraphic` pointed straight at `https://tile.openstreetmap.org` on every
render. Two separate problems, one of them already live:

1. **That host now blocks it.** It answers with a "403 Access blocked - App is
   not following the tile usage policy" NOTICE IMAGE, served under HTTP 200.
   No exception, no 404. Caught only by opening a downloaded tile and looking
   at it. The default provider here is therefore CARTO Positron, which permits
   this use with attribution and is already pale grayscale - a better palette
   match than a desaturated OSM tile.
2. **A live fetch makes the render non-deterministic.** Missing tiles come out
   as blank grey squares, and because MapGraphic deliberately releases its
   `delayRender` handle on a 20s deadline rather than hanging, a broken map
   renders *successfully* with a hole in it. Nothing fails; the defect only
   surfaces when a human watches the file.

Usage
-----

    py -3 .claude/skills/vox-collage-video/scripts/cache_map_tiles.py \
        --center 126.9945,37.5340 --zoom 14 --radius 2

`--radius` is in tiles around the centre tile (2 -> a 5x5 block, which covers
a 1080x1920 frame at 256px tiles with margin for the Ken Burns drift). Pass
`--zoom` more than once to cache several zoom levels.

Then in the scene:

    import { MapGraphic, LOCAL_RASTER_STYLE } from "./MapGraphic";
    <MapGraphic center={[126.9945, 37.5340]} zoom={14} style={LOCAL_RASTER_STYLE} ... />

Re-running skips tiles already on disk, so it is cheap to call again after
nudging a centre point.
"""

import argparse
import hashlib
import math
import os
import sys
import time
import urllib.request

# CARTO Positron ("light_all"): a pale, near-grayscale OSM basemap. It is the
# default for two reasons - it permits this use with attribution, and it is
# already the right palette, so the map does not depend on a desaturation
# filter to fit the video's look.
#
# NOT tile.openstreetmap.org. That host now answers automated requests with a
# 403 "Access blocked - App is not following the tile usage policy" NOTICE
# IMAGE served under HTTP **200**. Nothing raises, nothing 404s, and the cache
# fills up with 135 identical warning graphics that a size check happily
# passes. That is exactly why `looks_like_placeholder` below exists.
PROVIDERS = {
    "carto-light": "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    "carto-voyager": "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
    "osm-de": "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def looks_like_placeholder(hashes):
    """Different coordinates must produce different bytes. When several do
    not, the server is handing back one canned image (a block notice, an
    error tile, an empty tile) rather than map data - the failure that this
    whole script exists to make impossible to ship unnoticed."""
    return len(hashes) >= 3 and len(set(hashes)) == 1


def deg2tile(lon, lat, zoom):
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if len(data) < 100:
        raise ValueError(f"suspiciously small response ({len(data)} bytes)")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(data)
    return len(data), hashlib.md5(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--center", required=True, help="'lon,lat' - same order MapGraphic takes")
    ap.add_argument("--zoom", type=int, action="append", required=True,
                    help="zoom level to cache; repeat for several")
    ap.add_argument("--radius", type=int, default=2,
                    help="tiles around the centre tile in each direction, default 2 (5x5 block)")
    ap.add_argument("--provider", default="carto-light", choices=sorted(PROVIDERS),
                    help="tile source, default carto-light (pale grayscale, palette-matched)")
    ap.add_argument("--min-zoom", type=int, default=0,
                    help="also cache every ancestor level down to this zoom (default 0) - "
                         "MapLibre requests them as overzoom placeholders while loading")
    ap.add_argument("--out-dir", default="public/map_tiles")
    ap.add_argument("--delay", type=float, default=0.15,
                    help="seconds between requests, default 0.15 - be a good citizen")
    args = ap.parse_args()

    try:
        lon, lat = (float(v) for v in args.center.split(","))
    except ValueError:
        sys.exit(f"--center must be 'lon,lat', got {args.center!r}")

    # MapLibre does not request only the target zoom. While the real tiles are
    # still in flight it walks UP the pyramid asking for each ancestor level
    # to overzoom as a placeholder - verified in a render log, which 404'd on
    # z12 down to z5 after only z14 had been cached. A cache missing those
    # ancestors still renders, just with grey flashes, so every level from the
    # requested zoom down to `--min-zoom` gets fetched (a small radius is
    # enough at low zoom: one tile covers a continent).
    levels = {}
    for zoom in args.zoom:
        levels[zoom] = args.radius
        for parent in range(args.min_zoom, zoom):
            levels[parent] = max(levels.get(parent, 0), 1)

    template = PROVIDERS[args.provider]
    digests = []
    got = skipped = failed = 0
    for zoom in sorted(levels):
        radius = levels[zoom]
        cx, cy = deg2tile(lon, lat, zoom)
        n = 2 ** zoom
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                x, y = cx + dx, cy + dy
                if not (0 <= x < n and 0 <= y < n):
                    continue
                dest = os.path.join(args.out_dir, str(zoom), str(x), f"{y}.png")
                if os.path.exists(dest):
                    skipped += 1
                    continue
                url = template.format(z=zoom, x=x, y=y)
                try:
                    size, digest = fetch(url, dest)
                    got += 1
                    digests.append(digest)
                    print(f"z{zoom} {x},{y}  {size/1024:.0f}KB")
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    failed += 1
                    print(f"z{zoom} {x},{y}  FAILED: {exc}")
                time.sleep(args.delay)

    print(f"\n{got} downloaded, {skipped} already cached, {failed} failed -> {args.out_dir}")

    if looks_like_placeholder(digests):
        print(f"\nABORT: every tile came back byte-identical. {args.provider} is serving one "
              f"canned image, not map data - typically a block/error notice returned under "
              f"HTTP 200. Delete {args.out_dir} and switch --provider.")
        sys.exit(1)
    if failed:
        print("Re-run to retry the failures. A render with missing tiles will NOT fail - it "
              "will quietly show grey squares - so do not proceed until this reports 0 failed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
