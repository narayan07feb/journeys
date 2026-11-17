import pytest

from journeys.pack import Pack, PackError, substitute


def test_example_pack_is_valid(pack_dir):
    p = Pack.load(pack_dir)
    assert p.errors == []
    assert p.app == "com.android.settings"
    assert [j.name for j in p.select("smoke")] == ["android-version", "open-display"]
    assert len(p.select("all")) == 3
    with pytest.raises(PackError):
        p.select("nope")


def test_missing_pack_file(tmp_path):
    with pytest.raises(PackError):
        Pack.load(tmp_path)


def test_validation_errors(pack_dir):
    (pack_dir / "journeys" / "bad.yaml").write_text(
        "name: bad\nsteps:\n  - expect: NOWHERE\n  - hook: missing\n  - event: {name: x}\n  - jump: 1\n"
    )
    (pack_dir / "journeys" / "dup.yaml").write_text("name: open-display\nsteps:\n  - see: x\n")
    (pack_dir / "sets.yaml").write_text("sets:\n  s: {journeys: [ghost]}\n")
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "a step is one of" in errs  # the bad step stops the journey from loading
    assert "journey open-display is also in" in errs
    assert "unknown journey ghost" in errs and "selects no journey" in errs
    (pack_dir / "journeys" / "bad.yaml").write_text(
        "name: bad\nsteps:\n  - expect: NOWHERE\n  - hook: missing\n  - event: {name: x}\n"
    )
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "bad.yaml:3: expect names unknown screen NOWHERE" in errs
    assert "hook missing is not in pack.yaml hooks" in errs
    assert "event step needs events.check" in errs


def test_variables_only_declared(pack_dir):
    (pack_dir / "pack.yaml").write_text(
        "name: n\napp: a\nenv: [X]\naccounts:\n  main: {user: U_VAR}\n"
    )
    p = Pack.load(pack_dir)
    v = p.variables({"X": "1", "U_VAR": "bob", "SECRET": "no"})
    assert v == {"X": "1", "U_VAR": "bob", "main.user": "bob"}
    assert p.declared() == {"X", "U_VAR", "main.user"}


def test_substitute():
    assert substitute({"a": ["${X}-${main.user}"]}, {"X": "1", "main.user": "b"}, {"X"}) == {"a": ["1-b"]}
    assert substitute("${output.y}", {}, {"X"}) == "${output.y}"  # not ours: left for the driver
    with pytest.raises(KeyError):
        substitute("${X}", {}, {"X"})
