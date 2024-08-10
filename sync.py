import os
import time
import glob
import shutil
import ftplib
import fnmatch
import configparser
import lhafile
import zipfile
import hashlib
import xml.etree.ElementTree as ET
import dateparser.search
import argparse

class CustomError(Exception):
	pass

# ================================================================
def find_element(list, element):
	try:
		return list.index(element)
	except ValueError:
		return None

# ================================================================
def hash_file_md5(filepath):
	with open(filepath,"rb") as f:
		return hashlib.md5(f.read()).hexdigest()
	raise CustomError(f"Failed to calculate md5 hash for file '{filepath}'")

# ================================================================
def ftp_list(connection):
	file_list, dirs, files = [], [], []
	connection.retrlines('LIST', lambda x: file_list.append(x.split(None, 8)))
	for info in file_list:
		if info[0] == "total":
			continue
		elif info[0].startswith('d'):
			dirs.append(info[-1])
		else:
			files.append( (info[-1], int(info[4])) )
	return dirs, files

# ================================================================
def ftp_walk(connection, path):
	connection.cwd("/" + path)
	dirs, files = ftp_list(connection)
	yield path, dirs, files
	for name in dirs:
		new_path = os.path.join(path, name).replace("\\", "/")
		yield from ftp_walk(connection, new_path)

# ================================================================
def ftp_download(connection, host_filepath, local_filepath):
	local_dir = os.path.dirname(local_filepath)
	os.makedirs(local_dir, exist_ok=True)
	file = open(local_filepath, 'wb')
	try:
		connection.retrbinary("RETR /" + host_filepath, file.write)
	except Exception as e:
		file.close()
		os.remove(local_filepath)
		raise e

# ================================================================
def slave_filter(name, accepted_exts, ignored_names, ignored_tags):
	for s in ignored_names:
		if name.startswith(s):
			return False
	for s in ignored_tags:
		if name.find(s + "_") != -1 or name.find(s + ".") != -1:
			return False
	if len(accepted_exts) > 0:
		for s in accepted_exts:
			if name.endswith("." + s):
				return True
		return False
	else:
		return True

# ================================================================
def slave_is_aga(name):
	return name.find("_AGA") != -1

# ================================================================
def slave_get_name(filepath):
	lha = lhafile.Lhafile(filepath)
	for info in lha.infolist():
		if not info.directory and info.filename.endswith(".info"):
			return os.path.splitext(info.filename)[0]
	raise CustomError(f"No .info file found in archive '{filepath}'")

# ================================================================
def download_database(connection, database_pattern):
	basepath = os.path.dirname(database_pattern)
	filepattern = os.path.basename(database_pattern)

	# Search for most recent database
	newest_date = None
	database_filepath = None

	connection.cwd("/" + basepath)
	dirs, files = ftp_list(connection)
	for info in files:
		if fnmatch.fnmatch(info[0], filepattern):
			date = dateparser.search.search_dates(info[0])
			if database_filepath == None or (date != None and newest_date != None and date[0][1] > newest_date[0][1]):
				newest_date = date
				database_filepath = os.path.join(basepath, info[0]).replace("\\", "/")

	if database_filepath == None:
		raise CustomError("No database found using pattern '" + filepattern + "'")

	# Download
	download_filepath = os.path.join("Temp/", filepattern)
	download_filepath = download_filepath.replace("*", "").replace("?", "")

	print(f"Found database '{os.path.basename(database_filepath)}'")
	ftp_download(connection, database_filepath, download_filepath)
	return download_filepath

# ================================================================
def parse_database(root, host_basepath, sync_settings):
	accepted_exts = sync_settings["AcceptedExtentions"].split()
	ignored_names = sync_settings["IgnoredNames"].split()
	ignored_tags = sync_settings["IgnoredTags"].split()
	host_fileinfos = []

	for dir_child in root:
		if dir_child.tag == "machine":
			dir = dir_child.attrib["name"]
			for rom_child in dir_child:
				if rom_child.tag == "rom":
					filename = rom_child.attrib["name"]
					filesize = int(rom_child.attrib["size"])
					filemd5 = rom_child.attrib["md5"]
					filepath = os.path.join(host_basepath, dir, filename).replace("\\", "/")
					if slave_filter(filename, accepted_exts, ignored_names, ignored_tags):
						host_fileinfos.append((filename, filepath, filesize, filemd5))

	return host_fileinfos

# ================================================================
def get_host_files_using_database(connection, host_basepath, database_filepattern, sync_settings):
	database_filepath = download_database(connection, database_filepattern)
	host_fileinfos = []

	with zipfile.ZipFile(database_filepath) as zip:
		filenames = zip.namelist()
		if len(filenames) != 1:
			raise CustomError("Expected database to contain exactly one file")
		with zip.open(filenames[0]) as file:
			root = ET.fromstring(file.read())
			host_fileinfos = parse_database(root, host_basepath, sync_settings)
	
	print(f"Found {len(host_fileinfos)} slaves on FTP")
	return host_fileinfos

