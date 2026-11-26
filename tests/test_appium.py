"""The Appium driver: W3C WebDriver to the team's Appium server, read with the shared selector rules."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from journeys.drivers.appium import AppiumDriver, parse_source, reachable
from journeys.drivers.base import DriverError
from phone_sim import Phone

APP = {"app": "com.example.app", "start": "HOME", "screens": {
    "HOME": {"elements": [{"kind": "Text", "text": f"Row {i}"} for i in range(8)]
             + [{"kind": "Button", "text": "Far down", "to": "END"}]},
    "END": {"elements": [{"kind": "Text", "text": "The end"}]}}}


def driver(phone, **kw):
    d = AppiumDriver(wait_s=0, scroll_s=2, **kw)
    d._http, d._adb = phone.appium, phone.adb
    return d


def test_it_never_starts_on_a_device_the_pack_does_not_allow_to_change():
    phone, calls = Phone(APP), []
    d = driver(phone)  # allow_changes defaults to emulator; the pretend phone is a real device
    real = d._http
    d._http = lambda *a: calls.append(a) or real(*a)
    with pytest.raises(DriverError, match="installs its helper apps, sets the hidden API policy .* mock location .* unlocks"):
        d.start("com.example.app", "192.168.0.5:5555")
    assert calls == []  # not even a session request


def test_the_session_names_the_device_and_app_and_resets_nothing():
    phone, sent = Phone(APP), []
    d = driver(phone, allow_changes="any")
    real = d._http
    d._http = lambda m, p, b=None: sent.append((m, p, b)) or real(m, p, b)
    d.start("com.example.app", "emulator-5554")
    caps = sent[0][2]["capabilities"]["alwaysMatch"]
    assert caps["appium:udid"] == "emulator-5554" and caps["appium:appPackage"] == "com.example.app"
    assert caps["appium:noReset"] is True and caps["appium:autoLaunch"] is False
    d.stop()
    assert sent[-1][:2] == ("DELETE", "/session/s1") and d.session is None


def test_parse_source_reads_labels_states_and_skips_what_cannot_be_tapped():
    xml = ("<hierarchy>"
           '<android.widget.ImageButton class="android.widget.ImageButton" content-desc="Menu" clickable="true" '
           'bounds="[0,0][100,100]"/>'
           '<android.widget.Switch class="android.widget.Switch" text="Wi-Fi" checked="true" enabled="false" '
           'clickable="true" bounds="[0,100][1080,200]"/>'
           '<android.widget.FrameLayout class="android.widget.FrameLayout" bounds="[0,0][1080,2400]"/>'
           '<android.widget.TextView class="android.widget.TextView" text="No bounds"/>'
           "</hierarchy>")
    screen, centres = parse_source(xml)
    assert [e.line() for e in screen.elements] == ['@1 Button "Menu"', '@2 Switch "Wi-Fi" checked disabled']
    assert centres == [(0, 0, 100, 100), (0, 100, 1080, 200)]
    with pytest.raises(DriverError, match="unreadable XML"):
        parse_source("<hierarchy><oops")


def test_scrolling_finds_what_is_further_down():
    phone = Phone(APP, page=3)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp"]).ok
    assert "Far down" not in [e.text for e in d.look().elements]
    assert d.do([{"scrollUntilVisible": {"element": "Far down"}}, {"tapOn": "Far down"}]).ok
    assert [e.text for e in d.look().elements] == ["The end"]


def test_clear_failures_for_keys_typing_and_screenshots():
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    assert not d.do([{"pressKey": "Volume"}]).ok
    res = d.do([{"inputText": "hi"}])  # nothing focused: Appium answers 404
    assert not res.ok and "no field has the focus" in res.message
    real = d._http
    d._http = lambda m, p, b=None: {"value": "bm90IGEgcG5n"} if p.endswith("/screenshot") else real(m, p, b)
    assert d.screenshot(__import__("pathlib").Path("/nonexistent/x.png")) is None  # not a PNG: no file


class _Server(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        if self.path == "/status":
            return self._reply(200, {"value": {"ready": True}})
        self._reply(404, {"value": {"error": "unknown command", "message": "no such route"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/session":
            return self._reply(200, {"value": {"sessionId": "abc", "echo": body}})
        self._reply(500, {"value": {"error": "unknown error", "message": "the device went away"}})


def test_the_wire_over_real_http():
    srv = HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        assert reachable(url) is None
        d = AppiumDriver(url=url, allow_changes="any")
        d._adb = Phone(APP).adb
        d.start("com.example.app", "emulator-5554")
        assert d.session == "abc"
        with pytest.raises(DriverError, match="500 unknown error: the device went away"):
            d.do(["back"])
    finally:
        srv.shutdown()
        srv.server_close()
    assert "no Appium server" in reachable(url, timeout=0.5)  # gone now


def test_make_driver_says_when_no_appium_server_answers(pack_dir, tmp_path, monkeypatch):
    from journeys import cli
    from journeys.pack import Pack
    from journeys.results import Store
    monkeypatch.setenv("JOURNEYS_APPIUM_URL", "http://127.0.0.1:9")  # nothing listens on the discard port
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    with pytest.raises(DriverError, match="driver appium cannot run: no Appium server"):
        cli.make_driver("appium", Pack.load(pack_dir), None, Store(tmp_path))


def test_launch_with_clear_state_clears_the_app_first():
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    assert d.do([{"launchApp": {"clearState": True}}]).ok
    assert "pm clear com.example.app" in phone.shell_log
    assert d.do(["launchApp"]).ok and phone.shell_log.count("pm clear com.example.app") == 1


def test_launch_starts_fresh_even_when_the_app_is_running():
    """Appium's activateApp only brings a running app up; every journey's launch must start it again."""
    app = {"app": "com.example.app", "start": "HOME", "screens": {
        "HOME": {"elements": [{"kind": "Button", "text": "Next", "to": "TWO"}]},
        "TWO": {"elements": [{"kind": "Text", "text": "Second"}]}}}
    phone = Phone(app)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp", {"tapOn": "Next"}]).ok and [e.text for e in d.look().elements] == ["Second"]
    assert d.do(["launchApp"]).ok and [e.text for e in d.look().elements] == ["Next"]


def test_ids_labels_and_taps_like_the_other_drivers():
    xml = ('<hierarchy><android.widget.EditText class="android.widget.EditText" hint="Email" '
           'resource-id="com.example.app:id/email" bounds="[0,0][1080,100]"/>'
           '<android.widget.Button class="android.widget.Button" text="Pay" clickable="true" '
           'bounds="[0,2300][1080,2500]"/></hierarchy>')
    screen, boxes = parse_source(xml)
    assert [e.line() for e in screen.elements] == ['@1 Field "Email" #email', '@2 Button "Pay"']
    phone, taps = Phone(APP), []
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    real = d._http

    def http(m, p, b=None):
        if p.endswith("/source"):
            return {"value": xml}
        if p.endswith("/actions"):
            taps.append(b["actions"][0]["actions"][0])
            return {"value": None}
        return real(m, p, b)
    d._http = http
    assert d.do([{"tapOn": "Pay"}]).ok
    assert (taps[0]["x"], taps[0]["y"]) == (540, 2350)  # the part of the button that is on the screen


def test_odd_replies_are_driver_errors_not_tracebacks():
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    for reply in ([1], "x", None):
        d._http = lambda m, p, b=None, r=reply: {"value": r} if p == "/session" else {}
        with pytest.raises(DriverError):
            d.start("com.example.app", "emulator-5554")
    d.session = "s1"
    d._http = lambda m, p, b=None: {"value": {"not": "xml"}}
    with pytest.raises(DriverError, match="not XML text"):
        d.look()


class _Broken(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.wfile.write(b"garbage\r\n\r\n")  # not HTTP at all

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path == "/session":
            body = b"[1, 2]"  # JSON, but not a WebDriver reply
            self.send_response(200)
        else:
            body = b'["odd"]'  # an error whose body is a list
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def test_a_broken_server_is_a_driver_error():
    srv = HTTPServer(("127.0.0.1", 0), _Broken)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        assert "no Appium server" in reachable(url)
        d = AppiumDriver(url=url, allow_changes="any")
        d._adb = Phone(APP).adb
        with pytest.raises(DriverError, match="not a WebDriver reply"):
            d.start("com.example.app", "emulator-5554")
        d.session = "s1"
        with pytest.raises(DriverError, match="500"):
            d.do(["back"])
        with pytest.raises(DriverError, match="appium GET"):
            d.look()  # the reply is not HTTP at all
    finally:
        srv.shutdown()
        srv.server_close()


def test_typing_erasing_keys_and_the_keyboard():
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    seen = []
    real = d._http
    d._http = lambda m, p, b=None: seen.append((m, p)) or real(m, p, b)
    phone.field = True  # a field has the focus
    assert d.do([{"inputText": "hi"}]).ok and ("GET", "/session/s1/element/active") in seen
    assert phone.typed == ["hi"]
    assert not d.do([{"eraseText": "lots"}]).ok
    assert d.do([{"pressKey": "home"}]).ok  # any case
    d._http = lambda m, p, b=None: (_ for _ in ()).throw(DriverError("connection refused"))
    with pytest.raises(DriverError):
        d.do(["hideKeyboard"])  # a dead server is never "no keyboard"


def test_round_2_session_outlives_a_long_hook_and_offscreen_is_not_visible():
    from journeys.drivers.appium import IDLE_S
    phone, sent = Phone(APP), []
    d = driver(phone, allow_changes="any")
    real = d._http
    d._http = lambda m, p, b=None: sent.append(b) or real(m, p, b)
    d.start("com.example.app", "emulator-5554")
    assert sent[0]["capabilities"]["alwaysMatch"]["appium:newCommandTimeout"] == IDLE_S > 300
    xml = ('<hierarchy><android.widget.Button class="android.widget.Button" text="Below" clickable="true" '
           'bounds="[0,2500][1080,2600]"/></hierarchy>')
    d._http = lambda m, p, b=None: {"value": xml} if p.endswith("/source") else real(m, p, b)
    assert not d.do([{"assertVisible": "Below"}]).ok and not d.do([{"tapOn": "Below"}]).ok


def test_round_2_odd_window_screenshot_and_url():
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    real = d._http
    for rect in ([1], "x", {"width": "wide", "height": 1}):
        d._http = lambda m, p, b=None, r=rect: {"value": r} if p.endswith("/window/rect") else real(m, p, b)
        with pytest.raises(DriverError, match="window size"):
            d.do(["scroll"])
    d._http = lambda m, p, b=None: {"value": {"a": 1}} if p.endswith("/screenshot") else real(m, p, b)
    assert d.screenshot(__import__("pathlib").Path("/nonexistent/x.png")) is None
    bad = AppiumDriver(url="appium-host", allow_changes="any")
    bad._adb = phone.adb
    with pytest.raises(DriverError):
        bad.start("com.example.app", "emulator-5554")


def test_round_2_an_expired_session_is_never_hidden():
    from journeys.drivers.appium import AppiumError
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")

    def http(m, p, b=None, msg="404 invalid session id"):
        raise AppiumError(f"appium {m} {p}: {msg}", "", msg)
    d._http = http
    with pytest.raises(AppiumError):
        d.do(["hideKeyboard"])
    with pytest.raises(AppiumError):
        d.do([{"inputText": "x"}])
    d._http = lambda m, p, b=None: http(m, p, b, "500 Soft keyboard not present")
    assert d.do(["hideKeyboard"]).ok


def test_round_2_not_see_waits_for_the_element_to_go():
    phone = Phone(APP)
    d = AppiumDriver(wait_s=2, allow_changes="any")
    d._http, d._adb = phone.appium, phone.adb
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp"]).ok
    reads = []
    real = d._http

    def http(m, p, b=None):
        if p.endswith("/source"):
            reads.append(1)
            if len(reads) == 2:  # the dialog closes after the first look
                phone.app.do([{"tapOn": "Far down"}])
        return real(m, p, b)
    d._http = http
    assert d.do([{"assertNotVisible": "Row 0"}]).ok and len(reads) >= 2


def test_round_2_only_the_chosen_driver_is_probed_and_a_proxy_is_skipped_locally(monkeypatch):
    from journeys import drivers
    from journeys.drivers import appium
    probes = []
    monkeypatch.setattr(appium, "reachable", lambda *a, **k: probes.append(1))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)  # adb is there: only the probe decides
    drivers.availability("maestro")
    assert probes == []
    drivers.availability()
    assert probes == [1]
    srv = HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # a proxy that answers nothing
        monkeypatch.setattr(appium, "reachable", reachable)
        assert reachable(f"http://127.0.0.1:{srv.server_address[1]}") is None
    finally:
        srv.shutdown()
        srv.server_close()


class _Expired(BaseHTTPRequestHandler):
    """A server whose session is gone: the standard W3C error body, as Appium sends it."""
    def log_message(self, *a):
        pass

    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        self._reply(404, {"value": {"error": "invalid session id", "message": "A session is either terminated or not started"}})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/hide_keyboard"):
            return self._reply(404, {"value": {"error": "invalid session id", "message": "A session is gone"}})
        self._reply(404, {"value": {"error": "invalid session id", "message": "A session is gone"}})


def test_round_3_an_expired_session_fails_by_its_w3c_code():
    srv = HTTPServer(("127.0.0.1", 0), _Expired)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = AppiumDriver(url=f"http://127.0.0.1:{srv.server_address[1]}", allow_changes="any", wait_s=0)
        d._adb = Phone(APP).adb
        d.session = "old"
        for cmd in ("hideKeyboard", {"inputText": "x"}):
            with pytest.raises(DriverError, match="invalid session id"):
                d.do([cmd])
    finally:
        srv.shutdown()
        srv.server_close()


def test_round_3_other_errors_are_not_mistaken_for_no_keyboard_or_no_focus():
    from journeys.drivers.appium import AppiumError
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    real = d._http

    def http(m, p, b=None, err=("500 unknown error: instrumentation is not running", "unknown error")):
        if p.endswith(("/hide_keyboard", "/element/active")):
            raise AppiumError(f"appium {m} {p}: {err[0]}", err[1], err[0])
        return real(m, p, b)
    d._http = http
    for cmd in ("hideKeyboard", {"inputText": "x"}):
        with pytest.raises(AppiumError):
            d.do([cmd])
    d._http = lambda m, p, b=None: http(m, p, b, ("500 unknown error: Soft keyboard not present", "unknown error"))
    assert d.do(["hideKeyboard"]).ok


def test_round_3_a_passing_source_error_is_looked_at_again_and_launch_waits_for_the_app():
    from journeys.drivers.appium import AppiumError
    phone = Phone(APP)
    d = AppiumDriver(wait_s=3, scroll_s=2, allow_changes="any")
    d._http, d._adb = phone.appium, phone.adb
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp"]).ok
    fails = []
    real = d._http

    def http(m, p, b=None):
        if p.endswith("/source") and not fails:
            fails.append(1)
            raise AppiumError(f"appium {m} {p}: 500 unknown error: could not dump", "unknown error")
        return real(m, p, b)
    d._http = http
    assert d.do([{"tapOn": "Row 0"}]).ok and fails == [1]
    seen = []
    d._http = real
    d.foreground_app = lambda: seen.append(1) or ("com.example.app" if len(seen) > 1 else "launcher")
    assert d.do(["launchApp"]).ok and len(seen) >= 2  # it waited for the app to come up


def test_round_3_no_such_window_is_fatal_and_a_session_start_gets_time():
    from journeys.drivers.appium import START_S, AppiumError
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    timeouts = []
    real = d._http
    d._http = lambda m, p, b=None: (timeouts.append((p, d.timeout)), real(m, p, b))[1]
    d.start("com.example.app", "emulator-5554")
    assert timeouts[0] == ("/session", START_S) and d.timeout < START_S  # longer for the start only
    d._http = lambda m, p, b=None: (_ for _ in ()).throw(AppiumError("appium: 404 gone", "no such window",
                                                                     "keyboard window gone"))
    with pytest.raises(AppiumError):
        d.do(["hideKeyboard"])  # the app's window is gone: fatal, even though the words say keyboard


def _custom(phone, sources, calls=None):
    """A driver whose page source comes from `sources` (one per read; the last one repeats)."""
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    real, reads = d._http, []

    def http(m, p, b=None):
        if calls is not None:
            calls.append((m, p, b))
        if p.endswith("/source"):
            reads.append(1)
            return {"value": sources[min(len(reads), len(sources)) - 1]}
        if p.endswith("/actions"):
            return {"value": None}
        return real(m, p, b)
    d._http = http
    return d, reads


def _btn(text, y1, y2):
    return (f'<android.widget.Button class="android.widget.Button" text="{text}" clickable="true" '
            f'bounds="[0,{y1}][1080,{y2}]"/>')


def test_round_4_an_offscreen_duplicate_never_hides_the_visible_one():
    xml = "<hierarchy>" + _btn("Next", 3000, 3100) + _btn("Next", 100, 200) + "</hierarchy>"
    calls = []
    d, _ = _custom(Phone(APP), [xml], calls)
    assert d.do([{"tapOn": "Next"}]).ok
    tap = [b for m, p, b in calls if p.endswith("/actions")][0]["actions"][0]["actions"][0]
    assert tap["y"] == 150 and [e.line() for e in d.look().elements] == ['@1 Button "Next"']


def test_round_4_wait_idle_waits_until_the_screen_stops_changing():
    a = "<hierarchy>" + _btn("A", 0, 100) + "</hierarchy>"
    b = "<hierarchy>" + _btn("B", 0, 100) + "</hierarchy>"
    d, reads = _custom(Phone(APP), [a, b, b])
    assert d.do(["waitForAnimationToEnd"]).ok and len(reads) == 3  # a, b, b: settled


def test_round_4_scroll_to_wants_all_of_the_element_on_screen():
    part = "<hierarchy>" + _btn("Pay", 2300, 2500) + "</hierarchy>"
    whole = "<hierarchy>" + _btn("Pay", 1000, 1200) + "</hierarchy>"
    calls = []
    d, _ = _custom(Phone(APP), [part, part, whole], calls)
    assert d.do([{"scrollUntilVisible": {"element": "Pay"}}]).ok
    assert any(p.endswith("/actions") for _, p, _ in calls)  # it scrolled once the button was only partly in


def test_round_4_hide_keyboard_falls_back_and_typing_reads_a_legacy_key():
    from journeys.drivers.appium import AppiumError
    phone = Phone(APP)
    d = driver(phone, allow_changes="any")
    d.start("com.example.app", "emulator-5554")
    real, sent = d._http, []

    def http(m, p, b=None):
        sent.append((p, b))
        if p.endswith("/hide_keyboard"):
            raise AppiumError("appium: 404 unknown command", "unknown command", "The requested resource could not be found")
        if p.endswith("/element/active"):
            return {"value": {"ELEMENT": "e1"}}
        return real(m, p, b)
    d._http = http
    assert d.do(["hideKeyboard"]).ok
    assert any(b and b.get("script") == "mobile: hideKeyboard" for _, b in sent)
    assert d.do([{"inputText": "hi"}]).ok and phone.typed == ["hi"]
