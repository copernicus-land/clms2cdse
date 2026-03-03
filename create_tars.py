import os
import tarfile
import time
import csv
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
	that contains a subdirectory named 'legend'.

	Discovery rule:
	- Walk recursively through input_folder
	- When a folder named 'legend' is found, archive its parent directory
	- Continue traversal recursively

	Archives are written into output_folder. The archive name equals the source
	directory's basename plus the '.tar' extension.

	To match the reference tar structure more closely for external validation:
	- Write arcname entries with a leading './'
	- Add explicit directory entries for './', './<basename>/' and all subdirectories (e.g., './<basename>/legend/')
	- Normalize owner/group metadata to strings (uname/gname) and set uid/gid to 0 (default 'eea')
	- Set directory mode to 775 and file mode to 666 (adjustable via parameters)

	Logging: Writes CSV rows to log_file with columns:
	  time,status,tar,file_count
	where file_count is the number of regular files written into the .tar.

	Returns 0 if all tars created or skipped successfully, 1 if any errors occurred.
	"""
	input_folder = os.path.abspath(input_folder)
	output_folder = os.path.abspath(output_folder)
	os.makedirs(output_folder, exist_ok=True)

	try:
		log_handle = open(log_file, 'w', encoding='utf-8', newline='')
	except Exception as e:
		print(f"Cannot open log file '{log_file}': {e}")
		return 1

	csv_writer = csv.writer(log_handle)
	csv_writer.writerow(["time", "status", "tar", "file_count"])

	def log(status: str, tar_path: str, file_count: int = 0):
		ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		csv_writer.writerow([ts, status, tar_path, file_count])
		try:
			log_handle.flush()
			os.fsync(log_handle.fileno())
		except Exception:
			pass

	errors = 0
	total_archived = 0
	total_skipped = 0

	for root, dirs, _files in os.walk(input_folder, topdown=True):
		# Trigger archive when current directory contains a subfolder named 'legend'
		has_legend_child = any(d.lower() == 'legend' for d in dirs)
		if not has_legend_child:
			continue

		# Build tar filename: use the actual folder name (basename of root)
		arc_base = os.path.basename(root)
		tar_path = os.path.join(output_folder, f"{arc_base}.tar")

		if os.path.exists(tar_path) and not overwrite:
			existing_file_count = 0
			try:
				with tarfile.open(tar_path, 'r') as tf_existing:
					existing_file_count = sum(1 for m in tf_existing.getmembers() if m.isfile())
			except Exception:
				existing_file_count = 0
			log("SKIPPED_EXISTS", tar_path, existing_file_count)
			total_skipped += 1
			continue

		try:
			file_counter = [0]
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
						file_counter[0] += 1

				# Capture a stable timestamp (earliest file mtime) for directories
				all_file_paths = []
				for sub_root, _sub_dirs, sub_files in os.walk(root, topdown=True):
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
						for sub_root, _sub_dirs, sub_files in os.walk(root, topdown=True):
							for fname in sorted(sub_files):
								real = os.path.join(sub_root, fname)
								rel = os.path.relpath(real, root).replace('\\', '/')
								_add_file(real, f"{arc_root}/{rel}")
					_generic_add()

			log("CREATED", tar_path, file_counter[0])
			total_archived += 1
		except Exception as e:
			log("ERROR", tar_path, 0)
			errors += 1

	try:
		log_handle.close()
	except Exception:
		pass
	return 0 if errors == 0 else 1


# ----------------- Editable invocation -----------------
INPUT_FOLDER = r"M:\delivery_data\reingestion-cfg25"  # change to your source root
OUTPUT_FOLDER = r"M:\delivery_data\reingestion-cfg25_tar2"  # where tar files will go
LOG_FILE = r"M:\delivery_data\reingestion-cfg25_logs\tar_creation_log.csv"          # relative or absolute path


def main() -> int:
	exit_code = create_tars(
		INPUT_FOLDER,
		OUTPUT_FOLDER,
		LOG_FILE,
		overwrite=False,
		validator_mode=False,
	)
	print(f"Done. Exit code: {exit_code}")
	return exit_code

if __name__ == "__main__":
	raise SystemExit(main())