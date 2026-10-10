import itertools
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



NO_POSTER = "String.IsEmpty(Window.Property(CW.Poster))"


def _poster_box(control: ET.Element) -> tuple[str | None, ...]:
    return tuple(control.findtext(key) for key in ("left", "top", "width", "height"))


def test_a_missing_poster_shows_brand_tinted_corners():
    """One neutral white fade, flipped into each corner and tinted in the window, so the
    colours live in the XML with the rest of the palette rather than in an image."""
    glows = [c for c in _root().iter("control") if c.findtext("texture") == "crosswatch/glow.png"]
    assert len(glows) == 4
    corners = set()
    for glow in glows:
        texture = glow.find("texture")
        assert texture is not None
        assert glow.findtext("visible") == NO_POSTER
        assert texture.get("diffuse") == "crosswatch/mask_poster.png"
        assert glow.findtext("colordiffuse")
        assert _poster_box(glow) == ("50", "60", "300", "450")
        corners.add((texture.get("flipx"), texture.get("flipy")))
    assert corners == {(None, None), ("true", None), (None, "true"), ("true", "true")}


def test_a_missing_poster_says_so_in_translatable_text():
    from resources.lib.constants import WHO_WATCHED_NO_POSTER

    labels = [
        c for c in _root().iter("control") if c.findtext("label") == f"$ADDON[{ADDON_ID} {WHO_WATCHED_NO_POSTER}]"
    ]
    assert len(labels) == 1
    assert labels[0].findtext("visible") == NO_POSTER
    assert _poster_box(labels[0]) == ("50", "60", "300", "450")

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


def test_the_close_label_comes_from_a_property_so_a_pick_list_can_say_cancel():
    root = ET.parse(LIST).getroot()
    close = root.find(".//control[@id='21']")
    assert close is not None and close.findtext("label") == "$INFO[Window.Property(CW.Close)]"


PICK = "String.IsEqual(Window.Property(CW.Pick),true)"


def test_a_pick_list_has_an_accent_done_button_where_the_bulk_button_sits():
    root = ET.parse(LIST).getroot()
    done = root.find(".//control[@id='22']")
    bulk = root.find(".//control[@id='20']")
    assert done is not None and bulk is not None
    assert done.findtext("visible") == PICK
    assert done.findtext("label") == f"$ADDON[{ADDON_ID} {WHO_WATCHED_DONE}]"
    assert done.findtext("texturenofocus") == "crosswatch/box_accent.png"
    assert done.findtext("texturefocus") == "crosswatch/box_accent_focus.png"
    assert (done.findtext("left"), done.findtext("top")) == (bulk.findtext("left"), bulk.findtext("top"))
    assert done.findtext("onup") == "100" and done.findtext("onright") == "21"


def test_down_from_a_pick_list_reaches_done_first():
    root = ET.parse(LIST).getroot()
    rows = root.find(".//control[@id='100']")
    close = root.find(".//control[@id='21']")
    assert rows is not None and close is not None
    assert _navigation(rows, "ondown")[0] == (PICK, "22")
    assert _navigation(close, "onleft")[0] == (PICK, "22")


def test_pick_rows_show_a_tick_and_hide_the_thumbnail():
    root = ET.parse(LIST).getroot()
    for layout in ("itemlayout", "focusedlayout"):
        found = root.find(f".//control[@id='100']/{layout}")
        assert found is not None
        images = found.findall("control[@type='image']")
        ticks = [i for i in images if i.findtext("texture") == "crosswatch/tick.png"]
        assert len(ticks) == 1 and ticks[0].findtext("visible") == "String.IsEqual(ListItem.Property(chosen),true)"
        assert (ticks[0].findtext("width"), ticks[0].findtext("height")) == ("48", "48")
        thumbs = [i for i in images if "thumb" in (i.findtext("texture") or "") or "poster_empty" in (i.findtext("texture") or "")]
        assert thumbs and all(i.findtext("visible") == f"!{PICK}" for i in thumbs)
        chosen = [i for i in images if (i.findtext("texture") or "").startswith("crosswatch/box_chosen")]
        assert chosen


def test_pick_row_columns_leave_room_for_long_playlist_names_without_overlapping():
    """Real playlist names run long ("EasyTV - TVShow - Season Premieres"), and a row also
    carries the Also line, the tag and the tick."""
    root = ET.parse(LIST).getroot()
    for layout in ("itemlayout", "focusedlayout"):
        found = root.find(f".//control[@id='100']/{layout}")
        assert found is not None
        spans = {}
        for control in found.findall("control"):
            visible = control.findtext("visible") or ""
            label = control.findtext("label") or control.findtext("texture") or ""
            if visible == PICK or "tick.png" in label:
                left, width = int(control.findtext("left") or 0), int(control.findtext("width") or 0)
                spans[label] = (left, left + width)
        title = spans["$INFO[ListItem.Label]"]
        assert title[1] - title[0] >= 600
        ordered = sorted(spans.values())
        assert len(ordered) == 4  # title, detail, tag, tick
        assert all(a[1] <= b[0] for a, b in itertools.pairwise(ordered)), ordered


VIEWER = SKIN / "1080i" / "crosswatch-viewer.xml"


def test_the_viewer_window_buttons_sit_in_one_row_starting_on_playlists():
    from resources.lib.constants import LABEL_BACK, VIEWER_PLAYLISTS, VIEWER_PROFILES, VIEWER_REMOVE, VIEWER_RENAME

    root = ET.parse(VIEWER).getroot()
    assert root.findtext("defaultcontrol") == "10"
    row = root.find(".//control[@type='grouplist']")
    assert row is not None and row.findtext("orientation") == "horizontal"
    buttons = row.findall("control")
    assert [b.get("id") for b in buttons] == ["10", "11", "12", "13", "14"]
    labels = [VIEWER_PLAYLISTS, VIEWER_PROFILES, VIEWER_RENAME, VIEWER_REMOVE, LABEL_BACK]
    assert [b.findtext("label") for b in buttons] == [f"$ADDON[{ADDON_ID} {i}]" for i in labels]
    VIEWER.read_text(encoding="ascii")
