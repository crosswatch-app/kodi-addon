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
    """A grouplist, so the buttons sit together and left and right move between them."""
    row = _root().find(".//control[@type='grouplist']")
    assert row is not None and row.findtext("orientation") == "horizontal"
    assert [c.get("id") for c in row.findall("control")] == ["20", "21"]
    assert _control("200").findtext("ondown") == "20"
    for button in ("20", "21"):
        assert _control(button).findtext("onup") == "200"


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
    assert control("100").findtext("pagecontrol") == "101"
    assert control("30").findtext("top") == "110" and control("100").findtext("top") == "206"
    LIST.read_text(encoding="ascii")


def _navigation(control: ET.Element, key: str) -> list[tuple[str | None, str]]:
    return [(nav.get("condition"), (nav.text or "").strip()) for nav in control.findall(key)]


def test_the_list_window_is_the_picker_only():
    """The Viewers and Remembered answers screens have their own split windows, so this one
    has no filter, no bulk button and no thumbnails, and nothing is conditional on pick mode."""
    root = ET.parse(LIST).getroot()
    assert {c.get("id") for c in root.iter("control") if c.get("id")} == {"30", "100", "101", "21", "22"}
    text = LIST.read_text(encoding="ascii")
    assert "CW.Pick" not in text and "CW.Bulk" not in text and "CW.Filter" not in text
    assert "ListItem.Art(thumb)" not in text and "poster_empty" not in text


def test_the_close_label_comes_from_a_property_so_a_pick_list_can_say_cancel():
    root = ET.parse(LIST).getroot()
    close = root.find(".//control[@id='21']")
    assert close is not None and close.findtext("label") == "$INFO[Window.Property(CW.Close)]"


def test_a_pick_list_has_an_accent_done_button_below_the_list():
    root = ET.parse(LIST).getroot()
    done = root.find(".//control[@id='22']")
    rows = root.find(".//control[@id='100']")
    close = root.find(".//control[@id='21']")
    assert done is not None and rows is not None and close is not None
    assert done.findtext("visible") is None
    assert done.findtext("label") == f"$ADDON[{ADDON_ID} {WHO_WATCHED_DONE}]"
    assert done.findtext("texturenofocus") == "crosswatch/box_accent.png"
    assert done.findtext("texturefocus") == "crosswatch/box_accent_focus.png"
    assert (done.findtext("left"), done.findtext("top")) == ("50", "740")
    assert done.findtext("onup") == "100" and done.findtext("onright") == "21"
    assert _navigation(rows, "ondown") == [(None, "22")]
    assert _navigation(close, "onleft") == [(None, "22")]


def test_pick_rows_show_a_tick():
    root = ET.parse(LIST).getroot()
    for layout in ("itemlayout", "focusedlayout"):
        found = root.find(f".//control[@id='100']/{layout}")
        assert found is not None
        images = found.findall("control[@type='image']")
        ticks = [i for i in images if i.findtext("texture") == "crosswatch/tick.png"]
        assert len(ticks) == 1 and ticks[0].findtext("visible") == "String.IsEqual(ListItem.Property(chosen),true)"
        assert (ticks[0].findtext("width"), ticks[0].findtext("height")) == ("48", "48")
        assert [i for i in images if (i.findtext("texture") or "").startswith("crosswatch/box_chosen")]


def test_pick_row_columns_leave_room_for_long_playlist_names_without_overlapping():
    """Real playlist names run long ("EasyTV - TVShow - Season Premieres"), and a row also
    carries the Also line, the tag and the tick."""
    root = ET.parse(LIST).getroot()
    for layout in ("itemlayout", "focusedlayout"):
        found = root.find(f".//control[@id='100']/{layout}")
        assert found is not None
        spans = {}
        for control in found.findall("control"):
            label = control.findtext("label") or control.findtext("texture") or ""
            if control.get("type") == "label" or "tick.png" in label:
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


def _nav(control: ET.Element, key: str) -> list[tuple[str | None, str]]:
    return [(n.get("condition"), (n.text or "").strip()) for n in control.findall(key)]


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


def test_the_viewers_route_line_sits_under_the_name_and_the_lines_never_wrap():
    root = ET.parse(VIEWERS).getroot()
    route = next(
        c for c in root.iter("control") if c.findtext("label") == "$INFO[Container(100).ListItem.Property(route_text)]"
    )
    route_bottom = int(route.findtext("top") or 0) + int(route.findtext("height") or 0)
    name = next(c for c in root.iter("control") if c.findtext("label") == "$INFO[Container(100).ListItem.Label]" and c.findtext("left") == "530")
    assert int(name.findtext("top") or 0) + int(name.findtext("height") or 0) <= int(route.findtext("top") or 0)
    for control in root.iter("control"):
        label = control.findtext("label") or ""
        if "Property(slot" in label:
            assert control.findtext("wrapmultiline") in (None, "false"), label
            top = int(control.findtext("top") or 0)
            assert route_bottom <= top and top + int(control.findtext("height") or 0) <= 740, label


def test_the_viewers_window_navigates_between_list_actions_and_bottom_row():
    rows, add, close = _viewers_control("100"), _viewers_control("20"), _viewers_control("21")
    assert _nav(rows, "onright") == [(None, "10")] and _nav(rows, "ondown") == [(None, "20")]
    assert _nav(add, "onup") == [(NOT_EMPTY, "100")]
    assert _nav(add, "onright") == [(NOT_EMPTY, "10"), (None, "21")]
    assert _nav(close, "onup") == [(NOT_EMPTY, "100")]
    assert _nav(close, "onleft") == [(NOT_EMPTY, "13"), (None, "20")]
    actions = _bottom_group(VIEWERS)
    assert [c.get("id") for c in actions.findall("control")] == ["10", "11", "12", "13"]
    assert _nav(actions, "onleft") == [(None, "20")] and _nav(actions, "onright") == [(None, "21")]
    for button in actions.findall("control"):
        assert _nav(button, "onup") == [(None, "100")], button.get("id")


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


