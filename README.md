# ZipROFS
[![Build Status](https://travis-ci.com/openscopeproject/ZipROFS.svg?branch=dev)](https://travis-ci.com/openscopeproject/ZipROFS)

This is a FUSE filesystem that acts as pass through to another FS except it
expands zip files like folders and allows direct transparent access to the contents.

### Dependencies
* FUSE
* fusepy

### Limitations
* Read only
* Nested zip files are not expanded, they are still just files

### Example usage
To mount run ziprofs.py:
```shell
$ ./ziprofs.py ~/root ~/mount -o allowother,cachesize=2048
```

Example results:
```shell
$ tree root
root
├── folder
├── test.zip
└── text.txt

$ tree mount
mount
├── folder
├── test.zip
│   ├── folder
│   │   ├── emptyfile
│   │   └── subfolder
│   │       └── file.txt
│   ├── script.sh
│   └── text.txt
└── text.txt
```

You can later unmount it using:
```shell
$ fusermount -u ~/mount
```

Or:
```shell
$ umount ~/mount
```

Full help:
```shell
$ ./ziprofs.py -h
usage: ziprofs.py [-h] [-o options] [root] [mountpoint]

ZipROFS read only transparent zip filesystem.

positional arguments:
  root        filesystem root (default: None)
  mountpoint  filesystem mount point (default: None)

optional arguments:
  -h, --help  show this help message and exit
  -o options  comma separated list of options: foreground, debug, allowother, async, cachesize=N (default: {})
```

`foreground` and `allowother` options are passed to FUSE directly.

`debug` option is used to print all syscall details to stdout.

By default ZipROFS disables async reads to improve performance since async syscalls can
be reordered in fuse which heavily impacts read speeds.
If async reads are preferable, pass `async` option on mount.

`cachesize` option determines in memory zipfile cache size, defaults to 1000
