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
        "name: n\napp: com.a\nenv: [X]\naccounts:\n  main: {user: U_VAR}\n"
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


def test_after_and_set_hooks_are_checked(pack_dir):
    (pack_dir / "journeys" / "hk.yaml").write_text(
        "- name: hk-a\n  after: [note, nope, {name: note, args: {v: '${SECRET}'}}, {name: note, oops: 1}]\n"
        "  steps:\n    - expect: SETTINGS-HOME\n"
        "- name: hk-b\n  after: note\n  steps:\n    - expect: SETTINGS-HOME\n"
        "- name: hk-c\n  after: no\n  steps:\n    - expect: SETTINGS-HOME\n")
    (pack_dir / "sets.yaml").write_text(
        "sets:\n  s1: {journeys: [hk-a], before: [gone], after: [note]}\n"
        "  s2: {journeys: [hk-a], after: note}\n  s3: {journeys: [hk-a], setup: [note]}\n"
        "  s4: {journeys: [hk-a], after: [off]}\n  s5: {journeys: [hk-a], after: off, description: x}\n")
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "hk.yaml:1: after: hook nope is not in pack.yaml hooks" in errs
    assert "hk.yaml:1: after: ${SECRET} is not declared in pack.yaml" in errs
    assert "after: a hook is a name or {name: ..., args: {...}}, not {'name': 'note', 'oops': 1}" in errs
    assert "hk.yaml:5: after is a list of hooks, like [reset_flag], not 'note'" in errs
    assert "sets.yaml: set s1 before: hook gone is not in pack.yaml hooks" in errs
    assert "set s1 after" not in errs
    assert "set s4 after: a hook is a name or {name: ..., args: {...}}, not False (YAML reads a bare on" in errs
    assert "set s2 is a map with journeys" in errs and "set s5 is a map with journeys: [names] and/or tags: [tags], and before: / after: [hooks] (YAML reads" in errs
    assert "set s3" not in errs  # other keys (a description, say) stay allowed, as before
    assert "hk.yaml:9: after is a list of hooks, like [reset_flag], not False (YAML reads a bare" in errs


def test_set_hooks_only_for_a_set(pack_dir):
    (pack_dir / "sets.yaml").write_text("sets:\n  s1: {tags: [smoke], before: [note], after: [{name: note}]}\n")
    p = Pack.load(pack_dir)
    assert p.errors == []
    assert p.set_hooks("s1") == {"before": ["note"], "after": [{"name": "note"}]}
    assert p.set_hooks("all") == p.set_hooks("open-display") == p.set_hooks(None) == {}


def test_hook_args_keys_are_text_and_a_journey_has_no_before(pack_dir):
    (pack_dir / "journeys" / "hk.yaml").write_text(
        "- name: hk-a\n  before: [note]\n  after: [{name: note, args: {on: true}}]\n"
        "  steps:\n    - hook: {name: note, args: {yes: 1, ok: 2, 3: x}}\n")
    (pack_dir / "sets.yaml").write_text("sets:\n  s1: {journeys: [hk-a], before: [{name: note, args: {off: x}}]}\n")
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "hk.yaml:1: a journey has no before:; start its steps with a hook: step" in errs
    assert "hk.yaml:1: after: hook args keys [True] are not text (YAML reads a bare on" in errs
    assert "hk.yaml:5: hook args keys [True] are not text" in errs
    assert "sets.yaml: set s1 before: hook args keys [False] are not text" in errs


def test_nested_args_keys_and_hook_step_maps_follow_one_rule(pack_dir):
    (pack_dir / "journeys" / "hk.yaml").write_text(
        "- name: hk-a\n  after: [{name: note, args: {opts: {on: true}, list: [{no: 1}]}}]\n"
        "  steps:\n    - hook: {name: note, arg: {x: 1}}\n")
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "hk.yaml:1: after: hook args keys [True, False] are not text" in errs
    assert "hk.yaml:4: a hook is a name or {name: ..., args: {...}}, not {'name': 'note', 'arg': {'x': 1}}" in errs


def _uses(pack_dir, names, event=False):
    """A journey that calls each hook (a declared script nothing calls is never checked)."""
    steps = "".join(f"    - hook: {n}\n" for n in names) + ("    - event: opened\n" if event else "")
    (pack_dir / "journeys" / "uses.yaml").write_text(f"name: uses\nsteps:\n{steps}")


