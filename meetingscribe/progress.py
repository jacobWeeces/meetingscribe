import threading

import AppKit
import objc
from PyObjCTools import AppHelper


def _run_on_main(fn):
    """Run fn on the main thread now if we're already on it, else schedule it.

    AppKit objects (NSProgressIndicator, NSWindow, ...) may only be touched on the
    main thread; _update_progress calls these setters from the background
    processing thread.
    """
    if threading.current_thread() is threading.main_thread():
        fn()
    else:
        AppHelper.callAfter(fn)


class ProgressWindow:
    def __init__(self):
        self._window = None
        self._progress_bar = None
        self._label = None
        self._stage_label = None

    def show(self):
        if self._window is not None:
            return

        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
        )
        rect = AppKit.NSMakeRect(0, 0, 400, 130)
        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False,
        )
        self._window.setTitle_("MeetingScribe")
        self._window.setLevel_(AppKit.NSFloatingWindowLevel)
        self._window.setReleasedWhenClosed_(False)
        self._window.center()

        content = self._window.contentView()

        self._stage_label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(20, 85, 360, 24),
        )
        self._stage_label.setStringValue_("Processing...")
        self._stage_label.setBezeled_(False)
        self._stage_label.setDrawsBackground_(False)
        self._stage_label.setEditable_(False)
        self._stage_label.setSelectable_(False)
        self._stage_label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(14))
        content.addSubview_(self._stage_label)

        self._progress_bar = AppKit.NSProgressIndicator.alloc().initWithFrame_(
            AppKit.NSMakeRect(20, 55, 360, 20),
        )
        self._progress_bar.setStyle_(AppKit.NSProgressIndicatorStyleBar)
        self._progress_bar.setMinValue_(0)
        self._progress_bar.setMaxValue_(100)
        self._progress_bar.setDoubleValue_(0)
        self._progress_bar.setIndeterminate_(False)
        content.addSubview_(self._progress_bar)

        self._label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(20, 20, 360, 24),
        )
        self._label.setStringValue_("")
        self._label.setBezeled_(False)
        self._label.setDrawsBackground_(False)
        self._label.setEditable_(False)
        self._label.setSelectable_(False)
        self._label.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        self._label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        content.addSubview_(self._label)

        self._window.makeKeyAndOrderFront_(None)

    def set_stage(self, stage_text):
        if self._stage_label:
            self._stage_label.performSelectorOnMainThread_withObject_waitUntilDone_(
                objc.selector(None, selector=b"setStringValue:", signature=b"v@:@"),
                stage_text,
                False,
            )

    def set_detail(self, detail_text):
        if self._label:
            self._label.performSelectorOnMainThread_withObject_waitUntilDone_(
                objc.selector(None, selector=b"setStringValue:", signature=b"v@:@"),
                detail_text,
                False,
            )

    def set_progress(self, percent):
        bar = self._progress_bar
        if not bar:
            return
        _run_on_main(lambda: bar.setDoubleValue_(percent))

    def set_indeterminate(self, indeterminate):
        bar = self._progress_bar
        if not bar:
            return

        def _apply():
            bar.setIndeterminate_(indeterminate)
            if indeterminate:
                bar.startAnimation_(None)
            else:
                bar.stopAnimation_(None)

        _run_on_main(_apply)

    def close(self):
        if self._window:
            self._window.performSelectorOnMainThread_withObject_waitUntilDone_(
                objc.selector(None, selector=b"close", signature=b"v@:"),
                None,
                False,
            )
            self._window = None
