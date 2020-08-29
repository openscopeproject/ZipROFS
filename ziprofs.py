#!/usr/bin/env python3
from __future__ import print_function, absolute_import, division

import logging

from os.path import realpath
from sys import argv, exit
from threading import Lock

import argparse
import errno
import logging
import os
import zipfile
import stat

from fusepy import FUSE, FuseOSError, Operations, LoggingMixIn, S_IFDIR
from collections import OrderedDict


class CachedZipFile(object):
    MAX_SEEK_READ = 1 << 24

    def __init__(self, path):
        self.cache = {}
        self.zf = zipfile.ZipFile(path)

    def read(self, subpath, size, offset):
        if subpath not in self.cache:
            self.cache[subpath] = (0, self.zf.open(subpath))
        pos, f = self.cache[subpath]
        if f.seekable():
            pos = f.seek(offset)
            buf = f.read(size)
        else:
            if offset < pos:
                pos = 0
                f.close()
                f = self.zf.open(subpath)
            else:
                offset -= pos
            while offset > 0:
                read_len = min(self.MAX_SEEK_READ, offset)
                buf = f.read(read_len)
                pos += len(buf)
                offset -= read_len
            buf = f.read(size)

        pos += len(buf)
        self.cache[subpath] = (pos, f)
        return buf

    def close(self):
        for k, v in self.cache.items():
            v[1].close()
        self.zf.close()

    # pass through methods
    def getinfo(self, subpath):
        return self.zf.getinfo(subpath)

    def infolist(self):
        return self.zf.infolist()


class CachedZipFactory(object):
    MAX_CACHE_SIZE=1000
    cache = OrderedDict()
    log = logging.getLogger('ziprofs.cache')
    rwlock = Lock()

    def _cleanup(self, zf: CachedZipFile):
        zf.close()
        del zf

    def _add(self, path: str):
        if path in self.cache:
            return
        with self.rwlock:
            if len(self.cache) == self.MAX_CACHE_SIZE:
                oldkey, oldvalue = self.cache.popitem(last=False)
                self.log.debug('Popping cache entry: %s', oldkey)
                self._cleanup(oldvalue[1])
            mtime = os.lstat(path).st_mtime
            self.log.debug("Caching path (%s:%s)", path, mtime)
            self.cache[path] = (mtime, CachedZipFile(path))

    def get(self, path: str) -> object:
        if path in self.cache:
            self.cache.move_to_end(path)
            mtime = os.lstat(path).st_mtime
            if mtime > self.cache[path][0]:
                with self.rwlock:
                    oldvalue = self.cache.pop(path)
                self._cleanup(oldvalue[1])
                self._add(path)
        else:
            self._add(path)
        return self.cache[path][1]


class ZipROFS(LoggingMixIn, Operations):
    zip_factory = CachedZipFactory()

    def __init__(self, root):
        self.root = realpath(root)
        self.rwlock = Lock()

    def __call__(self, op, path, *args):
        return super(ZipROFS, self).__call__(op, self.root + path, *args)

    @staticmethod
    def get_zip_path(path: str) -> str:
        parts = []
        head, tail = os.path.split(path)
        while tail:
            parts.append(tail)
            head, tail = os.path.split(head)
        parts.reverse()
        cur_path = '/'
        for part in parts:
            cur_path = os.path.join(cur_path, part)
            if zipfile.is_zipfile(cur_path):
                return cur_path
        return None

    def access(self, path, mode):
        zip_path = self.get_zip_path(path)
        if not zip_path:
            if not os.access(path, mode):
                raise FuseOSError(errno.EACCES)
        if mode == os.W_OK:
            raise FuseOSError(errno.EACCES)

    def getattr(self, path, fh=None):
        zip_path = self.get_zip_path(path)
        st = os.lstat(zip_path) if zip_path else os.lstat(path)
        result = {key: getattr(st, key) for key in ('st_atime', 'st_ctime',
            'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid')}
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
        zip_path = self.get_zip_path(path)
        if not zip_path:
            return os.open(path, flags)
        return 0

    def read(self, path, size, offset, fh):
        with self.rwlock:
            zip_path = self.get_zip_path(path)
            if not zip_path:
                os.lseek(fh, offset, 0)
                return os.read(fh, size)
            zf = self.zip_factory.get(zip_path)
            subpath = path[len(zip_path)+1:]
            return zf.read(subpath, size, offset)

    def readdir(self, path, fh):
        zip_path = self.get_zip_path(path)
        if not zip_path:
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
        zip_path = self.get_zip_path(path)
        if not zip_path:
            return os.close(fh)
        return 0

    def statfs(self, path):
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree',
            'f_blocks', 'f_bsize', 'f_favail', 'f_ffree', 'f_files', 'f_flag',
            'f_frsize', 'f_namemax'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='ZipROFS read only transparent zip filesystem.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('ROOT', nargs='?', help="filesystem root")
    parser.add_argument('MOUNTPOINT', nargs='?', help="filesystem mount point")
    parser.add_argument(
        '-o', metavar='options', dest='opts',
        help="comma separated list of options: foreground, debug, allowother")
    args = parser.parse_args()
    opts = args.opts.split(',') if args.opts else []
    print(opts)

    logging.basicConfig(level=logging.DEBUG if 'debug' in opts else logging.INFO)

    fuse = FUSE(
        ZipROFS(args.ROOT),
        args.MOUNTPOINT,
        foreground=('foreground' in opts),
        allow_other=('allowother' in opts))
