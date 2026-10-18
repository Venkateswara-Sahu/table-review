"""Set unattended native-error policy before importing application libraries."""

import os


def prepare_test_process():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetErrorMode.argtypes = []
        kernel.GetErrorMode.restype = wintypes.UINT
        kernel.SetErrorMode.argtypes = [wintypes.UINT]
        kernel.SetErrorMode.restype = wintypes.UINT
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0003)
