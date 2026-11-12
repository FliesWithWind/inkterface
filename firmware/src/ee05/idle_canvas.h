// 2-bit-per-pixel software canvas for the idle renderer (648x480, level 0 black .. 3 white,
// row-major, 4 px per byte MSB first: the same packing the gray4 driver streams).
// Pure C++11. Drawing is clipped to the screen and to the current clip rectangle.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <vector>

#include "idle_layout.h"

namespace idle {

constexpr size_t GRAY_BYTES = (size_t)SCREEN_W * SCREEN_H / 4;  // 77,760
constexpr size_t MONO_BYTES = (size_t)SCREEN_W * SCREEN_H / 8;  // 38,880

class Canvas {
  public:
    Canvas() : px_(GRAY_BYTES, 0xFF) { resetClip(); }

    void clear(uint8_t level) { memset(px_.data(), (level & 3) * 0x55, px_.size()); }
    void setClip(int x, int y, int w, int h);
    void resetClip() { cx0_ = cy0_ = 0; cx1_ = SCREEN_W; cy1_ = SCREEN_H; }

    uint8_t get(int x, int y) const
    {
        if ((unsigned)x >= SCREEN_W || (unsigned)y >= SCREEN_H) return 3;
        size_t i = (size_t)y * SCREEN_W + x;
        return (px_[i >> 2] >> ((3 - (i & 3)) * 2)) & 3;
    }
    void put(int x, int y, uint8_t level)
    {
        if (x < cx0_ || x >= cx1_ || y < cy0_ || y >= cy1_) return;
        size_t i = (size_t)y * SCREEN_W + x;
        unsigned sh = (3 - (i & 3)) * 2;
        px_[i >> 2] = (uint8_t)((px_[i >> 2] & ~(3u << sh)) | ((level & 3u) << sh));
    }

    void fillRect(int x, int y, int w, int h, uint8_t level);
    void frame(int x, int y, int w, int h, int thickness, uint8_t level);  // inside the box
    void fillCircle(int cx, int cy, int r, uint8_t level);
    void ring(int cx, int cy, int r, int thickness, uint8_t level);
    void line(int x0, int y0, int x1, int y1, int thickness, uint8_t level);
    // 1-bpp mask (1 = ink, rows of `stride` bytes, MSB first) painted in `level`
    void blit1(const uint8_t *bits, size_t stride, int x, int y, int w, int h, uint8_t level);
    // 2-bpp bitmap (value = level, 4 px per byte MSB first, rows of `stride` bytes)
    void blit2(const uint8_t *bits, size_t stride, int x, int y, int w, int h);

    // Pack to the e-paper's 1bpp framebuffer (1 = black). Levels 1 and 2 dither (Bayer 4x4).
    void toMono(uint8_t *out) const;
    const uint8_t *gray() const { return px_.data(); }
    uint8_t *grayMutable() { return px_.data(); }

  private:
    std::vector<uint8_t> px_;
    int cx0_, cy0_, cx1_, cy1_;
};

}  // namespace idle
