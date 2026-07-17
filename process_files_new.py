"""Process GeoTIFFs: create COGs + generate comparison reports.

This script traverses an input root folder recursively, finds GeoTIFF files (``.tif``),
optionally converts each one into a Cloud Optimized GeoTIFF (COG), and optionally
collects diagnostic metadata from the original and output files for comparison.

Diagnostics collected (when enabled) include:
    - Image dimensions, pixel resolution, and number of bands
    - Compression, layout, and block size
    - Photometric interpretation
    - Overview count and dimensions
    - Approximate header size (MB)
    - EPSG codes extracted from GeoTIFF tags

Outputs:
    - COG files written under the configured output folder, mirroring the input folder structure.
        For each input file, a per-file subfolder is created (named after the file stem) and all
        generated/copied outputs are written inside it.
    - A progressive pipe-delimited CSV log written to ``log_path`` (one row per processed file).
    - Excel report(s) written to ``report_dir``. The current implementation writes one report per
        detected product group (based on the parent folder name prefix) as the traversal proceeds.

Optional extras:
    - Legends: selects a legends source folder based on product/year heuristics and copies a
        filtered subset into a ``legend`` folder inside the per-file output directory.
    - Additional files: copies ``<basename>.tif.aux.xml`` and ``<stem>.xml`` (if present next to
        the input TIFF) into the per-file output directory.

Configuration:
    Update the paths and flags in the PARAMETERS section at the bottom of the file.
"""

import os
import shutil
import re
import subprocess
from datetime import datetime
import csv
import pandas as pd
from osgeo import gdal
from tifffile import TiffFile
import json

# Custom modules
import convert_to_cog

#colormapping for legends . JSON dictionary. load once 
SCRIPT_DIR = os.path.dirname(__file__)
json_file = os.path.join(SCRIPT_DIR, "colormaps.json")
with open(json_file, "r" , encoding="utf-8-sig") as f:
    LEGEND_MAPPING = json.load(f)

#print(f"GDAL version = {0}", format(gdal.VersionInfo())) #Added by tonnudottir

os.environ["GDAL_DATA"] = r"C:\ProgramData\anaconda3\Library\share\gdal" #Added by tonnudottir
gdal.UseExceptions() #Added by tonnudottir

# External tools (kept for parity with SynergiseFlow; GDAL_TRANSLATE unused when using Python API)
TIFFSET = shutil.which("tiffset") or r"C:\ProgramData\anaconda3\Library\bin\tiffset.exe" # tonnudottir replaced from "tiffset". That way it will work on systems where tiffset is already on the PATH and still fall back to your Anaconda installation when it isn't.
GDAL_TRANSLATE = "gdal_translate"


def run_command(cmd: list[str]) -> bool:
    """Run a shell command and print output (mirrors SynergiseFlow.py)."""
    try:
        print("  →", " ".join(cmd))
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            print("    ❌ Error:", (result.stderr or result.stdout).strip())
            return False
        else:
            print("    ✅ Done")
            return True
    except FileNotFoundError:
        print(f"    ❌ Error: command not found: {cmd[0]}")
        return False
    except Exception as e:
        print(f"    ❌ Error running command: {e}")
        return False

# ---------------------------------------------------------------------
# --- 1. COG CREATION FUNCTIONS ---
# ---------------------------------------------------------------------

def create_cog_with_convert_to_cog(
    input_tiff_path: str,
    output_cog_path: str
):

    settings = convert_to_cog.CogSettings()

    # Override everything needed.
    settings.input_file = input_tiff_path
    settings.output = output_cog_path
    #ukbharatha : update file from the input
    settings.report_file = report_file

    convert_to_cog.pipeline(settings)

# ---------------------------------------------------------------------
# --- 2. DIAGNOSTIC FUNCTIONS ---
# ---------------------------------------------------------------------
def get_header_size_mb(file_path):
    ds = gdal.Open(file_path, gdal.GA_ReadOnly)
    if not ds:
        return None
    band = ds.GetRasterBand(1)
    val = band.GetMetadataItem("BLOCK_OFFSET_0_0", "TIFF")
    ds = None
    if val is None:
        return None
    try:
        return int(val) / (1024 * 1024)
    except Exception:
        return None


