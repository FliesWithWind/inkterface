# UC8179 register and LUT tables: where every value comes from

What `firmware/src/ee05/uc8179_fast.h` (and the waveform builder `fastpartial.h`) writes to the panel controller, with the
datasheet page for each value. Nothing here is copied from another driver: every row was re-derived from the panel
datasheet, and the rows the datasheet does not settle are marked as our own, with the evidence we have.

Source: [`EPD583 648x480 Datasheet.pdf`](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface/-/blob/477563c/docs/EPD583%20648x480%20Datasheet.pdf) (see [references.md](references.md)), Zhongjingyuan module `ZJY648480-0583AAAMFGN`,
product specification v3.0 (13 Jul 2022), 45 pages. "p.N" is the printed "Page N of 45". Pages 14-35 (command table and
command descriptions) are page images, not text; they were read from renders of each page.

Status column:

* **D** the datasheet gives the value, or fully defines the field encoding for the function we need (cited).
* **D=def** the value is the datasheet power-on default (cited).
* **maker** the value is the panel maker's own (Good Display's demo for this panel), used for the 4-gray mode only (section 6). It is
  register values, no waveform table is copied.
* **T** our own choice or tuning: it differs from the datasheet default, or the datasheet is silent. The evidence is stated.
  Every **T** row is the configuration that has run on the panel since the bring-up. None has an A/B measurement against the
  datasheet default, because nothing in this project can read the glass back; each row says what that means.

