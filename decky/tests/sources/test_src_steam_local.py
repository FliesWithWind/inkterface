import os

import pytest
import sourcefs as F

from inkterface_core.sources.steam_local import (ID64_BASE, SteamLocal, box_art_path, find_steam_dir, parse_appid)

ID64 = str(ID64_BASE + 1234)  # account id 1234


def localconfig(apps: dict) -> str:
    body = "".join(f'"{a}" {{ "Playtime" "{p}" "Playtime2wks" "{w}" "LastPlayed" "{lp}" }}\n' for a, (p, w, lp) in apps.items())
    return '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" {\n' + body + '"7" { "cloud" { "x" "y" } }\n} } } } }'


@pytest.fixture
def steam(tmp_path):
    root = tmp_path / "Steam"
    F.w(root, "config/loginusers.vdf", f'"users" {{ "{ID64}" {{ "PersonaName" "Tester" "MostRecent" "1" "timestamp" "5" }} '
                                       f'"76561198999999999" {{ "PersonaName" "Other" "MostRecent" "0" "timestamp" "9" }} }}')
    F.w(root, "userdata/1234/config/localconfig.vdf", localconfig({
        220: (600, 0, 100), 730: (1500, 30, 300), 892970: (900, 5, 200), 440: (60, 30, 250), 10: (0, 0, 1)}))
    F.w(root, "steamapps/appmanifest_730.acf", '"AppState" { "appid" "730" "name" "Counter-Strike 2" }')
    F.w(root, "steamapps/appmanifest_440.acf", '"AppState" { "appid" "440" "name" "Team Fortress 2" }')
    (root / "appcache/librarycache").mkdir(parents=True)
    return root


def mk(root, clock=lambda: 1000.0, **kw):
    return SteamLocal(steam_dir=str(root), clock=clock, **kw)


def test_idle_user_and_recent(steam):
    r = mk(steam, steamid64=ID64).poll()
    assert r["steam.now_playing"] is None
    assert r["steam.user"] == {"persona": "Tester", "level": None, "games_owned": None, "hours_total": round(3060 / 60, 1)}
    # sorted by 2-week minutes desc, ties by last played: 730 and 440 both 30 -> 730 (last 300) first
    assert [x["appid"] for x in r["steam.recent"]] == [730, 440, 892970]
    assert r["steam.recent"][0] == {"appid": 730, "name": "Counter-Strike 2", "minutes_2w": 30, "minutes_total": 1500}
    assert r["steam.recent"][2]["name"] is None  # not installed: no manifest


def test_recent_limit(steam):
    assert len(mk(steam, recent_limit=1).poll()["steam.recent"]) == 1


def test_now_playing_enriched(steam):
    t = [1000.0]
    s = mk(steam, clock=lambda: t[0])
    art = steam / "appcache/librarycache/730_library_600x900.jpg"
    art.write_bytes(b"x")
    s.set_now_playing("730")
    t[0] += 125
    np = s.poll()["steam.now_playing"]
    assert np == {"appid": 730, "name": "Counter-Strike 2", "box_art_path": str(art), "session_minutes": 2,
                  "total_minutes": 1500, "last_achievement": None}
    s.set_now_playing(730)  # same game again must not restart the session timer
    t[0] += 60
    assert s.poll()["steam.now_playing"]["session_minutes"] == 3
    s.set_now_playing(None)
    assert s.poll()["steam.now_playing"] is None


def test_now_playing_unknown_app(steam):
    s = mk(steam)
    s.set_now_playing(999999)
    np = s.poll()["steam.now_playing"]
    assert np["name"] is None and np["box_art_path"] is None and np["total_minutes"] is None


def test_set_now_playing_new_game_resets_timer(steam):
    t = [0.0]
    s = mk(steam, clock=lambda: t[0])
    s.set_now_playing(730)
    t[0] = 600
    s.set_now_playing(440)
    t[0] = 660
    assert s.poll()["steam.now_playing"]["session_minutes"] == 1


@pytest.mark.parametrize("bad", ["", "abc", "-5", "0", "../../etc", "12 3", 1.5, True, [1], "٣"])
def test_invalid_appid_rejected(steam, bad):
    s = mk(steam)
    with pytest.raises(ValueError):
        s.set_now_playing(bad)
    assert s.poll()["steam.now_playing"] is None


def test_parse_appid():
    assert parse_appid(None) is None and parse_appid(" 12 ") == 12 and parse_appid(12) == 12


