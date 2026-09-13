"""Fail-closed Linux filesystem primitives for autonomous workspace safety."""

from __future__ import annotations

import ctypes
import fcntl
import os
import platform
import stat
from dataclasses import dataclass


class LinuxFilesystemError(PermissionError):
    """A required Linux filesystem guarantee could not be established."""


RESOLVE_NO_XDEV = 0x01
RESOLVE_NO_MAGICLINKS = 0x02
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_BENEATH = 0x08
SAFE_RESOLVE = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV

RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
AT_EMPTY_PATH = 0x1000
AT_REMOVEDIR = 0x200
STATX_BASIC_STATS = 0x07FF
STATX_MNT_ID = 0x1000
FS_IOC_GETFLAGS = 0x80086601

FS_SYNC_FL = 0x00000008
FS_IMMUTABLE_FL = 0x00000010
FS_APPEND_FL = 0x00000020
FS_NODUMP_FL = 0x00000040
FS_NOATIME_FL = 0x00000080
FS_COMPR_FL = 0x00000004
FS_DIRSYNC_FL = 0x00010000
FS_NOCOW_FL = 0x00800000
FS_PROJINHERIT_FL = 0x20000000
FS_CASEFOLD_FL = 0x40000000
FS_EXTENT_FL = 0x00080000
FS_INDEX_FL = 0x00001000
FS_INLINE_DATA_FL = 0x10000000
FS_ENCRYPT_FL = 0x00000800
FS_VERITY_FL = 0x00100000
FS_DAX_FL = 0x02000000

REGULAR_FILE_ALLOWED_FLAGS = FS_EXTENT_FL | FS_INLINE_DATA_FL
DIRECTORY_ALLOWED_FLAGS = FS_EXTENT_FL | FS_INDEX_FL


