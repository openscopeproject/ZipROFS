#!/usr/bin/env python3
from __future__ import print_function, absolute_import, division

from functools import lru_cache

from os.path import realpath

import argparse
import ctypes
import errno
import logging
import os
import time
import zipfile
import stat
from threading import RLock
from typing import Optional, Dict

try:
    from fuse import FUSE, FuseOSError, Operations, LoggingMixIn, S_IFDIR, fuse_operations
    import fuse as fusepy
except ImportError:
    # ubuntu renamed package in repository
    from fusepy import FUSE, FuseOSError, Operations, LoggingMixIn, S_IFDIR, fuse_operations
    import fusepy

from collections import OrderedDict


@lru_cache(maxsize=2048)
def is_zipfile(path, mtime):
    # mtime just to miss cache on changed files
    return zipfile.is_zipfile(path)


class ZipFile(zipfile.ZipFile):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__lock = RLock()

    def lock(self):
        return self.__lock


class CachedZipFactory(object):
    MAX_CACHE_SIZE = 1000
    cache = OrderedDict()
    log = logging.getLogger('ziprofs.cache')

    def __init__(self):
        self.__lock = RLock()

    def _add(self, path: str):
        if path in self.cache:
            return
        while len(self.cache) >= self.MAX_CACHE_SIZE:
            oldpath, val = self.cache.popitem(last=False)
            self.log.debug('Popping cache entry: %s', oldpath)
            val[1].close()
        mtime = os.lstat(path).st_mtime
        self.log.debug("Caching path (%s:%s)", path, mtime)
        self.cache[path] = (mtime, ZipFile(path))

    def get(self, path: str) -> ZipFile:
        with self.__lock:
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


class ZipROFS(Operations):
    zip_factory = CachedZipFactory()

    def __init__(self, root, zip_check):
        self.root = realpath(root)
        self.zip_check = zip_check
        # odd file handles are files inside zip, even fhs are system-wide files
        self._zip_file_fh: Dict[int, zipfile.ZipExtFile] = {}
        self._zip_zfile_fh: Dict[int, ZipFile] = {}
        self._fh_locks: Dict[int, RLock] = {}
        self._lock = RLock()

    def __call__(self, op, path, *args):
        return super().__call__(op, self.root + path, *args)

    def _get_free_zip_fh(self):
        i = 5   # avoid confusion with stdin/err/out
        while i in self._zip_file_fh:
            i += 2
        return i

    def get_zip_path(self, path: str) -> Optional[str]:
        parts = []
        head, tail = os.path.split(path)
        while tail:
            parts.append(tail)
            head, tail = os.path.split(head)
        parts.reverse()
        cur_path = '/'
        for part in parts:
            cur_path = os.path.join(cur_path, part)
            if part[-4:] == '.zip' and (
                    not self.zip_check or is_zipfile(cur_path, os.lstat(cur_path).st_mtime)):
                return cur_path
        return None

    def access(self, path, mode):
        if self.get_zip_path(path):
            if mode & os.W_OK:
                raise FuseOSError(errno.EROFS)
        else:
            if not os.access(path, mode):
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
            subpath = path[len(zip_path) + 1:]
            info = None
            try:
                info = zf.getinfo(subpath)
                result['st_size'] = info.file_size
                result['st_mode'] = stat.S_IFREG | 0o555
            except KeyError:
                # check if it is a valid subdirectory
                try:
                    info = zf.getinfo(subpath + '/')
                except KeyError:
                    pass
                found = False
                if not info:
                    infolist = zf.infolist()
                    for f in infolist:
                        if f.filename.find(subpath + '/') == 0:
                            found = True
                            break
                if found or info:
                    result['st_mode'] = S_IFDIR | 0o555
                else:
                    raise FuseOSError(errno.ENOENT)
            if info:
                # update mtime
                try:
                    mtime = time.mktime(info.date_time + (0, 0, -1))
                    result['st_mtime'] = mtime
                except Exception:
                    pass
        return result

    def open(self, path, flags):
        zip_path = self.get_zip_path(path)
        if zip_path:
            with self._lock:
                fh = self._get_free_zip_fh()
                zf = self.zip_factory.get(zip_path)
                self._zip_zfile_fh[fh] = zf
                self._zip_file_fh[fh] = zf.open(path[len(zip_path) + 1:])
                return fh
        else:
            fh = os.open(path, flags) << 1
            self._fh_locks[fh] = RLock()
            return fh

    def read(self, path, size, offset, fh):
        if fh in self._zip_file_fh:
            # should be here (file is first opened, then read)
            f = self._zip_file_fh[fh]
            with self._zip_zfile_fh[fh].lock():
                if not f.seekable():
                    raise FuseOSError(errno.EBADF)

                f.seek(offset)
                return f.read(size)
        else:
            with self._fh_locks[fh]:
                os.lseek(fh >> 1, offset, 0)
                return os.read(fh >> 1, size)

    def readdir(self, path, fh):
        zip_path = self.get_zip_path(path)
        if not zip_path:
            return ['.', '..'] + os.listdir(path)
        subpath = path[len(zip_path) + 1:]
        zf = self.zip_factory.get(zip_path)
        infolist = zf.infolist()

        result = ['.', '..']
        subdirs = set()
        for info in infolist:
            if info.filename.find(subpath) == 0 and info.filename > subpath:
                suffix = info.filename[len(subpath) + 1 if subpath else 0:]
                if not suffix:
                    continue
                if '/' not in suffix:
                    result.append(suffix)
                else:
                    subdirs.add(suffix[:suffix.find('/')])
        result.extend(subdirs)
        return result

    def release(self, path, fh):
        if fh in self._zip_file_fh:
            with self._lock:
                f = self._zip_file_fh[fh]
                with self._zip_zfile_fh[fh].lock():
                    del self._zip_file_fh[fh]
                    del self._zip_zfile_fh[fh]
                    return f.close()
        else:
            with self._fh_locks[fh]:
                del self._fh_locks[fh]
                return os.close(fh >> 1)

    def statfs(self, path):
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in (
            'f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
            'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax'
        ))


