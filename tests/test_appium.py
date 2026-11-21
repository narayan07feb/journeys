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
    d = AppiumDriver(wait_s=0, **kw)
    d._http, d._adb = phone.appium, phone.adb
    return d


def test_it_never_starts_on_a_device_the_pack_does_not_allow_to_change():
    phone, calls = Phone(APP), []
    d = driver(phone)  # allow_changes defaults to emulator; the pretend phone is a real device
    real = d._http
    d._http = lambda *a: calls.append(a) or real(*a)
    with pytest.raises(DriverError, match="helper apps"):
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
    assert centres == [(50, 50), (540, 150)]
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
    real = d._http
    d._http = lambda m, p, b=None: {"value": None} if p.endswith("/element/active") else real(m, p, b)
    res = d.do([{"inputText": "hi"}])
    assert not res.ok and "no field has the focus" in res.message
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
        with pytest.raises(DriverError, match="500 the device went away"):
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
