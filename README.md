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
└── donGURALesko - Totem Lesnych Ludzi (2010).zip

$ tree mount
mount
└── donGURALesko - Totem Lesnych Ludzi (2010).zip
    └── donGURALesko - Totem Lesnych Ludzi (2010)
        ├── 01. donGURALesko - Daleko (prod. Matheo).flac
        ├── 02. donGURALesko - Zanim Powstal Totem (prod. Matheo).flac
        ├── 03. donGURALesko - Giovanni Dziadzia (prod. Matheo) feat. Dj Kostek.flac
        ├── 04. donGURALesko - Betonowe Lasy Mokna (prod. Matheo) feat. Dj Kostek.flac
        ├── 05. donGURALesko - Dzieci Kosmosu (prod. Matheo) feat. Dj Hen.flac
        ├── 06. donGURALesko - Goryl (prod. Matheo) feat. Waldemar Kasta _ Miodu _ Grubson.flac
        ├── 07. donGURALesko - To o tym jest miedzy innymi (prod. Donatan) feat. Dj Hen.flac
        ├── 08. donGURALesko - Mowia tam na blokach (prod. Donatan) feat. Dj Hen.flac
        ├── 09. donGURALesko - Pale Majki (prod. Matheo).flac
        ├── 10. donGURALesko - Tanczymy Walczyk (prod. Matheo) feat. Dj Kostek.flac
        ├── 11. donGURALesko - Braga 30 (prod. Matheo) feat. Dj Kostek.flac
        ├── 12. donGURALesko - Migaja Lampy (prod. Tasty Beats) feat. Dj Hen.flac
        ├── 13. donGURALesko - R.A.P. (prod. Matheo) feat. Dj Soina.flac
        ├── 14. donGURALesko - Zdarzylo sie wczoraj (prod. Dj Story).flac
        ├── 15. donGURALesko - Zloty rog (prod. Matheo) feat. Dj Kostek.flac
        ├── 16. donGURALesko - Wladcy much (prod. Matheo).flac
        ├── coverart.jpg
        └── missing_tracks.txt
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
  -o options  comma separated list of options: foreground, debug, allowother, cachesize=N (default: {})
```
