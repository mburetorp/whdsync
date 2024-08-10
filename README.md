## Amiga WHDSync
WHDSync is a script to download Retroplay's WHDLoad Packs and install incremental updates on the Amiga. I wrote it for my own needs, but it should be relatively easy to modify.

### Prerequisites
Python3 and the following pip packages are required:
* dateparser
* lhafile

### Configuration file
The ini file `sync.ini` is configured to download PAL Games and Demos targeting Amiga 1200 while ignoring most foreign languages. You may want to change this.

### Directories
`Library/` is the default storage for all downloaded slaves and is essentially a local mirror of the FTP.\
`Updates/` is meant for transfering and installing updates on the Amiga. It contains all new and modified slaves since the last install and a AmigaDOS shell script to install them. After installing updates you should clear this directory using the `reset.bat` script.

### How to use
Simply run `sync.py` to download all changes since the last run. This will populate the `Library/` and `Updates/` directories.

To install updates, copy the `Updates/` directory to your Amiga/CF, open a shell and run the command `execute install`. **Beware that the path to where everything is extracted is currently hardcoded in this shell script, so you will want to change this first.**