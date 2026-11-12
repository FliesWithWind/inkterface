#include "idle_painter.h"

#include <string.h>

#include "usbproto.h"

namespace idle {

PaintResult Painter::paint(const Layout &l, const Data &data, const Env &env)
{
    renderLayout(l, data, env, cv_);
    if (l.mode == RM_GRAY4) {
        uint32_t crc = usbproto::crc32(cv_.gray(), GRAY_BYTES);
        if (haveGray_ && crc == grayCrc_) return PAINT_SKIPPED;  // identical picture: spare the panel
        p_.gray4(cv_.gray());
        grayCrc_ = crc;
        haveGray_ = true;
        haveGlass_ = false;  // gray glass is not a valid mono baseline
        sinceFull_ = 0;
        return PAINT_GRAY;
    }
    cv_.toMono(mono_.data());
    haveGray_ = false;
    const uint8_t *glass = p_.glass();
    if (!haveGlass_ || !glass || sinceFull_ >= l.fullEvery) {
        p_.full(mono_.data());
        haveGlass_ = true;
        sinceFull_ = 0;
        return PAINT_FULL;
    }
    // bounding box of changed bytes, x snapped to bytes
    int y0 = -1, y1 = -1, xb0 = (int)SCREEN_W / 8, xb1 = -1;
    const size_t stride = SCREEN_W / 8;
    for (int y = 0; y < (int)SCREEN_H; y++) {
        const uint8_t *a = mono_.data() + y * stride, *b = glass + y * stride;
        if (!memcmp(a, b, stride)) continue;
        if (y0 < 0) y0 = y;
        y1 = y;
        for (size_t i = 0; i < stride; i++)
            if (a[i] != b[i]) {
                if ((int)i < xb0) xb0 = (int)i;
                if ((int)i > xb1) xb1 = (int)i;
            }
    }
    if (y0 < 0) return PAINT_SKIPPED;
    p_.partial(mono_.data(), (uint16_t)(xb0 * 8), (uint16_t)y0, (uint16_t)((xb1 - xb0 + 1) * 8), (uint16_t)(y1 - y0 + 1));
    sinceFull_++;
    return PAINT_PARTIAL;
}

}  // namespace idle
