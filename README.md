# clms2cdse

Collection of tools and scripts for preparing, converting, and submitting
[CLMS](https://land.copernicus.eu) products to the
[Copernicus Data Space Ecosystem (CDSE)](https://dataspace.copernicus.eu/).

As part of the migration of Copernicus Land Monitoring Service products from
the legacy distribution infrastructure to CDSE, every dataset must be
re-packaged and validated to meet the CDSE ingestion requirements. This
repository provides reusable, well-documented scripts that automate those
steps — one tool per concern, easy to run locally or in CI/CD pipelines.

---

## Scripts

### `convert_to_cog.py` — Raster to Cloud Optimized GeoTIFF

Converts a single raster or every raster in a directory into a [Cloud
Optimized GeoTIFF](https://www.cogeo.org/) (COG). Built on top of GDAL's
`gdal_translate` with the `COG` driver, it handles the common practical
details that CDSE ingestion requires.

**What it does**

- Reads any GDAL-supported raster format (GeoTIFF, NetCDF, JP2, …).
- Optionally wraps the source in a VRT to strip unwanted palette / color-table
  metadata.
- Translates to a tiled, compressed, overview-rich COG.
- Validates the output with `gdalinfo` (dimensions, layout, overviews, size).
- Supports both **single-file** and **batch** (whole directory) modes.

**Key features**

- Palette-to-Gray conversion via VRT (safe — never touches source pixels)
- Configurable compression (LZW, DEFLATE, ZSTD, NONE)
- Adjustable blocksize, overview levels, predictor, and resampling
- `BIGTIFF`, `nodata`, and output data type overrides
- JSON config file support (save / load settings)
- Dry-run mode to preview commands before executing
- Verbose per-file progress logging with timing

**Quick start**

```bash
# Single file
python convert_to_cog.py --input data/sample.tif --output output/sample_cog.tif

# All TIFFs in a directory
python convert_to_cog.py --input-dir ./raw_data --output-dir ./cogs

# Custom compression + no validation
python convert_to_cog.py \
    --input scene.tif --output scene_cog.tif \
    --compress ZSTD --predictor 2 --no-validate

# Dry-run to check what would happen
python convert_to_cog.py --input-dir ./raw --output-dir ./cogs --dry-run

# Save/load settings as JSON
python convert_to_cog.py --save-config my_config.json --compress DEFLATE
python convert_to_cog.py --config my_config.json --input scene.tif --output out.tif
```

**Dependencies**

- Python ≥ 3.10
- [GDAL](https://gdal.org/) ≥ 3.6 (the `gdal_translate` and `gdalbuildvrt`
  commands must be on `PATH`)

---

## Layout

More scripts will be added here as the CDSE migration pipeline grows. Each
tool lives in its own top-level `.py` file with a matching section in this
README.

```
clms2cdse/
├── convert_to_cog.py   ← raster → COG
├── README.md
└── LICENSE.txt
```

---

## License

[EUPL-1.2](LICENSE.txt)