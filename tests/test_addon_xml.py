import re
from pathlib import Path
from xml.etree import ElementTree

from resources.lib.constants import ADDON_ID

ADDON_XML = Path(__file__).resolve().parents[1] / "addon.xml"


def _root():
    return ElementTree.parse(ADDON_XML).getroot()


def test_addon_xml_id_matches_constants():
    assert _root().get("id") == ADDON_ID


def test_addon_declares_a_service_entry_point():
    points = {e.get("point"): e for e in _root().findall("extension")}
    assert points["xbmc.service"].get("library") == "service.py"


def test_addon_declares_a_script_entry_point_at_the_addon_root():
    points = {e.get("point"): e for e in _root().findall("extension")}
    assert points["xbmc.python.script"].get("library") == "configure.py"


def test_addon_requires_the_kodi_21_python_api():
    imports = {i.get("addon"): i.get("version") for i in _root().findall(".//import")}
    assert imports["xbmc.python"] == "3.0.1"


CHANGELOG = ADDON_XML.parent / "changelog.txt"
# v1.0.0, v1.0.0~beta1; the date is filled in when the version is released.
HEADER = re.compile(r"^v(\d+\.\d+\.\d+(?:~(?:alpha|beta|rc)\d+)?) \((\d{4}-\d{2}-\d{2}|unreleased)\)$")


def _changelog_versions() -> list[str]:
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    return [m.group(1) for line in lines if (m := HEADER.match(line))]


def test_every_changelog_entry_header_has_the_release_format():
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    headers = [line for line in lines if line.startswith("v")]
    assert headers
    for line in headers:
        assert HEADER.match(line), line


def test_the_addon_version_is_the_newest_changelog_entry():
    """Kodi reads the tilde form; tags and GitHub use a hyphen."""
    assert _root().get("version") == _changelog_versions()[0]


def test_news_fits_kodis_metadata_schema():
    """The schema caps <news> at 1500 characters, and the checker fails the build past it."""
    news = _root().find("extension/news")
    assert news is None or len(news.text or "") <= 1500
