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


def test_frameworks_without_a_driver_yet_fall_back_to_uiautomator_and_say_so(tmp_path):
    proj = _project(tmp_path, {"e2e/wdio.conf.ts": "export const config = {}",
                               "build/ignored/wdio.conf.js": "", "app/build.gradle": "applicationId 'com.a'"})
    found, app = scan(proj)
    assert [f.framework for f in found] == ["appium"] and app == "com.a"
    driver, why = pick(found)
    assert driver == "uiautomator" and "appium (e2e/wdio.conf.ts)" in why
    assert pick([])[0] == "uiautomator"


def test_init_writes_a_valid_empty_pack(tmp_path, capsys):
    proj = _project(tmp_path, {"flows/home.yml": "appId: com.shop\n---\n- tapOn: Buy\n",
                               "app/build.gradle": "applicationId \"com.shop\""})
    dest = tmp_path / "shop-pack"
    assert main(["--pack", str(dest), "init", "--project", str(proj)]) == 0
    pack = Pack.load(dest)
    assert (pack.app, pack.driver, pack.errors, pack.project) == ("com.shop", "maestro", [], proj.resolve())
    assert "flows/home.yml" in capsys.readouterr().out
    assert main(["--pack", str(dest), "init", "--app", "x"]) == 2  # never overwrites a pack