def get_overviews_gdal(file_path):
    ds = gdal.Open(file_path, gdal.GA_ReadOnly)
    if not ds:
        return 0, None
    band = ds.GetRasterBand(1)
    n = band.GetOverviewCount()
    sizes = [band.GetOverview(i).XSize for i in range(n)] if n > 0 else None
    ds = None
    return n, sizes


def get_dimensions_and_epsg(file_path):
    width = height = None
    epsg2048 = epsg3072 = None
    try:
        with TiffFile(file_path) as tif:
            page = tif.pages[0]
            for tag in page.tags.values():
                if tag.code == 256:
                    width = tag.value
                elif tag.code == 257:
                    height = tag.value
            geo_tag = page.tags.get(34735)
            if geo_tag:
                geo = geo_tag.value
                if len(geo) >= 4:
                    nkeys = geo[3]
                    for i in range(nkeys):
                        base = 4 + i * 4
                        key_id = geo[base]
                        value_offset = geo[base + 3]
                        if key_id == 2048:
                            epsg2048 = value_offset
                        elif key_id == 3072:
                            epsg3072 = value_offset
    except Exception:
        pass
    return width, height, epsg2048, epsg3072


def get_photometric_interpretation(file_path):
    """Return the PhotometricInterpretation tag value, e.g., 'RGB', 'Palette', 'MinIsBlack', etc."""
    try:
        with TiffFile(file_path) as tif:
            return str(tif.pages[0].photometric)
    except Exception:
        pass
    return None


def get_cog_summary(file_path):
    ds = gdal.Open(file_path, gdal.GA_ReadOnly)
    if not ds:
        return {}
    out = {
        "Driver": ds.GetDriver().ShortName,
        "Size": (ds.RasterXSize, ds.RasterYSize, ds.RasterCount)
    }
    gt = ds.GetGeoTransform()
    if gt:
        out["Pixel Size"] = (gt[1], gt[5])
    band = ds.GetRasterBand(1)
    out["Block Size"] = band.GetBlockSize()
    

    # Color interpretation for all bands
    color_interps = []
    for i in range(1, ds.RasterCount + 1):
        b = ds.GetRasterBand(i)
        ci = gdal.GetColorInterpretationName(b.GetColorInterpretation())
        color_interps.append(ci)

    out["ColorInterp"] = ",".join(color_interps)

    img = ds.GetMetadata("IMAGE_STRUCTURE") or {}
    out["LAYOUT"] = img.get("LAYOUT")
    out["COMPRESSION"] = img.get("COMPRESSION")
    ds = None
    return out


def collect_summary(file_path):
    header_mb = get_header_size_mb(file_path)
    ovr_count, ovr_sizes = get_overviews_gdal(file_path)
    width, height, epsg2048, epsg3072 = get_dimensions_and_epsg(file_path)
    photometric = get_photometric_interpretation(file_path)
    cog = get_cog_summary(file_path)

    size = cog.get("Size")
    pxsize = cog.get("Pixel Size")
    block = cog.get("Block Size")

    return {
        "file": file_path,
        "driver": cog.get("Driver"),
        "size_x": size[0] if size else None,
        "size_y": size[1] if size else None,
        "bands": size[2] if size else None,
        "pixel_size_x": pxsize[0] if pxsize else None,
        "pixel_size_y": pxsize[1] if pxsize else None,
        "block_x": block[0] if block else None,
        "block_y": block[1] if block else None,
        "layout": cog.get("LAYOUT"),
        "compression": cog.get("COMPRESSION"),
        "photometric": photometric, # <---- Added
        "colorinterp": cog.get("ColorInterp"),  
        "header_mb": round(header_mb, 2) if header_mb is not None else None,
        "overview_count": ovr_count,
        "overview_sizes": ";".join(map(str, ovr_sizes)) if ovr_sizes else None,
        "epsg_2048": epsg2048,
        "epsg_3072": epsg3072,
    }

