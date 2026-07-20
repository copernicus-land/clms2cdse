#!/usr/bin/env python3
"""
Convert QGIS colour map .txt files → SLD + QML.

Reads standard QGIS colour map export .txt files and generates:
  - .sld  (GeoServer / MapServer OGC Styled Layer Descriptor)
  - .qml  (QGIS native style XML)

Usage:
  python3 txt2sld2qml.py [source] --outdir ./output [--scale N]

The .txt format is:
  # QGIS Generated Color Map Export File
  INTERPOLATION:INTERPOLATED   (or DISCRETE)
  value, R, G, B, Alpha, label
"""

import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom
from urllib.request import urlopen
from pathlib import Path

NS_SLD = "http://www.opengis.net/sld"
NS_OG = "http://www.opengis.net/ogc"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("", NS_SLD)
ET.register_namespace("ogc", NS_OG)
ET.register_namespace("xsi", NS_XSI)

# ── Parse .txt colour map ───────────────────────────────────────────────────
def parse_colourmap(text: str) -> dict:
    lines = text.strip().splitlines()
    interpolation = "ramp"
    entries = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("INTERPOLATION:"):
            raw = line.split(":", 1)[1].strip().upper()
            interpolation = "ramp" if raw == "INTERPOLATED" else "intervals"
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            value = float(parts[0])
            r = int(parts[1])
            g = int(parts[2])
            b = int(parts[3])
            a = int(parts[4]) if len(parts) > 4 else 255
            label = parts[5] if len(parts) > 5 else ""
            entries.append(dict(value=value, r=r, g=g, b=b, a=a, label=label))
        except ValueError:
            continue
    return dict(interpolation=interpolation, entries=entries)

def rgb_to_hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"

# ── Generate SLD ────────────────────────────────────────────────────────────
def make_sld(name: str, cmap: dict, scale: float = 1.0) -> str:
    """Build an SLD 1.0.0 raster colour map. Quantity values from .txt × scale."""
    sld = ET.Element(f"{{{NS_SLD}}}StyledLayerDescriptor", {
        "version": "1.0.0",
        f"{{{NS_XSI}}}schemaLocation": (
            "http://www.opengis.net/sld "
            "http://schemas.opengis.net/sld/1.0.0/StyledLayerDescriptor.xsd"
        ),
    })
    nl = ET.SubElement(sld, f"{{{NS_SLD}}}NamedLayer")
    en = ET.SubElement(nl, f"{{{NS_SLD}}}Name")
    en.text = name
    us = ET.SubElement(nl, f"{{{NS_SLD}}}UserStyle")
    usn = ET.SubElement(us, f"{{{NS_SLD}}}Name")
    usn.text = name
    ust = ET.SubElement(us, f"{{{NS_SLD}}}Title")
    ust.text = name
    fts = ET.SubElement(us, f"{{{NS_SLD}}}FeatureTypeStyle")
    rule = ET.SubElement(fts, f"{{{NS_SLD}}}Rule")
    rs = ET.SubElement(rule, f"{{{NS_SLD}}}RasterSymbolizer")
    cm = ET.SubElement(rs, f"{{{NS_SLD}}}ColorMap", {
        "type": cmap["interpolation"],
    })
    for entry in cmap["entries"]:
        ET.SubElement(cm, f"{{{NS_SLD}}}ColorMapEntry", {
            "quantity": str(entry["value"] * scale),
            "color": rgb_to_hex(entry["r"], entry["g"], entry["b"]),
            "opacity": str(entry["a"] / 255.0),
            "label": entry["label"],
        })
    return _prettify(sld)

# ── Generate QML ────────────────────────────────────────────────────────────
def make_qml(name: str, cmap: dict) -> str:
    """Build a QGIS QML raster colour map style."""
    qgis = ET.Element("qgis", {
        "version": "3.34",
        "styleCategories": "AllStyleCategories",
        "minScale": "1e+8",
        "maxScale": "0",
        "hasScaleBasedVisibilityFlag": "0",
    })
    pipe = ET.SubElement(qgis, "pipe")
    vals = [e["value"] for e in cmap["entries"]]
    rast = ET.SubElement(pipe, "rasterrenderer", {
        "type": "singlebandpseudocolor", "band": "1", "opacity": "1",
        "alphaBand": "-1",
        "classificationMax": str(max(vals)),
        "classificationMin": str(min(vals)),
    })
    ET.SubElement(rast, "rasterTransparency")
    mmo = ET.SubElement(rast, "minMaxOrigin")
    mmo.text = "Unknown,Unknown,Unknown,Unknown,Unknown,No"
    shader = ET.SubElement(rast, "rastershader")
    crs = ET.SubElement(shader, "colorrampshader", {
        "minimumValue": str(min(vals)),
        "maximumValue": str(max(vals)),
        "classificationMode": "2" if cmap["interpolation"] == "ramp" else "1",
        "colorRampType": "INTERPOLATED" if cmap["interpolation"] == "ramp" else "DISCRETE",
        "labelPrecision": "4",
    })
    for entry in cmap["entries"]:
        ET.SubElement(crs, "item", {
            "value": str(entry["value"]),
            "label": entry["label"],
            "color": rgb_to_hex(entry["r"], entry["g"], entry["b"]),
            "alpha": str(entry["a"]),
            "attributes": "",
        })
    clip = ET.SubElement(pipe, "rasterclipperenderer", {
        "clippingLines": "", "clippingSource": "NoClipping", "clippingType": "0",
    })
    ET.SubElement(clip, "rasterTransparency")
    ET.SubElement(clip, "minMaxOrigin")
    return _prettify(qgis)

def _prettify(elem: ET.Element) -> str:
    rough = ET.tostring(elem, encoding="unicode")
    dom = minidom.parseString(rough.encode())
    result = dom.toprettyxml(indent="  ")
    lines = result.splitlines()
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    return "\n".join(lines)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    outdir = Path("output")
    if "--outdir" in sys.argv:
        outdir = Path(sys.argv[sys.argv.index("--outdir") + 1])

    scale = 1.0
    if "--scale" in sys.argv:
        scale = float(sys.argv[sys.argv.index("--scale") + 1])

    source = "."
    # Last non-flag argument is the source directory
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(positional) > 0:
        source = positional[-1]

    sld_dir = outdir / "sld"
    qml_dir = outdir / "qml"
    sld_dir.mkdir(parents=True, exist_ok=True)
    qml_dir.mkdir(parents=True, exist_ok=True)

    src_path = Path(source)
    if src_path.is_dir():
        files = sorted(src_path.glob("*.txt"))
    else:
        files = [src_path]

    for f in files:
        stem = f.stem
        print(f"  {f.name} … ", end="", flush=True)
        try:
            text = f.read_text()
        except Exception as e:
            print(f"READ FAIL: {e}")
            continue

        try:
            cmap = parse_colourmap(text)
        except Exception as e:
            print(f"PARSE FAIL: {e}")
            continue

        if not cmap["entries"]:
            print("no entries — skip")
            continue

        sld = make_sld(stem, cmap, scale=scale)
        (sld_dir / f"{stem}.sld").write_text(sld)

        qml = make_qml(stem, cmap)
        (qml_dir / f"{stem}.qml").write_text(qml)

        print(f"✓  ({len(cmap['entries'])} entries, {cmap['interpolation']})")

    print(f"\nDone! Files written to:")
    print(f"  SLD: {sld_dir.resolve()}/")
    print(f"  QML: {qml_dir.resolve()}/")

if __name__ == "__main__":
    main()