def _bottom_group(window: Path) -> ET.Element:
    groups = [g for g in ET.parse(window).getroot().iter("control") if g.get("type") == "grouplist" and g.findtext("top") == "740"]
    assert len(groups) == 1, window.name
    return groups[0]


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
            assert int(control.findtext("top") or 0) + int(control.findtext("height") or 0) <= 740


def test_the_remembered_window_navigates_between_header_list_panel_and_buttons():
    assert _nav(_remembered_control("30"), "ondown") == [(None, "31")]
    assert _nav(_remembered_control("31"), "onup") == [(None, "30")]
    assert _nav(_remembered_control("31"), "ondown") == [(None, "100")]
    rows = _remembered_control("100")
    assert _nav(rows, "onup") == [(None, "31")]
    assert _nav(rows, "onright") == [(CHANGEABLE, "40"), (None, "41")]
    assert _nav(rows, "ondown") == [(HAS_BULK, "20"), (None, "21")]
    actions = _bottom_group(REMEMBERED)
    assert [c.get("id") for c in actions.findall("control")] == ["40", "41"]
    assert _nav(actions, "onleft") == [(HAS_BULK, "20"), (SHOWN, "100")]
    assert _nav(actions, "onright") == [(None, "21")]
    for button in actions.findall("control"):
        assert _nav(button, "onup") == [(None, "100")], button.get("id")
    bulk = _remembered_control("20")
    assert _nav(bulk, "onup") == [(None, "100")]
    assert _nav(bulk, "onright") == [(CHANGEABLE, "40"), (SHOWN, "41"), (None, "21")]
    close = _remembered_control("21")
    assert _nav(close, "onleft") == [(SHOWN, "41"), (HAS_BULK, "20")]
    assert _nav(close, "onup") == [(SHOWN, "100"), (None, "30")]


def test_change_shows_only_for_a_changeable_answer_and_both_hide_with_nothing_shown():
    from resources.lib.constants import REMEMBERED_CHANGE, REMEMBERED_FORGET

    change, forget = _remembered_control("40"), _remembered_control("41")
    assert change.findtext("label") == f"$ADDON[{ADDON_ID} {REMEMBERED_CHANGE}]"
    assert forget.findtext("label") == f"$ADDON[{ADDON_ID} {REMEMBERED_FORGET}]"
    assert change.findtext("visible") == f"{CHANGEABLE} + {SHOWN}"
    assert forget.findtext("visible") == SHOWN
    root = ET.parse(REMEMBERED).getroot()
    assert root.findtext("defaultcontrol") == "21"


def test_the_split_windows_keep_close_at_the_far_right_apart_from_the_actions():
    """The same three groups on both: the left button, the actions on the highlighted row,
    and Close alone in the corner, so Close is in one place on every screen."""
    for window, close_left in ((VIEWERS, 1040), (REMEMBERED, 1000)):
        root = ET.parse(window).getroot()
        close = root.find(".//control[@id='21']")
        actions = _bottom_group(window)
        assert close is not None and int(close.findtext("left") or 0) == close_left
        assert int(close.findtext("left") or 0) + int(close.findtext("width") or 0) == 1190
        assert int(actions.findtext("left") or 0) + int(actions.findtext("width") or 0) <= close_left - 40, window.name
        assert close.findtext("top") == "740" and actions.findtext("top") == "740"


def test_the_remembered_poster_has_the_no_poster_placeholder():
    root = ET.parse(REMEMBERED).getroot()
    empty = "String.IsEmpty(Container(100).ListItem.Art(thumb))"
    placeholders = [c for c in root.iter("control") if c.findtext("visible") == empty]
    assert any(c.findtext("label") == f"$ADDON[{ADDON_ID} 30101]" for c in placeholders)
    assert sum(1 for c in placeholders if "glow.png" in (c.findtext("texture") or "")) == 4
    poster = [c for c in root.iter("control") if c.findtext("texture") == "$INFO[Container(100).ListItem.Art(thumb)]"]
    assert len(poster) == 1 and (poster[0].findtext("width"), poster[0].findtext("height")) == ("240", "360")


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


SCREENS = (LIST, VIEWERS, REMEMBERED)


def test_the_settings_screens_share_one_header_line():
    """The brand fixed on the left, the heading centred on the panel, the count on the
    right, all on one line: a separate heading line cost the lists a row."""
    for window in SCREENS:
        root = ET.parse(window).getroot()
        by_label = {c.findtext("label"): c for c in root.iter("control") if c.get("type") == "label"}
        brand, heading = by_label["CROSSWATCH"], by_label["$INFO[Window.Property(CW.Heading)]"]
        count = by_label["$INFO[Window.Property(CW.Count)]"]
        assert (brand.findtext("left"), brand.findtext("top")) == ("50", "40"), window.name
        assert (heading.findtext("left"), heading.findtext("width"), heading.findtext("align")) == ("270", "700", "center")
        assert (count.findtext("left"), count.findtext("width"), count.findtext("align")) == ("990", "200", "right")
        assert brand.findtext("top") == heading.findtext("top") == count.findtext("top") == "40"


def test_every_button_sets_its_own_text_offset():
    """A skin's default button offset applies to script windows (Arctic Zephyr Mod's is 30)
    and cut the narrow labels; every button sets one so no skin can."""
    for window in sorted((SKIN / "1080i").glob("*.xml")):
        for control in ET.parse(window).getroot().iter("control"):
            if control.get("type") == "button":
                assert control.findtext("textoffsetx") == "10", (window.name, control.get("id"))