def test_steam_missing_is_a_warning_not_an_error(tmp_path):
    s = SteamLocal(steam_dir=str(tmp_path / "nowhere"))
    assert s.poll() == {"steam.now_playing": None, "steam.user": None, "steam.recent": None}
    assert "not found" in s.warning
    s.set_now_playing(5)
    np = s.poll()["steam.now_playing"]
    assert np["appid"] == 5 and np["name"] is None


def test_default_dirs_via_home(tmp_path):
    home = tmp_path / "h"
    (home / ".local/share/Steam/steamapps").mkdir(parents=True)
    assert find_steam_dir(str(home)) == str(home / ".local/share/Steam")
    assert find_steam_dir(str(tmp_path / "empty")) is None
    assert find_steam_dir(None, str(home / ".local/share/Steam"))


def test_localconfig_of_most_recent_login_beats_newer_files(steam):
    F.w(steam, "userdata/77/config/localconfig.vdf", localconfig({1: (5, 0, 1)}))  # newer mtime, other account
    assert mk(steam).poll()["steam.user"]["hours_total"] == round(3060 / 60, 1)


def test_without_any_login_info_newest_localconfig_wins(tmp_path):
    root = tmp_path / "S"
    F.w(root, "userdata/1/config/localconfig.vdf", localconfig({1: (60, 0, 1)}))
    F.w(root, "userdata/2/config/localconfig.vdf", localconfig({2: (120, 0, 1)}))
    os.utime(root / "userdata/1/config/localconfig.vdf", (1, 1))
    assert SteamLocal(steam_dir=str(root)).poll()["steam.user"]["hours_total"] == 2.0


def test_localconfig_glob_fallback_when_id_dir_missing(tmp_path):
    root = tmp_path / "S"
    F.w(root, "userdata/55/config/localconfig.vdf", localconfig({9: (120, 10, 5)}))
    r = SteamLocal(steam_dir=str(root), steamid64="7656119800000").poll()
    assert r["steam.user"]["hours_total"] == 2.0 and r["steam.recent"][0]["appid"] == 9


def test_no_localconfig(tmp_path):
    root = tmp_path / "S"
    (root / "steamapps").mkdir(parents=True)
    r = SteamLocal(steam_dir=str(root)).poll()
    assert r["steam.user"] == {"persona": None, "level": None, "games_owned": None, "hours_total": None}
    assert r["steam.recent"] == []


def test_loginusers_without_mostrecent_uses_latest_timestamp(tmp_path):
    root = tmp_path / "S"
    F.w(root, "config/loginusers.vdf", '"users" { "1" { "PersonaName" "Old" "timestamp" "5" } "2" { "PersonaName" "New" "timestamp" "9" } "3" "junk" }')
    assert SteamLocal(steam_dir=str(root)).poll()["steam.user"]["persona"] == "New"


def test_extra_library_folder_new_and_old_format(steam, tmp_path):
    lib = tmp_path / "SD"
    F.w(lib, "steamapps/appmanifest_892970.acf", '"AppState" { "name" "Valheim" }')
    F.w(steam, "steamapps/libraryfolders.vdf", f'"libraryfolders" {{ "0" {{ "path" "{steam}" }} "1" {{ "path" "{lib}" }} }}')
    assert mk(steam).poll()["steam.recent"][2]["name"] == "Valheim"


def test_old_format_library_folders(steam, tmp_path):
    lib = tmp_path / "SD2"
    F.w(lib, "steamapps/appmanifest_892970.acf", '"AppState" { "name" "Valheim" }')
    F.w(steam, "config/libraryfolders.vdf", f'"libraryfolders" {{ "TimeNextStatsReport" "123" "1" "{lib}" "2" {{ "nopath" "x" }} }}')
    assert mk(steam).poll()["steam.recent"][2]["name"] == "Valheim"


def test_libraryfolders_wrong_shape(steam):
    F.w(steam, "steamapps/libraryfolders.vdf", '"libraryfolders" "oops"')
    assert mk(steam).poll()["steam.recent"][0]["appid"] == 730


def test_corrupt_vdf_keeps_previous_parse_and_warns(steam):
    s = mk(steam)
    assert s.poll()["steam.user"]["hours_total"] == 51.0
    lc = steam / "userdata/1234/config/localconfig.vdf"
    lc.write_text('"UserLocalConfigStore" { "Software" {')  # half-written by Steam
    os.utime(lc, (os.stat(lc).st_atime, os.stat(lc).st_mtime + 5))
    r = s.poll()
    assert r["steam.user"]["hours_total"] == 51.0 and "localconfig.vdf" in s.warning
    lc.write_text(localconfig({1: (60, 0, 1)}))
    os.utime(lc, (os.stat(lc).st_atime, os.stat(lc).st_mtime + 10))
    r = s.poll()
    assert r["steam.user"]["hours_total"] == 1.0 and s.warning is None


