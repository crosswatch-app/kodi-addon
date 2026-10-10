import itertools
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from resources.lib.constants import ADDON_ID, WHO_WATCHED_DONE, WHO_WATCHED_SKIP

SKIN = Path(__file__).resolve().parents[1] / "resources" / "skins" / "Default"
WHO = SKIN / "1080i" / "crosswatch-who.xml"
ICONS = SKIN / "media" / "crosswatch" / "icons"
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




def test_the_icons_are_64_pixel_rgba_pngs():
    """White on transparent at 64 px, drawn at 32 and tinted in the XML."""
    names = sorted(p.name for p in ICONS.glob("*.png"))
    assert names == ["check_circle.png", "warning.png"]
    for name in names:
        head = (ICONS / name).read_bytes()[:26]
        assert head[:8] == b"\x89PNG\r\n\x1a\n"
        width, height, depth, colour = struct.unpack(">IIBB", head[16:26])
        assert (width, height, depth, colour) == (64, 64, 8, 6), name  # 6: RGBA


VIEWERS = SKIN / "1080i" / "crosswatch-viewers.xml"
NOT_EMPTY = "!String.IsEqual(Window.Property(CW.Empty),true)"


def _viewers_control(control_id: str) -> ET.Element:
    found = ET.parse(VIEWERS).getroot().find(f".//control[@id='{control_id}']")
    assert found is not None, control_id
    return found


def test_the_viewers_window_has_one_set_of_controls_per_panel_slot():
    from resources.lib.ui.viewers_window import PANEL_SLOTS

    root = ET.parse(VIEWERS).getroot()
    labels = [c.findtext("label") or "" for c in root.iter("control")]
    visible = [c.findtext("visible") or "" for c in root.iter("control")]
    for n in range(1, PANEL_SLOTS + 1):
        for prop in (f"slot{n}_head", f"slot{n}_tag"):
            assert labels.count(f"$INFO[Container(100).ListItem.Property({prop})]") == 1, prop
        assert visible.count(f"String.IsEqual(Container(100).ListItem.Property(slot{n}_warn),true)") == 1, n
    assert not any(f"Property(slot{PANEL_SLOTS + 1}" in text for text in labels + visible)


def test_the_viewers_panel_lines_never_wrap_and_end_above_the_route_line():
    root = ET.parse(VIEWERS).getroot()
    route = next(
        c for c in root.iter("control") if c.findtext("label") == "$INFO[Container(100).ListItem.Property(route_text)]"
    )
    route_top = int(route.findtext("top") or 0)
    for control in root.iter("control"):
        label = control.findtext("label") or ""
        if "Property(slot" in label:
            assert control.findtext("wrapmultiline") in (None, "false"), label
            assert int(control.findtext("top") or 0) + int(control.findtext("height") or 0) <= route_top, label
    assert route_top + int(route.findtext("height") or 0) <= int(_viewers_control("10").findtext("top") or 0)


def test_the_viewers_window_navigates_between_list_actions_and_bottom_row():
    expected = {
        "100": {"onright": "10", "ondown": "20"},
        "10": {"onleft": "100", "onright": "11", "ondown": "12"},
        "11": {"onleft": "10", "ondown": "13"},
        "12": {"onleft": "100", "onup": "10", "onright": "13", "ondown": "20"},
        "13": {"onleft": "12", "onup": "11", "ondown": "20"},
        "20": {"onright": "21"},
        "21": {"onleft": "20"},
    }
    for control_id, moves in expected.items():
        control = _viewers_control(control_id)
        for key, target in moves.items():
            assert control.findtext(key) == target, (control_id, key)
    for control_id, target in (("20", "12"), ("21", "13")):
        up = _viewers_control(control_id).find("onup")
        assert up is not None and up.text == target and up.get("condition") == NOT_EMPTY


def test_the_viewers_actions_hide_with_no_viewers_and_add_viewer_never_does():
    from resources.lib.constants import (
        VIEWER_PLAYLISTS,
        VIEWER_PROFILES,
        VIEWER_REMOVE,
        VIEWER_RENAME,
        VIEWERS_ADD,
        WINDOW_CLOSE,
    )

    labels = {
        "10": VIEWER_PLAYLISTS, "11": VIEWER_PROFILES, "12": VIEWER_RENAME, "13": VIEWER_REMOVE,
        "20": VIEWERS_ADD, "21": WINDOW_CLOSE,
    }
    for control_id, string_id in labels.items():
        control = _viewers_control(control_id)
        assert control.findtext("label") == f"$ADDON[{ADDON_ID} {string_id}]"
        assert control.findtext("visible") == (NOT_EMPTY if control_id in ("10", "11", "12", "13") else None)
    assert ET.parse(VIEWERS).getroot().findtext("defaultcontrol") == "21"


