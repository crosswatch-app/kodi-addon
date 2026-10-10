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


def test_the_window_opens_on_a_control_that_can_take_focus():
    """The viewer list is empty until Python fills it in onInit, so Kodi cannot focus it when
    the window opens and logs an error each time; Skip can, and onInit then moves focus to
    the list."""
    default = _root().find("defaultcontrol")
    assert default is not None and default.text == "21"


def test_the_buttons_sit_in_one_row_below_the_list():
    """A grouplist, so a hidden Forget leaves no gap and no dead end for left and right."""
    row = _root().find(".//control[@type='grouplist']")
    assert row is not None and row.findtext("orientation") == "horizontal"
    assert [c.get("id") for c in row.findall("control")] == ["20", "22", "21"]
    assert _control("200").findtext("ondown") == "20"
    for button in ("20", "21", "22"):
        assert _control(button).findtext("onup") == "200"


def test_forget_shows_only_when_offered_and_its_label_comes_from_strings_po():
    from resources.lib.constants import REMEMBERED_FORGET

    forget = _control("22")
    assert forget.findtext("visible") == "String.IsEqual(Window.Property(CW.OfferForget),true)"
    assert forget.findtext("label") == f"$ADDON[{ADDON_ID} {REMEMBERED_FORGET}]"


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


LIST = SKIN / "1080i" / "crosswatch-list.xml"


def test_the_list_window_navigates_between_search_rows_and_buttons():
    root = ET.parse(LIST).getroot()

    def control(control_id: str) -> ET.Element:
        found = root.find(f".//control[@id='{control_id}']")
        assert found is not None, control_id
        return found

    # Close, not the list: the list is empty until Python fills it, see the who-watched test.
    assert root.findtext("defaultcontrol") == "21"
    assert control("100").findtext("onup") == "30"
    assert control("30").get("type") == "edit" and control("30").findtext("ondown") == "100"
    assert control("30").findtext("onright") == "31" and control("31").findtext("onleft") == "30"
    assert control("20").findtext("onright") == "21"
    assert control("100").findtext("pagecontrol") == "101"
    LIST.read_text(encoding="ascii")


def _navigation(control: ET.Element, key: str) -> list[tuple[str | None, str]]:
    return [(nav.get("condition"), (nav.text or "").strip()) for nav in control.findall(key)]


def test_the_bulk_button_hides_and_down_reaches_close_when_nothing_is_shown():
    """Kodi takes the first navigation whose condition holds, so the unconditioned Close
    comes last; a hidden bulk button would otherwise swallow Down."""
    root = ET.parse(LIST).getroot()
    rows = root.find(".//control[@id='100']")
    bulk = root.find(".//control[@id='20']")
    close = root.find(".//control[@id='21']")
    assert rows is not None and bulk is not None and close is not None
    shown = "!String.IsEmpty(Window.Property(CW.Bulk))"
    assert bulk.findtext("visible") == shown
    assert _navigation(rows, "ondown")[-2:] == [(shown, "20"), (None, "21")]
    assert _navigation(close, "onleft")[-1] == (shown, "20")
