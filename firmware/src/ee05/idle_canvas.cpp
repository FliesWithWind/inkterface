#include "idle_canvas.h"

#include <stdlib.h>
#include <string.h>

namespace idle {

void Canvas::setClip(int x, int y, int w, int h)
{
    cx0_ = x < 0 ? 0 : x;
    cy0_ = y < 0 ? 0 : y;
    cx1_ = x + w > (int)SCREEN_W ? SCREEN_W : x + w;
    cy1_ = y + h > (int)SCREEN_H ? SCREEN_H : y + h;
    if (cx1_ < cx0_) cx1_ = cx0_;
    if (cy1_ < cy0_) cy1_ = cy0_;
}

void Canvas::fillRect(int x, int y, int w, int h, uint8_t level)
{
    int x0 = x < cx0_ ? cx0_ : x, y0 = y < cy0_ ? cy0_ : y;
    int x1 = x + w > cx1_ ? cx1_ : x + w, y1 = y + h > cy1_ ? cy1_ : y + h;
    for (int yy = y0; yy < y1; yy++)
        for (int xx = x0; xx < x1; xx++) put(xx, yy, level);
}

void Canvas::frame(int x, int y, int w, int h, int t, uint8_t level)
{
    if (t <= 0 || w <= 0 || h <= 0) return;
    if (2 * t >= w || 2 * t >= h) return fillRect(x, y, w, h, level);
    fillRect(x, y, w, t, level);
    fillRect(x, y + h - t, w, t, level);
    fillRect(x, y + t, t, h - 2 * t, level);
    fillRect(x + w - t, y + t, t, h - 2 * t, level);
}

void Canvas::fillCircle(int cx, int cy, int r, uint8_t level)
{
    if (r < 0) return;
    for (int dy = -r; dy <= r; dy++) {
        int dx = 0;
        while ((dx + 1) * (dx + 1) + dy * dy <= r * r + r) dx++;  // +r: rounder small discs
        fillRect(cx - dx, cy + dy, 2 * dx + 1, 1, level);
    }
}

void Canvas::ring(int cx, int cy, int r, int t, uint8_t level)
{
    for (int dy = -r; dy <= r; dy++)
        for (int dx = -r; dx <= r; dx++) {
            int d2 = dx * dx + dy * dy;
            int ri = r - t;
            if (d2 <= r * r + r && (ri < 0 || d2 > ri * ri + ri)) put(cx + dx, cy + dy, level);
        }
}

void Canvas::line(int x0, int y0, int x1, int y1, int t, uint8_t level)
{
    int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    for (;;) {
        if (t <= 1) put(x0, y0, level);
        else fillRect(x0 - t / 2, y0 - t / 2, t, t, level);
        if (x0 == x1 && y0 == y1) break;
        int e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

void Canvas::blit1(const uint8_t *bits, size_t stride, int x, int y, int w, int h, uint8_t level)
{
    for (int r = 0; r < h; r++) {
        if (y + r < cy0_ || y + r >= cy1_) continue;
        const uint8_t *row = bits + (size_t)r * stride;
        for (int c = 0; c < w; c++)
            if (row[c >> 3] & (0x80 >> (c & 7))) put(x + c, y + r, level);
    }
}

void Canvas::blit2(const uint8_t *bits, size_t stride, int x, int y, int w, int h)
{
    for (int r = 0; r < h; r++) {
        const uint8_t *row = bits + (size_t)r * stride;
        for (int c = 0; c < w; c++) put(x + c, y + r, (row[c >> 2] >> ((3 - (c & 3)) * 2)) & 3);
    }
}

namespace {
const uint8_t BAYER4[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};
}

void Canvas::toMono(uint8_t *out) const
{
    for (int y = 0; y < (int)SCREEN_H; y++)
        for (int xb = 0; xb < (int)SCREEN_W / 8; xb++) {
            uint8_t b = 0;
            for (int k = 0; k < 8; k++) {
                int x = xb * 8 + k;
                uint8_t lv = get(x, y);
                bool black = lv == 0 ? true : lv == 3 ? false : (uint8_t)(lv * 5) <= BAYER4[y & 3][x & 3];
                b = (uint8_t)((b << 1) | (black ? 1 : 0));
            }
            out[(size_t)y * (SCREEN_W / 8) + xb] = b;
        }
}

}  // namespace idle