def test_hook_scripts_in_the_pack_must_be_there_and_executable(pack_dir):
    (pack_dir / "hooks" / "plain.sh").write_text("#!/bin/sh\n")  # not executable
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text().replace("hooks:\n", "hooks:\n  gone: [hooks/gone.sh]\n  plain: [hooks/plain.sh]\n"
                                       "  py: [python3, hooks/any.py]\n  abs: [/opt/team-tools/not-on-this-mac.sh]\n"
                                       "  unused: [hooks/never-written.sh]\n")
                 + "events:\n  check: [hooks/check.sh]\n")
    _uses(pack_dir, ["gone", "plain", "py", "abs"], event=True)
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "pack.yaml: hook gone: hooks/gone.sh is not in the pack" in errs
    assert ("pack.yaml: hook plain: hooks/plain.sh is not executable (chmod +x hooks/plain.sh, or name its "
            "interpreter first, like [sh, hooks/plain.sh])") in errs
    assert "pack.yaml: events.check: hooks/check.sh is not in the pack" in errs
    assert "hook py" not in errs and "hook abs" not in errs and "hook note" not in errs
    assert "unused" not in errs  # declared, never called: it never stops a run
    _uses(pack_dir, ["plain"])
    assert "events.check" not in "\n".join(Pack.load(pack_dir).errors)  # no event: step, no check


def test_hook_script_paths_like_the_runner_finds_them(pack_dir):
    h = pack_dir / "hooks"
    (h / "ok.sh").write_text("#!/bin/sh\n")
    (h / "ok.sh").chmod(0o755)
    (pack_dir / "run.sh").write_text("#!/bin/sh\n")  # bare name, a file in the pack: the runner runs it
    (h / "dir").mkdir()
    (pack_dir / "node").mkdir()  # a bare name that is a folder: the runner runs the program on PATH
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text().replace("hooks:\n", "hooks:\n  ok: [./hooks/ok.sh]\n  bare: [run.sh]\n"
                                       "  folder: [hooks/dir]\n  home: [~/tools/x.sh]\n  var: [$FLAGS_TOOL/x.sh]\n"
                                       "  prog: [node, hooks/x.js]\n")
                 + "events:\n  check: [hooks/ok.sh]\n")
    _uses(pack_dir, ["ok", "bare", "folder", "home", "var", "prog"], event=True)
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "hook ok" not in errs and "events.check" not in errs and "hook prog" not in errs
    assert "hook bare: run.sh is not executable" in errs
    assert "hook folder: hooks/dir is a folder, not a script" in errs
    assert "hook home: ~/tools/x.sh is not in the pack (no ~ or $NAME here" in errs
    assert "hook var: $FLAGS_TOOL/x.sh is not in the pack (no ~ or $NAME here" in errs


def test_a_bad_hook_entry_is_an_error_not_a_crash(pack_dir):
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text().replace("hooks:\n", "hooks:\n  empty: []\n  num: [5]\n"))
    _uses(pack_dir, ["empty", "num"])
    errs = "\n".join(Pack.load(pack_dir).errors)
    assert "hook empty is a command list like [hooks/empty.sh], not []" in errs
    assert "hook num is a command list like [hooks/num.sh], not [5]" in errs


def test_each_use_counts_and_a_draft_only_use_stops_only_that_draft(pack_dir):
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text().replace("hooks:\n", "hooks:\n  s: [hooks/s.sh]\n  a: [hooks/a.sh]\n  d: [hooks/d.sh]\n"))
    (pack_dir / "sets.yaml").write_text((pack_dir / "sets.yaml").read_text() + "  flagged: {tags: [smoke], before: [s]}\n")
    (pack_dir / "journeys" / "uses.yaml").write_text("name: uses\nafter: [a]\nsteps:\n  - expect: SETTINGS-HOME\n")
    (pack_dir / "drafts").mkdir()
    (pack_dir / "drafts" / "u.yaml").write_text("name: u\nsteps:\n  - hook: d\n")
    pack = Pack.load(pack_dir)
    errs = "\n".join(pack.errors)
    assert "pack.yaml: hook s: hooks/s.sh is not in the pack" in errs  # a set's before:
    assert "pack.yaml: hook a: hooks/a.sh is not in the pack" in errs  # a journey's after:
    assert f"{pack_dir / 'drafts' / 'u.yaml'}: hook d: hooks/d.sh is not in the pack" in errs
    assert not any("hook d" in e for e in pack.run_errors())  # an unfinished draft never stops a set
    assert any("hook d" in e for e in pack.run_errors("u"))   # but it stops that draft's own run
