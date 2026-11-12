"""Fake /proc, /sys and Steam trees for the sources tests."""
import os
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fx(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def w(root, rel: str, text: str = "") -> Path:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def link(root, rel: str, target) -> None:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(target), p)


def proc(root, stat="cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1\n", mhz=(3400.0, 3600.0), memtotal=16000, memavail=4000):
    w(root, "proc/stat", stat)
    w(root, "proc/cpuinfo", "".join(f"processor\t: {i}\ncpu MHz\t\t: {m}\nflags\t: x\n\n" for i, m in enumerate(mhz)))
    w(root, "proc/meminfo", f"MemTotal:       {memtotal} kB\nMemFree:         100 kB\nMemAvailable:   {memavail} kB\nBuffers: 1 kB\n")


def hwmon(root, idx: int, name: str, files: dict, device=None) -> Path:
    d = Path(root) / f"sys/class/hwmon/hwmon{idx}"
    w(root, f"sys/class/hwmon/hwmon{idx}/name", name + "\n")
    for k, v in files.items():
        w(root, f"sys/class/hwmon/hwmon{idx}/{k}", str(v) + "\n")
    if device is not None:
        link(root, f"sys/class/hwmon/hwmon{idx}/device", device)
    return d


def gpu(root, card: int, pci: str, busy=37, sclk="0: 600Mhz\n1: 1600Mhz *\n2: 2200Mhz\n", mclk="0: 400Mhz\n1: 1600Mhz *\n"):
    """A drm card whose PCI device dir holds the amdgpu files. Returns the pci dir."""
    dev = Path(root) / "sys/devices" / pci
    dev.mkdir(parents=True, exist_ok=True)
    if busy is not None:
        (dev / "gpu_busy_percent").write_text(f"{busy}\n")
    if sclk is not None:
        (dev / "pp_dpm_sclk").write_text(sclk)
    if mclk is not None:
        (dev / "pp_dpm_mclk").write_text(mclk)
    (Path(root) / f"sys/class/drm/card{card}").mkdir(parents=True, exist_ok=True)
    link(root, f"sys/class/drm/card{card}/device", dev)
    (Path(root) / f"sys/class/drm/card{card}-DP-1").mkdir(exist_ok=True)  # connector entries must be ignored
    return dev


def amd_apu(root):
    """Steam Deck / handheld-like APU: amdgpu + k10temp + steamdeck_hwmon."""
    proc(root)
    dev = gpu(root, 0, "pci0000:00/0000:c1:00.0")
    hwmon(root, 0, "amdgpu", {"temp1_input": 45000, "temp1_label": "edge", "power1_average": 12500000,
                              "freq1_input": 1600000000, "freq1_label": "sclk"}, device=dev)
    hwmon(root, 1, "k10temp", {"temp1_input": 52500, "temp1_label": "Tctl"})
    hwmon(root, 2, "steamdeck_hwmon", {"fan1_input": 3200})
    return root


def desktop(root):
    """Desktop: NVIDIA card0 (no amdgpu files), AMD dGPU card1, k10temp with Tctl+Tdie, two nvme, a case fan."""
    proc(root, mhz=(4200.0,))
    link(root, "sys/class/drm/card0/device", Path(root) / "sys/devices/pci0/nvidia")  # dangling-ish: dir made below
    (Path(root) / "sys/devices/pci0/nvidia").mkdir(parents=True)
    dev = gpu(root, 1, "pci0/0000:03:00.0", busy=88)
    hwmon(root, 0, "nvme", {"temp1_input": 40000, "temp1_label": "Composite", "temp2_input": 99000, "temp2_label": "Sensor 1"})
    hwmon(root, 1, "nvme", {"temp1_input": 45000, "temp1_label": "Composite"})
    hwmon(root, 2, "k10temp", {"temp1_input": 70000, "temp1_label": "Tctl", "temp2_input": 60000, "temp2_label": "Tdie"})
    hwmon(root, 3, "amdgpu", {"temp1_input": 65000, "temp1_label": "edge", "temp2_input": 80000, "temp2_label": "junction",
                              "power1_input": 150000000}, device=dev)
    hwmon(root, 10, "nct6798", {"fan1_input": 900, "fan2_input": 1250, "fan3_input": 0})
    return root