# ---------------------------------------------------------------------
# --- LOGGING HELPERS ---
# ---------------------------------------------------------------------
def log_status(
    handle,
    writer,
    index: int,
    total: int,
    file_path: str,
    cog_status: str,
    legends_status: str | None,
    additional_status: str | None,
    legend_path: str | None,
    legend_file_count: int | None,
    additional_file_count: int | None,
    *,
    durable: bool = True,
) -> None:
    """Print to console and append to CSV progressively.

    Console: basename and status with count.
    File: CSV row with pipe delimiter: file|cog_status|legends_status|additional_status|legend_path|legend_file_count|additional_file_count
    """
    short_name = os.path.basename(file_path)
    # Display minimal combined status in console
    parts = [cog_status]
    if legends_status:
        parts.append(legends_status)
    if additional_status:
        parts.append(additional_status)
    console_status = ';'.join(parts)
    print(f"{index}/{total} {short_name} - {console_status}")
    try:
        # Minimal, consistent CSV output with explicit columns
        writer.writerow([
            file_path,
            cog_status,
            legends_status or "",
            additional_status or "",
            legend_path or "",
            legend_file_count if legend_file_count is not None else 0,
            additional_file_count if additional_file_count is not None else 0,
        ])
        handle.flush()
        if durable:
            os.fsync(handle.fileno())
    except Exception:
        pass


# ---------------------------------------------------------------------
# --- 3. MAIN MERGED WORKFLOW ---
# ---------------------------------------------------------------------
def find_legends_source(legends_folder: str | None, tif_path: str) -> str | None:
    """Return the legends subfolder path to use for tif_path, or None if none found.

    Selection priority:
      1) product+rangeDigits (e.g., CPBSA10), exact match or first prefix match.
      2) product only (e.g., CPBSA), exact match or first prefix match.
    """
    if not legends_folder:
        return None
    if not os.path.isdir(legends_folder):
        return None
    # 1) Determine the context folder name from the source tif's parent directory
    context_name = os.path.basename(os.path.dirname(tif_path))

    # 2) Parse product, year, range_digits
    product = None
    year = None
    range_digits = None
    import re
    m = re.match(r"^(?P<product>[A-Za-z]+)(?P<year>\d+)(?:_(?P<suffix>.*))?$", context_name)
    if m:
        product = m.group("product")
        year = m.group("year")
        suffix = m.group("suffix") or ""
        if suffix:
            # Concatenate all digits in the suffix, e.g., '10m' -> '10', 'ad1d0dm' -> '10'
            digits = ''.join(ch for ch in suffix if ch.isdigit())
            range_digits = digits if digits else None

    # 3) Build subdir list once
    try:
        subdirs = [d for d in os.listdir(legends_folder)
                   if os.path.isdir(os.path.join(legends_folder, d))]
    except Exception:
        subdirs = []

    chosen_src = None
    
    # First: product + rangeDigits (e.g., CPBSA10)
    if product and range_digits:
        key = f"{product}{range_digits}"    
    #ukbharatha 
    #Third: mapping the legends using the JSON
    # First try JSON mapping
    resolution = None

    mapping = LEGEND_MAPPING.get(key)

    if mapping:
        mapped_folder = mapping["folder"]
        resolution = mapping.get("resolution")

        if mapped_folder in subdirs:
            chosen_src = os.path.join(legends_folder, mapped_folder)
                
    return (chosen_src, resolution) if chosen_src and os.path.isdir(chosen_src) else (None, None)

def copy_legends_filtered(src_dir: str, dest_dir: str, year: str | None,  filename_contains: str | None = None,) -> bool:
    try:
        os.makedirs(dest_dir, exist_ok=True)
        entries = [f for f in os.listdir(src_dir) if os.path.isfile(os.path.join(src_dir, f))]
        if filename_contains:
            entries = [f for f in entries if filename_contains in f]
    except Exception:
        return False
    matches = []
    filters = []
    if filename_contains:
        filters.append(filename_contains)
    if year and len(year) == 4 and year.isdigit():
        filters.append(year)
    for name in entries:
        if all(f in name for f in filters):
            matches.append(name)
    to_copy = matches if matches else entries
    if not to_copy:
        return False

    any_copied = False
    for name in to_copy:
        src_path = os.path.join(src_dir, name)
        dst_path = os.path.join(dest_dir, name)
        try:
            shutil.copy2(src_path, dst_path)
            any_copied = True
        except Exception:
            # Continue copying others; overall success if at least one copied
            pass
    return any_copied


