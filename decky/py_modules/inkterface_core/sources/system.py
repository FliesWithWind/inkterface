"""Host telemetry from procfs and sysfs. A missing sensor yields None, never an exception.

`root` re-roots every path (`<root>/proc/stat`, `<root>/sys/class/hwmon`) so tests use fake trees.
"""
from __future__ import annotations

import glob
import math
import os
import re
import time

from .base import Source

METRICS = (
    "system.cpu.load",
    "system.cpu.temp",
    "system.cpu.clock",
    "system.gpu.load",
    "system.gpu.temp",
    "system.gpu.clock",
    "system.gpu.mem_clock",
    "system.mem.used_pct",
    "system.fan.rpm",
    "system.power.package_w",
    "system.disk.temp",
)

CPU_HWMON = ("k10temp", "zenpower", "coretemp")
CPU_TEMP_LABELS = ("Tdie", "Tctl", "Package id 0")  # Tdie is the real die temp; Tctl carries an offset on some Ryzens
GPU_TEMP_LABELS = ("edge", "junction")
FAN_PREFERRED = ("steamdeck_hwmon",)
HWMON_TTL_S = 5.0  # how long the list of hwmon devices is reused (sensor VALUES are always read fresh)
DISK_TTL_S = 10.0  # system.disk.temp is slow-moving and not bindable by screens: read it at most this often
_DPM = re.compile(r"(\d+(?:\.\d+)?)\s*mhz(\s*\*)?", re.I)


