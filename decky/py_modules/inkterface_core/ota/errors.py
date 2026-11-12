"""Exception types for the OTA flow. Every failure the plugin UI can show is one of these."""
from __future__ import annotations


class OtaError(Exception):
    """Base class. str(e) is safe to show the user."""


# -- release discovery / trust ------------------------------------------------
class NetworkError(OtaError): ...
class RateLimited(OtaError):
    def __init__(self, msg: str, retry_at: float | None = None, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_at = retry_at        # unix time the limit resets (X-RateLimit-Reset), if GitHub said
        self.retry_after = retry_after  # seconds to wait (Retry-After), if GitHub said; wins over retry_at
class BadResponse(OtaError): ...       # malformed JSON / unexpected shape / missing asset
class TooLarge(OtaError): ...          # download bigger than the cap
class NoTrustKey(OtaError): ...        # no production public key bundled: refuse everything
class BadSignature(OtaError): ...      # unsigned, tampered or signed with another key
class BadManifest(OtaError): ...       # signed but fails schema validation
class ProtocolMismatch(OtaError): ...  # image speaks another USB protocol than this plugin
class PluginTooOld(OtaError): ...      # manifest.min_plugin_version > this plugin
class Downgrade(OtaError): ...         # release is older than what the panel runs
class HashMismatch(OtaError): ...      # downloaded bytes != manifest sha256 / size


# -- USB streaming ------------------------------------------------------------
class OtaUnsupported(OtaError): ...    # panel HELLO has no OTA capability
class OtaRejected(OtaError):
    """Panel answered NAK."""

    def __init__(self, reason: int, name: str, offset: int | None = None):
        super().__init__(f"panel rejected OTA: {name}" + (f" at offset {offset}" if offset is not None else ""))
        self.reason, self.name, self.offset = reason, name, offset
class Cancelled(OtaError): ...
class Retryable(OtaError): ...         # base of failures a reconnect + resume can fix
class LinkLost(Retryable): ...         # no answer in time / serial error
class OtaResync(Retryable): ...        # panel's offset differs from ours (bytes lost on the wire)
class BootMismatch(OtaError): ...      # after reboot the panel did not report the new version
