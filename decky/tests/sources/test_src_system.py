import math
import os

import pytest
import sourcefs as F

from inkterface_core.sources import system as sysmod
from inkterface_core.sources.system import METRICS, SystemSource


def poll_twice(root, stat2="cpu  150 0 150 900 0 0 0 0 0 0\n"):
    s = SystemSource(str(root))
    s.poll()
    F.w(root, "proc/stat", stat2)
    return s, s.poll()


def test_amd_apu_full(tmp_path):
    F.amd_apu(tmp_path)
    s, r = poll_twice(tmp_path)
    assert set(r) == set(METRICS)
    assert r["system.cpu.load"] == pytest.approx(50.0)  # busy +100 of total +200
    assert r["system.cpu.temp"] == 52.5
    assert r["system.cpu.clock"] == 3500.0
    assert r["system.gpu.load"] == 37
    assert r["system.gpu.temp"] == 45.0
    assert r["system.gpu.clock"] == 1600.0
    assert r["system.gpu.mem_clock"] == 1600.0
    assert r["system.mem.used_pct"] == 75.0
    assert r["system.fan.rpm"] == 3200
    assert r["system.power.package_w"] == 12.5
    assert r["system.disk.temp"] is None  # no nvme on this box


def test_mem_clock_reads_mclk_not_sclk(tmp_path):
    """The old C++ collector registered GPU MCLK with getGPUSCLK. Distinct values make a mix-up visible."""
    F.amd_apu(tmp_path)
    F.w(tmp_path, "sys/devices/pci0000:00/0000:c1:00.0/pp_dpm_sclk", "0: 800Mhz *\n")
    F.w(tmp_path, "sys/devices/pci0000:00/0000:c1:00.0/pp_dpm_mclk", "0: 2400Mhz *\n")
    r = SystemSource(str(tmp_path)).poll()
    assert (r["system.gpu.clock"], r["system.gpu.mem_clock"]) == (800.0, 2400.0)


def test_gpu_clock_falls_back_to_hwmon_labels(tmp_path):
    F.amd_apu(tmp_path)
    for f in ("pp_dpm_sclk", "pp_dpm_mclk"):
        os.remove(tmp_path / "sys/devices/pci0000:00/0000:c1:00.0" / f)
    F.w(tmp_path, "sys/class/hwmon/hwmon0/freq2_input", "2400000000\n")
    F.w(tmp_path, "sys/class/hwmon/hwmon0/freq2_label", "mclk\n")
    r = SystemSource(str(tmp_path)).poll()
    assert r["system.gpu.clock"] == 1600.0 and r["system.gpu.mem_clock"] == 2400.0


def test_dpm_without_active_marker_is_none(tmp_path):
    F.amd_apu(tmp_path)
    F.w(tmp_path, "sys/devices/pci0000:00/0000:c1:00.0/pp_dpm_mclk", "0: 400Mhz\n1: 1600Mhz\n")
    assert SystemSource(str(tmp_path)).poll()["system.gpu.mem_clock"] is None  # no mclk label fallback present


def test_desktop_nvme_k10temp_and_card_selection(tmp_path):
    F.desktop(tmp_path)
    r = SystemSource(str(tmp_path)).poll()
    assert r["system.cpu.temp"] == 60.0  # Tdie preferred over Tctl
    assert r["system.disk.temp"] == 45.0  # hottest Composite; Sensor 1 (99 C) ignored
    assert r["system.gpu.load"] == 88  # card0 (NVIDIA, no gpu_busy_percent) skipped
    assert r["system.gpu.temp"] == 65.0  # edge preferred over junction
    assert r["system.power.package_w"] == 150.0  # power1_input fallback
    assert r["system.fan.rpm"] == 1250  # highest fan on a non-preferred hwmon
    assert r["system.cpu.clock"] == 4200.0


def test_hwmon_paired_with_card_by_pci_device(tmp_path):
    """Two amdgpu hwmons: the one sharing the card's PCI device wins even when listed later."""
    F.proc(tmp_path)
    igpu = F.gpu(tmp_path, 0, "pci0/igpu", busy=5)
    dgpu = F.gpu(tmp_path, 1, "pci0/dgpu", busy=95)
    F.hwmon(tmp_path, 0, "amdgpu", {"temp1_input": 90000}, device=dgpu)
    F.hwmon(tmp_path, 1, "amdgpu", {"temp1_input": 41000}, device=igpu)
    r = SystemSource(str(tmp_path)).poll()
    assert r["system.gpu.load"] == 5 and r["system.gpu.temp"] == 41.0


