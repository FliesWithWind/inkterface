"""Minimal parser for Valve text KeyValues (.vdf / .acf). Stdlib only."""
from __future__ import annotations

_ESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


class VdfError(ValueError):
    pass


def _tokens(text: str):
    """Yield ('s', string) | ('{', None) | ('}', None). Handles quoted strings with escapes,
    bare words, and // line comments."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n\ufeff":
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c in "{}":
            yield c, None
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while True:
                if i >= n:
                    raise VdfError("unterminated string")
                c = text[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append(_ESC.get(nxt, "\\" + nxt))  # unknown escape (Windows paths): keep verbatim
                    i += 2
                else:
                    buf.append(c)
                    i += 1
            yield "s", "".join(buf)
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n"{}':
                j += 1
            yield "s", text[i:j]
            i = j


def parse_vdf(text: str) -> dict:
    """Parse into nested dicts of str. Duplicate keys: last wins. Raises VdfError on malformed input."""
    toks = list(_tokens(text))
    pos = 0

    def block(top: bool) -> dict:
        nonlocal pos
        out: dict = {}
        while True:
            if pos >= len(toks):
                if top:
                    return out
                raise VdfError("unclosed '{'")
            kind, val = toks[pos]
            pos += 1
            if kind == "}":
                if top:
                    raise VdfError("unexpected '}'")
                return out
            if kind == "{":
                raise VdfError("'{' without a key")
            if pos >= len(toks):
                raise VdfError(f"key {val!r} has no value")
            vkind, vval = toks[pos]
            pos += 1
            if vkind == "{":
                out[val] = block(False)
            elif vkind == "s":
                out[val] = vval
            else:
                raise VdfError(f"key {val!r} has no value")

    return block(True)


def vget(d, *path, default=None):
    """Case-insensitive nested lookup (Steam writes 'Software' and 'software' in different versions)."""
    for key in path:
        if not isinstance(d, dict):
            return default
        if key in d:
            d = d[key]
            continue
        low = key.lower()
        for k, v in d.items():
            if k.lower() == low:
                d = v
                break
        else:
            return default
    return d
