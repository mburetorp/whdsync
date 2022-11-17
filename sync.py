import os
import glob
import shutil
import ftplib
import fnmatch
import configparser
import lhafile
import zipfile
import xml.etree.ElementTree as ET

class CustomError(Exception):
	pass

# ================================================================
def find_element(list, element):
	try:
		return list.index(element)
	except ValueError:
		return None

# ================================================================
def ftp_list(host):
	file_list, dirs, files = [], [], []
	host.retrlines('LIST', lambda x: file_list.append(x.split(None, 8)))
	for info in file_list:
		if info[0] == "total":
			continue
		elif info[0].startswith('d'):
			dirs.append(info[-1])
		else:
			files.append( (info[-1], int(info[4])) )
	return dirs, files

# ================================================================
def ftp_walk(host, path):
	host.cwd("/" + path)
	dirs, files = ftp_list(host)
	yield path, dirs, files
	for name in dirs:
		new_path = os.path.join(path, name).replace("\\", "/")
		yield from ftp_walk(host, new_path)

# ================================================================
def ftp_download(host, host_filepath, local_filepath):
	local_dir = os.path.dirname(local_filepath)
	os.makedirs(local_dir, exist_ok=True)
	file = open(local_filepath, 'wb')
	try:
		host.retrbinary("RETR /" + host_filepath, file.write)
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
def get_host_files(host, host_basepath, sync_settings):
	accepted_exts = sync_settings["AcceptedExtentions"].split()
	ignored_names = sync_settings["IgnoredNames"].split()
	ignored_tags = sync_settings["IgnoredTags"].split()
	host_fileinfos = []

	for path, _, fileinfos in ftp_walk(host, host_basepath):
		for fileinfo in fileinfos:
			filename = fileinfo[0]
			filesize = fileinfo[1]
			if slave_filter(filename, accepted_exts, ignored_names, ignored_tags):
				filepath = os.path.join(path, filename).replace("\\", "/")
				host_fileinfos.append((filename, filepath, filesize))
				print("\rFound %d slaves on FTP" % (len(host_fileinfos)), end="")

	if len(host_fileinfos) > 0:
		print("")

	return host_fileinfos

# ================================================================
def download_database(host, database_pattern):
	basepath = os.path.dirname(database_pattern)
	filepattern = os.path.basename(database_pattern)
	host_filepath = None
	local_filepath = "Temp/Database.zip"

	host.cwd("/" + basepath)
	dirs, files = ftp_list(host)
	for info in files:
		if fnmatch.fnmatch(info[0], filepattern):
			if host_filepath == None:
				host_filepath = os.path.join(basepath, info[0]).replace("\\", "/")
			else:
				raise CustomError("Multiple database files found using pattern '" + filepattern + "'")

	if host_filepath == None:
		raise CustomError("No database found using pattern '" + filepattern + "'")

	print("Downloading database '%s' to '%s'..." % (os.path.basename(host_filepath), local_filepath))
	ftp_download(host, host_filepath, local_filepath)
	return local_filepath

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
					filepath = os.path.join(host_basepath, dir, filename).replace("\\", "/")
					if slave_filter(filename, accepted_exts, ignored_names, ignored_tags):
						host_fileinfos.append((filename, filepath, filesize))

	return host_fileinfos

# ================================================================
def get_host_files_using_database(host, host_basepath, database_filepattern, sync_settings):
	database_filepath = download_database(host, database_filepattern)
	host_fileinfos = []

	with zipfile.ZipFile(database_filepath) as zip:
		filenames = zip.namelist()
		if len(filenames) != 1:
			raise CustomError("Expected database to contain exactly one file")
		with zip.open(filenames[0]) as file:
			root = ET.fromstring(file.read())
			host_fileinfos = parse_database(root, host_basepath, sync_settings)
	
	print("Found %d slaves on FTP" % (len(host_fileinfos)))
	return host_fileinfos

