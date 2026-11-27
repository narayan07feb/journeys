from journeys.drivers.maestro import parse_hierarchy

TREE = {"attributes": {}, "children": [
    {"attributes": {"class": "android.widget.FrameLayout"}, "children": [
        {"attributes": {"text": "Sign in", "class": "android.widget.TextView"}},
        {"attributes": {"resource-id": "com.example:id/email", "class": "android.widget.EditText",
                        "hintText": "Email", "focused": "true"}},
        {"attributes": {"text": "Next", "resource-id": "com.example:id/next",
                        "class": "android.widget.Button", "enabled": "false", "clickable": "true"}},
        {"attributes": {"accessibilityText": "Close", "class": "android.widget.ImageView", "clickable": "true"}},
        {"attributes": {"class": "android.view.View", "clickable": "true"}},
        {"attributes": {"text": "Dark", "class": "android.widget.Switch", "checked": "true"}},
    ]}]}


def test_parse_hierarchy():
    s = parse_hierarchy(TREE)
    got = [(e.kind, e.text, e.id, e.state) for e in s.elements]
    assert got == [
        ("Text", "Sign in", "", ()),
        ("Field", "Email", "com.example:id/email", ("focused",)),
        ("Button", "Next", "com.example:id/next", ("disabled",)),
        ("Image", "Close", "", ()),
        ("Button", "", "", ()),
        ("Switch", "Dark", "", ("checked",)),
    ]
    assert [e.ref for e in s.elements] == ["@1", "@2", "@3", "@4", "@5", "@6"]
