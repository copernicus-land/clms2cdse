import os
import re
import csv
import logging
from collections import defaultdict

import boto3
from botocore.config import Config

# ==========================
# CONFIGURATION
# ==========================
S3_BUCKET = "delivery"
S3_ENDPOINT_URL = "https://s3.waw3-1.cloudferro.com"
S3_PREFIX = "continuous/"   # keep empty if S3 looks like: <bucket>/<product_abbr>/<tile>.tif

LOCAL_BASE_DIR = r"A:/Copernicus"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "tile_comparison.csv")

DEBUG_SUMMARY = True


# ==========================
# LOGGING
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================
# PATTERNS
# ==========================
# Example:
# CLMS_HRLVLCC_GRA_S2017_R10m_E09N27_03035_V01_R00.tif
# -> base = CLMS_HRLVLCC_GRA_S2017_R10m_E09N27_03035_V01
# -> revision = 0
CLMS_PATTERN = re.compile(r"(.+_V\d+)_R(\d+)\.tif$", re.IGNORECASE)

# Example:
# GRA2017 -> product_abbr = GRA
INSTANCE_PATTERN = re.compile(r"^([A-Z]+)(\d{4})$")


# ==========================
# HELPERS
# ==========================
def get_s3_credentials():
    """
    Read S3 credentials from environment variables.
    """
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        raise RuntimeError(
            "Missing S3 credentials. Please set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY as environment variables."
        )

    logging.info("Found credentials in environment variables.")
    return access_key, secret_key


def extract_base_and_revision(filename):
    """
    Extract base tile name and revision from a CLMS-style filename.

    Example:
    CLMS_HRLVLCC_GRA_S2017_R10m_E09N27_03035_V01_R02.tif
    -> ("CLMS_HRLVLCC_GRA_S2017_R10m_E09N27_03035_V01", 2)
    """
    match = CLMS_PATTERN.match(filename)
    if not match:
        return None, None

    base_name = match.group(1)
    revision = int(match.group(2))
    return base_name, revision


def extract_product_abbr_from_relative_path(rel_parts):
    """
    Find a product instance folder like GRA2017 / BCD2018 inside the path
    and extract the product abbreviation from it.

    Example:
    ['Grassland', 'Grassland', 'GRA2017', 'GRA2017_10m']
    -> 'GRA'
    """
    for part in rel_parts:
        match = INSTANCE_PATTERN.match(part)
        if match:
            return match.group(1)
    return None


# ==========================
# S3 SCAN
# ==========================
def get_s3_tiles():
    """
    Traverse S3 bucket and build:
    {
        product_abbr: {
            base_tile_name: highest_revision
        }
    }

    Expected S3 structure:
    delivery/continuous/<product_instance>/<tile>.tif

    Example key:
    continuous/CPBSB2017/CLMS_HRLVLCC_CPBSB_S2017_R10m_E32N36_03035_V01_R01.tif
    """
    access_key, secret_key = get_s3_credentials()

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(s3={"addressing_style": "path"})
    )

    paginator = s3.get_paginator("list_objects_v2")
    tiles = defaultdict(dict)

    logging.info("Scanning S3 bucket...")

    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]

            if not key.lower().endswith(".tif"):
                continue

            parts = key.split("/")

            # Expect:
            # continuous / CPBSB2017 / filename.tif
            if len(parts) < 3:
                continue

            product_folder = parts[1]

            # Normalize CPBSB2017 -> CPBSB
            m = re.match(r"^([A-Z]+)(\d{4})$", product_folder)
            if m:
                product_abbr = m.group(1)
            else:
                product_abbr = product_folder

            filename = os.path.basename(key)

            base_name, revision = extract_base_and_revision(filename)
            if base_name is None:
                continue

            current_rev = tiles[product_abbr].get(base_name, -1)
            if revision > current_rev:
                tiles[product_abbr][base_name] = revision

    logging.info("Finished scanning S3.")
    return tiles
