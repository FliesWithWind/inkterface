// Display-ownership state machine: who draws the glass, the host or the panel's idle layout.
// Pure C++11, no clock of its own (callers pass millis()), unit tested on the host
// (firmware/host_test/idle_mode_host.cpp). See docs/idle-mode.md "State machine".
//
//   WAIT --MODE HOST--> HOST --silence >= timeout--> IDLE (auto)
//   WAIT --silence >= timeout (from boot / last frame)--> IDLE (auto)
//   IDLE(auto) --any valid frame--> back to what it was (HOST, or WAIT if no MODE HOST was seen)
//   any --MODE PANEL--> IDLE (pinned): only MODE HOST leaves it, frames and heartbeats do not
//   WAIT/HOST --USB bus quiet (onBusSuspend)--> IDLE (auto)
//   any --MODE HOST--> HOST
#pragma once

#include <stdint.h>

namespace idlemode {

constexpr uint16_t DEFAULT_TIMEOUT_S = 60;
constexpr uint16_t MIN_TIMEOUT_S = 15;    // host heartbeats every 5 s: tolerate two late ticks
constexpr uint16_t MAX_TIMEOUT_S = 3600;

enum State : uint8_t {
    WAIT = 0,  // no host owns the display yet; the glass keeps whatever it shows
    HOST = 1,  // host renders (RECT/REFRESH accepted)
    IDLE = 2,  // panel renders its idle layout
};

class Controller {
  public:
    explicit Controller(uint16_t timeoutS = DEFAULT_TIMEOUT_S) { setTimeoutS(timeoutS); }

    // Boot reference: the silence clock starts here.
    void start(uint32_t nowMs) { last_ = nowMs; }

    // true if accepted (0 = keep, otherwise MIN..MAX)
    bool setTimeoutS(uint16_t s)
    {
        if (s == 0) return true;
        if (s < MIN_TIMEOUT_S || s > MAX_TIMEOUT_S) return false;
        timeoutS_ = s;
        return true;
    }
    uint16_t timeoutS() const { return timeoutS_; }

    // Any CRC-valid host frame (HELLO, HEARTBEAT, RECT, ...). Call before handling it.
    void onFrame(uint32_t nowMs)
    {
        last_ = nowMs;
        if (state_ == IDLE && !pinned_) setState(back_);
    }

    // MODE frame: 0 HOST, 1 PANEL (show idle now and stay there)
    void onMode(bool panel, uint32_t nowMs)
    {
        last_ = nowMs;
        if (panel) {
            pinned_ = true;
            if (state_ != IDLE) back_ = state_;
            setState(IDLE);
        } else {
            pinned_ = false;
            setState(HOST);
        }
    }

    // The USB bus went quiet (host suspended or gone, see usb_bus_watch.h): idle now, like the silence timeout firing early.
    // Any valid frame brings the host back.
    void onBusSuspend()
    {
        if (state_ == IDLE) return;
        back_ = state_;
        pinned_ = false;
        setState(IDLE);
    }

    void tick(uint32_t nowMs)
    {
        if (state_ == IDLE) return;
        if ((uint32_t)(nowMs - last_) >= (uint32_t)timeoutS_ * 1000u) {
            back_ = state_;
            pinned_ = false;
            setState(IDLE);
        }
    }

    State state() const { return state_; }
    bool pinned() const { return pinned_ && state_ == IDLE; }
    bool hostOwns() const { return state_ == HOST; }
    bool idle() const { return state_ == IDLE; }

    // Edge flags for the firmware loop (each reads and clears).
    bool takeEnteredIdle() { bool r = entered_; entered_ = false; return r; }
    bool takeLeftIdle() { bool r = left_; left_ = false; return r; }

  private:
    void setState(State s)
    {
        if (s == state_) return;
        if (s == IDLE) { entered_ = true; left_ = false; }
        else if (state_ == IDLE) { left_ = true; entered_ = false; }
        state_ = s;
    }
    State state_ = WAIT, back_ = WAIT;
    bool pinned_ = false, entered_ = false, left_ = false;
    uint16_t timeoutS_ = DEFAULT_TIMEOUT_S;
    uint32_t last_ = 0;
};

}  // namespace idlemode
