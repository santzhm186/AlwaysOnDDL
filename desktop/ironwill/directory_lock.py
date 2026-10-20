# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Cross-process directory ownership using only the OS and standard library.

Lock files stay in place: unlinking a held file can create a second lock inode.
The OS releases the lock on normal exit or process termination.
"""
import errno
import os
from pathlib import Path


class DirectoryBusyError(OSError):
    pass


class DirectoryLock:
    def __init__(self, directory, name=".ironwill-instance.lock"):
        self.path = Path(directory).expanduser().resolve() / name
        self.stream = None

    def acquire(self):
        if self.stream is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or getattr(error, "winerror", None) in (32, 33):
                raise DirectoryBusyError("此数据目录已由另一写入者占用。") from error
            raise
        except BaseException:
            stream.close()
            raise
        self.stream = stream
        return self

    def release(self):
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
