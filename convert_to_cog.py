"""
Convert one raster, or every raster in an input directory, into Cloud
Optimized GeoTIFFs (COGs) using gdal_translate, with optional validation of
the generated output.

Example:
    -- Single file --
    python convert_to_cog.py --input data/sample.tif --output output/result.tif
    
    -- All files in directory --
    python convert_to_cog.py --input-dir data --output-dir output
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("convert_to_cog")


# ── Settings ────────────────────────────────────────────────────────

@dataclass
class CogSettings:
    """All tunables in one place. Override via CLI flags or JSON config."""

    # Input
    input_file: str = ""
    input_dir: str = ""

    # Output
    output: str = ""                    # output COG path
    output_dir: str = ""                # output directory for batch conversion

    # COG options
    compress: str = "LZW"               # LZW, DEFLATE, ZSTD, NONE
    predictor: int = 2                  # 1=none, 2=horizontal, 3=floating point
    blocksize: int = 1024
    overview_resampling: str = "MODE"   # MODE, NEAREST, AVERAGE, BILINEAR
    overviews: str = "AUTO"             # AUTO, NONE, or explicit levels
    num_threads: str = "ALL_CPUS"
    bigtiff: str | None = None          # None=leave GDAL default; YES, NO, IF_NEEDED
    output_type: str = ""               # empty=keep source, Byte, UInt16, Float32...
    nodata: str | None = None           # nodata value

    # Behaviour
    validate: bool = True               # run gdalinfo after creation
    dry_run: bool = False               # print commands without executing

    @classmethod
    def from_json(cls, path: str) -> "CogSettings":
        with open(path) as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_json(self, path: str):
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)
        log.info(f"Settings saved to {path}")


# ── Helpers ─────────────────────────────────────────────────────────

def run(cmd: list[str], dry_run: bool = False) -> subprocess.CompletedProcess:
    """Run a command, log it, return result."""
    cmd_str = " ".join(cmd)
    if dry_run:
        log.info(f"[DRY RUN] {cmd_str}")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    log.info(f"Running: {cmd_str}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(f"FAILED (rc={result.returncode}): {result.stderr.strip()}")
        sys.exit(1)
    return result


def resolve_jobs(settings: CogSettings) -> list[tuple[str, str]]:
    """Resolve single-file or batch conversion jobs."""
    if settings.input_file:
        if not os.path.isfile(settings.input_file):
            log.error(f"Input file not found: {settings.input_file}")
            sys.exit(1)
        log.info(f"Using input raster: {settings.input_file}")
        return [(settings.input_file, settings.output)]

    if not settings.input_dir:
        log.error("No input: set --input or --input-dir")
        sys.exit(1)
    if not os.path.isdir(settings.input_dir):
        log.error(f"Input directory not found: {settings.input_dir}")
        sys.exit(1)

    input_files = sorted(
        os.path.join(settings.input_dir, name)
        for name in os.listdir(settings.input_dir)
        if os.path.isfile(os.path.join(settings.input_dir, name))
        and name.lower().endswith((".tif", ".tiff"))
    )
    if not input_files:
        log.error(f"No TIFF files found in input directory: {settings.input_dir}")
        sys.exit(1)

    os.makedirs(settings.output_dir, exist_ok=True)
    log.info(f"Found {len(input_files)} input rasters in {settings.input_dir}")
    return [
        (input_path, os.path.join(settings.output_dir, os.path.basename(input_path)))
        for input_path in input_files
    ]


# ── Pipeline ────────────────────────────────────────────────────────

def build_cog(input_path: str, output_path: str, settings: CogSettings) -> str:
    """Convert a source raster directly to COG."""
    cmd = [
        "gdal_translate",
        "-of", "COG",
        "-co", f"COMPRESS={settings.compress}",
        "-co", f"PREDICTOR={settings.predictor}",
        "-co", f"BLOCKSIZE={settings.blocksize}",
        "-co", f"OVERVIEW_RESAMPLING={settings.overview_resampling}",
        "-co", f"OVERVIEWS={settings.overviews}",
        "-co", f"NUM_THREADS={settings.num_threads}",
    ]

    if settings.bigtiff is not None:
        cmd.extend(["-co", f"BIGTIFF={settings.bigtiff}"])
    if settings.output_type:
        cmd.extend(["-ot", settings.output_type])
    if settings.nodata is not None:
        cmd.extend(["-a_nodata", str(settings.nodata)])

    cmd.extend([input_path, output_path])
    run(cmd, settings.dry_run)
    return output_path


def validate_cog(output: str, dry_run: bool = False):
    """Step 3: Quick validation via gdalinfo."""
    result = run(["gdalinfo", output], dry_run)
    if not dry_run:
        for line in result.stdout.splitlines():
            if any(k in line for k in ["Size", "LAYOUT", "Band 1", "Overviews:"]):
                log.info(f"  {line.strip()}")
        size_mb = os.path.getsize(output) / (1024 * 1024)
        log.info(f"Output size: {size_mb:,.1f} MB")


def pipeline(settings: CogSettings):
    """Run the full pipeline."""
    t0 = time.time()
    log.info("=" * 60)
    log.info("COG Builder Pipeline")
    log.info("=" * 60)

    jobs = resolve_jobs(settings)
    total_jobs = len(jobs)

    for index, (input_path, output_path) in enumerate(jobs, start=1):
        log.info(f"[{index}/{total_jobs}] Converting to COG → {output_path}")
        build_cog(input_path, output_path, settings)

        if settings.validate:
            log.info(f"[{index}/{total_jobs}] Validating...")
            validate_cog(output_path, settings.dry_run)

    elapsed = time.time() - t0
    log.info(f"Done in {elapsed:.0f}s ✓ ({total_jobs} file(s))")


# ── CLI ─────────────────────────────────────────────────────────────

def parse_args() -> CogSettings:
    p = argparse.ArgumentParser(
        description="Generic raster → COG pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Input/Output
    p.add_argument("--input", help="Input raster path")
    p.add_argument("--input-dir", help="Directory containing input rasters")
    p.add_argument("--output", "-o", help="Output COG path")
    p.add_argument("--output-dir", help="Output directory for batch conversion")

    # Config
    p.add_argument("--config", "-c", help="Load settings from JSON file")
    p.add_argument("--save-config", help="Save current settings to JSON and exit")

    # COG options
    p.add_argument("--compress", default="LZW", choices=["LZW", "DEFLATE", "ZSTD", "NONE"])
    p.add_argument("--predictor", type=int, default=2)
    p.add_argument("--blocksize", type=int, default=1024)
    p.add_argument("--overview-resampling", default="MODE")
    p.add_argument("--overviews", default="AUTO")
    p.add_argument("--bigtiff", default=None, choices=["YES", "NO", "IF_NEEDED"])
    p.add_argument("--output-type", default="", help="GDAL output type (Byte, UInt16, ...)")
    p.add_argument("--nodata", default=None, help="Nodata value")

    # Behaviour
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    args = p.parse_args()

    # Start from config file or defaults
    if args.config:
        settings = CogSettings.from_json(args.config)
    else:
        settings = CogSettings()

    # CLI overrides
    if args.input:
        settings.input_file = args.input
    if args.input_dir:
        settings.input_dir = args.input_dir
    if args.output:
        settings.output = args.output
    if args.output_dir:
        settings.output_dir = args.output_dir

    settings.compress = args.compress
    settings.predictor = args.predictor
    settings.blocksize = args.blocksize
    settings.overview_resampling = args.overview_resampling
    settings.overviews = args.overviews
    settings.bigtiff = args.bigtiff
    settings.output_type = args.output_type
    settings.nodata = args.nodata

    settings.validate = not args.no_validate
    settings.dry_run = args.dry_run

    # Save config mode
    if args.save_config:
        settings.to_json(args.save_config)
        sys.exit(0)

    # Validate required
    using_single_input = bool(settings.input_file)
    using_batch_input = bool(settings.input_dir)
    if using_single_input == using_batch_input:
        p.error("set exactly one of --input or --input-dir")

    if using_single_input and not settings.output:
        p.error("--output is required when using --input")
    if using_batch_input and not settings.output_dir:
        p.error("--output-dir is required when using --input-dir")

    return settings


if __name__ == "__main__":
    settings = parse_args()
    pipeline(settings)