def test_corrupt_vdf_without_previous_parse(steam):
    (steam / "userdata/1234/config/localconfig.vdf").write_text("}}}")
    r = mk(steam).poll()
    assert r["steam.recent"] == [] and r["steam.user"]["hours_total"] is None


def test_deeply_nested_vdf_does_not_crash(steam):
    (steam / "userdata/1234/config/localconfig.vdf").write_text('"a" {' * 5000 + "}" * 5000)
    assert mk(steam).poll()["steam.recent"] == []


def test_vdf_cached_by_mtime(steam, monkeypatch):
    from inkterface_core.sources import steam_local as m
    calls = []
    real = m.parse_vdf
    monkeypatch.setattr(m, "parse_vdf", lambda t: calls.append(1) or real(t))
    s = mk(steam)
    s.poll()
    n = len(calls)
    s.poll()
    assert len(calls) == n  # nothing re-read while mtimes are unchanged


def test_localconfig_ignores_non_numeric_and_non_dict_apps(tmp_path):
    root = tmp_path / "S"
    F.w(root, "userdata/1/config/localconfig.vdf",
        '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "x" { "Playtime" "5" } "3" "str" "4" { "Playtime" "bad" "Playtime2wks" "2" } } } } } }')
    r = SteamLocal(steam_dir=str(root)).poll()
    assert r["steam.recent"] == [{"appid": 4, "name": None, "minutes_2w": 2, "minutes_total": 0}]


# -- box art -------------------------------------------------------------------------------------

def test_box_art_flat_layout(tmp_path):
    F.w(tmp_path, "appcache/librarycache/730_library_600x900.jpg", "x")
    F.w(tmp_path, "appcache/librarycache/730_header.jpg", "x")
    assert box_art_path(str(tmp_path), 730).endswith("730_library_600x900.jpg")


def test_box_art_subfolder_layout(tmp_path):
    F.w(tmp_path, "appcache/librarycache/730/library_600x900.jpg", "x")
    assert box_art_path(str(tmp_path), 730).endswith("730/library_600x900.jpg")


def test_box_art_hashed_subfolder_layout(tmp_path):
    F.w(tmp_path, "appcache/librarycache/730/abcdef0123/library_600x900.jpg", "x")
    F.w(tmp_path, "appcache/librarycache/730/zzz/library_header.jpg", "x")
    assert box_art_path(str(tmp_path), 730).endswith("730/abcdef0123/library_600x900.jpg")


def test_box_art_prefers_flat_over_subfolder(tmp_path):
    F.w(tmp_path, "appcache/librarycache/5_library_600x900.jpg", "x")
    F.w(tmp_path, "appcache/librarycache/5/library_600x900.jpg", "x")
    assert box_art_path(str(tmp_path), 5).endswith("5_library_600x900.jpg")


def test_box_art_missing(tmp_path):
    F.w(tmp_path, "appcache/librarycache/730/library_header.jpg", "x")  # header only, not box art
    assert box_art_path(str(tmp_path), 730) is None
    assert box_art_path(str(tmp_path / "none"), 730) is None


def test_box_art_dir_named_like_file_is_ignored(tmp_path):
    (tmp_path / "appcache/librarycache/9_library_600x900.jpg").mkdir(parents=True)
    assert box_art_path(str(tmp_path), 9) is None


def test_box_art_falls_back_to_capsule(tmp_path):
    """Regression (found on a real Steam install): Valheim has only librarycache/892970/<hash>/library_capsule.jpg."""
    F.w(tmp_path, "appcache/librarycache/892970/3dee/library_capsule.jpg", "x")
    F.w(tmp_path, "appcache/librarycache/892970/df65/library_header.jpg", "x")
    assert box_art_path(str(tmp_path), 892970).endswith("892970/3dee/library_capsule.jpg")


def test_box_art_prefers_600x900_over_capsule_in_any_layout(tmp_path):
    F.w(tmp_path, "appcache/librarycache/7/aaaa/library_capsule.jpg", "x")
    F.w(tmp_path, "appcache/librarycache/7/bbbb/library_600x900.jpg", "x")
    assert box_art_path(str(tmp_path), 7).endswith("7/bbbb/library_600x900.jpg")
    F.w(tmp_path, "appcache/librarycache/8_library_capsule.jpg", "x")
    F.w(tmp_path, "appcache/librarycache/8/cccc/library_600x900.jpg", "x")
    assert box_art_path(str(tmp_path), 8).endswith("8/cccc/library_600x900.jpg")