def test_the_empty_viewers_list_says_so_in_translatable_text():
    from resources.lib.constants import VIEWERS_EMPTY

    root = ET.parse(VIEWERS).getroot()
    empty = [c for c in root.iter("control") if c.findtext("label") == f"$ADDON[{ADDON_ID} {VIEWERS_EMPTY}]"]
    assert len(empty) == 1 and empty[0].findtext("visible") == "String.IsEqual(Window.Property(CW.Empty),true)"


def test_the_viewers_icons_ship_and_are_tinted_by_meaning():
    from tests.test_window_style import PALETTE

    root = ET.parse(VIEWERS).getroot()
    icons = [i for i in root.iter("control") if i.get("type") == "image" and "icons/" in (i.findtext("texture") or "")]
    assert icons
    for image in icons:
        texture = image.find("texture")
        assert texture is not None
        name = texture.text or ""
        tint = PALETTE["positive"] if name.endswith("check_circle.png") else PALETTE["danger"]
        assert texture.get("colordiffuse") == tint, name
        assert (image.findtext("width"), image.findtext("height")) == ("32", "32")
    for texture in root.iter("texture"):
        if (texture.text or "").startswith("crosswatch/"):
            assert (SKIN / "media" / (texture.text or "")).is_file(), texture.text


def test_the_highlighted_viewer_stays_marked_while_the_buttons_have_focus():
    layout = ET.parse(VIEWERS).getroot().find(".//control[@id='100']/focusedlayout")
    assert layout is not None
    boxes = {i.findtext("texture"): i.findtext("visible") for i in layout.findall("control[@type='image']")}
    assert boxes.get("crosswatch/box_focus.png") == "Control.HasFocus(100)"
    assert boxes.get("crosswatch/box_chosen.png") == "!Control.HasFocus(100)"


def test_a_panel_line_takes_the_full_width_unless_it_has_a_tag():
    """Playlist names run long ("EasyTV - TVShow - Season Premieres"); only a tagged line
    gives up room for its tag."""
    from resources.lib.ui.viewers_window import PANEL_SLOTS

    root = ET.parse(VIEWERS).getroot()
    for n in range(1, PANEL_SLOTS + 1):
        tag = f"Container(100).ListItem.Property(slot{n}_tag)"
        lines = {
            c.findtext("visible"): c for c in root.iter("control")
            if c.findtext("label") == f"$INFO[Container(100).ListItem.Property(slot{n})]"
        }
        assert set(lines) == {f"String.IsEmpty({tag})", f"!String.IsEmpty({tag})"}, n
        wide, narrow = lines[f"String.IsEmpty({tag})"], lines[f"!String.IsEmpty({tag})"]
        tag_label = next(c for c in root.iter("control") if c.findtext("label") == f"$INFO[{tag}]")
        assert int(narrow.findtext("left") or 0) + int(narrow.findtext("width") or 0) <= int(tag_label.findtext("left") or 0)
        assert int(wide.findtext("width") or 0) > int(narrow.findtext("width") or 0)


REMEMBERED = SKIN / "1080i" / "crosswatch-remembered.xml"
CHANGEABLE = "!String.IsEmpty(Container(100).ListItem.Property(changeable))"
HAS_BULK = "!String.IsEmpty(Window.Property(CW.Bulk))"
SHOWN = "!String.IsEqual(Window.Property(CW.Empty),true)"


def _remembered_control(control_id: str) -> ET.Element:
    found = ET.parse(REMEMBERED).getroot().find(f".//control[@id='{control_id}']")
    assert found is not None, control_id
    return found


