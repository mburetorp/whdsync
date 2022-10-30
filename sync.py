import os
import glob
import shutil
import ftplib

def find_element_in_list(list, element):
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

def slave_filter(name):
	if not name.endswith(".lha"):
		return False
	ignore_strings = [
		"CD32", "CDTV", "NTSC",
		"_De", "_Fr", "_Cz", "_Pl", "_Es", "_It", "_Gr", "_Dk",
		"_DeEsFrIt"
	]
	for ignore_string in ignore_strings:
		if name.find(ignore_string + "_") != -1 or name.find(ignore_string + ".") != -1:
			return False
	return True

def slave_is_aga(name):
	return name.find("_AGA") != -1

def sync(host, dirname):
	host_basepath = "Retroplay WHDLoad Packs/Commodore_Amiga_-_WHDLoad_-_" + dirname + "/"
	local_path = "Downloaded/" + dirname + "/"
	transfer_path = "Transfer/" + dirname + "/"
	transfer_aga_path = "TransferAGA/" + dirname + "/"
	num_deleted = 0
	num_modified = 0
	num_downloaded = 0

	print("# Processing %s (%s)" % (dirname, host_basepath))

	os.makedirs(local_path, exist_ok=True)
	os.makedirs(transfer_path, exist_ok=True)
	os.makedirs(transfer_aga_path, exist_ok=True)

	# Get local file list
	local_filenames = []
	local_filepaths = glob.glob(local_path + "*.*", recursive=False)
	for i,filepath in enumerate(local_filepaths):
		local_filepaths[i] = filepath.replace("\\", "/")
		local_filenames.append(os.path.basename(filepath))
	print("Found %d slaves locally" % (len(local_filepaths)))

	# Get host file list
	host_filenames = []
	host_fileinfos = []
	for path, _, fileinfos in ftp_walk(host, host_basepath):
		for fileinfo in fileinfos:
			filename = fileinfo[0]
			filesize = fileinfo[1]
			if slave_filter(filename):
				filepath = os.path.join(path, filename).replace("\\", "/")
				host_filenames.append(filename)
				host_fileinfos.append((filepath, filesize))
				print("\rFound %d slaves on FTP" % (len(host_fileinfos)), end="")
	print("")
	print("")

	# Delete old local slaves
	print("Scanning for old slaves...")
	old_filenames = []
	for local_filename in local_filenames:
		if not local_filename in host_filenames:
			old_filenames.append(local_filename)
	for old_filename in old_filenames:
		print("[OLD] " + old_filename)
		old_filepath = local_path + old_filename
		local_index = find_element_in_list(local_filenames, old_filename)
		local_filenames.pop(local_index)
		local_filepaths.pop(local_index)
		os.remove(old_filepath)
		num_deleted += 1

	# Download new slaves
	print("Scanning for new or modified slaves...")
	for host_fileinfo in host_fileinfos:
		host_filename = os.path.basename(host_fileinfo[0])
		host_filesize = host_fileinfo[1]
		is_downloaded = False
		size_diff = 0
		local_index = find_element_in_list(local_filenames, host_filename)
		if local_index != None:
			is_downloaded = True
			size_diff = host_filesize - os.path.getsize(local_filepaths[local_index])
		if not is_downloaded:
			print("[NEW] " + host_filename)
		elif size_diff != 0:
			print("[MOD] %s (%+d bytes)" % (host_filename, size_diff))
			num_modified += 1
		if not is_downloaded or size_diff != 0:
			local_filepath = local_path + host_filename
			ftp_download(host, host_fileinfo[0], local_filepath)
			if slave_is_aga(host_filename):
				shutil.copyfile(local_filepath, transfer_aga_path + host_filename)
			else:
				shutil.copyfile(local_filepath, transfer_path + host_filename)
			num_downloaded += 1

	print("")
	print("All done! Downloaded: %d, Modified: %d, Deleted: %d" % (num_downloaded, num_modified, num_deleted))
	print("")

# Connect and sync
with ftplib.FTP("ftp.grandis.nu", "ftp", "ftp", encoding='ISO-8859-1') as host:
	sync(host, "Games")
	sync(host, "Demos")

os.system("pause")