# ================================================================
def sync(host, settings, sync_settings):
	host_basepath = sync_settings["FTPDirectory"].replace("\\", "/")
	download_path = os.path.normpath(os.path.join(settings["DownloadDirectory"], sync_settings["LocalDirectory"]))
	changed_ecs_path = os.path.normpath(os.path.join(settings["ChangedECSDirectory"], sync_settings["LocalDirectory"]))
	changed_aga_path = os.path.normpath(os.path.join(settings["ChangedAGADirectory"], sync_settings["LocalDirectory"]))
	num_deleted = 0
	num_changed = 0
	num_downloaded = 0

	print("# Synchronizing %s:%s -> %s" % (host.host, host_basepath, download_path))

	# Create output directories
	os.makedirs(download_path, exist_ok=True)
	os.makedirs(changed_ecs_path, exist_ok=True)
	os.makedirs(changed_aga_path, exist_ok=True)

	# Get host file list
	try:
		host_fileinfos = get_host_files_using_database(host, host_basepath, sync_settings["UseDatabaseFile"], sync_settings)
	except KeyError:
		host_fileinfos = get_host_files(host, host_basepath, sync_settings)

	if len(host_fileinfos) == 0:
		print("No files found on host, aborting")
		return
	host_filenames = [info[0] for info in host_fileinfos]

	# Get local file list
	local_filenames = []
	local_filepaths = glob.glob(os.path.join(download_path, "*.*"), recursive=False)
	for i,filepath in enumerate(local_filepaths):
		local_filepaths[i] = filepath
		local_filenames.append(os.path.basename(filepath))
	print("Found %d slaves locally" % (len(local_filepaths)))

	print("")
	print("Synchronizing files...")

	# Delete old local slaves
	old_filenames = []
	for local_filename in local_filenames:
		if not local_filename in host_filenames:
			old_filenames.append(local_filename)
	for old_filename in old_filenames:
		print("- [OLD] " + old_filename)
		local_index = find_element(local_filenames, old_filename)

		# Remove from arrays
		local_filenames.pop(local_index)
		local_filepaths.pop(local_index)

		# Delete from download directory
		os.remove(os.path.join(download_path, old_filename))

		# Delete from changed directories
		if os.path.exists(os.path.join(changed_ecs_path, old_filename)):
			os.remove(os.path.join(changed_ecs_path, old_filename))
		if os.path.exists(os.path.join(changed_aga_path, old_filename)):
			os.remove(os.path.join(changed_aga_path, old_filename))

		num_deleted += 1

	# Download new slaves
	for host_fileinfo in host_fileinfos:
		host_filename = host_fileinfo[0]
		host_filesize = host_fileinfo[2]
		is_downloaded = False
		size_diff = 0
		local_index = find_element(local_filenames, host_filename)
		if local_index != None:
			is_downloaded = True
			size_diff = host_filesize - os.path.getsize(local_filepaths[local_index])

		if not is_downloaded:
			print("- [NEW] " + host_filename)
		elif size_diff != 0:
			print("- [DIF] %s (%+d bytes)" % (host_filename, size_diff))
			num_changed += 1

		if not is_downloaded or size_diff != 0:
			local_filepath = os.path.join(download_path, host_filename)
			try:
				ftp_download(host, host_fileinfo[1], local_filepath)
				if slave_is_aga(host_filename):
					shutil.copyfile(local_filepath, os.path.join(changed_aga_path, host_filename))
				else:
					shutil.copyfile(local_filepath, os.path.join(changed_ecs_path, host_filename))
				num_downloaded += 1
			except Exception as e:
				print("  Download failed with error:", str(e))

	print("- Downloaded: %d, Changed: %d, Deleted: %d\n" % (num_downloaded, num_changed, num_deleted))

# ================================================================
def build_names(settings, sync_settings):
	try:
		names_ecs_dir = settings["NamesECSDirectory"]
		names_aga_dir = settings["NamesAGADirectory"]
	except KeyError:
		return

	names_ecs_path = os.path.normpath(os.path.join(names_ecs_dir, sync_settings["LocalDirectory"]))
	names_aga_path = os.path.normpath(os.path.join(names_aga_dir, sync_settings["LocalDirectory"]))
	local_path = os.path.normpath(os.path.join(settings["DownloadDirectory"], sync_settings["LocalDirectory"]))

	# Create output directories
	if os.path.exists(names_ecs_path):
		shutil.rmtree(names_ecs_path)
	os.makedirs(names_ecs_path)

	if os.path.exists(names_aga_path):
		shutil.rmtree(names_aga_path)
	os.makedirs(names_aga_path)

	# Get local file list
	local_filepaths = glob.glob(os.path.join(local_path, "*.*"), recursive=False)

	# Get .info files
	info_ecs_filenames = []
	info_aga_filenames = []
	for i,local_filepath in enumerate(local_filepaths):
		print("\rScanning .info files... %d%%" % (100 * i / (len(local_filepaths) - 1)), end="")
		try:
			lha = lhafile.Lhafile(local_filepath)
			info_filename = None
			for info in lha.infolist():
				if not info.directory and info.filename.endswith(".info"):
					info_filename = info.filename
					break

			if info_filename == None:
				print(" : ERROR:: No .info file found in archive '%s'" % (local_filepath))
			elif find_element(info_aga_filenames, info_filename):
				print(" : ERROR: '%s' found in multiple archives" % (info_filename))
			elif slave_is_aga(local_filepath):
				info_aga_filenames.append(info_filename)
			else:
				info_aga_filenames.append(info_filename)
				info_ecs_filenames.append(info_filename)
		except:
			print(" : ERROR: Failed to read archive '%s'" % (local_filepath))

	print("")

	if len(info_aga_filenames) != len(local_filepaths):
		print("ERROR: Number of .info files does not match number of archvies, aborting")
		print("")
		return

	for i,info_filename in enumerate(info_aga_filenames):
		print("\rCreating AGA names... %d%%" % (100 * i / (len(info_aga_filenames) - 1)), end="")
		filename_no_ext = os.path.splitext(info_filename)[0]
		open(os.path.join(names_aga_path, filename_no_ext), "wb")
	print("")

	for i,info_filename in enumerate(info_ecs_filenames):
		print("\rCreating ECS names... %d%%" % (100 * i / (len(info_ecs_filenames) - 1)), end="")
		filename_no_ext = os.path.splitext(info_filename)[0]
		open(os.path.join(names_ecs_path, filename_no_ext), "wb")

	print("")
	print("")

# ================================================================
def main():
	# Read config
	config_file = "sync.ini"
	config = configparser.ConfigParser()
	config.read(config_file)
	ftpinfo = config["FTP"]
	settings = config["Settings"]

	# Connect
	print("Connecting to '%s'..." % (ftpinfo["Host"]), end="")
	try:
		host = ftplib.FTP(ftpinfo["Host"], ftpinfo["Username"], ftpinfo["Password"], encoding=ftpinfo["Encoding"])
		print(" Done")
	except Exception as e:
		print(" Failed with error message:", str(e))
		return
	print("")

	# Sync
	try:
		for sync_name in settings["Sync"].split():
			print("## Processing %s ##" % (sync_name))
			sync_settings = config[sync_name]
			sync(host, settings, sync_settings)
			build_names(settings, sync_settings)
	except CustomError as e:
		print("ERROR: " + str(e))
		print("")

	host.close()

main()

input("Press Enter to exit...")