def test_the_remembered_window_has_one_set_of_controls_per_panel_slot():
    from resources.lib.remembered import REMEMBERED_SLOTS

    root = ET.parse(REMEMBERED).getroot()
    labels = [c.findtext("label") or "" for c in root.iter("control")]
    visible = [c.findtext("visible") or "" for c in root.iter("control")]
    for n in range(1, REMEMBERED_SLOTS + 1):
        for prop in (f"slot{n}", f"slot{n}_head"):
            assert labels.count(f"$INFO[Container(100).ListItem.Property({prop})]") == 1, prop
        assert visible.count(f"String.IsEqual(Container(100).ListItem.Property(slot{n}_warn),true)") == 1, n
    assert not any(f"Property(slot{REMEMBERED_SLOTS + 1}" in text for text in labels + visible)
    for control in root.iter("control"):
        if "Property(slot" in (control.findtext("label") or ""):
            assert control.findtext("wrapmultiline") in (None, "false")
            assert int(control.findtext("top") or 0) + int(control.findtext("height") or 0) <= 206 + 390


def _nav(control: ET.Element, key: str) -> list[tuple[str | None, str]]:
    return [(n.get("condition"), (n.text or "").strip()) for n in control.findall(key)]


def test_the_remembered_window_navigates_between_header_list_panel_and_buttons():
    assert _nav(_remembered_control("30"), "ondown") == [(None, "31")]
    assert _nav(_remembered_control("31"), "onup") == [(None, "30")]
    assert _nav(_remembered_control("31"), "ondown") == [(None, "100")]
    rows = _remembered_control("100")
    assert _nav(rows, "onup") == [(None, "31")]
    assert _nav(rows, "onright") == [(CHANGEABLE, "40"), (None, "41")]
    assert _nav(rows, "ondown") == [(HAS_BULK, "20"), (None, "21")]
    change = _remembered_control("40")
    assert _nav(change, "onleft") == [(None, "100")] and _nav(change, "onright") == [(None, "41")]
    assert _nav(change, "ondown") == [(HAS_BULK, "20"), (None, "21")]
    forget = _remembered_control("41")
    assert _nav(forget, "onleft") == [(CHANGEABLE, "40"), (None, "100")]
    assert _nav(forget, "ondown") == [(None, "21")]
    bulk = _remembered_control("20")
    assert _nav(bulk, "onup") == [(None, "100")] and _nav(bulk, "onright") == [(None, "21")]
    close = _remembered_control("21")
    assert _nav(close, "onleft") == [(HAS_BULK, "20")]
    assert _nav(close, "onup") == [(SHOWN, "41"), (None, "30")]


def test_change_shows_only_for_a_changeable_answer_and_both_hide_with_nothing_shown():
    from resources.lib.constants import REMEMBERED_CHANGE, REMEMBERED_FORGET

    change, forget = _remembered_control("40"), _remembered_control("41")
    assert change.findtext("label") == f"$ADDON[{ADDON_ID} {REMEMBERED_CHANGE}]"
    assert forget.findtext("label") == f"$ADDON[{ADDON_ID} {REMEMBERED_FORGET}]"
    assert change.findtext("visible") == CHANGEABLE
    root = ET.parse(REMEMBERED).getroot()
    panel = next(g for g in root.iter("control") if g.get("type") == "group" and g.findtext("visible") == SHOWN)
    assert {c.get("id") for c in panel.iter("control")} >= {"40", "41"}
    assert root.findtext("defaultcontrol") == "21"


def test_the_remembered_poster_has_the_no_poster_placeholder():
    root = ET.parse(REMEMBERED).getroot()
    empty = "String.IsEmpty(Container(100).ListItem.Art(thumb))"
    placeholders = [c for c in root.iter("control") if c.findtext("visible") == empty]
    assert any(c.findtext("label") == f"$ADDON[{ADDON_ID} 30101]" for c in placeholders)
    assert sum(1 for c in placeholders if "glow.png" in (c.findtext("texture") or "")) == 4
    poster = [c for c in root.iter("control") if c.findtext("texture") == "$INFO[Container(100).ListItem.Art(thumb)]"]
    assert len(poster) == 1 and (poster[0].findtext("width"), poster[0].findtext("height")) == ("200", "300")


def test_the_remembered_icons_are_danger_warnings_and_ship():
    from tests.test_window_style import PALETTE

    root = ET.parse(REMEMBERED).getroot()
    icons = [c for c in root.iter("control") if "icons/" in (c.findtext("texture") or "")]
    assert icons
    for image in icons:
        texture = image.find("texture")
        assert texture is not None and texture.text == "crosswatch/icons/warning.png"
        assert texture.get("colordiffuse") == PALETTE["danger"]
    for texture in root.iter("texture"):
        name = texture.text or ""
        if name.startswith("crosswatch/"):
            assert (SKIN / "media" / name).is_file(), name
            assert texture.get("diffuse") in (None, "crosswatch/mask_poster.png")