def _natural(name: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:  # missing, ENODEV/EIO from a sleeping GPU, permission
        return None


def _num(path: str, scale: float = 1.0) -> float | None:
    t = _read(path)
    if not t:
        return None
    try:
        v = float(t) * scale
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class SystemSource(Source):
    name = "system"
    interval = 1.0
    max_backoff = 30.0
    fast_interval = 0.15  # while a screen shows one of these; a poll is ~0.7 ms (disk temp and hwmon discovery are cached, see below)
    paths = METRICS
    history_paths = METRICS

    def __init__(self, root: str = "/", clock=time.monotonic):
        super().__init__()
        self.root = root
        self._clock = clock
        self._disk: tuple[float, float | None] | None = None  # (clock at read, value): the NVMe sensor read is ~3 ms
        self._hw: tuple[float, list] | None = None             # (clock at scan, [(dir, name)]): was re-read 4x per poll
        self._prev: tuple[float, float] | None = None  # (busy, total) jiffies from the last poll
        self._last_load: float | None = None

    def _p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    # -- hwmon / drm discovery -------------------------------------------------------------

    def _hwmons(self) -> list[tuple[str, str]]:
        now = self._clock()
        if self._hw is None or now - self._hw[0] >= HWMON_TTL_S:   # a hwmon that appears later (GPU resume, hotplug) shows up within this
            self._hw = (now, self._scan_hwmons())
        return self._hw[1]

    def _scan_hwmons(self) -> list[tuple[str, str]]:
        base = self._p("sys/class/hwmon")
        try:
            names = sorted(os.listdir(base), key=_natural)
        except OSError:
            return []
        out = []
        for d in names:
            path = os.path.join(base, d)
            n = _read(os.path.join(path, "name"))
            if n:
                out.append((path, n))
        return out

    @staticmethod
    def _labelled(hw: str, kind: str, suffix: str = "input") -> dict[str, str]:
        """{lowercase label: input file path} for `<kind>*_label` files; also '' -> lowest-numbered input."""
        found: dict[str, str] = {}
        inputs = sorted(glob.glob(os.path.join(glob.escape(hw), f"{kind}[0-9]*_{suffix}")), key=_natural)
        for f in inputs:
            lab = _read(f[: -len(suffix)] + "label")
            if lab:
                found.setdefault(lab.lower(), f)
        if inputs:
            found[""] = inputs[0]
        return found

    def _temp(self, hw: str, labels=()) -> float | None:
        found = self._labelled(hw, "temp")
        for lab in (*labels, ""):
            f = found.get(lab.lower())
            if f:
                v = _num(f, 0.001)
                if v is not None:
                    return v
        return None

    def _first_hwmon_temp(self, names, labels) -> float | None:
        hws = self._hwmons()
        for want in names:
            for path, n in hws:
                if n == want:
                    v = self._temp(path, labels)
                    if v is not None:
                        return v
        return None

    def _gpu(self) -> tuple[str | None, str | None]:
        """(drm device dir, amdgpu hwmon dir) of the first card exposing gpu_busy_percent."""
        cards = []
        try:
            cards = sorted((c for c in os.listdir(self._p("sys/class/drm")) if re.fullmatch(r"card\d+", c)), key=_natural)
        except OSError:
            pass
        dev = None
        for c in cards:
            d = self._p("sys/class/drm", c, "device")
            if os.path.exists(os.path.join(d, "gpu_busy_percent")):
                dev = d
                break
        amd = [p for p, n in self._hwmons() if n == "amdgpu"]
        hw = None
        if dev:  # pair by PCI device: hwmonN/device and cardN/device resolve to the same directory
            real = os.path.realpath(dev)
            hw = next((p for p in amd if os.path.realpath(os.path.join(p, "device")) == real), None)
        return dev, hw or (amd[0] if amd else None)

    @staticmethod
    def _dpm_mhz(path: str) -> float | None:
        """Active level of pp_dpm_*: the line marked '*', e.g. '1: 700Mhz *'."""
        t = _read(path)
        for m in _DPM.finditer(t or ""):
            if m.group(2):
                return float(m.group(1))
        return None

    def _hw_freq_mhz(self, hw: str | None, label: str) -> float | None:
        """amdgpu hwmon freqN_input (Hz) selected by freqN_label ('sclk' / 'mclk')."""
        if not hw:
            return None
        f = self._labelled(hw, "freq").get(label)
        return _num(f, 1e-6) if f else None

    # -- individual metrics ----------------------------------------------------------------

    def _cpu_load(self) -> float | None:
        t = _read(self._p("proc/stat"))
        line = t.splitlines()[0].split() if t else []
        if len(line) < 5 or line[0] != "cpu":
            return self._last_load
        try:
            f = [float(x) for x in line[1:9]]  # user nice system idle iowait irq softirq steal
        except ValueError:
            return self._last_load
        total = sum(f)
        busy = total - f[3] - (f[4] if len(f) > 4 else 0.0)  # iowait counts as idle
        prev, self._prev = self._prev, (busy, total)
        if prev is None:
            return None
        dt = total - prev[1]
        if dt < 0:  # counter wrapped or reset (reboot, 32-bit wrap): resync, keep the last value
            return self._last_load
        if dt == 0:  # polled twice within one jiffy
            return self._last_load
        self._last_load = min(100.0, max(0.0, (busy - prev[0]) / dt * 100.0))
        return self._last_load

    def _cpu_clock(self) -> float | None:
        t = _read(self._p("proc/cpuinfo")) or ""
        mhz = [float(m) for m in re.findall(r"^cpu MHz\s*:\s*([0-9.]+)", t, re.M)]
        if not mhz:  # ARM and some VMs: no 'cpu MHz'
            pat = glob.escape(self._p("sys/devices/system/cpu")) + "/cpu[0-9]*/cpufreq/scaling_cur_freq"
            mhz = [v for v in (_num(f, 0.001) for f in glob.glob(pat)) if v is not None]
        return sum(mhz) / len(mhz) if mhz else None

    def _mem_pct(self) -> float | None:
        t = _read(self._p("proc/meminfo")) or ""
        kv = {k: float(v) for k, v in re.findall(r"^(\w+):\s+(\d+)", t, re.M)}
        total = kv.get("MemTotal", 0)
        if total <= 0:
            return None
        avail = kv.get("MemAvailable")
        if avail is None:  # kernels < 3.14
            avail = kv.get("MemFree", 0) + kv.get("Buffers", 0) + kv.get("Cached", 0)
        return min(100.0, max(0.0, (total - avail) / total * 100.0))

    def _fan(self) -> float | None:
        best: dict[bool, list[float]] = {True: [], False: []}
        for path, n in self._hwmons():
            for f in glob.glob(os.path.join(glob.escape(path), "fan[0-9]*_input")):
                v = _num(f)
                if v is not None:
                    best[n in FAN_PREFERRED].append(v)
        pool = best[True] or best[False]
        return max(pool) if pool else None

    def _power(self, gpu_hw: str | None) -> float | None:
        others = [p for p, _ in self._hwmons() if p != gpu_hw]
        for hw in ([gpu_hw] if gpu_hw else []) + others:
            for f in ("power1_average", "power1_input"):
                v = _num(os.path.join(hw, f), 1e-6)
                if v is not None:
                    return v
        return None

    def _disk_temp(self) -> float | None:
        now = self._clock()
        if self._disk is None or now - self._disk[0] >= DISK_TTL_S:
            self._disk = (now, self._read_disk_temp())
        return self._disk[1]

    def _read_disk_temp(self) -> float | None:
        vals = [v for p, n in self._hwmons() if n == "nvme" for v in [self._temp(p, ("Composite",))] if v is not None]
        return max(vals) if vals else None

    def poll(self) -> dict:
        dev, gpu_hw = self._gpu()
        sclk = mclk = None
        if dev:
            sclk = self._dpm_mhz(os.path.join(dev, "pp_dpm_sclk"))
            mclk = self._dpm_mhz(os.path.join(dev, "pp_dpm_mclk"))
        return {
            "system.cpu.load": self._cpu_load(),
            "system.cpu.temp": self._first_hwmon_temp(CPU_HWMON, CPU_TEMP_LABELS),
            "system.cpu.clock": self._cpu_clock(),
            "system.gpu.load": _num(os.path.join(dev, "gpu_busy_percent")) if dev else None,
            "system.gpu.temp": self._temp(gpu_hw, GPU_TEMP_LABELS) if gpu_hw else None,
            "system.gpu.clock": sclk if sclk is not None else self._hw_freq_mhz(gpu_hw, "sclk"),
            "system.gpu.mem_clock": mclk if mclk is not None else self._hw_freq_mhz(gpu_hw, "mclk"),
            "system.mem.used_pct": self._mem_pct(),
            "system.fan.rpm": self._fan(),
            "system.power.package_w": self._power(gpu_hw),
            "system.disk.temp": self._disk_temp(),
        }
