from journeys.cli import main
from journeys.detect import pick, scan
from journeys.pack import Pack


def _project(tmp_path, files):
    for rel, text in files.items():
        f = tmp_path / "proj" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return tmp_path / "proj"


def test_maestro_flows_pick_maestro_and_the_app_id_comes_from_gradle(tmp_path):
    proj = _project(tmp_path, {".maestro/login.yaml": "appId: com.shop\n---\n- launchApp\n",
                               "app/build.gradle.kts": 'android { defaultConfig { applicationId = "com.shop" } }\n'
                                                       'dependencies { androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1") }'})
    found, app = scan(proj)
    assert [f.framework for f in found] == ["maestro", "espresso"] and app == "com.shop"
    assert pick(found)[0] == "maestro"


def test_frameworks_without_a_driver_need_an_explicit_driver(tmp_path):
    proj = _project(tmp_path, {"e2e/wdio.conf.ts": "export const config = {}",
                               "build/ignored/wdio.conf.js": "", "app/build.gradle": "applicationId 'com.a'"})
    found, app = scan(proj)
    assert [f.framework for f in found] == ["appium"] and app == "com.a"
    driver, why = pick(found)
    assert driver is None and "appium (e2e/wdio.conf.ts)" in why and "no driver" in why
    assert pick([])[0] is None
    import pytest
    from journeys.detect import init_pack
    with pytest.raises(ValueError, match="pass --driver maestro | speedrun"):
        init_pack(tmp_path / "p", proj, "p", None)
    assert "chosen with --driver" in init_pack(tmp_path / "p", proj, "p", None, "maestro")[1]


def test_init_writes_a_valid_empty_pack(tmp_path, capsys):
    proj = _project(tmp_path, {"flows/home.yml": "appId: com.shop\n---\n- tapOn: Buy\n",
                               "app/build.gradle": "applicationId \"com.shop\""})
    dest = tmp_path / "shop-pack"
    assert main(["--pack", str(dest), "init", "--project", str(proj)]) == 0
    pack = Pack.load(dest)
    assert (pack.app, pack.driver, pack.errors, pack.project) == ("com.shop", "maestro", [], proj.resolve())
    assert "flows/home.yml" in capsys.readouterr().out
    assert main(["--pack", str(dest), "init", "--app", "x"]) == 2  # never overwrites a pack


def test_init_quotes_odd_names_and_doctor_reports_a_missing_project(tmp_path, capsys):
    dest = tmp_path / "p"
    assert main(["--pack", str(dest), "init", "--app", "com.a", "--name", "shop: v2 #1", "--driver", "maestro"]) == 0
    assert Pack.load(dest).name == "shop: v2 #1"
    assert main(["--pack", str(tmp_path / "q"), "init", "--project", str(tmp_path / "nope"), "--app", "a"]) == 2
    f = dest / "pack.yaml"
    f.write_text(f.read_text() + "project: ../missing\n")
    capsys.readouterr()
    main(["--pack", str(dest), "doctor"])
    assert "project folder" in capsys.readouterr().out


def test_scan_skips_broken_links(tmp_path):
    proj = _project(tmp_path, {"app/build.gradle": "applicationId 'com.a'"})
    (proj / "x.yaml").symlink_to(proj / "gone.yaml")
    (proj / "build.gradle.kts").symlink_to(proj / "gone.kts")
    assert scan(proj) == ([], "com.a")


def test_several_app_modules_need_app(tmp_path):
    import pytest
    from journeys.detect import init_pack
    proj = _project(tmp_path, {"a/build.gradle.kts": 'plugins { id("com.android.application") }\napplicationId = "com.acme.one"',
                               "b/build.gradle.kts": 'plugins { id("com.android.application") }\napplicationId = "com.acme.two"',
                               "macrobenchmark/build.gradle.kts": 'applicationId = "com.acme.bench"'})
    assert scan(proj) == ([], None)
    with pytest.raises(ValueError, match="several app ids.*com.acme.one.*com.acme.two"):
        init_pack(tmp_path / "p", proj, "p", None)
    init_pack(tmp_path / "q", proj, "q", "com.acme.two", "speedrun")


def test_an_app_module_with_a_benchmark_build_type_still_counts(tmp_path):
    proj = _project(tmp_path, {"app/build.gradle.kts": 'plugins { id("com.android.application") }\n'
                                                       'applicationId = "com.example.shop"\n'
                                                       'buildTypes { create("benchmark") { } }',
                               "bench/build.gradle.kts": 'plugins { id("com.android.test"); id("androidx.benchmark") }\n'
                                                         'applicationId = "com.example.bench"'})
    assert scan(proj, strict=True)[1] == "com.example.shop"
