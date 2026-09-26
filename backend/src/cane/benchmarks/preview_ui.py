"""Main-thread OpenCV UI with one sequential worker for blocking operations.

Shared by the depth-only preview and the fused detection+depth preview. The
window owns the chrome (toggle buttons, status lines, legend) and every input
event; the caller's render function owns only the image itself.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

FONT = 0  # cv2.FONT_HERSHEY_SIMPLEX, without importing cv2 in this module.
CHROME = (28, 28, 32)
ON_FILL, ON_EDGE, ON_TEXT = (168, 116, 40), (226, 174, 98), (255, 255, 255)
OFF_FILL, OFF_EDGE, OFF_TEXT = (46, 46, 52), (86, 86, 94), (148, 148, 154)
QUIT_FILL, QUIT_EDGE = (52, 52, 178), (108, 108, 224)
STATUS_TEXT = (226, 226, 230)
BUTTON_ROW, STATUS_ROW, LEGEND_ROW = 34, 21, 30
BUTTON_SCALE, STATUS_SCALE = 0.46, 0.44
MIN_WIDTH = 660
QUIT = "__quit__"
RESERVED_KEYS = ("q", "s")


class PreviewQuit(Exception):
    """User requested an orderly stop after the current operation."""


class Toggle:
    """One named layer the user switches with a key or by clicking its button."""

    def __init__(self, name, key, label, value=True, available=True):
        if key.lower() in RESERVED_KEYS:
            raise ValueError(f"{key!r} is reserved for quit/save, not a layer key")
        self.name, self.key, self.label = name, key.lower(), label
        self.available = available
        self.value = bool(value) and available

    def flip(self):
        if self.available:
            self.value = not self.value
        return self.value

    def caption(self):
        if not self.available:
            return f"{self.label}: off (not loaded)"
        return f"{self.label}: {'ON' if self.value else 'off'}  [{self.key}]"


def fit_display(frame, max_width=1280, max_height=760):
    """Scale and size for UI buffers only; inference inputs are never resized."""
    height, width = frame.shape[:2]
    scale = min(1.0, max_width / width, max_height / height)
    return scale, (max(1, round(width * scale)), max(1, round(height * scale)))


def status_bar(cv2, np, width, lines, height=None):
    """Dark strip carrying the per-frame status lines, one per row."""
    rows = max(1, len(lines))
    bar = np.full((height or rows * STATUS_ROW, width, 3), CHROME, dtype=np.uint8)
    for index, line in enumerate(lines):
        cv2.putText(bar, line, (12, STATUS_ROW * index + 15), FONT, STATUS_SCALE, STATUS_TEXT, 1)
    return bar


class PreviewWindow:
    def __init__(self, cv2, np, name, opacity, render, toggles, *, opacity_label="Overlay %",
                 opacity_toggle=None, legend=None, starting="Starting preview..."):
        self.cv2, self.np, self.name, self.render = cv2, np, name, render
        self.toggles = {toggle.name: toggle for toggle in toggles}
        self.keys = {toggle.key: toggle for toggle in toggles}
        self.opacity_toggle, self.legend = opacity_toggle, legend
        self.opacity = opacity
        self.dirty, self.closed = True, False
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview-compute")
        self.payload, self.lines = None, []
        self.annotated = None
        self.sample = None
        self.save_request = None
        self.buttons = []
        self.quit_box = None
        cv2.namedWindow(name, cv2.WINDOW_NORMAL)
        cv2.createTrackbar(opacity_label, name, opacity, 100, self.set_opacity)
        cv2.setMouseCallback(name, self.on_mouse)
        self.status(starting)
        self.pump()

    def state(self):
        return {name: toggle.value for name, toggle in self.toggles.items()}

    def status(self, *lines):
        """Show chrome over a blank image before the first frame is ready."""
        self.lines = list(lines)
        self.show(self.np.full((150, MIN_WIDTH, 3), (16, 16, 18), dtype=self.np.uint8))

    def set_opacity(self, value):
        self.opacity = value
        # A slider movement should always have a visible effect, even after the
        # depth layer was switched off.
        if self.opacity_toggle in self.toggles:
            self.toggles[self.opacity_toggle].value = self.toggles[self.opacity_toggle].available
        self.dirty = True

    def request_quit(self):
        self.stop.set()

    def on_mouse(self, event, x, y, flags, userdata):
        if event != self.cv2.EVENT_LBUTTONDOWN:
            return
        for name, x0, y0, x1, y1 in self.buttons:
            if x0 <= x <= x1 and y0 <= y <= y1:
                if name == QUIT:
                    self.request_quit()
                else:
                    self.toggles[name].flip()
                    self.dirty = True
                return

    def button_rows(self, width):
        """Wrap the buttons so a narrow window still exposes every toggle."""
        entries = [(name, toggle.caption()) for name, toggle in self.toggles.items()]
        entries.append((QUIT, "Quit  [q]"))
        rows, row, x = [], [], 10
        for name, caption in entries:
            (text_width, _), _ = self.cv2.getTextSize(caption, FONT, BUTTON_SCALE, 1)
            box_width = text_width + 22
            if row and x + box_width > width - 10:
                rows.append(row)
                row, x = [], 10
            row.append((name, caption, x, box_width))
            x += box_width + 8
        rows.append(row)
        return rows

    def chrome(self, width):
        cv2, np = self.cv2, self.np
        rows = self.button_rows(width)
        height = len(rows) * BUTTON_ROW + max(1, len(self.lines)) * STATUS_ROW
        if self.legend is not None:
            height += LEGEND_ROW
        bar = np.full((height, width, 3), CHROME, dtype=np.uint8)
        self.buttons, self.quit_box = [], None
        for index, row in enumerate(rows):
            top = index * BUTTON_ROW + 5
            for name, caption, x, box_width in row:
                self.draw_button(bar, name, caption, x, top, box_width, BUTTON_ROW - 10)
        offset = len(rows) * BUTTON_ROW
        bar[offset:offset + max(1, len(self.lines)) * STATUS_ROW] = status_bar(
            cv2, np, width, self.lines, max(1, len(self.lines)) * STATUS_ROW)
        if self.legend is not None:
            bar[height - LEGEND_ROW:height] = self.legend(cv2, np, width, LEGEND_ROW)
        return bar

    def draw_button(self, bar, name, caption, x, y, width, height):
        cv2 = self.cv2
        if name == QUIT:
            fill, edge, text = QUIT_FILL, QUIT_EDGE, (255, 255, 255)
        elif not self.toggles[name].available:
            fill, edge, text = OFF_FILL, OFF_EDGE, (110, 110, 116)
        elif self.toggles[name].value:
            fill, edge, text = ON_FILL, ON_EDGE, ON_TEXT
        else:
            fill, edge, text = OFF_FILL, OFF_EDGE, OFF_TEXT
        cv2.rectangle(bar, (x, y), (x + width, y + height), fill, -1)
        cv2.rectangle(bar, (x, y), (x + width, y + height), edge, 1)
        cv2.putText(bar, caption, (x + 11, y + height - 8), FONT, BUTTON_SCALE, text, 1)
        box = (name, x, y, x + width, y + height)
        self.buttons.append(box)
        if name == QUIT:
            self.quit_box = box[1:]

    def show(self, canvas):
        np = self.np
        width = max(canvas.shape[1], MIN_WIDTH)
        if canvas.shape[1] < width:
            pad = np.full((canvas.shape[0], width - canvas.shape[1], 3), CHROME, dtype=np.uint8)
            canvas = np.concatenate([canvas, pad], axis=1)
        composed = np.concatenate([self.chrome(width), canvas], axis=0)
        self.annotated = composed
        self.cv2.imshow(self.name, composed)

    def set_frame(self, index, payload, sample, lines):
        self.payload, self.lines = payload, list(lines)
        self.sample = (index, sample)
        self.dirty = True
        self.redraw()

    def redraw(self):
        if self.dirty and self.payload is not None and not self.closed:
            self.show(self.render(self.cv2, self.np, self.payload, self.opacity / 100, self.state()))
            self.dirty = False

    def handle_key(self, key):
        if key < 0:
            return
        key &= 0xFF
        if key in (ord("q"), ord("Q"), 27):
            self.request_quit()
        elif key in (ord("s"), ord("S")) and self.sample is not None:
            # Save the displayed frame, even if a newer frame is being inferred.
            self.redraw()
            self.save_request = (*self.sample, self.annotated.copy())
        elif 0 < key < 128 and chr(key).lower() in self.keys:
            self.keys[chr(key).lower()].flip()
            self.dirty = True

    def pump(self):
        if self.closed:
            return
        self.handle_key(self.cv2.waitKeyEx(10))
        if self.stop.is_set():
            self.hide()
        if not self.closed:
            try:
                if self.cv2.getWindowProperty(self.name, self.cv2.WND_PROP_VISIBLE) == 0:
                    self.request_quit()
            except self.cv2.error:
                self.request_quit()
        if self.stop.is_set():
            self.hide()
        self.redraw()

    def call(self, function, *args, **kwargs):
        if self.stop.is_set():
            raise PreviewQuit()
        future = self.executor.submit(function, *args, **kwargs)
        while not future.done():
            if self.closed:
                time.sleep(.01)
            else:
                self.pump()
        if self.stop.is_set():
            # The operation has completed, so cleanup won't race camera/model IO.
            raise PreviewQuit()
        return future.result()

    def take_save_request(self):
        result, self.save_request = self.save_request, None
        return result

    def hide(self):
        if not self.closed:
            self.closed = True
            try:
                self.cv2.destroyWindow(self.name)
                # Flush native teardown after the event callback has returned.
                self.cv2.waitKeyEx(1)
            except self.cv2.error:
                pass  # Native close button may have already destroyed it.

    def close(self):
        self.stop.set()
        self.hide()
        self.executor.shutdown(wait=True, cancel_futures=True)
