// POSIX-TZ wall clock for the firmware and the host harness: time() + setenv("TZ") + localtime_r.
#pragma once

#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "idle_data.h"

namespace idle {

struct PosixEnv : Env {
    const char *globalTz = "UTC0";
    int64_t fixedEpoch = 0;  // tests: non-zero freezes the clock
    int64_t epoch() const override { return fixedEpoch ? fixedEpoch : (int64_t)time(nullptr); }
    void localTime(int64_t e, const char *tz, struct tm &out) const override
    {
        const char *use = (tz && *tz) ? tz : globalTz;
        setenv("TZ", use, 1);
        tzset();
        time_t t = (time_t)e;
        localtime_r(&t, &out);
    }
};

}  // namespace idle
