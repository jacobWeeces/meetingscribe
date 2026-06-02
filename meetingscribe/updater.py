"""Sparkle auto-update integration (active only in the packaged .app)."""
import logging
import os

from Foundation import NSBundle

log = logging.getLogger("meetingscribe")

_updater_controller = None  # module-level ref so PyObjC never deallocates it


def _framework_path():
    priv = NSBundle.mainBundle().privateFrameworksPath()
    return os.path.join(priv or "", "Sparkle.framework")


def init_sparkle():
    """Start Sparkle's updater. No-op (returns None) when the framework is absent."""
    global _updater_controller
    path = _framework_path()
    if not os.path.exists(path):
        log.info("Sparkle.framework not present (%s); skipping auto-update", path)
        return None
    try:
        import objc
        objc.loadBundle("Sparkle", globals(), bundle_path=path)
        _updater_controller = SPUStandardUpdaterController.alloc(  # noqa: F821
            ).initWithStartingUpdater_updaterDelegate_userDriverDelegate_(True, None, None)
        log.info("Sparkle updater started")
        return _updater_controller
    except Exception:
        log.exception("Failed to start Sparkle")
        return None


def check_for_updates(_sender=None):
    if _updater_controller is not None:
        _updater_controller.checkForUpdates_(None)
