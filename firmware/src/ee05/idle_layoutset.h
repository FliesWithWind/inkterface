// The layouts the panel can show: the stored ISTR set (rotation, selection) or the built-in
// fallback when nothing valid is stored. Pure C++11, host tested (idle_mode_host.cpp).
#pragma once

#include <stdint.h>
#include <vector>

#include "idle_layout.h"
#include "idle_store.h"

namespace idle {

class LayoutSet {
  public:
    // Built-in layout (an ILY1 blob that must decode). Used whenever the store is absent or corrupt.
    void setFallback(const uint8_t *blob, size_t len);
    // Install a stored blob. false = it does not validate: the previous set (or the fallback) stays.
    bool setStore(const std::vector<uint8_t> &blob);
    void clearStore();  // back to the fallback only

    bool fromStore() const { return haveStore_; }
    uint8_t count() const { return haveStore_ ? view_.nLayouts : 1; }
    uint8_t index() const { return idx_; }
    bool select(uint8_t idx);  // false: out of range
    // Start a rotation period now (idle entry).
    void restart(uint32_t nowMs) { rotAt_ = nowMs; }
    // true when rotation moved to another layout
    bool tick(uint32_t nowMs);

    const Layout &current() const { return cur_; }
    const StoreConfig *config() const { return haveStore_ ? &view_.cfg : nullptr; }

  private:
    void decodeCurrent();
    std::vector<uint8_t> blob_;
    StoreView view_;
    bool haveStore_ = false;
    uint8_t idx_ = 0;
    uint32_t rotAt_ = 0;
    const uint8_t *fb_ = nullptr;
    size_t fbLen_ = 0;
    Layout cur_;
};

}  // namespace idle