def copy_additional_files(in_file: str, target_dir: str) -> str:
    """Copy two additional files next to the generated COG inside target_dir.

    Expected additional files (in the same folder as the input tif):
      - <basename>.tif.aux.xml  (where <basename>.tif is the original filename)
      - <stem>.xml              (where <stem> is filename without extension)

    Returns a status code string for logging:
      - ADDITIONAL_OK: both files copied
      - ADDITIONAL_PARTIAL: one of two copied (the other missing)
      - ADDITIONAL_MISSING: none found
      - ADDITIONAL_ERROR: an error occurred copying at least one file
    """
    src_dir = os.path.dirname(in_file)
    base_name = os.path.basename(in_file)
    stem, _ = os.path.splitext(base_name)

    files = [
        (os.path.join(src_dir, base_name + '.aux.xml'), os.path.join(target_dir, base_name + '.aux.xml')),
        (os.path.join(src_dir, stem + '.xml'), os.path.join(target_dir, stem + '.xml')),
    ]

    copied = 0
    errors = 0
    for src, dst in files:
        try:
            if os.path.exists(src):
                shutil.copy2(src, dst)
                copied += 1
        except Exception:
            errors += 1

    if errors > 0:
        return 'ADDITIONAL_ERROR'
    if copied == 2:
        return 'ADDITIONAL_OK'
    if copied == 1:
        return 'ADDITIONAL_PARTIAL'
    return 'ADDITIONAL_MISSING'


