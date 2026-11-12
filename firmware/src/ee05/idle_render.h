// Idle layout interpreter: Layout + Data -> Canvas. Pure C++11 (host tested with PNG goldens:
// firmware/host_test/idle_render_host.cpp). Missing data draws "--" / an empty frame, never crashes.
#pragma once

#include <string>

#include "idle_canvas.h"
#include "idle_data.h"
#include "idle_fonts.h"
#include "idle_layout.h"

namespace idle {

// Draws every widget of `l` into `cv` (clears it to the layout background first).
void renderLayout(const Layout &l, const Data &data, const Env &env, Canvas &cv);

// --- exposed for unit tests ---
// decimals 0..6, or DECIMALS_DEFAULT: up to one decimal with a trailing ".0" dropped
std::string formatNumber(double v, uint8_t decimals);
// strftime subset: %H %M %S %I %p %A %a %d %e %B %b %m %y %Y %j %%   (English names)
std::string formatTime(const char *fmt, const struct tm &tm);
// same format, every conversion replaced by "--" (clock not synced yet)
std::string formatTimePlaceholder(const char *fmt);
// text template -> string (tokens resolved through `data`, missing = "--")
std::string expandTemplate(const Layout &l, const char *tpl, const Data &data);
// WMO weather code -> icon (IC_SUN, IC_CLOUD, IC_RAIN, IC_SNOW, IC_STORM, IC_FOG) or 255 for "partly cloudy"
uint8_t weatherIconFor(int code);
constexpr uint8_t IC_PARTLY = 255;
// font used for a number's unit
uint8_t unitFontFor(uint8_t fontId);

}  // namespace idle
