#!/usr/bin/env python3
"""
lawn_bounce_heatmap.py
----------------------
Turn a Physics Toolbox Sensor Suite recording (g-force + GPS) into an interactive
heatmap of where your mower bounces the most, drawn over satellite imagery of
your own property.

Usage:
    python lawn_bounce_heatmap.py my_mowing.csv
    python lawn_bounce_heatmap.py my_mowing.csv -o map.html --sample 0 --basemap esri

Run with --help for every option.
"""

import argparse
import sys

import numpy as np
import pandas as pd
import folium
from folium.plugins import HeatMap

BASEMAPS = {
    "google": {
        "tiles": "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        "attr": "Imagery &copy; Google",
    },
    "esri": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    },
}


def parse_args():
    p = argparse.ArgumentParser(
        description="Build a mower-bounce heatmap from a Physics Toolbox CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("csv", help="CSV exported from Physics Toolbox Sensor Suite (Multi Record)")
    p.add_argument("-o", "--output", default="bounce_heatmap.html", help="output HTML file")
    p.add_argument("--sample", type=int, default=3000,
                   help="random rows to plot (0 = use every row; bigger = denser, slower map)")
    p.add_argument("--top-pct", type=float, default=95,
                   help="percentile at or above which points get a red 'worst bump' dot")
    p.add_argument("--min-speed", type=float, default=0.0,
                   help="drop rows slower than this many m/s (e.g. 0.3 removes time sitting still)")
    p.add_argument("--lawn-fraction", type=float, default=0.5,
                   help="how much of the screen the mowed area should fill when the map opens (0-1)")
    p.add_argument("--basemap", choices=BASEMAPS.keys(), default="google",
                   help="satellite imagery source")
    p.add_argument("--max-native-zoom", type=int, default=20,
                   help="deepest real imagery zoom level; lower to 19 if tiles turn gray when zoomed in")
    p.add_argument("--hotspots", metavar="CSV",
                   help="also save the red-dot locations and their bounce values to this CSV")
    p.add_argument("--seed", type=int, default=42, help="random seed for sampling")
    return p.parse_args()


def find_col(df, *names):
    """Return the first column whose name matches one of `names` (case-insensitive,
    ignoring spaces and any '(units)' suffix). Physics Toolbox column names vary a
    little between app versions."""
    normalized = {c.split("(")[0].strip().lower().replace(" ", ""): c for c in df.columns}
    for n in names:
        if n.lower() in normalized:
            return normalized[n.lower()]
    return None


def load(csv_path, min_speed):
    df = pd.read_csv(csv_path)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]  # trailing comma in app exports

    lat, lon = find_col(df, "latitude", "lat"), find_col(df, "longitude", "lon", "lng")
    if not (lat and lon):
        sys.exit("No Latitude/Longitude columns found. Was GPS turned on in Multi Record?")

    gx, gy, gz = find_col(df, "gfx"), find_col(df, "gfy"), find_col(df, "gfz")
    total = find_col(df, "tgf", "gftotal", "totalgf")
    if gx and gy and gz:
        bounce = np.sqrt(df[gx] ** 2 + df[gy] ** 2 + df[gz] ** 2)
    elif total:
        bounce = df[total]
    else:
        sys.exit("No g-force columns (gFx/gFy/gFz) found. Was the G-Force Meter turned on?")

    out = pd.DataFrame({"lat": df[lat], "lon": df[lon], "bounce": bounce})
    speed = find_col(df, "speed")
    if speed:
        out["speed"] = df[speed]

    # Rows logged before the GPS gets a fix come through as 0,0 or blank
    out = out.dropna(subset=["lat", "lon", "bounce"])
    out = out[(out["lat"] != 0) & (out["lon"] != 0)]
    if min_speed > 0:
        if "speed" not in out:
            print("Note: no speed column found, so --min-speed was ignored.")
        else:
            out = out[out["speed"] >= min_speed]
    if out.empty:
        sys.exit("No usable rows left after cleaning. Check that GPS had a fix while recording.")
    return out


def add_legend(m, threshold, pct):
    html = f"""
    <div style="position:fixed;bottom:24px;left:24px;z-index:9999;background:rgba(20,32,26,.88);
                color:#f4f1ea;padding:12px 16px;border-radius:10px;font:14px/1.4 Arial,sans-serif;
                max-width:260px">
      <b>Mower bounce</b><br>
      Heat = how hard the ride was (log-scaled g-force)<br>
      <span style="color:#ff4d4d">&#9679;</span> worst {100 - pct:g}% of sampled points
      (&ge; {threshold:.2f} g)<br>
      <span style="opacity:.75">1.0 g = sitting still. Click a red dot for its value.</span>
    </div>"""
    m.get_root().html.add_child(folium.Element(html))


def build_map(df, args):
    pts = df.sample(n=args.sample, random_state=args.seed) if 0 < args.sample < len(df) else df

    south, north = df["lat"].min(), df["lat"].max()
    west, east = df["lon"].min(), df["lon"].max()
    frac = min(max(args.lawn_fraction, 0.05), 1.0)
    pad = (1 / frac - 1) / 2
    lat_pad, lon_pad = (north - south) * pad, (east - west) * pad

    m = folium.Map(location=[(south + north) / 2, (west + east) / 2], zoom_start=19,
                   max_zoom=22, tiles=None, zoom_snap=0.25, zoom_delta=0.25)
    base = BASEMAPS[args.basemap]
    folium.TileLayer(tiles=base["tiles"], attr=base["attr"], name="Satellite",
                     max_zoom=22, max_native_zoom=args.max_native_zoom).add_to(m)

    heat = folium.FeatureGroup(name="Bounce heatmap")
    HeatMap(pts[["lat", "lon"]].assign(w=np.log1p(pts["bounce"])).values.tolist(),
            min_opacity=0.5, max_zoom=22, radius=15, blur=10).add_to(heat)
    heat.add_to(m)

    threshold = pts["bounce"].quantile(args.top_pct / 100)
    worst = pts[pts["bounce"] >= threshold].sort_values("bounce", ascending=False)
    dots = folium.FeatureGroup(name=f"Worst {100 - args.top_pct:g}% bumps")
    for r in worst.itertuples():
        folium.CircleMarker([r.lat, r.lon], radius=1, color="red", fill=True,
                            fill_color="red", fill_opacity=0.8, weight=3,
                            popup=f"Bounce: {r.bounce:.2f} g").add_to(dots)
    dots.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    add_legend(m, threshold, args.top_pct)
    m.fit_bounds([[float(south - lat_pad), float(west - lon_pad)],
                  [float(north + lat_pad), float(east + lon_pad)]])
    return m, pts, worst, threshold


def main():
    args = parse_args()
    df = load(args.csv, args.min_speed)
    m, pts, worst, threshold = build_map(df, args)
    m.save(args.output)

    print(f"Rows used:            {len(df):,} (plotted {len(pts):,})")
    print(f"Typical bounce:       {df['bounce'].median():.2f} g (median)")
    print(f"Hardest hit:          {df['bounce'].max():.2f} g")
    print(f"Red-dot threshold:    {threshold:.2f} g  ({len(worst)} dots)")
    print(f"Saved map:            {args.output}")
    if args.hotspots:
        worst[["lat", "lon", "bounce"]].round({"bounce": 3}).to_csv(args.hotspots, index=False)
        print(f"Saved hotspots:       {args.hotspots}")


if __name__ == "__main__":
    main()
