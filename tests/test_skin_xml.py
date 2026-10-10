import re
import xml.etree.ElementTree as ET
from pathlib import Path

from resources.lib.constants import ADDON_ID, WHO_WATCHED_DONE, WHO_WATCHED_SKIP

SKIN = Path(__file__).resolve().parents[1] / "resources" / "skins" / "Default"
WHO = SKIN / "1080i" / "crosswatch-who.xml"
ANCHORS = {"font40_title", "font32_title", "font30_title", "font25_title", "font13", "font12"}


def _root() -> ET.Element:
    return ET.parse(WHO).getroot()


def _control(control_id: str) -> ET.Element:
    found = _root().find(f".//control[@id='{control_id}']")
    assert found is not None, f"control {control_id} missing"
    return found


def test_the_window_is_ascii_only():
    """Not every skin font has every glyph; symbols are images instead."""
    WHO.read_text(encoding="ascii")


def test_the_window_uses_only_anchor_fonts():
    fonts = {font.text for font in _root().iter("font")}
    assert fonts and fonts <= ANCHORS


def test_every_texture_ships():
    texts = [t.text or "" for t in _root().iter() if t.tag.startswith("texture")]
    texts += [t.get("diffuse") or "" for t in _root().iter("texture")]
    files = {t for t in texts if t.startswith("crosswatch/")}
    assert files
    for name in files:
        assert (SKIN / "media" / name).is_file(), name


def test_focus_starts_on_the_viewer_list():
    default = _root().find("defaultcontrol")
    assert default is not None and default.text == "200"


def test_down_from_the_list_reaches_done_and_both_buttons_return():
    assert _control("200").findtext("ondown") == "20"
    assert _control("20").findtext("onup") == "200"
    assert _control("21").findtext("onup") == "200"
    assert _control("20").findtext("onright") == "21"
    assert _control("21").findtext("onleft") == "20"


def test_button_labels_come_from_strings_po():
    assert _control("20").findtext("label") == f"$ADDON[{ADDON_ID} {WHO_WATCHED_DONE}]"
    assert _control("21").findtext("label") == f"$ADDON[{ADDON_ID} {WHO_WATCHED_SKIP}]"


def test_no_include_or_named_colour_is_used():
    """Kodi resolves both from the active skin only (GUIWindow.cpp, GUIColorManager.cpp)."""
    text = WHO.read_text(encoding="ascii")
    assert "<include" not in text
    for colour in re.findall(r"<(?:textcolor|focusedcolor)>([^<]*)<", text):
        assert re.fullmatch(r"[0-9A-F]{8}", colour), colour


def test_the_viewer_list_shows_when_there_are_more_rows():
    """The list holds Everyone and three names; a fourth viewer is already out of sight."""
    page = _control("200").findtext("pagecontrol")
    assert page is not None
    scrollbar = _control(page)
    assert scrollbar.get("type") == "scrollbar"
    assert scrollbar.findtext("showonepage") == "false"


CONFIRM = SKIN / "1080i" / "crosswatch-confirm.xml"


def test_confirm_starts_on_no_and_labels_come_from_strings_po():
    from resources.lib.constants import WINDOW_NO, WINDOW_YES

    root = ET.parse(CONFIRM).getroot()
    assert root.findtext("defaultcontrol") == "11"
    yes = root.find(".//control[@id='10']")
    no = root.find(".//control[@id='11']")
    assert yes is not None and no is not None
    assert yes.findtext("label") == f"$ADDON[{ADDON_ID} {WINDOW_YES}]"
    assert no.findtext("label") == f"$ADDON[{ADDON_ID} {WINDOW_NO}]"
    CONFIRM.read_text(encoding="ascii")
