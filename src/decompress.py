""" Lazy Oodle Kraken decompression support """

from __future__ import annotations

import ctypes
import os
from pathlib import Path

class OodleUnavailableError(RuntimeError):
    """Raised when a usable Oodle runtime cannot be found"""

_oodle = None

def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []

    configured = os.environ.get("R6_OODLE_DLL")
    if configured:
        candidates.append(Path(configured).expanduser())

    project_root = Path(__file__).resolve().parent.parent
    candidates.extend(sorted(project_root.glob("oo2core_*_win64.dll"), reverse=True))

    return candidates

def _load_oodle():
    global _oodle

    if _oodle is not None:
        return _oodle

    errors: list[str] = []

    for path in _candidate_paths():
        if not path.is_file():
            continue

        try:
            dll = ctypes.WinDLL(str(path))
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue

        function = dll.OodleLZ_Decompress
        function.restype = ctypes.c_int64
        function.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int32,
        ]

        _oodle = dll
        return dll

    details = f" Attempts: {'; '.join(errors)}" if errors else ""

    raise OodleUnavailableError("Oodle runtime not found. Place oo2core_*_win64.dll in the project root or set R6_OODLE_DLL to its full path." + details)

def oodle_decompress(src: bytes, dst_size: int) -> bytes:
    """ Decompress one Oodle chunk and verify its output size """

    if dst_size < 0:
        raise ValueError("Destination size cannot be negative")

    dll = _load_oodle()
    destination = ctypes.create_string_buffer(dst_size)

    result = dll.OodleLZ_Decompress(
        src,
        len(src),
        destination,
        dst_size,
        0,      # fuzz
        0,      # check CRC
        0,      # verbosity
        None,   # destination base
        0,
        None,   # callback
        None,   # callback context
        None,   # scratch memory
        0,
        3       # unthreaded decode phase
    )

    if result != dst_size:
        raise RuntimeError(f"Oodle returned {result} bytes; expected {dst_size}")

    return destination.raw[:result]