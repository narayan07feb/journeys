import subprocess

from journeys.drivers.uiautomator import UiAutomatorDriver, parse_dump

DUMP = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node text="" resource-id="" class="android.widget.FrameLayout" package="com.example" content-desc="" clickable="false" bounds="[0,0][1080,2400]">
 <node text="Sign in" resource-id="" class="android.widget.TextView" content-desc="" clickable="false" bounds="[40,100][600,180]" />
 <node text="" resource-id="com.example:id/email" class="android.widget.EditText" content-desc="" clickable="true" focused="true" bounds="[40,200][1040,300]" />
 <node text="Next" resource-id="com.example:id/next" class="android.widget.Button" content-desc="" clickable="true" enabled="false" bounds="[40,320][1040,420]" />
 <node text="" resource-id="" class="android.widget.ImageButton" content-desc="Close" clickable="true" bounds="[980,20][1060,100]" />
 <node text="" resource-id="" class="android.view.View" content-desc="" clickable="true" bounds="[0,500][1080,600]" />
 <node text="Dark" resource-id="" class="android.widget.Switch" content-desc="" checkable="true" checked="true" bounds="[40,620][1040,700]" />
 <node text="Hidden" resource-id="" class="android.widget.TextView" content-desc="" clickable="false" bounds="[0,0][0,0]" />
 <node text="" resource-id="" class="android.widget.LinearLayout" content-desc="" clickable="false" bounds="[0,700][1080,800]" />
</node></hierarchy>UI hierchary dumped to: /dev/tty
"""


def test_parse_dump():
    screen, centres = parse_dump(DUMP)
    got = [(e.kind, e.text, e.id, e.state) for e in screen.elements]
    assert got == [
        ("Text", "Sign in", "", ()),
        ("Field", "", "com.example:id/email", ("focused",)),
        ("Button", "Next", "com.example:id/next", ("disabled",)),
        ("Button", "Close", "", ()),
        ("Button", "", "", ()),
        ("Switch", "Dark", "", ("checked",)),
    ]
    assert centres[2] == (540, 370) and screen.header.startswith("uiautomator com.example")


def test_look_falls_back_to_a_file_dump_and_deletes_it():
    calls = []

    def adb(*args, timeout=60):
        calls.append(args)
        out = b"ERROR: could not dump to tty" if args[-1] == "/dev/tty" else DUMP.encode()
        return subprocess.CompletedProcess(args, 0, out, b"")

    d = UiAutomatorDriver()
    d._adb = adb
    assert len(d.look().elements) == 6
    assert calls[1][1].endswith("rm -f /data/local/tmp/journeys-window.xml")
