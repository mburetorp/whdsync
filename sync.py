import os
import glob
import shutil
import ftplib
import configparser
import lhafile

def find_element(list, element):
	try:
		return list.index(element)
	except ValueError:
		return None

def ftp_list(host):
	file_list, dirs, files = [], [], []
	host.retrlines('LIST', lambda x: file_list.append(x.split()))
	for info in file_list:
		if info[0] == "total":
			continue
		elif info[0].startswith('d'):
			dirs.append(info[-1])
		else:
			files.append( (info[-1], int(info[4])) )
	return dirs, files

def ftp_walk(host, path):
	host.cwd("/" + path)
	dirs, files = ftp_list(host)
	yield path, dirs, files
	for name in dirs:
		new_path = os.path.join(path, name).replace("\\", "/")
		yield from ftp_walk(host, new_path)

def ftp_download(host, host_file, local_file):
	local_dir = os.path.dirname(local_file)
	os.makedirs(local_dir, exist_ok=True)
	host.retrbinary("RETR /" + host_file, open(local_file, 'wb').write)

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

def slave_is_aga(name):
	return name.find("_AGA") != -1

def sync(host, settings, sync_settings):
	host_basepath = sync_settings["FTPDirectory"].replace("\\", "/")
	download_path = os.path.normpath(os.path.join(settings["DownloadDirectory"], sync_settings["LocalDirectory"]))
	changed_path = os.path.normpath(os.path.join(settings["ChangedDirectory"], sync_settings["LocalDirectory"]))
	changed_aga_path = os.path.normpath(os.path.join(settings["ChangedDirectoryAGA"], sync_settings["LocalDirectory"]))
	num_deleted = 0
	num_changed = 0
	num_downloaded = 0

	print("# Synchronizing %s:%s -> %s" % (host.host, host_basepath, download_path))

	# Create output directories
	os.makedirs(download_path, exist_ok=True)
	os.makedirs(changed_path, exist_ok=True)
	os.makedirs(changed_aga_path, exist_ok=True)

	# Get local file list
	local_filenames = []
	local_filepaths = glob.glob(os.path.join(download_path, "*.*"), recursive=False)
	for i,filepath in enumerate(local_filepaths):
		local_filepaths[i] = filepath
		local_filenames.append(os.path.basename(filepath))
	print("Found %d slaves locally" % (len(local_filepaths)))

	# Get host file list
	accepted_exts = sync_settings["AcceptedExtentions"].split()
	ignored_names = sync_settings["IgnoredNames"].split()
	ignored_tags = sync_settings["IgnoredTags"].split()
	host_filenames = []
	host_fileinfos = []
	for path, _, fileinfos in ftp_walk(host, host_basepath):
		for fileinfo in fileinfos:
			filename = fileinfo[0]
			filesize = fileinfo[1]
			if slave_filter(filename, accepted_exts, ignored_names, ignored_tags):
				filepath = os.path.join(path, filename).replace("\\", "/")
				host_filenames.append(filename)
				host_fileinfos.append((filepath, filesize))
				print("\rFound %d slaves on FTP" % (len(host_fileinfos)), end="")

	print("")
	print("")
	print("Synchronizing files...")

	# Delete old local slaves
	old_filenames = []
	for local_filename in local_filenames:
		if not local_filename in host_filenames:
			old_filenames.append(local_filename)
	for old_filename in old_filenames:
		print("  [OLD] " + old_filename)
		local_index = find_element(local_filenames, old_filename)

		# Remove from arrays
		local_filenames.pop(local_index)
		local_filepaths.pop(local_index)

		# Delete from download directory
		os.remove(os.path.join(download_path, old_filename))

		# Delete from changed directories
		if os.path.exists(os.path.join(changed_path, old_filename)):
			os.remove(os.path.join(changed_path, old_filename))
		if os.path.exists(os.path.join(changed_aga_path, old_filename)):
			os.remove(os.path.join(changed_aga_path, old_filename))

		num_deleted += 1

	# Download new slaves
	for host_fileinfo in host_fileinfos:
		host_filename = os.path.basename(host_fileinfo[0])
		host_filesize = host_fileinfo[1]
		is_downloaded = False
		size_diff = 0
		local_index = find_element(local_filenames, host_filename)
		if local_index != None:
			is_downloaded = True
			size_diff = host_filesize - os.path.getsize(local_filepaths[local_index])

		if not is_downloaded:
			print("  [NEW] " + host_filename)
		elif size_diff != 0:
			print("  [DIF] %s (%+d bytes)" % (host_filename, size_diff))
			num_changed += 1

		if not is_downloaded or size_diff != 0:
			local_filepath = os.path.join(download_path, host_filename)
			ftp_download(host, host_fileinfo[0], local_filepath)
			if slave_is_aga(host_filename):
				shutil.copyfile(local_filepath, os.path.join(changed_aga_path, host_filename))
			else:
				shutil.copyfile(local_filepath, os.path.join(changed_path, host_filename))
			num_downloaded += 1

	print("  Downloaded: %d, Changed: %d, Deleted: %d\n" % (num_downloaded, num_changed, num_deleted))

def build_names(settings, sync_settings):
	try:
		names_dir = settings["NamesDirectory"]
	except KeyError:
		return

	mirror_path = os.path.normpath(os.path.join(names_dir, sync_settings["LocalDirectory"]))
	local_path = os.path.normpath(os.path.join(settings["DownloadDirectory"], sync_settings["LocalDirectory"]))

	# Create output directories
	if os.path.exists(mirror_path):
		shutil.rmtree(mirror_path)
	os.makedirs(mirror_path)

	# Get local file list
	local_filepaths = glob.glob(os.path.join(local_path, "*.*"), recursive=False)

	# Get .info files
	info_filenames = []
	for i,local_filepath in enumerate(local_filepaths):
		print("\rScanning archives for .info files... %d%%" % (100 * i / (len(local_filepaths) - 1)), end="")
		try:
			lha = lhafile.Lhafile(local_filepath)
			info_filename = None
			for info in lha.infolist():
				if not info.directory and info.filename.endswith(".info"):
					info_filename = info.filename
					break

			if info_filename == None:
				print(" : ERROR:: No .info file found in archive '%s'" % (local_filepath))
			elif find_element(info_filenames, info_filename):
				print(" : ERROR: '%s' found in multiple archives" % (info_filename))
			else:
				info_filenames.append(info_filename)
		except:
			print(" : ERROR: Failed to read archive '%s'" % (local_filepath))

	print("")

	if len(info_filenames) != len(local_filepaths):
		print("Number of .info files does not match number of archvies, aborting automation")
		print("")
		return

	for i,info_filename in enumerate(info_filenames):
		print("\rCreating name files... %d%%" % (100 * i / (len(info_filenames) - 1)), end="")
		filename_no_ext = os.path.splitext(info_filename)[0]
		open(os.path.join(mirror_path, filename_no_ext), "wb")

	print("")
	print("")

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
	except:
		print(" Failed, check credentials in '%s'" % (config_file))
		return
	print("")

	# Sync
	for sync_name in settings["Sync"].split():
		print("## Processing %s ##" % (sync_name))
		sync_settings = config[sync_name]
		sync(host, settings, sync_settings)
		build_names(settings, sync_settings)

	host.close()

main()	