# ==========================
# LOCAL SCAN
# ==========================
def get_local_tiles():
    """
    Traverse local directory and build:
    {
        product_abbr: {
            base_tile_name: highest_revision
        }
    }

    Expected local structure example:
    A:/Copernicus/Grassland/Grassland/GRA2017/GRA2017_10m/*.tif

    Important rule:
    - product abbreviation comes from the instance folder (e.g. GRA2017 -> GRA)
    - not from the product full name folder (e.g. Grassland)
    """
    tiles = defaultdict(dict)

    logging.info("Scanning local directory...")

    for root, _, files in os.walk(LOCAL_BASE_DIR):
        tif_files = [f for f in files if f.lower().endswith(".tif")]
        if not tif_files:
            continue

        rel_path = os.path.relpath(root, LOCAL_BASE_DIR)
        rel_parts = os.path.normpath(rel_path).split(os.sep)

        product_abbr = extract_product_abbr_from_relative_path(rel_parts)
        if not product_abbr:
            continue

        for fname in tif_files:
            base_name, revision = extract_base_and_revision(fname)
            if base_name is None:
                continue

            current_rev = tiles[product_abbr].get(base_name, -1)
            if revision > current_rev:
                tiles[product_abbr][base_name] = revision

    logging.info("Finished scanning local directory.")
    return tiles


# ==========================
# COMPARISON
# ==========================
def compare_tiles(s3_tiles, local_tiles):
    """
    Compare S3 tiles against local tiles.

    Returns rows like:
    [product_abbreviation, tile_name, s3_revision, local_revision, status]
    """
    results = []

    for product_abbr, s3_product_tiles in s3_tiles.items():
        local_product_tiles = local_tiles.get(product_abbr, {})

        for base_name, s3_rev in s3_product_tiles.items():
            local_rev = local_product_tiles.get(base_name)

            if local_rev is None:
                results.append([
                    product_abbr,
                    base_name,
                    s3_rev,
                    "missing",
                    "missing"
                ])
            elif local_rev < s3_rev:
                results.append([
                    product_abbr,
                    base_name,
                    s3_rev,
                    local_rev,
                    "outdated"
                ])

    return results


# ==========================
# CSV WRITER
# ==========================
def write_csv(results):
    """
    Write comparison results to CSV.
    """
    logging.info(f"Writing results to {OUTPUT_FILE}")

    try:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "product_abbreviation",
                "tile_name",
                "s3_revision",
                "local_revision",
                "status"
            ])
            writer.writerows(results)

    except PermissionError:
        raise RuntimeError(
            f"Cannot write to {OUTPUT_FILE}. "
            "The file is probably open in Excel or another program."
        )

    logging.info("CSV writing complete.")


# ==========================
# DEBUG SUMMARY
# ==========================
def print_debug_summary(s3_tiles, local_tiles, results):
    logging.info(f"S3 products indexed: {sorted(s3_tiles.keys())}")
    logging.info(f"Local products indexed: {sorted(local_tiles.keys())}")

    all_products = sorted(set(s3_tiles.keys()) | set(local_tiles.keys()))
    for product in all_products:
        s3_count = len(s3_tiles.get(product, {}))
        local_count = len(local_tiles.get(product, {}))
        logging.info(f"DEBUG PRODUCT {product}: S3={s3_count}, LOCAL={local_count}")

    missing_count = sum(1 for row in results if row[4] == "missing")
    outdated_count = sum(1 for row in results if row[4] == "outdated")
    logging.info(
        f"DEBUG results summary: missing={missing_count}, outdated={outdated_count}"
    )

    debug_product = "GRA"
    if debug_product in s3_tiles:
        sample_s3 = list(s3_tiles[debug_product].items())[:5]
        logging.info(f"DEBUG S3 sample for {debug_product}: {sample_s3}")
    if debug_product in local_tiles:
        sample_local = list(local_tiles[debug_product].items())[:5]
        logging.info(f"DEBUG local sample for {debug_product}: {sample_local}")

# ==========================
# DEBUG
# ==========================

def debug_search_s3_for_cpbsb():
    access_key, secret_key = get_s3_credentials()

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(s3={"addressing_style": "path"})
    )

    paginator = s3.get_paginator("list_objects_v2")

    found = 0
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "CPBSB" in key:
                print("FOUND IN S3:", key)
                found += 1
                if found >= 20:
                    return

    if found == 0:
        print("No CPBSB keys found in S3 scan.")

# ==========================
# MAIN
# ==========================
def main():
    debug_search_s3_for_cpbsb()
    s3_tiles = get_s3_tiles()
    local_tiles = get_local_tiles()

    results = compare_tiles(s3_tiles, local_tiles)

    if DEBUG_SUMMARY:
        print_debug_summary(s3_tiles, local_tiles, results)

    write_csv(results)

    logging.info(f"Done. Found {len(results)} missing/outdated tiles.")


if __name__ == "__main__":
    main()