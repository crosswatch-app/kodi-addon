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
