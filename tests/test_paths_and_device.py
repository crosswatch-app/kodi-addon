import json

from resources.lib import paths
from resources.lib.config import Settings
from resources.lib.device import device_identity
from tests.fakes import FakeKodi


def test_paths_all_hang_off_one_profile_directory():
    kodi = FakeKodi(root="/kodi")
    base = paths.profile_dir(kodi)
    every = (
        paths.viewers_path(kodi),
        paths.prompts_path(kodi),
        paths.device_path(kodi),
        paths.outbox_path(kodi),
        paths.log_dir(kodi),
    )
    for path in every:
        assert path.startswith(base)


def test_a_configured_device_id_wins(tmp_path):
    settings = Settings(device_id="htpc-lounge", device_name="Lounge")
    got = device_identity(FakeKodi(), settings, str(tmp_path / "device.json"))
    assert got.id == "htpc-lounge"


def test_an_unset_device_id_is_generated_and_persisted(tmp_path):
    path = str(tmp_path / "device.json")
    first = device_identity(FakeKodi(), Settings(device_name="Lounge"), path)
    second = device_identity(FakeKodi(), Settings(device_name="Lounge"), path)
    assert first.id == second.id
    assert len(first.id) >= 16
    assert json.loads(open(path, encoding="utf-8").read())["device_id"] == first.id


def test_two_installs_do_not_share_an_id(tmp_path):
    one = device_identity(FakeKodi(), Settings(), str(tmp_path / "a.json"))
    two = device_identity(FakeKodi(), Settings(), str(tmp_path / "b.json"))
    assert one.id != two.id


def test_the_device_name_comes_from_settings(tmp_path):
    got = device_identity(FakeKodi(), Settings(device_name="Living room"), str(tmp_path / "device.json"))
    assert got.name == "Living room"
