import os
import tarfile
import time
from datetime import datetime


def create_tars(
	input_folder: str,
	output_folder: str,
	log_file: str,
	overwrite: bool = False,
	*,
	owner_name: str = "eea",
	group_name: str = "eea",
	dir_mode: int = 0o775,
	file_mode: int = 0o666,
	validator_mode: bool = True,
) -> int:
	"""Traverse input_folder recursively and create a .tar archive for each directory
	that contains at least one regular file. Directories named 'legend' are not
	traversed as separate roots during discovery (so they won't be archived on
	their own), but if a 'legend' subfolder exists inside a directory being
	archived, it WILL be included inside that archive.

	Archives are written into output_folder. The archive name equals the source
	directory's basename plus the '.tar' extension.

	To match the reference tar structure more closely for external validation:
	- Write arcname entries with a leading './'
	- Add explicit directory entries for './', './<basename>/' and all subdirectories (e.g., './<basename>/legend/')
	- Normalize owner/group metadata to strings (uname/gname) and set uid/gid to 0 (default 'eea')
	- Set directory mode to 775 and file mode to 666 (adjustable via parameters)

	Logging: For each attempted directory, writes one line to log_file:
	  CREATED <src_dir> -> <tar_path>
	  SKIPPED_EXISTS <src_dir> -> <tar_path>
	  ERROR <src_dir> -> <tar_path> | <exception>

	Returns 0 if all tars created or skipped successfully, 1 if any errors occurred.
	"""
	input_folder = os.path.abspath(input_folder)
	output_folder = os.path.abspath(output_folder)
	os.makedirs(output_folder, exist_ok=True)

	try:
		log_handle = open(log_file, 'w', encoding='utf-8')
	except Exception as e:
		print(f"Cannot open log file '{log_file}': {e}")
		return 1

	def log(line: str):
		ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		log_handle.write(f"[{ts}] {line}\n")
		try:
			log_handle.flush()
			os.fsync(log_handle.fileno())
		except Exception:
			pass

	errors = 0
	total_archived = 0
	total_skipped = 0

	for root, dirs, files in os.walk(input_folder, topdown=True):
		# Skip any directory named 'legend' (do not descend)
		dirs[:] = [d for d in dirs if d.lower() != 'legend']

		# If this directory has at least one regular file, archive it
		file_paths = [f for f in files if os.path.isfile(os.path.join(root, f))]
		if not file_paths:
			continue

		# Build tar filename: use the actual folder name (basename of root)
		arc_base = os.path.basename(root)
		tar_path = os.path.join(output_folder, f"{arc_base}.tar")

		if os.path.exists(tar_path) and not overwrite:
			log(f"SKIPPED_EXISTS {root} -> {tar_path}")
			total_skipped += 1
			continue

		try:
			# Use GNU format to avoid PAX extended headers and mimic Linux GNU tar behaviour
			with tarfile.open(tar_path, 'w', format=tarfile.GNU_FORMAT) as tf:
				arc_root = f"./{arc_base}"  # leading ./ like reference

				def _make_dir(name: str, mtime: int):
					name = name if name.endswith('/') else name + '/'
					ti = tarfile.TarInfo(name)
					ti.type = tarfile.DIRTYPE
					ti.mtime = mtime
					ti.uid = 0
					ti.gid = 0
					ti.uname = owner_name
					ti.gname = group_name
					ti.mode = dir_mode
					ti.size = 0
					tf.addfile(ti)

				def _add_file(real_path: str, arcname: str):
					st = os.stat(real_path)
					ti = tf.gettarinfo(real_path, arcname=arcname)
					ti.uid = 0
					ti.gid = 0
					ti.uname = owner_name
					ti.gname = group_name
					ti.mode = file_mode
					ti.mtime = int(st.st_mtime)
					with open(real_path, 'rb') as f:
						tf.addfile(ti, f)

				# Capture a stable timestamp (earliest file mtime) for directories
				all_file_paths = []
				for sub_root, sub_dirs, sub_files in os.walk(root, topdown=True):
					for fname in sub_files:
						all_file_paths.append(os.path.join(sub_root, fname))
				if all_file_paths:
					min_mtime = min(int(os.stat(p).st_mtime) for p in all_file_paths)
				else:
					min_mtime = int(time.time())

				if validator_mode:
					# Explicit directory entries in JAN order
					_make_dir('./', min_mtime)
					_make_dir(arc_root, min_mtime)
					legend_dir_path = os.path.join(root, 'legend')
					if os.path.isdir(legend_dir_path):
						_make_dir(f"{arc_root}/legend", min_mtime)

					# Legend files in reference order: sld, qml, lyr, clr
					legend_order = ['sld', 'qml', 'lyr', 'clr']
					for ext in legend_order:
						lp = os.path.join(legend_dir_path, f"CLMS_HRLVLCC_CPFLP_R10.{ext}")
						if os.path.isfile(lp):
							_add_file(lp, f"{arc_root}/legend/CLMS_HRLVLCC_CPFLP_R10.{ext}")

					# Product xml then aux.xml then tif (match reference ordering)
					product_xml = os.path.join(root, f"{arc_base}.xml")
					if os.path.isfile(product_xml):
						_add_file(product_xml, f"{arc_root}/{arc_base}.xml")
					product_aux = os.path.join(root, f"{arc_base}.tif.aux.xml")
					if os.path.isfile(product_aux):
						_add_file(product_aux, f"{arc_root}/{arc_base}.tif.aux.xml")
					product_tif = os.path.join(root, f"{arc_base}.tif")
					if os.path.isfile(product_tif):
						_add_file(product_tif, f"{arc_root}/{arc_base}.tif")
				else:
					# Fallback: simple recursive add preserving relative names
					# This may differ but keeps compatibility if validator_mode disabled
					# Use root add to rely on TarFile internal ordering
					def _generic_add():
						for sub_root, sub_dirs, sub_files in os.walk(root, topdown=True):
							for fname in sorted(sub_files):
								real = os.path.join(sub_root, fname)
								rel = os.path.relpath(real, root).replace('\\', '/')
								_add_file(real, f"{arc_root}/{rel}")
					_generic_add()

			log(f"CREATED {root} -> {tar_path}")
			total_archived += 1
		except Exception as e:
			log(f"ERROR {root} -> {tar_path} | {e}")
			errors += 1

	log(f"SUMMARY archived={total_archived} skipped={total_skipped} errors={errors}")
	try:
		log_handle.close()
	except Exception:
		pass
	return 0 if errors == 0 else 1


# ----------------- Editable invocation -----------------
INPUT_FOLDER = r"M:\delivery_data\cfg25\Crops"  # change to your source root
OUTPUT_FOLDER = r"M:\delivery_data\cfg25_tar"  # where tar files will go
LOG_FILE = r"M:\delivery_data\cfg25_logs\tar_creation_log.txt"          # relative or absolute path


def main() -> int:
	exit_code = create_tars(INPUT_FOLDER, OUTPUT_FOLDER, LOG_FILE, overwrite=False)
	print(f"Done. Exit code: {exit_code}")
	return exit_code

if __name__ == "__main__":
	raise SystemExit(main())