def test_hwmon_without_matching_card_uses_first_amdgpu(tmp_path):
    F.proc(tmp_path)
    F.hwmon(tmp_path, 4, "amdgpu", {"temp1_input": 50000})
    r = SystemSource(str(tmp_path)).poll()
    assert r["system.gpu.temp"] == 50.0 and r["system.gpu.load"] is None


def test_intel_coretemp_and_label_fallback(tmp_path):
    F.proc(tmp_path)
    F.hwmon(tmp_path, 0, "coretemp", {"temp1_input": 55000, "temp1_label": "Package id 0", "temp2_input": 50000, "temp2_label": "Core 0"})
    assert SystemSource(str(tmp_path)).poll()["system.cpu.temp"] == 55.0
    F.hwmon(tmp_path, 0, "coretemp", {"temp1_input": 58000, "temp1_label": "Core 7"})
    os.remove(tmp_path / "sys/class/hwmon/hwmon0/temp2_input")
    os.remove(tmp_path / "sys/class/hwmon/hwmon0/temp1_label")
    os.remove(tmp_path / "sys/class/hwmon/hwmon0/temp2_label")
    assert SystemSource(str(tmp_path)).poll()["system.cpu.temp"] == 58.0  # no known label: lowest-numbered input


def test_no_sensors_everything_none_and_no_exception(tmp_path):
    (tmp_path / "proc").mkdir()
    r = SystemSource(str(tmp_path)).poll()
    assert set(r) == set(METRICS) and all(v is None for v in r.values())


def test_missing_root_entirely(tmp_path):
    r = SystemSource(str(tmp_path / "nope")).poll()
    assert all(v is None for v in r.values())


def test_garbage_values_are_none(tmp_path):
    F.amd_apu(tmp_path)
    F.w(tmp_path, "sys/class/hwmon/hwmon1/temp1_input", "N/A\n")
    F.w(tmp_path, "sys/class/hwmon/hwmon2/fan1_input", "\n")
    F.w(tmp_path, "sys/class/hwmon/hwmon0/power1_average", "nan\n")
    F.w(tmp_path, "sys/devices/pci0000:00/0000:c1:00.0/gpu_busy_percent", "x\n")
    r = SystemSource(str(tmp_path)).poll()
    assert r["system.cpu.temp"] is None and r["system.fan.rpm"] is None
    assert r["system.power.package_w"] is None and r["system.gpu.load"] is None


def test_unreadable_sensor_is_none(tmp_path):
    """sysfs read errors (EIO, ENODEV from a runtime-suspended GPU) are OSError: a directory stands in."""
    F.amd_apu(tmp_path)
    os.remove(tmp_path / "sys/devices/pci0000:00/0000:c1:00.0/gpu_busy_percent")
    (tmp_path / "sys/devices/pci0000:00/0000:c1:00.0/gpu_busy_percent").mkdir()
    assert SystemSource(str(tmp_path)).poll()["system.gpu.load"] is None


def test_preferred_fan_hwmon_wins_over_louder_one(tmp_path):
    F.amd_apu(tmp_path)
    F.hwmon(tmp_path, 5, "nct6798", {"fan1_input": 9000})
    assert SystemSource(str(tmp_path)).poll()["system.fan.rpm"] == 3200


def test_fan_zero_rpm_is_a_value_not_missing(tmp_path):
    F.proc(tmp_path)
    F.hwmon(tmp_path, 0, "asusec", {"fan1_input": 0})
    assert SystemSource(str(tmp_path)).poll()["system.fan.rpm"] == 0


def test_hwmon_dir_without_name_is_skipped(tmp_path):
    F.proc(tmp_path)
    (tmp_path / "sys/class/hwmon/hwmon0").mkdir(parents=True)
    F.hwmon(tmp_path, 1, "k10temp", {"temp1_input": 40000})
    assert SystemSource(str(tmp_path)).poll()["system.cpu.temp"] == 40.0


