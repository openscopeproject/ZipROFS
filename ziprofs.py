#!/usr/bin/env python3
from __future__ import print_function, absolute_import, division

from functools import lru_cache

from os.path import realpath

import argparse
import errno
import logging
import os
import zipfile
import stat
from typing import Optional

try:
    from fuse import FUSE, FuseOSError, Operations, LoggingMixIn, S_IFDIR
except ImportError:
    # ubuntu renamed package in repository
    from fusepy import FUSE, FuseOSError, Operations, LoggingMixIn, S_IFDIR

from collections import OrderedDict


@lru_cache(maxsize=2048)
def is_zipfile(path):
    return zipfile.is_zipfile(path)


class CachedZipFactory(object):
    MAX_CACHE_SIZE = 1000
    cache = OrderedDict()
    log = logging.getLogger('ziprofs.cache')

    def _add(self, path: str):
        if path in self.cache:
            return
        while len(self.cache) >= self.MAX_CACHE_SIZE:
            path, val = self.cache.popitem(last=False)
            self.log.debug('Popping cache entry: %s', path)
            val[1].close()
        mtime = os.lstat(path).st_mtime
        self.log.debug("Caching path (%s:%s)", path, mtime)
        self.cache[path] = (mtime, zipfile.ZipFile(path))

    def get(self, path: str) -> zipfile.ZipFile:
        if path in self.cache:
            self.cache.move_to_end(path)
            mtime = os.lstat(path).st_mtime
            if mtime > self.cache[path][0]:
                val = self.cache.pop(path)
                val[1].close()
                self._add(path)
        else:
            self._add(path)
        return self.cache[path][1]


class ZipROFS(LoggingMixIn, Operations):
    MAX_SEEK_READ = 1 << 24
    zip_factory = CachedZipFactory()

    def __init__(self, root):
        self.root = realpath(root)
        self._zip_fh = {}  # odd file handles are files inside zip, even fhs are system-wide files - collision avoidance

    def __call__(self, op, path, *args):
        return super(ZipROFS, self).__call__(op, self.root + path, *args)

    def _get_free_zip_fh(self):
        i = 5   # avoid confusion with stdin/err/out
        while True:
            if i not in self._zip_fh:
                return i
            i += 2

    @staticmethod
    def get_zip_path(path: str) -> Optional[str]:
        parts = []
        head, tail = os.path.split(path)
        while tail:
            parts.append(tail)
            head, tail = os.path.split(head)
        parts.reverse()
        cur_path = '/'
        for part in parts:
            cur_path = os.path.join(cur_path, part)
            if part[-4:] == '.zip' and is_zipfile(cur_path):
                return cur_path
        return None

    def access(self, path, mode):
        if not self.get_zip_path(path):
            if not os.access(path, mode):
                raise FuseOSError(errno.EACCES)
        if mode == os.W_OK:
            raise FuseOSError(errno.EACCES)

    def getattr(self, path, fh=None):
        zip_path = self.get_zip_path(path)
        st = os.lstat(zip_path) if zip_path else os.lstat(path)
        result = {key: getattr(st, key) for key in (
            'st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'
        )}
        if zip_path == path:
            result['st_mode'] = S_IFDIR | (result['st_mode'] & 0o555)
        elif zip_path:
            zf = self.zip_factory.get(zip_path)
            subpath = path[len(zip_path)+1:]
            try:
                info = zf.getinfo(subpath)
                result['st_size'] = info.file_size
                result['st_mode'] = stat.S_IFREG | 0o555
            except KeyError:
                # check if it is a valid subdirectory
                infolist = zf.infolist()
                found = False
                for info in infolist:
                    if info.filename.find(subpath + '/') == 0:
                        found = True
                        break
                if found:
                    result['st_mode'] = S_IFDIR | 0o555
                else:
                    raise FuseOSError(errno.ENOENT)
        return result

    def open(self, path, flags):
        if zip_path := self.get_zip_path(path):
            fh = self._get_free_zip_fh()
            zf = self.zip_factory.get(zip_path)
            self._zip_fh[fh] = zf.open(path[len(zip_path) + 1:])
            return fh
        else:
            return os.open(path, flags) << 1

    def read(self, path, size, offset, fh):
        if self.get_zip_path(path):
            f = self._zip_fh[fh]  # should be here (file is first opened, then read)
            if f.seekable():
                f.seek(offset)
                return f.read(size)
            else:
                # emulate seek by reading and skipping 1mb chunks
                while offset > 0:
                    read_len = min(self.MAX_SEEK_READ, offset)
                    f.read(read_len)
                    offset -= read_len
                return f.read(size)
        else:
            os.lseek(fh >> 1, offset, 0)
            return os.read(fh >> 1, size)

    def readdir(self, path, fh):
        if not (zip_path := self.get_zip_path(path)):
            return ['.', '..'] + os.listdir(path)
        subpath = path[len(zip_path)+1:]
        zf = self.zip_factory.get(zip_path)
        infolist = zf.infolist()

        result = ['.', '..']
        subdirs = set()
        for info in infolist:
            if info.filename.find(subpath) == 0 and info.filename > subpath:
                suffix = info.filename[len(subpath)+1 if subpath else 0:]
                if not suffix:
                    continue
                if '/' not in suffix:
                    result.append(suffix)
                else:
                    subdirs.add(suffix[:suffix.find('/')])
        result.extend(subdirs)
        return result

    def release(self, path, fh):
        if self.get_zip_path(path):
            f = self._zip_fh[fh]
            del self._zip_fh[fh]
            return f.close()
        else:
            return os.close(fh >> 1)

    def statfs(self, path):
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in (
            'f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
            'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax'
        ))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='ZipROFS read only transparent zip filesystem.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('root', nargs='?', help="filesystem root")
    parser.add_argument('mountpoint', nargs='?', help="filesystem mount point")
    parser.add_argument(
        '-o', metavar='options', dest='opts',
        help="comma separated list of options: foreground, debug, allowother")
    parser.add_argument('--cachesize', help="zip files cache size", default=1000)
    arg = parser.parse_args()

    CachedZipFactory.MAX_CACHE_SIZE = arg.cachesize

    opts = arg.opts.split(',') if arg.opts else []

    logging.basicConfig(level=logging.DEBUG if 'debug' in opts else logging.INFO)

    fuse = FUSE(
        ZipROFS(arg.root),
        arg.mountpoint,
        foreground=('foreground' in opts),
        allow_other=('allowother' in opts)
    )
