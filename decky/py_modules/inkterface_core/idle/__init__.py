"""Idle-mode host side: layout compiler, store packer, decoder, uploader.

See docs/idle-mode.md (authoritative wire format) and firmware/src/ee05/idle_layout.h.
The compiler (compile.py) and the firmware decoder must agree byte for byte; decode.py
is the Python oracle the C++ decoder is fuzz-compared against.
"""