class _OpenHow(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


class _StatxTimestamp(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    _fields_ = [
        ("mask", ctypes.c_uint32),
        ("blksize", ctypes.c_uint32),
        ("attributes", ctypes.c_uint64),
        ("nlink", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("ino", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("blocks", ctypes.c_uint64),
        ("attributes_mask", ctypes.c_uint64),
        ("atime", _StatxTimestamp),
        ("btime", _StatxTimestamp),
        ("ctime", _StatxTimestamp),
        ("mtime", _StatxTimestamp),
        ("rdev_major", ctypes.c_uint32),
        ("rdev_minor", ctypes.c_uint32),
        ("dev_major", ctypes.c_uint32),
        ("dev_minor", ctypes.c_uint32),
        ("mnt_id", ctypes.c_uint64),
        ("dio_mem_align", ctypes.c_uint32),
        ("dio_offset_align", ctypes.c_uint32),
        ("spare3", ctypes.c_uint64 * 12),
    ]


@dataclass(frozen=True, slots=True)
class LinuxStat:
    device: int
    inode: int
    mount_id: int
    mode: int
    uid: int
    gid: int
    nlink: int


_LIBC = ctypes.CDLL(None, use_errno=True)
_OPENAT2_NUMBERS = {"x86_64": 437, "aarch64": 437}


def _require_linux() -> None:
    if platform.system() != "Linux":
        raise LinuxFilesystemError("La protección de filesystem requiere Linux.")


def _raise_oserror(label: str) -> None:
    number = ctypes.get_errno()
    raise LinuxFilesystemError(f"{label} falló: errno={number}.") from OSError(
        number, os.strerror(number)
    )


def openat2(
    directory_fd: int,
    path: str,
    flags: int,
    *,
    mode: int = 0,
    resolve: int = SAFE_RESOLVE,
) -> int:
    """Open beneath a trusted descriptor with kernel-enforced resolution rules."""
    _require_linux()
    number = _OPENAT2_NUMBERS.get(platform.machine())
    if number is None:
        raise LinuxFilesystemError("Arquitectura sin syscall openat2 soportada.")
    if not path or path.startswith("/") or "\x00" in path:
        raise LinuxFilesystemError("Ruta descriptor-relativa inválida.")
    how = _OpenHow(flags=flags, mode=mode, resolve=resolve)
    result = _LIBC.syscall(
        number,
        directory_fd,
        os.fsencode(path),
        ctypes.byref(how),
        ctypes.sizeof(how),
    )
    if result < 0:
        _raise_oserror("openat2")
    return int(result)


def statx_fd(fd: int) -> LinuxStat:
    """Return stable inode and mount identity for an open descriptor."""
    _require_linux()
    function = getattr(_LIBC, "statx", None)
    if function is None:
        raise LinuxFilesystemError("statx no está disponible.")
    value = _Statx()
    result = function(fd, b"", AT_EMPTY_PATH, STATX_BASIC_STATS | STATX_MNT_ID, ctypes.byref(value))
    if result != 0:
        _raise_oserror("statx")
    if not value.mask & STATX_MNT_ID:
        raise LinuxFilesystemError("statx no informó mount_id.")
    metadata = os.fstat(fd)
    return LinuxStat(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mount_id=int(value.mnt_id),
        mode=metadata.st_mode,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        nlink=metadata.st_nlink,
    )


def renameat2(old_fd: int, old: str, new_fd: int, new: str, flags: int) -> None:
    _require_linux()
    function = getattr(_LIBC, "renameat2", None)
    if function is None:
        raise LinuxFilesystemError("renameat2 no está disponible.")
    if function(old_fd, os.fsencode(old), new_fd, os.fsencode(new), flags) != 0:
        _raise_oserror("renameat2")


def linkat(old_fd: int, old: str, new_fd: int, new: str, flags: int = 0) -> None:
    _require_linux()
    function = getattr(_LIBC, "linkat", None)
    if function is None or function(old_fd, os.fsencode(old), new_fd, os.fsencode(new), flags) != 0:
        _raise_oserror("linkat")


def unlinkat(directory_fd: int, path: str, *, directory: bool = False) -> None:
    _require_linux()
    function = getattr(_LIBC, "unlinkat", None)
    flags = AT_REMOVEDIR if directory else 0
    if function is None or function(directory_fd, os.fsencode(path), flags) != 0:
        _raise_oserror("unlinkat")


def filesystem_flags(fd: int) -> int:
    """Read Linux inode flags; unsupported inspection fails closed."""
    value = ctypes.c_uint(0)
    try:
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, value, True)
    except OSError as exc:
        raise LinuxFilesystemError("No se pudieron inspeccionar flags Linux.") from exc
    return int(value.value)


def validate_metadata(fd: int, *, directory: bool) -> LinuxStat:
    """Apply the frozen 9A.4 metadata allowlist to an open object."""
    metadata = statx_fd(fd)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.mode):
        raise LinuxFilesystemError("Tipo de objeto filesystem no permitido.")
    allowed = DIRECTORY_ALLOWED_FLAGS if directory else REGULAR_FILE_ALLOWED_FLAGS
    flags = filesystem_flags(fd)
    if flags & ~allowed:
        raise LinuxFilesystemError("El objeto tiene flags filesystem no permitidos.")
    try:
        attributes = os.listxattr(fd)
    except OSError as exc:
        raise LinuxFilesystemError("No se pudieron inspeccionar xattrs.") from exc
    if attributes:
        raise LinuxFilesystemError("El objeto tiene xattrs no permitidos.")
    return metadata


def fsync_fd(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        raise LinuxFilesystemError("fsync falló.") from exc


def probe_required_primitives() -> None:
    """Fail early when the required Linux syscall surface is unavailable."""
    _require_linux()
    for name in ("statx", "renameat2", "linkat", "unlinkat"):
        if getattr(_LIBC, name, None) is None:
            raise LinuxFilesystemError(f"Primitiva Linux requerida ausente: {name}.")
    root = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        child = openat2(root, ".", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        os.close(child)
        statx_fd(root)
    finally:
        os.close(root)
