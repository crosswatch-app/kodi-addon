from resources.lib import status
from resources.lib.config import KEY_STATUS
from resources.lib.constants import STATUS_LINKED, STATUS_NOT_PAIRED, STATUS_PAIRED
from tests.fakes import FakeKodi


def test_fill_replaces_each_placeholder_in_order():
    assert status.fill("Paired with %s at %s", "Living room", "http://nas:8787") == "Paired with Living room at http://nas:8787"


def test_address_of_keeps_scheme_and_host_only():
    assert status.address_of("http://user:pw@nas:8787/webhook/kodiwatcher?x=1") == "http://nas:8787"


def test_address_of_returns_an_unparseable_url_unchanged():
    assert status.address_of("http://[::1/hook") == "http://[::1/hook"


def test_the_three_lines_use_their_strings():
    kodi = FakeKodi()
    assert status.paired(kodi, "Living room", "http://nas/webhook/kodiwatcher") == f"#{STATUS_PAIRED}"
    assert status.linked(kodi, "http://nas/webhook/kodiwatcher") == f"#{STATUS_LINKED}"
    assert status.not_paired(kodi) == f"#{STATUS_NOT_PAIRED}"


def test_write_skips_an_unchanged_line():
    kodi = FakeKodi(settings={KEY_STATUS: "Not paired"})
    assert status.write(kodi, "Not paired") is False
    assert status.write(kodi, "Paired") is True
    assert kodi.writes == [(KEY_STATUS, "Paired")]