The wire bytes are pinned by `firmware/host_test/golden/uc8179_sequence.txt`, produced by compiling the real
`uc8179_fast.h` against a fake SPI (`firmware/host_test/uc8179_golden_host.cpp`) and checked by
`decky/tests/test_uc8179_golden.py`. A deliberate waveform change regenerates the golden (command in the test's docstring).

## 1. Common to all three modes (`initCommon`)

| Step | Bytes | Meaning | Datasheet | Status |
|------|-------|---------|-----------|--------|
| RES# low 10 ms, high 10 ms | n/a | hardware reset; the datasheet asks for > 50 us low, > 1 ms high before the first command | p.39 "Reset IC" | D |
| `0x01` PWR, byte 1 | `07` | BD_EN=0, VSR_EN=1, VS_EN=1, VG_EN=1: all three internal DC/DC converters on, border LDO off | p.14 #2, p.17 | D=def |
| `0x01` PWR, byte 2 | `07` | bit 4 VCOM_SLEW=0, VG_LVL[2:0]=111: VGH=+20 V, VGL=-20 V | p.14 #2, p.17-18 (VG_LVL table) | VG_LVL D=def. VCOM_SLEW: **T**, default is `17`; the datasheet says the bit "is fixed at 1" (p.17), so writing 0 should have no effect. Not changed, to keep the shipped drive identical. |
| `0x01` PWR, byte 3 | `3F` | VDH_LVL=111111: **+15.0 V** source high | p.18 (VDH table, last row) | **T**: datasheet default `3A` = +14.0 V. 15.0 V is the top of the allowed range. No A/B against 14 V exists (see below). |
| `0x01` PWR, byte 4 | `3F` | VDL_LVL=111111: **-15.0 V** source low | p.18 (VDL table) | **T**: default `3A` = -14.0 V. Same remark. |
| `0x01` PWR, byte 5 (VDHR) | not written | stays `03` (3.0 V); only used by the red LUT, never selected in KW mode | p.14 #2, p.17-18 | D=def |
| `0x00` PSR | `1F` full, `3F` partial and gray | REG (bit 5): 0 = LUT from OTP (full), 1 = LUT from registers (partial, gray). KW/R=1 (bit 4): black/white only. UD=1, SHL=1 (scan up, shift right), SHD_N=1 (booster on), RST_N=1 | p.14 #1, p.17 | D. Default is `0F` (KW/R=0). The vendor's sample code (p.40) writes only `0F`; we run KW mode, which is what every shipped refresh has used. The `0F` path was not tried. |
| `0x61` TRES | `02 88 01 E0` | HRES=0x288=648, VRES=0x1E0=480. HRES bits 2:0 must be 0 (648 is a multiple of 8) | p.30 (33) | D (default is `03 20 02 58` = 800x600) |
| `0x15` DUSPI | `00` | MM_EN=0, DUSPI_EN=0: single SPI | p.21 (13) | D=def (written explicitly) |
| `0x60` TCON | `22` | S2G=2, G2S=2: 12 x 667 ns non-overlap | p.30 (32) | D=def |

Left at their power-on values and therefore not written: PFS `0x03` (`00`, 1 frame, p.19), BTST `0x06` (`17 17 17 17`, p.19-20),
PLL `0x30` (`06` = FRS 0110 = 50 Hz, p.25), TSE `0x41` (`00`, internal temperature sensor, p.26), PWS `0xE3` (`00`, p.16).
The vendor flow chart (p.39) lists PFS, BTST, PLL and PWS in "Initialize"; skipping them is the same as writing their defaults.
50 Hz is 20.00 ms per frame on paper; the driver's timing model uses **20.35 ms**, which is our own measurement on the
panel (`docs/partial-refresh.md`, "Where the 1.8 s went").

Not used: Deep Sleep `0x07` (p.20). `sleep()` only powers the DC/DC off (`0x02`, p.19).

Commands the driver sends during a refresh (all **D**):

| Command | Use | Datasheet |
|---------|-----|-----------|
| `0x04` PON, wait BUSY | power the DC/DC on before a refresh | p.19 (5) |
| `0x91` PTIN, `0x90` PTL (9 bytes), data, `0x92` PTOUT | window setup. Bytes: HRST[9:8], HRST[7:3], HRED[9:8], HRED[7:3], VRST[9:8], VRST[7:0], VRED[9:8], VRED[7:0], PT_SCAN. The driver snaps x to 8 pixels, so HRST bits 2:0 are 000 and HRED bits 2:0 are 111 as the datasheet requires. PT_SCAN=`00`: gates scan only inside the window (default is 1, scan everywhere). | p.33 (40-42), p.16 #40 |
| `0x10` DTM1 | KW mode: writes the **OLD** plane | p.20 (9) |
| `0x13` DTM2 | KW mode: writes the **NEW** plane | p.21 (12) |
| `0x12` DRF, wait BUSY | refresh | p.21 (11) |
| `0x02` POF, wait BUSY | power off after the refresh | p.19 (3) |

RAM polarity: with DDX=01 (default, p.28) a RAM bit of 1 is white. `plane()` sends the inverted framebuffer (our
framebuffer has 1 = black); `grayPlane()` sends the gray code as is (0 black .. 3 white).
BUSY: the command descriptions call it `BUSY_N`, low while busy (p.21, p.31 FLG). The pin note on p.7 says "when Busy is
high ... should not be interrupted", which contradicts that. The driver waits while the pin reads **low**; this is what the
bench timings in `docs/partial-refresh.md` were measured with (161 ms power on, 1484 ms stock refresh).

## 2. VCOM and data interval, per mode (`0x50` CDI, `0x82` VDCS)

CDI byte 1 (p.27-28): bit 7 BDZ, bits 5:4 BDV[1:0], bit 3 N2OCP, bits 1:0 DDX[1:0]. Byte 2: CDI[3:0] interval. Default `31 07`.

| Mode | `0x50` | Decode | Status |
|------|--------|--------|--------|
| full | `29 07` | BDZ=0, **BDV=10**, **N2OCP=1**, DDX=01, CDI=7 (10 hsync, p.28). In KW mode with DDX[0]=1, BDV=10 makes the border follow LUTKW (p.28 table). | DDX and CDI **D=def**. BDV and N2OCP **T** (default BDV=11, N2OCP=0). N2OCP=1 copies NEW to OLD after the refresh; the driver rewrites both planes before every refresh and never relies on the copy, so it does nothing observable. BDV=10 sends the border along with the K to W waveform of the OTP set; nothing else was tried. |
| partial | `39 07` | BDZ=0, **BDV=11**, **N2OCP=1**, DDX=01, CDI=7. BDV=11 selects LUTBD (`0x25`) for the border, which is all zero here, so the border is not driven. | BDV=11 **D=def**; N2OCP **T** as above; rest D=def |
| gray | `31 07` | BDV=11 (LUTBD, loaded with the gray border LUT below), N2OCP=0, DDX=01, CDI=7 | **D=def**, exactly the datasheet default |

`0x82` VDCS, partial mode only: `00` = VDCS[6:0] 000 0000b = **VCOM_DC -0.10 V**, the datasheet default (p.16 #39, p.32 table: 0.05 V steps,
`26` = -2.00 V). **D=def**. The full and gray
paths do not write it.

Why: in our waveform the WW and KK LUTs and the VCOM LUT are all zero, so a pixel that does not change sits at source GND while VCOM sits
at VCOM_DC for the whole refresh. Its field is `0 V - VCOM_DC`: +2.0 V toward black at -2.0 V, but only +0.1 V at -0.1 V. At -2.0 V every
white pixel the window merely covers got 46 V*frames per refresh (13 % of the 346 V*frames of a full white->black drive), the two drive
directions were unequal (W->K 346, K->W -254 V*frames: black->white 27 % weaker), and a pixel that went there and back netted +92.
At -0.1 V: 2.3 V*frames left alone, W->K 302, K->W -298. `decky/tests/test_panel_model.py` computes this from the bytes the driver
writes (`firmware/host_test/panel_model_host.cpp`) and keeps a control build at `-D INK_PARTIAL_VCOM=0x26`. What the pigment does with
a given dose (threshold, saturation) cannot be read back by any tool here: look at the glass.

## 3. LUT format (what the 42 bytes mean)

`0x20` VCOM, `0x21` WW, `0x22` KW, `0x23` WK, `0x24` KK, `0x25` border (p.14-15, p.22-24). In KW mode (PSR bit 4 = 1) the
controller reads **7 groups of 6 bytes = 42 bytes** per LUT; the 61-byte, 10-group form is for KWR (three colour) mode only
(p.22-23). Each group:

| Byte | Content |
|------|---------|
| 0 | four 2-bit level selects, phase 0 in bits 7:6 ... phase 3 in bits 1:0. Source LUTs (`0x21-0x24`): `00` GND, `01` VDH, `10` VDL, `11` VDHR. VCOM LUT (`0x20`): `00` VCOM_DC, `01` VDH+VCOM_DC, `10` VDL+VCOM_DC, `11` floating. Border LUT (`0x25`, BD_EN=0): `00` VCOM, `01` VDH, `10` VDL, `11` VDHR (p.24). |
| 1-4 | number of frames in phase 0..3 (0-255) |
| 5 | times to repeat |

Which LUT drives a pixel (KW mode, DDX=01, `{NEW,OLD}`, p.28): `00` LUTKK `0x24`, `01` LUTWK `0x23` (white to black),
`10` LUTKW `0x22` (black to white), `11` LUTWW `0x21`. The datasheet describes "times to repeat" only as a count (0 = 0 times);
with a value of 1 the measured phase length equals the frame count (sweep table in `docs/partial-refresh.md`: 20 frames
give 669 ms = (20+3) x 20.35 ms + power cycling), so one pass.

## 4. Fast partial waveform (built by `fastpartial.h buildLuts`, default `INK_PARTIAL_DRIVE=20`)

All of this is **our own waveform**; the datasheet gives the LUT format (section 3) and the voltage ranges, not a partial waveform.

| Reg | 42 bytes (non-zero part) | Reading | Status |
|-----|--------------------------|---------|--------|
| `0x20` VCOM | all `00` | no VCOM phase: VCOM stays at VCOM_DC (`0x82`) | T |
| `0x21` WW | all `00` | white stays white: no drive | T |
| `0x22` KW | `80 14 00 00 00 01` | phase 0: level `10` = **VDL** for **20** frames, once | T |
| `0x23` WK | `40 14 00 00 00 01` | phase 0: level `01` = **VDH** for **20** frames, once | T |
| `0x24` KK | all `00` | black stays black: no drive | T |
| `0x25` border | all `00` | border not driven (BDV=11 selects this LUT) | T |

Evidence and limits:

* Frame count: 20 frames = 407 ms of drive, about 60 % of the 35-frame main phase of the stock partial waveform we
  replaced. `docs/partial-refresh.md` has the sweep (6 to 35 frames, median of 20 runs each) and the result
  (64x32: 671 ms median of 60 over USB; 200x96: 677 ms). The timing is measured; that 20 frames is enough drive for full contrast
  is **reasoning, not an optical measurement** (same doc, "Where `drive` comes from").
* Polarity (K to W with VDL, W to K with VDH): the datasheet names the source voltages but not which way they move the
  pigment, so it cannot settle this. It is the assignment validated on the glass and used in every hardware
  run. Reversing it would drive pixels away from their target.
* Equal drive for both directions, so a pixel that goes there and back nets zero charge: our design choice (`docs/partial-refresh.md`).
* Ghosting policy (full refresh every N partials) is a host decision, see the same doc.

## 5. Full refresh

PSR `1F` (REG=0): the waveform is read from the controller's OTP (p.17 REG, p.20 DTM1 "OTP data"), so there are no LUT bytes in
this repository. Both RAM planes get the image first (`0x10`, `0x13`), then `0x12`. 4.02 s measured (`docs/partial-refresh.md`).

## 6. Static 4-gray waveform (the maker's OTP waveform)

**No gray waveform bytes are in this repository.** The panel is Good Display's GDEY0583T81 (UC8179). Its 4-gray waveform is stored in
the controller's OTP, and Good Display's own demo for this panel (`EPD_Init_4G`) selects it with two register writes. The driver does the
same. Full research, candidates and licences: [gray-waveform.md](gray-waveform.md). (The Zhongjingyuan datasheet, which is no longer in this repo ([references.md](references.md)),
has no gray waveform; the UC8179 datasheet describes the mechanism below.)

Sequence of `Epd::gray4()` (all in the golden file, section `# gray4`):

| Step | Bytes | Meaning | Source | Status |
|------|-------|---------|--------|--------|
| reset | n/a | hard reset (this also clears the forced temperature of the previous gray refresh) | p.39 | D |
| `0x00` PSR | `1F` | REG=0: LUT from OTP; KW mode | p.14 #1, p.17 | D |
| `0x61` TRES, `0x15`, `0x60` | `02 88 01 E0`, `00`, `22` | as in every mode | section 1 | D |
| `0x01` PWR | **not written** | the controller keeps its power-on drive voltages. Every other mode writes VDH/VDL = +/-15 V (our tuning); the OTP gray waveform was made for the maker's defaults and their demo writes no PWR | GD demo | maker |
| `0x50` CDI | `21 07` | BDV=10 (border follows LUTKW), N2OCP=0, DDX=01, interval 7 | GD demo | maker |
| `0xE0` CCSET | `02` | **before PON** (PON senses the temperature once, before the booster is enabled, GDEY0583T81 datasheet p.13), so the 4-gray registers `E0`/`E5` are written before PON; written after it, the forced temperature would not be used. TSFIX=1: use the TS_SET register instead of the temperature sensor | UC8179 datasheet "(46) Cascade Setting" | D |
| `0xE5` TSSET | `5F` | forced temperature value. The OTP holds up to 12 waveform sets selected by temperature range ("TEMPERATURE RANGE", UC8179 datasheet); Good Display keeps their special waveforms behind forced values beyond real temperatures: `5A` fast, `5F` 4-gray, `6E` partial | GD demo comments | maker |
| `0x04` PON, wait BUSY | | | p.19 | D |
| `0x10` DTM1 (OLD), `0x13` DTM2 (NEW) | 2-bit code split into planes | **OLD = low bit, NEW = high bit** of the code (0 black .. 3 white). The old register LUT had it the other way round | GD demo | maker |
| `0x12` DRF, wait BUSY, `0x02` POF | | | p.19, p.21 | D |

Why it is better than what was here: the old waveform (5 LUTs + a border LUT, 6 x 42 bytes) was the GDEW075T7 **7.5-inch** demo's
4-gray LUT, taken through GxEPD2_4G (GPL-3.0), tuned for another glass. This is the maker's waveform for this glass, from the maker's
own memory, with nothing copied.

Limits (not verified, see [gray-waveform.md](gray-waveform.md)): the gray waveform is not temperature compensated (the temperature is
forced), and the panel's own datasheet does not publish the OTP waveform, so the gray levels can only be judged by eye.

## 7. What was not verified

* Panel behaviour for any value other than the shipped one: no A/B for the **T** rows (VDH/VDL 15 V, N2OCP,
  BDV in full mode, KW mode full refresh). Changing any of them means looking at the glass.
* The datasheet command pages are images; values were read by eye from 110 dpi renders and cross-checked against the defaults
  column of the command table (p.14-16) and the field descriptions (p.17-33).
* The OTP full-refresh waveform and the controller's reset values are the controller's; only the datasheet's word is used for them.
