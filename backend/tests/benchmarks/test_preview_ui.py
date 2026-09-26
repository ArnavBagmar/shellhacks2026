"""UI responds while a worker is busy; no real display/camera is required."""
import threading
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from cane.benchmarks.preview_ui import PreviewQuit, PreviewWindow, Toggle, fit_display


class FakeCV:
    WINDOW_NORMAL = EVENT_LBUTTONDOWN = WND_PROP_VISIBLE = 1
    error = RuntimeError

    def __init__(self):
        self.key = -1
        self.visible = 1
        self.shown = 0
        self.on_wait = lambda: None
        self.on_close = lambda: None
        self.ui_threads = []
    def namedWindow(self, *args): pass
    def createTrackbar(self, name, window, opacity, maximum, callback): self.slider = callback
    def setMouseCallback(self, name, callback): self.mouse = callback
    def putText(self, *args): pass
    def rectangle(self, *args): pass
    def getTextSize(self, text, font, scale, thickness): return (len(text) * 8, 11), 3
    def imshow(self, *args):
        self.shown += 1
        self.ui_threads.append(threading.get_ident())
    def waitKeyEx(self, delay):
        self.on_wait()
        value, self.key = self.key, -1
        return value
    def getWindowProperty(self, *args): return self.visible
    def destroyWindow(self, *args):
        self.visible = 0
        self.on_close()


@unittest.skipIf(np is None, 'Install depth extra')
class PreviewWindowTests(unittest.TestCase):
    def make_window(self, toggles=None):
        cv = FakeCV()
        def render(cv, np, payload, opacity, state):
            self.last_render = (opacity, dict(state))
            return np.zeros((180, 960, 3), dtype=np.uint8)
        ui = PreviewWindow(cv, np, 'test', 35, render,
                           toggles or [Toggle('overlay', 'd', 'Overlay'), Toggle('corridor', 'c', 'Corridor')],
                           opacity_toggle='overlay')
        self.addCleanup(ui.close)
        return cv, ui

    def test_slider_redraws_previous_frame_while_inference_busy(self):
        cv, ui = self.make_window()
        ui.payload = {'frame': None}
        ui.toggles['overlay'].value = False
        started, release = threading.Event(), threading.Event()
        worker_thread = []
        def slow_model():
            worker_thread.append(threading.get_ident())
            started.set()
            if not release.wait(2): raise RuntimeError('UI never processed slider')
            return 'result'
        def events():
            if started.wait(.01) and not release.is_set():
                cv.slider(80)
                ui.redraw()
                release.set()
        cv.on_wait = events
        self.assertEqual(ui.call(slow_model), 'result')
        # Moving the slider restores the layer it controls, so it always shows.
        self.assertEqual(self.last_render, (.8, {'overlay': True, 'corridor': True}))
        self.assertNotEqual(worker_thread[0], threading.get_ident())
        self.assertTrue(all(t == threading.get_ident() for t in cv.ui_threads))

    def test_quit_closes_window_before_busy_worker_finishes(self):
        cv, ui = self.make_window()
        started, window_closed = threading.Event(), threading.Event()
        cv.on_close = window_closed.set
        def slow_model():
            started.set()
            if not window_closed.wait(2): raise RuntimeError('Quit did not close window')
        def events():
            if started.wait(.01): cv.key = ord('q')
        cv.on_wait = events
        with self.assertRaises(PreviewQuit): ui.call(slow_model)
        self.assertTrue(window_closed.is_set())
        self.assertTrue(ui.stop.is_set())
        self.assertTrue(ui.closed)

    def test_escape_uppercase_and_click_quit(self):
        for key in (ord('Q'), 27):
            cv, ui = self.make_window()
            cv.key = key
            ui.pump()
            self.assertTrue(ui.closed)
        cv, ui = self.make_window()
        x0, y0, _, _ = ui.quit_box
        ui.on_mouse(cv.EVENT_LBUTTONDOWN, x0 + 1, y0 + 1, 0, None)
        ui.pump()
        self.assertTrue(ui.closed)

    def test_close_button_and_worker_error(self):
        cv, ui = self.make_window()
        with self.assertRaisesRegex(ValueError, 'model failed'):
            ui.call(lambda: (_ for _ in ()).throw(ValueError('model failed')))
        cv.visible = 0
        ui.pump()
        self.assertTrue(ui.closed)

    def test_save_captures_displayed_frame_not_next_inflight_frame(self):
        cv, ui = self.make_window()
        raw = np.ones((2, 2))
        ui.sample = (7, raw)
        ui.handle_key(ord('s'))
        index, saved, annotated = ui.take_save_request()
        self.assertEqual(index, 7)
        self.assertIs(saved, raw)
        self.assertIsNot(annotated, ui.annotated)
        self.assertIsNone(ui.take_save_request())

    def test_keys_and_buttons_switch_the_same_layers(self):
        cv, ui = self.make_window()
        for key in (ord('d'), ord('D')):
            expected = not ui.state()['overlay']
            ui.handle_key(key)
            self.assertEqual(ui.state()['overlay'], expected)
        self.assertTrue(ui.dirty)
        button = next(box for box in ui.buttons if box[0] == 'corridor')
        ui.on_mouse(cv.EVENT_LBUTTONDOWN, button[1] + 2, button[2] + 2, 0, None)
        self.assertFalse(ui.state()['corridor'])
        self.assertFalse(ui.closed)

    def test_unavailable_layer_cannot_be_switched_on(self):
        cv, ui = self.make_window([Toggle('overlay', 'd', 'Overlay'),
                                   Toggle('objects', 'o', 'Objects', available=False)])
        self.assertFalse(ui.state()['objects'])
        ui.handle_key(ord('o'))
        ui.on_mouse(cv.EVENT_LBUTTONDOWN, *next(box[1:3] for box in ui.buttons if box[0] == 'objects'), 0, None)
        self.assertFalse(ui.state()['objects'])
        self.assertIn('not loaded', ui.toggles['objects'].caption())

    def test_every_toggle_stays_clickable_in_a_narrow_window(self):
        cv, ui = self.make_window([Toggle(name, key, name.title()) for name, key in
                                   (('depth', 'd'), ('objects', 'o'), ('masks', 'm'), ('corridor', 'c'))])
        ui.show(np.zeros((120, 200, 3), dtype=np.uint8))
        names = [box[0] for box in ui.buttons]
        self.assertEqual(names, ['depth', 'objects', 'masks', 'corridor', '__quit__'])
        rows = {box[2] for box in ui.buttons}
        self.assertGreater(len(rows), 1, 'narrow chrome should wrap onto more rows')
        for _, x0, y0, x1, y1 in ui.buttons:
            self.assertLessEqual(x1, max(200, 660))

    def test_reserved_keys_are_rejected(self):
        for key in ('q', 'S'):
            with self.assertRaisesRegex(ValueError, 'reserved'):
                Toggle('depth', key, 'Depth')

    def test_display_fit_never_upscales(self):
        self.assertEqual(fit_display(np.zeros((10, 20, 3)), 640, 480), (1.0, (20, 10)))
        scale, size = fit_display(np.zeros((1080, 1920, 3)), 1280, 760)
        self.assertEqual(size, (1280, 720))
        self.assertAlmostEqual(scale, 1280 / 1920)


if __name__ == '__main__': unittest.main()