def process_and_report(input_root, output_folder, resampling, report_dir: str, log_path: str, legends_folder: str | None = None):
    """Process all .tif files under input_root, mirroring directory structure in output_folder.

    For each source file: create a per-file folder, generate COG (optional), copy legends folder
    into that per-file folder (optional), collect metadata and append to report.

    Respects global flags: DO_GENERATE_COGS, DO_COLLECT_METADATA, DO_WRITE_REPORT.
    """
    if not os.path.isdir(input_root):
        print(f"Input folder does not exist: {input_root}")
        return

    # Generated COGs and per-file folders are always written under output_folder
    os.makedirs(output_folder, exist_ok=True)

    # Set up progressive CSV log file (explicit log_path and report_dir provided by caller)
    log_handle = None
    log_writer = None
    try:
        os.makedirs(report_dir, exist_ok=True)
        log_dirname = os.path.dirname(log_path)
        if log_dirname:
            os.makedirs(log_dirname, exist_ok=True)
        # newline='' is recommended for csv module to avoid blank lines on Windows
        log_handle = open(log_path, 'w', encoding='utf-8', newline='')
        # Use pipe '|' as delimiter as requested
        log_writer = csv.writer(log_handle, delimiter='|', lineterminator='\n')
        # Lightweight header; only essential columns
        log_writer.writerow([
            "file",
            "cog_status",
            "legends_status",
            "additional_status",
            "legend_path",
            "legend_file_count",
            "additional_file_count",
        ])
        log_handle.flush()
    except Exception as e:
        print(f"Warning: could not open log file '{log_path}': {e}")

    # Collect all .tif files recursively
    print(f"Scanning: {input_root}")
    print(os.path.isdir(input_root))
    valid_files = []
    for root, _, files in os.walk(input_root):
        print(f"Folder: {root}")
        for name in files:
            if name.lower().endswith('.tif'):
                valid_files.append(os.path.join(root, name))

    if not valid_files:
        print("No .tif files found under input_root. Nothing to do.")
        return

    # Streaming per-product report: track current product and its rows
    current_product: str | None = None
    product_rows: list[dict] = []

    def flush_product_rows(prod: str | None, rows_to_write: list[dict]):
        if not DO_WRITE_REPORT or not rows_to_write:
            return
        token = prod or "unknown"
        df = pd.DataFrame(rows_to_write)
        if DO_COLLECT_METADATA:
            col_order = [
                "timestamp", "original_file", "new_file",
                "driver_orig", "driver_new",
                "size_x_orig", "size_x_new",
                "size_y_orig", "size_y_new",
                "bands_orig", "bands_new",
                "pixel_size_x_orig", "pixel_size_x_new",
                "pixel_size_y_orig", "pixel_size_y_new",
                "block_x_orig", "block_x_new",
                "block_y_orig", "block_y_new",
                "layout_orig", "layout_new",
                "compression_orig", "compression_new",
                "photometric_orig", "photometric_new",
                "colorinterp_orig", "colorinterp_new",
                "header_mb_orig", "header_mb_new",
                "overview_count_orig", "overview_count_new",
                "overview_sizes_orig", "overview_sizes_new",
                "epsg_2048_orig", "epsg_2048_new",
                "epsg_3072_orig", "epsg_3072_new"
            ]
            existing_cols = [c for c in col_order if c in df.columns]
            df = df[existing_cols]
        else:
            df = df[["timestamp", "original_file", "new_file"]]

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_token = re.sub(r"[^A-Za-z0-9_-]", "_", token)
        excel_path = os.path.join(report_dir, f"{safe_token}_Report_{ts}.xlsx")
        df.to_excel(excel_path, index=False)
        print(f"📊 Comparision Report saved at: {excel_path}")

    total = len(valid_files)
    last_parent_dir: str | None = None
    last_legends_src: str | None = None
    last_year: str | None = None
    # Ensure consistent traversal order to avoid interleaving products
    valid_files.sort()
    for idx, in_file in enumerate(valid_files, start=1):
        # Determine product from the context folder (parent of the tif)
        context_name_for_product = os.path.basename(os.path.dirname(in_file))
        m_prod = re.match(r"^(?P<product>[A-Za-z]+)", context_name_for_product)
        product_token = m_prod.group("product") if m_prod else context_name_for_product or "unknown"

        # If product changed, flush previous group's report
        if current_product is None:
            current_product = product_token
        elif product_token != current_product:
            flush_product_rows(current_product, product_rows)
            product_rows = []
            current_product = product_token

        base_name = os.path.basename(in_file)
        # Preserve relative folder structure under output_folder
        rel_dir = os.path.relpath(os.path.dirname(in_file), input_root)
        out_dir = os.path.join(output_folder, rel_dir) #if rel_dir != os.curdir else output_folder
        
        # Create a per-file folder named after the file (without extension)
        file_stem, ext = os.path.splitext(base_name)
        target_dir = os.path.join(out_dir, file_stem)
        os.makedirs(target_dir, exist_ok=True)
        # Place the generated file(s) inside this per-file folder
        out_file = os.path.join(target_dir, base_name)

        # Step 1: Create COG (optional)
        # Determine and log status
        cog_status = "SKIPPED"
        if DO_GENERATE_COGS:
            try:
                create_cog_with_convert_to_cog(in_file,out_file)
                cog_status = "CREATED"
            except Exception as e:
                cog_status = f"ERROR: {str(e)}"
                print(f"ERROR creating COG for {in_file} -> {out_file}: {e}")

        # Step 2: Copy legends folder after COG creation (or skip)
        legends_status = None
        # Cache legends source per input parent directory
        parent_dir = os.path.dirname(in_file)
        if parent_dir != last_parent_dir:
            last_legends_src , resolution  = find_legends_source(legends_folder, in_file)
            # Parse year from context folder name next to the tif
            context_name = os.path.basename(os.path.dirname(in_file))
            m = re.match(r"^(?P<product>[A-Za-z]+)(?P<year>\d+)(?:_.*)?$", context_name)
            last_year = m.group("year") if m else None
            last_parent_dir = parent_dir
        copied = False
        legend_dest_path = os.path.join(target_dir, "legend")
        if last_legends_src and os.path.isdir(last_legends_src):
            # Always name the destination folder 'legend' regardless of source folder name
            dest = legend_dest_path
            if os.path.exists(dest):
                copied = True
            else:
                copied = copy_legends_filtered(last_legends_src, dest, last_year,filename_contains=resolution)
        legends_status = "LEGENDS_OK" if copied else "LEGENDS_SKIP"

        # Determine legend folder path and file count for logging
        legend_path_value = legend_dest_path if os.path.isdir(legend_dest_path) else ""
        legend_files_count = 0
        if legend_path_value:
            try:
                legend_files_count = sum(
                    1 for n in os.listdir(legend_path_value)
                    if os.path.isfile(os.path.join(legend_path_value, n))
                )
            except Exception:
                legend_files_count = 0

        # Step 3: Copy additional files (.tif.aux.xml and .xml) next to the new .tif
        # Only attempt if the new .tif exists in target_dir
        additional_status = None
        if os.path.isfile(out_file):
            additional_status = copy_additional_files(in_file, target_dir)
        else:
            additional_status = 'ADDITIONAL_MISSING'

        # Count how many additional files are present next to the new .tif
        additional_files_count = 0
        try:
            if os.path.isfile(os.path.join(target_dir, base_name + '.aux.xml')):
                additional_files_count += 1
            if os.path.isfile(os.path.join(target_dir, file_stem + '.xml')):
                additional_files_count += 1
        except Exception:
            additional_files_count = 0

        # Step 4: Diagnostics for original & new (optional)
        if DO_COLLECT_METADATA:
            orig = collect_summary(in_file)
            # Only collect new metadata if the file exists (either generated or pre-existing)
            if os.path.isfile(out_file):
                new = collect_summary(out_file)
            else:
                new = {"file": out_file}
        else:
            # Minimal info when metadata collection is disabled
            orig = {"file": in_file}
            new = {"file": out_file}

        # Step 5: Merge into one row
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "original_file": orig.get("file"),
            "new_file": new.get("file"),
        }
        if DO_COLLECT_METADATA:
            for key, val in orig.items():
                if key != "file":
                    row[f"{key}_orig"] = val
            for key, val in new.items():
                if key != "file":
                    row[f"{key}_new"] = val

        product_rows.append(row)

        # Progressive log after each file
        if log_handle is not None and log_writer is not None:
            # Durable per-row write so progress is preserved on crash
            log_status(
                log_handle,
                log_writer,
                idx,
                total,
                in_file,
                cog_status,
                legends_status,
                additional_status,
                legend_path_value,
                legend_files_count,
                additional_files_count,
                durable=True,
            )

    # Final flush for the last product
    flush_product_rows(current_product, product_rows)

    # Close the log file if opened
    try:
        if log_handle is not None:
            log_handle.close()
    except Exception:
        pass


