// Host harness for idle_layout.cpp / idle_store.cpp (decoders) used by decky/tests/idle/test_cpp_decode.py.
//   idle_layout_host --dump-fields        field table, one line per field: src id path kind count
//   idle_layout_host --decode-stream      stdin: repeated (u32 LE len, bytes) layouts; prints one result name per item
//   idle_layout_host --store-stream       same for ISTR stores
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 idle_layout_host.cpp ../src/ee05/idle_{layout,store}.cpp ../src/ee05/usbproto.cpp
#include <stdio.h>
#include <string.h>
#include <vector>

#include "idle_store.h"

using namespace idle;

static bool readItem(std::vector<uint8_t> &b)
{
    uint8_t h[4];
    if (fread(h, 1, 4, stdin) != 4) return false;
    uint32_t n = h[0] | (h[1] << 8) | (h[2] << 16) | ((uint32_t)h[3] << 24);
    b.resize(n);
    return n == 0 || fread(b.data(), 1, n, stdin) == n;
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "--dump-fields")) {
        for (size_t i = 0; i < kFieldCount; i++)
            printf("%u %u %s %c %u\n", kFields[i].src, kFields[i].id, kFields[i].path, kFields[i].kind, kFields[i].count);
        return 0;
    }
    std::vector<uint8_t> b;
    if (!strcmp(argv[1], "--decode-stream")) {
        while (readItem(b)) {
            Layout l;
            puts(decodeErrorName(decodeLayout(b.data(), b.size(), l)));
        }
        return 0;
    }
    if (!strcmp(argv[1], "--store-stream")) {
        while (readItem(b)) {
            StoreView v;
            puts(storeErrorName(parseStore(b.data(), b.size(), v)));
        }
        return 0;
    }
    return 2;
}
