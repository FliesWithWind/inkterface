# 4-gray waveform: provenance

## Which waveform

The 4-gray mode uses **the panel maker's own waveform, stored in the UC8179 controller's OTP**. It is selected with two
documented controller registers, as in Good Display's demo for this panel (GDEY0583T81): `0xE0` (CCSET) = `02`, which makes the
chip take the temperature from register `0xE5` instead of its sensor, and `0xE5` (TS_SET) = `5F`, the forced value Good Display uses
to pick its 4-gray set. The OTP holds up to 12 waveform sets chosen by temperature range (UC8179 datasheet); Good Display's demo
comments name `0x5F` as the 4-gray one.

The panel is the Good Display GDEY0583T81 (5.83", 648x480, UC8179), sold by Seeed as SKU 104990858. The panel datasheets publish
no waveform bytes; the optical figures are only guaranteed under the maker's controller and waveform.

## Where the values come from

* The register values (`PSR 1F`, `CDI 21 07`, `E0 02`, `E5 5F`) and the plane packing (OLD plane = low bit, NEW plane = high bit of
  the 2-bit pixel; `11` white, `10` light, `01` dark, `00` black) are facts about the hardware, taken from Good Display's published
  GDEY0583T81 demo and the UC8179 datasheet. They are confirmed independently by MIT-licensed drivers from Seeed (Seeed_GFX2) and
  Waveshare (`epd5in83_V2.py`).
* **No third-party waveform bytes and no third-party code are in this repository.** The driver is `Epd::gray4()` in
  `firmware/src/ee05/uc8179_fast.h`. An earlier version carried six look-up tables (`LUT_GRAY`, `LUT_GRAY_BD`) that came, through
  GxEPD2_4G (GPL-3.0 parent, no licence file of its own), from Good Display's demo of a different panel, the 7.5-inch 800x480
  GDEW075T7. They were the wrong glass and a doubtful licence, and were removed.

## Register order

`E0`/`E5` are written **before `PON`**. The datasheet says `PON` senses the temperature once, before the booster is enabled, so the
forced value has to be in place already. Good Display's demo sends them after `PON`; on the panel that order left vertical
stripes in the darkest gray, and writing them before `PON` made them barely visible. A hard reset starts every other mode, so the
forced temperature cannot leak into a following mono refresh; a test asserts the mono paths never write `E0`/`E5`.

The sequence is pinned by `decky/tests/test_uc8179_golden.py`.

## Candidates considered

| Source | Licence | Outcome |
|--------|---------|---------|
| Good Display GDEY0583T81 demo (`gooddisplayshare/ESP32epd`, `ESP32epdx`) | none granted (empty `LICENSE`) | Source of the idea. Only the register values are used (no LUT bytes in it); no code copied |
| Seeed_GFX2 `Driver_UC8179::initGrayOTP` | MIT | Same OTP route and packing; its external LUTs are for 800x480 only and are not used |
| Waveshare `epd5in83_V2.py` `init_4GRAY` | MIT | Same OTP route and packing; its `BTST` write (`27 27 18 17`) was tried and striped the whole display |
| Seeed_GFX (older) `UC8179_Defines.h` | BSD-style, not read in full | Not used: copies of the 7.5" set |
| GxEPD2_4G `GxEPD2_750_T7` LUTs | no licence file (parent GPL-3.0) | Rejected: wrong panel, doubtful licence |

## Licence status

Two register values and the use of a documented controller feature are not copyrightable expression, and the driver code is
original. The repository's licence analysis is in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Known limits

* Not temperature compensated: the forced value fixes the waveform set at any room temperature, as in the maker's own demo.
* The packing (which of `01`/`10` is the dark gray) comes from the maker's demo and Seeed's driver and has not been checked by
  instrument. If the two middle grays come out swapped, swap the `high` argument of the two `grayPlane` calls in `gray4()`.
* The gray refresh takes about 2 s (2455 ms measured on the panel).