def test_cpu_clock_from_cpufreq_when_no_cpuinfo_mhz(tmp_path):
    F.proc(tmp_path)
    F.w(tmp_path, "proc/cpuinfo", "processor: 0\nBogoMIPS: 50\n")
    F.w(tmp_path, "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "1000000\n")
    F.w(tmp_path, "sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", "2000000\n")
    assert SystemSource(str(tmp_path)).poll()["system.cpu.clock"] == 1500.0
    os.remove(tmp_path / "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    os.remove(tmp_path / "sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq")
    assert SystemSource(str(tmp_path)).poll()["system.cpu.clock"] is None


def test_mem_without_memavailable_and_bad_totals(tmp_path):
    F.proc(tmp_path)
    F.w(tmp_path, "proc/meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 100 kB\nCached: 300 kB\n")
    assert SystemSource(str(tmp_path)).poll()["system.mem.used_pct"] == 50.0
    F.w(tmp_path, "proc/meminfo", "MemTotal: 0 kB\n")
    assert SystemSource(str(tmp_path)).poll()["system.mem.used_pct"] is None
    F.w(tmp_path, "proc/meminfo", "MemTotal: 100 kB\nMemAvailable: 500 kB\n")
    assert SystemSource(str(tmp_path)).poll()["system.mem.used_pct"] == 0.0  # clamped


# -- CPU load delta math ----------------------------------------------------------------------

def load_seq(tmp_path, *stats):
    s = SystemSource(str(tmp_path))
    out = []
    for st in stats:
        F.w(tmp_path, "proc/stat", st)
        out.append(s.poll()["system.cpu.load"])
    return out


def test_cpu_first_sample_is_none_then_delta(tmp_path):
    a = load_seq(tmp_path, "cpu  100 0 100 800 0 0 0 0\n", "cpu  150 0 150 900 0 0 0 0\n", "cpu  150 0 150 1100 0 0 0 0\n")
    assert a[0] is None and a[1] == pytest.approx(50.0) and a[2] == pytest.approx(0.0)


def test_cpu_iowait_counts_as_idle_steal_as_busy(tmp_path):
    a = load_seq(tmp_path, "cpu  0 0 0 100 0 0 0 0\n", "cpu  0 0 0 100 100 0 0 0\n", "cpu  0 0 0 100 100 0 0 100\n")
    assert a[1] == 0.0 and a[2] == 100.0


def test_cpu_counter_wrap_resyncs_and_keeps_last_value(tmp_path):
    a = load_seq(tmp_path,
                 "cpu  100 0 100 800 0 0 0 0\n",
                 "cpu  150 0 150 900 0 0 0 0\n",  # 50 %
                 "cpu  10 0 10 90 0 0 0 0\n",  # counters went backwards (wrap / reboot)
                 "cpu  60 0 60 190 0 0 0 0\n")  # +50+50 busy of +200 total -> 50 % again, from the new baseline
    assert a[1] == pytest.approx(50.0)
    assert a[2] == pytest.approx(50.0)  # last value kept, no negative or garbage sample
    assert a[3] == pytest.approx(50.0)


def test_cpu_wrap_before_any_value_is_none(tmp_path):
    a = load_seq(tmp_path, "cpu  500 0 500 5000 0 0 0 0\n", "cpu  1 0 1 1 0 0 0 0\n")
    assert a == [None, None]


def test_cpu_zero_delta_keeps_last(tmp_path):
    a = load_seq(tmp_path, "cpu  100 0 100 800 0 0 0 0\n", "cpu  200 0 100 800 0 0 0 0\n", "cpu  200 0 100 800 0 0 0 0\n")
    assert a[1] == 100.0 and a[2] == 100.0


@pytest.mark.parametrize("bad", ["", "cpu0 1 2 3 4\n", "cpu 1 2 3\n", "cpu a b c d e\n", "intr 1 2 3 4 5\n"])
def test_cpu_malformed_stat_never_raises(tmp_path, bad):
    a = load_seq(tmp_path, "cpu  100 0 100 800 0 0 0 0\n", bad)
    assert a == [None, None]


def test_cpu_old_kernel_four_fields(tmp_path):
    a = load_seq(tmp_path, "cpu  100 0 100 800\n", "cpu  150 0 150 900\n")
    assert a[1] == pytest.approx(50.0)


def test_cpu_full_load(tmp_path):
    s = SystemSource(str(tmp_path))
    s._prev = (0.0, 0.0)
    F.w(tmp_path, "proc/stat", "cpu  100 0 0 0 0 0 0 0\n")
    assert s.poll()["system.cpu.load"] == 100.0


def test_natural_sort_and_numeric_results_are_finite(tmp_path):
    F.desktop(tmp_path)
    r = SystemSource(str(tmp_path)).poll()
    assert all(v is None or math.isfinite(v) for v in r.values())
    assert sysmod._natural("hwmon10") > sysmod._natural("hwmon9")
