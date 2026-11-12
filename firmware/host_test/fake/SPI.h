#pragma once
#include <stddef.h>
#include <stdint.h>

#define MSBFIRST 1
#define SPI_MODE0 0
struct SPISettings {
    SPISettings(uint32_t, int, int) {}
};
struct SPIClass {
    void begin(int, int, int, int) {}
    void beginTransaction(SPISettings) {}
    void endTransaction() {}
    void transfer(uint8_t b);                     // defined by the test
    void writeBytes(const uint8_t *p, size_t n);  // defined by the test
};
inline SPIClass SPI;
