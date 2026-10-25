"""Small, bounded SRT live transport using the packaged native libsrt.

Encoding remains in the separately packaged FFmpeg. This adapter validates the
SRT handshake Stream ID itself, rather than treating a URL as authentication.
"""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path
import platform
import socket
import sys
import threading


class SRTError(OSError):
    pass


class _Address(C.Structure):
    _fields_ = [("prefix", C.c_ubyte * 2), ("port", C.c_ushort),
                ("address", C.c_ubyte * 4), ("padding", C.c_ubyte * 8)]


def _address(host, port):
    address = _Address()
    if sys.platform == "darwin":
        address.prefix[:] = [16, socket.AF_INET]
    else:
        address.prefix[:] = [socket.AF_INET, 0]
    address.port = socket.htons(port)
    address.address[:] = socket.inet_aton(socket.gethostbyname(host))
    return address


def runtime_directory():
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
    return root / "vendor" / "network-media"


_native = None
_native_lock = threading.Lock()
_dll_dirs = []


def library():
    global _native
    with _native_lock:
        if _native is not None:
            return _native
        directory = runtime_directory()
        names = {"Darwin": ["libsrt.1.5.dylib", "libsrt.dylib"],
                 "Windows": ["srt.dll", "libsrt.dll"],
                 "Linux": ["libsrt.so.1.5", "libsrt.so"]}[platform.system()]
        candidates = [directory / name for name in names]
        override = os.environ.get("ROCKET_SRT_LIBRARY")
        if override and not getattr(sys, "frozen", False):
            candidates.insert(0, Path(override))
        path = next((path for path in candidates if path.is_file()), None)
        if path is None:
            raise SRTError("Packaged SRT runtime missing; run scripts/prepare_network_media.py")
        if os.name == "nt":
            _dll_dirs.append(os.add_dll_directory(str(path.parent)))
        lib = C.CDLL(str(path))
        signatures = {
            "srt_startup": ([], C.c_int), "srt_create_socket": ([], C.c_int),
            "srt_close": ([C.c_int], C.c_int),
            "srt_setsockflag": ([C.c_int, C.c_int, C.c_void_p, C.c_int], C.c_int),
            "srt_bind": ([C.c_int, C.c_void_p, C.c_int], C.c_int),
            "srt_connect": ([C.c_int, C.c_void_p, C.c_int], C.c_int),
            "srt_listen": ([C.c_int, C.c_int], C.c_int),
            "srt_accept": ([C.c_int, C.c_void_p, C.c_void_p], C.c_int),
            "srt_sendmsg": ([C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int], C.c_int),
            "srt_recvmsg": ([C.c_int, C.c_void_p, C.c_int], C.c_int),
            "srt_getlasterror_str": ([], C.c_char_p),
            "srt_getsockstate": ([C.c_int], C.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(lib, name)
            function.argtypes, function.restype = args, result
        if lib.srt_startup() < 0:
            raise SRTError("SRT runtime initialization failed")
        _native = lib
        return lib


class SRTSocket:
    """One connection. Milliseconds here map directly to libsrt milliseconds."""

    def __init__(self, *, sender=False, accepted=None):
        self.lib = library()
        self.handle = self.lib.srt_create_socket() if accepted is None else accepted
        self._callback = None
        self._claimed = False
        self._lock = threading.Lock()
        if self.handle < 0:
            self._check(-1)
        if accepted is None:
            # TRANSTYPE=LIVE, TSBPD, late drop, 120ms receiver/peer latency,
            # 3s connect timeout, bounded blocking writes and reads.
            for option, value in ((50, 0), (21, int(sender)), (22, 1), (31, 1),
                                  (43, 120), (44, 120), (36, 3000),
                                  (13, 500), (14, 500), (49, 1316)):
                self.option(option, value)

    def _check(self, value):
        if value < 0:
            message = self.lib.srt_getlasterror_str()
            raise SRTError(message.decode(errors="replace") if message else "SRT failure")
        return value

    def option(self, name, value):
        if isinstance(value, str):
            data = value.encode("ascii")
            self._check(self.lib.srt_setsockflag(self.handle, name, data, len(data)))
        else:
            data = C.c_int(value)
            self._check(self.lib.srt_setsockflag(self.handle, name, C.byref(data), C.sizeof(data)))

    def listen(self, host, port, stream_id):
        callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_void_p, C.c_char_p)
        expected = stream_id.encode("ascii")

        def authorize(opaque, connection, version, peer, received):
            with self._lock:
                if received != expected or self._claimed:
                    return -1
                self._claimed = True
                return 0

        self._callback = callback_type(authorize)
        self.lib.srt_listen_callback.argtypes = [C.c_int, callback_type, C.c_void_p]
        self.lib.srt_listen_callback.restype = C.c_int
        self._check(self.lib.srt_listen_callback(self.handle, self._callback, None))
        addr = _address(host, port)
        self._check(self.lib.srt_bind(self.handle, C.byref(addr), C.sizeof(addr)))
        self._check(self.lib.srt_listen(self.handle, 1))

    def accept(self):
        return SRTSocket(accepted=self._check(self.lib.srt_accept(self.handle, None, None)))

    def connect(self, host, port, stream_id):
        self.option(46, stream_id)
        address = _address(host, port)
        self._check(self.lib.srt_connect(self.handle, C.byref(address), C.sizeof(address)))

    def send(self, data):
        if not 0 < len(data) <= 1316:
            raise ValueError("SRT payload must be 1..1316 bytes")
        self._check(self.lib.srt_sendmsg(self.handle, data, len(data), -1, 0))

    def receive(self):
        data = C.create_string_buffer(1316)
        size = self._check(self.lib.srt_recvmsg(self.handle, data, len(data)))
        return data.raw[:size]

    def close(self):
        with self._lock:
            handle, self.handle = self.handle, -1
        if handle >= 0:
            self.lib.srt_close(handle)

    @property
    def connected(self):
        return self.handle >= 0 and self.lib.srt_getsockstate(self.handle) == 5