class ZipROFSDebug(LoggingMixIn, ZipROFS):
    def __call__(self, op, path, *args):
        return super().__call__(op, self.root + path, *args)


class fuse_conn_info(ctypes.Structure):
    _fields_ = [
        ('proto_major', ctypes.c_uint),
        ('proto_minor', ctypes.c_uint),
        ('async_read', ctypes.c_uint),
        ('max_write', ctypes.c_uint),
        ('max_readahead', ctypes.c_uint),
        ('capable', ctypes.c_uint),
        ('want', ctypes.c_uint),
        ('reserved', ctypes.c_uint, 25)]


class ZipROFuse(FUSE):
    def __init__(self, operations, mountpoint, **kwargs):
        self.support_async = kwargs.get('support_async', False)
        del kwargs['support_async']
        if not self.support_async:
            # monkeypatch fuse_operations
            ops = fuse_operations._fields_
            for i in range(len(ops)):
                if ops[i][0] == 'init':
                    ops[i] = (
                        'init',
                        ctypes.CFUNCTYPE(
                            ctypes.c_voidp, ctypes.POINTER(fuse_conn_info))
                    )
                fusepy.fuse_operations = type(
                    'fuse_operations', (ctypes.Structure,), {'_fields_': ops})
        super().__init__(operations, mountpoint, **kwargs)

    def init(self, conn):
        if not self.support_async:
            conn[0].async_read = 0
            conn[0].want = conn.contents.want & ~1
        return self.operations('init', '/')


def parse_mount_opts(in_str):
    opts = {}
    for o in in_str.split(','):
        if '=' in o:
            name, val = o.split('=', 1)
            opts[name] = val
        else:
            opts[o] = True
    return opts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='ZipROFS read only transparent zip filesystem.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('root', nargs='?', help="filesystem root")
    parser.add_argument(
        'mountpoint',
        nargs='?',
        help="filesystem mount point")
    parser.add_argument(
        '-o', metavar='options', dest='opts',
        help="comma separated list of options: foreground, debug, allowother, "
        "nozipcheck, async, cachesize=N",
        type=parse_mount_opts, default={})
    arg = parser.parse_args()

    if 'cachesize' in arg.opts:
        cache_size = int(arg.opts['cachesize'])
        if cache_size < 1:
            raise ValueError("Bad cache size")
        CachedZipFactory.MAX_CACHE_SIZE = cache_size

    logging.basicConfig(
        level=logging.DEBUG if 'debug' in arg.opts else logging.INFO)

    zip_check = 'nozipcheck' not in arg.opts

    if 'debug' in arg.opts:
        fs = ZipROFSDebug(arg.root, zip_check)
    else:
        fs = ZipROFS(arg.root, zip_check)

    fuse = ZipROFuse(
        fs,
        arg.mountpoint,
        foreground=('foreground' in arg.opts),
        allow_other=('allowother' in arg.opts),
        support_async=('async' in arg.opts)
    )
