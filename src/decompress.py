import ctypes
import os

_dll_path = os.path.join(os.path.dirname(__file__), "..", "oo2core_8_win64.dll")
_oodle = ctypes.WinDLL(_dll_path)

# int64 OodleLZ_Decompress(src, src_len, dst, dst_len, fuzz=1, check=1, verbose=0, dst_base=0, e=0, cb=0, cb_ctx=0, scratch=0, scratch_len=0, threadPhase=3)
_oodle.OodleLZ_Decompress.restype = ctypes.c_int64
_oodle.OodleLZ_Decompress.argtypes = [
    ctypes.c_void_p, ctypes.c_int64, # src, src_len
    ctypes.c_void_p, ctypes.c_int64, # dst, dst_len
    ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, # fuzz, check, verbose
    ctypes.c_void_p, ctypes.c_int64, # dst_base, e
    ctypes.c_void_p, ctypes.c_void_p, # cb, cb_ctx
    ctypes.c_void_p, ctypes.c_size_t, # scratch, scratch_len
    ctypes.c_int32 # threadPhase
]

def oodle_decompress(src: bytes, dst_size: int) -> bytes:
    dst = ctypes.create_string_buffer(dst_size)
    n = _oodle.OodleLZ_Decompress(
        src, len(src),
        dst, dst_size,
        0, 0, 0, # fuzz, check, verbose
        None, 0,
        None, None,
        None, 0,
        3, # threadPhase = OodleLZ_Decode_Unthreaded
    )
    if n != dst_size:
        raise RuntimeError(f"Oodle returned {n}, expected {dst_size}")
    return dst.raw[:n]