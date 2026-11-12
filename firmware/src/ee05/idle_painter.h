// Decides HOW an idle frame reaches the glass: full vs windowed partial vs skip (mono), or a gray4
// full refresh. Pure C++11 over a Presenter, host tested with a fake (idle_painter_host.cpp).
#pragma once

#include <stdint.h>
#include <vector>

#include "idle_canvas.h"
#include "idle_data.h"
#include "idle_layout.h"
#include "idle_render.h"

namespace idle {

struct Presenter {
    virtual ~Presenter() {}
    // What the glass shows as a 1bpp image (1 = black), or nullptr when unknown (after gray, after reset).
    virtual const uint8_t *glass() = 0;
    virtual void full(const uint8_t *fb) = 0;                                          // OTP waveform
    virtual void partial(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h) = 0;
    virtual void gray4(const uint8_t *gray2) = 0;                                      // 4-level, full screen
};

enum PaintResult : uint8_t { PAINT_SKIPPED = 0, PAINT_PARTIAL, PAINT_FULL, PAINT_GRAY };

class Painter {
  public:
    explicit Painter(Presenter &p) : p_(p), mono_(MONO_BYTES) {}

    // Forget what we know about the glass (host drew on it, layout changed): next paint is a full one.
    void invalidate() { haveGlass_ = false; grayCrc_ = 0; haveGray_ = false; sinceFull_ = 0; }

    // Render `l` with `data` and push the difference. Always fully re-renders; cheap to call.
    PaintResult paint(const Layout &l, const Data &data, const Env &env);

    uint32_t partialsSinceFull() const { return sinceFull_; }
    Canvas &canvas() { return cv_; }

  private:
    Presenter &p_;
    Canvas cv_;
    std::vector<uint8_t> mono_;
    bool haveGlass_ = false;  // glass known to equal what we last painted in mono
    bool haveGray_ = false;
    uint32_t grayCrc_ = 0;
    uint32_t sinceFull_ = 0;
};

}  // namespace idle
