#include "idle_layoutset.h"

namespace idle {

void LayoutSet::setFallback(const uint8_t *blob, size_t len)
{
    fb_ = blob;
    fbLen_ = len;
    if (!haveStore_) decodeCurrent();
}

void LayoutSet::decodeCurrent()
{
    Layout l;
    if (haveStore_) {
        if (decodeLayout(blob_.data() + view_.layoutOff[idx_], view_.layoutLen[idx_], l) == DEC_OK) { cur_ = l; return; }
        haveStore_ = false;  // cannot happen after parseStore, but never leave cur_ dangling
    }
    if (fb_ && decodeLayout(fb_, fbLen_, l) == DEC_OK) cur_ = l;
}

bool LayoutSet::setStore(const std::vector<uint8_t> &blob)
{
    StoreView v;
    if (parseStore(blob.data(), blob.size(), v) != ST_OK) return false;
    blob_ = blob;
    view_ = v;
    haveStore_ = true;
    idx_ = v.active;
    decodeCurrent();
    return true;
}

void LayoutSet::clearStore()
{
    haveStore_ = false;
    blob_.clear();
    idx_ = 0;
    decodeCurrent();
}

bool LayoutSet::select(uint8_t idx)
{
    if (idx >= count()) return false;
    idx_ = idx;
    decodeCurrent();
    return true;
}

bool LayoutSet::tick(uint32_t nowMs)
{
    if (!haveStore_ || !view_.rotate || view_.nLayouts < 2) return false;
    if ((uint32_t)(nowMs - rotAt_) < (uint32_t)view_.rotateS * 1000u) return false;
    rotAt_ = nowMs;
    idx_ = (uint8_t)((idx_ + 1) % view_.nLayouts);
    decodeCurrent();
    return true;
}

}  // namespace idle