# ================================================================
def sync(connection, settings, sync_settings, dry_run):
	host_basepath = sync_settings["FTPDirectory"].replace("\\", "/")
	local_sync_dir = sync_settings["LocalDirectory"]
	updates_dir = settings["UpdatesDirectory"]
	library_path = os.path.normpath(os.path.join(settings["LibraryDirectory"], local_sync_dir))
	first_run = not os.path.exists(library_path)
	updates_ecs_path = os.path.normpath(os.path.join(updates_dir, "ExtractECS", local_sync_dir))
	updates_aga_path = os.path.normpath(os.path.join(updates_dir, "ExtractAGA", local_sync_dir))
	updates_delete_path = os.path.normpath(os.path.join(updates_dir, "Delete", local_sync_dir))

	num_deleted = 0
	num_changed = 0
	num_downloaded = 0

	print(f"> Synchronizing {connection.host}:{host_basepath} -> {library_path}")

	# Create output directories
	os.makedirs(library_path, exist_ok=True)
	os.makedirs(updates_ecs_path, exist_ok=True)
	os.makedirs(updates_aga_path, exist_ok=True)

	# Get host file list
	host_fileinfos = get_host_files_using_database(connection, host_basepath, sync_settings["DatabaseFile"], sync_settings)
	if len(host_fileinfos) == 0:
		print("No files found on host, aborting")
		return
	host_filenames = [info[0] for info in host_fileinfos]

	# Get local file list
	local_filenames = []
	local_filepaths = glob.glob(os.path.join(library_path, "*.*"), recursive=False)
	for i,filepath in enumerate(local_filepaths):
		local_filepaths[i] = filepath
		local_filenames.append(os.path.basename(filepath))
	print(f"Found {len(local_filepaths)} slaves locally")

	print("")
	print("Synchronizing files...")

	# Delete old local slaves
	delete_names = []
	old_filenames = []
	for local_filename in local_filenames:
		if not local_filename in host_filenames:
			old_filenames.append(local_filename)
	for old_filename in old_filenames:
		print("- [OLD] " + old_filename)
		local_index = find_element(local_filenames, old_filename)

		if not dry_run:
			# Add slave name to changed slaves
			slave_name = slave_get_name(local_filepaths[local_index])
			delete_names.append(slave_name)

			# Delete from download directory
			os.remove(os.path.join(library_path, old_filename))

			# Delete from changed directories
			if os.path.exists(os.path.join(updates_ecs_path, old_filename)):
				os.remove(os.path.join(updates_ecs_path, old_filename))
			if os.path.exists(os.path.join(updates_aga_path, old_filename)):
				os.remove(os.path.join(updates_aga_path, old_filename))

		# Remove from arrays
		local_filenames.pop(local_index)
		local_filepaths.pop(local_index)
		num_deleted += 1

	# Download new slaves
	for host_fileinfo in host_fileinfos:
		host_filename = host_fileinfo[0]
		host_filesize = host_fileinfo[2]
		host_filemd5 = host_fileinfo[3]
		is_downloaded = False
		is_changed = False
		size_diff = 0

		# Check if already downloaded
		local_index = find_element(local_filenames, host_filename)
		if local_index != None:
			local_filepath = local_filepaths[local_index]
			size_diff = host_filesize - os.path.getsize(local_filepath)
			is_downloaded = True
			is_changed = size_diff != 0 or host_filemd5 != hash_file_md5(local_filepath)

		# Print text
		if not is_downloaded:
			print(f"- [NEW] {host_filename}")
		elif is_changed:
			print(f"- [DIF] {host_filename} ({size_diff:+} bytes)")
			num_changed += 1

		# Download
		if not dry_run and (not is_downloaded or is_changed):
			local_filepath = os.path.join(library_path, host_filename)
			try:
				ftp_download(connection, host_fileinfo[1], local_filepath)

				# Verify size
				local_filesize = os.path.getsize(local_filepath)
				if local_filesize != host_filesize:
					os.remove(local_filepath)
					raise CustomError(f"Downloaded file '{local_filepath}' size ({local_filesize} bytes) does not match host ({host_filesize} bytes)")

				# Verify MD5
				local_filemd5 = hash_file_md5(local_filepath)
				if local_filemd5 != host_filemd5:
					os.remove(local_filepath)
					raise CustomError(f"Downloaded file '{local_filepath}' MD5 sum does not match host")

				if not first_run:
					# Copy
					if slave_is_aga(host_filename):
						shutil.copyfile(local_filepath, os.path.join(updates_aga_path, host_filename))
					else:
						shutil.copyfile(local_filepath, os.path.join(updates_ecs_path, host_filename))

					# Add slave name to changed slaves
					slave_name = slave_get_name(local_filepath)
					delete_names.append(slave_name)

				num_downloaded += 1
			except Exception as e:
				print("  Download failed with error:", str(e))

	# Write delete names
	os.makedirs(updates_delete_path, exist_ok=True)
	for i,name in enumerate(delete_names):
		open(os.path.join(updates_delete_path, name), "wb")

	print(f"- Downloaded: {num_downloaded}, Changed: {num_changed}, Deleted: {num_deleted}\n")

	return num_downloaded != 0 or num_changed != 0 or num_deleted != 0