# ---------------------------------------------------------------------
# --- PARAMETERS (functions will be run with the following parameters when executed.) ---
# ---------------------------------------------------------------------
# Provide input root folder. The script will recurse and process all .tif files,
# preserving the relative directory structure in the output folder.

#just enter the product to avaoid changing  the path or folders location in many places
product = "CropTypesConfidenceLayers"

input_folder = rf"E:\raw_data\Crops\CropTypes\{product}"
#ukbharatha: make sure this is from github repo (https://github.com/copernicus-land/colourmaps-legends/tree/main/colourmaps)
legends_folder = r"B:\Playground\colourmaps-legends\colourmaps" 
output_folder = rf"E:\delivery_data\Crops\CropTypes\{product}"
log_file = rf"E:\delivery_data\logs\Crops\CropTypes\{product}\log.csv"
report_dir = rf"E:\delivery_data\logs\Crops\CropTypes\{product}"
#ukbharatha :for full report including header size .
report_file = rf"E:\delivery_data\logs\Crops\CropTypes\{product}\report.csv"
resampling = "MODE"

DO_GENERATE_COGS = True #Create COGs from input files
DO_COLLECT_METADATA = True #Collect metadata for original/new files and write detailed report
DO_WRITE_REPORT = True #Write an Excel report at the end

def main() -> None:
    os.makedirs(report_dir, exist_ok=True)
    process_and_report(
        input_root=input_folder,
        output_folder=output_folder,
        resampling=resampling,
        report_dir=report_dir,
        log_path=log_file,
        legends_folder=legends_folder,
    )

if __name__ == "__main__":
    main()
