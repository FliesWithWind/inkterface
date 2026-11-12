// Minimal host stand-in for <Arduino.h>: just enough for uc8179_fast.h. Records the SPI traffic
// (see uc8179_golden_host.cpp); BUSY always reads "idle"; time advances by itself.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1

inline uint32_t g_us = 0;
inline uint32_t micros() { return g_us += 700; }  // every poll moves time on, so BUSY wait loops end
inline uint32_t millis() { return micros() / 1000; }
inline void delay(uint32_t ms) { g_us += ms * 1000; }
inline void delayMicroseconds(uint32_t us) { g_us += us; }

// the pins the driver is told about by -D EPD_*=...
inline void pinMode(int, int) {}
void digitalWrite(int pin, int level);  // defined by the test: records DC / CS
inline int digitalRead(int) { return HIGH; }  // BUSY is active low: HIGH = idle