# ================================================================
def create_all_names(settings, sync_settings, dry_run):
	if dry_run:
		return

	local_sync_dir = sync_settings["LocalDirectory"]
	updates_dir = settings["UpdatesDirectory"]
	library_path = os.path.normpath(os.path.join(settings["LibraryDirectory"], local_sync_dir))
	names_ecs_path = os.path.normpath(os.path.join(updates_dir, "NamesECS", local_sync_dir))
	names_aga_path = os.path.normpath(os.path.join(updates_dir, "NamesAGA", local_sync_dir))

	# Create output directories
	if os.path.exists(names_ecs_path):
		shutil.rmtree(names_ecs_path)
	if os.path.exists(names_aga_path):
		shutil.rmtree(names_aga_path)
	os.makedirs(names_ecs_path)
	os.makedirs(names_aga_path)

	# Get local file list
	local_filepaths = glob.glob(os.path.join(library_path, "*.*"), recursive=False)

	# Get .info files
	slave_names_ecs = []
	slave_names_aga = []
	for i,local_filepath in enumerate(local_filepaths):
		print(f"\rScanning archives... {100 * i // (len(local_filepaths) - 1)}%", end="")
		try:
			slave_name = slave_get_name(local_filepath)
			if find_element(slave_names_aga, slave_name):
				print(f" : ERROR: '{slave_name}' found in multiple archives")
			elif slave_is_aga(local_filepath):
				slave_names_aga.append(slave_name)
			else:
				slave_names_aga.append(slave_name)
				slave_names_ecs.append(slave_name)
		except:
			print(f" : ERROR: Failed to read archive '{local_filepath}'")

	print("")

	if len(slave_names_aga) != len(local_filepaths):
		print("ERROR: Number of .info files does not match number of archvies, aborting")
		print("")
		return

	for i,name in enumerate(slave_names_aga):
		print(f"\rCreating AGA names... {100 * i // (len(slave_names_aga) - 1)}%", end="")
		open(os.path.join(names_aga_path, name), "wb")
	print("")

	for i,name in enumerate(slave_names_ecs):
		print(f"\rCreating ECS names... {100 * i // (len(slave_names_ecs) - 1)}%", end="")
		open(os.path.join(names_ecs_path, name), "wb")
	print("")

	print("")

# ================================================================
def connect(ftpinfo):
	hosts = ftpinfo["Hosts"].split()
	max_attempts = 10
	for attempt in range(max_attempts):
		for host in hosts:
			print(f"Connecting to '{host}'...", end="")
			try:
				connection = ftplib.FTP(host, ftpinfo["Username"], ftpinfo["Password"], encoding=ftpinfo["Encoding"])
				print(" Successful")
				print("")
				return connection
			except Exception as e:
				print(" Failed with error message:", str(e))
				time.sleep(1)

		print("Waiting 30 seconds before next attempt")
		print("")
		time.sleep(5)

	print(f"Failed to connect on all {max_attempts} attempts")
	print("")

	return None

# ================================================================
def main():
	argparser = argparse.ArgumentParser()
	argparser.add_argument("--always-create-names", action="store_true", help="Create names even if nothing was changed") 
	argparser.add_argument("--dry-run", action="store_true", help="Do not download anything or modify the file system in any way") 
	args = argparser.parse_args()

	# Read config
	config_file = "sync.ini"
	config = configparser.ConfigParser()
	config.read(config_file)
	settings = config["Settings"]

	# Connect
	connection = connect(config["FTP"])
	if connection == None:
		return

	# Sync
	try:
		for sync_name in settings["SyncSections"].split():
			sync_settings = config[f"Sync.{sync_name}"]
			changed = sync(connection, settings, sync_settings, args.dry_run)
			if changed or args.always_create_names:
				create_all_names(settings, sync_settings, args.dry_run)
	except CustomError as e:
		print("ERROR: " + str(e))
		print("")

	connection.close()

main()

input("Press Enter to exit...")