"""The CrossWatch window style, in one place, checked against every window.

Kodi gives a script window no includes or named colours of its own, so the windows cannot
share a style file at runtime. These tokens are that file: a new window passes them, and a
window that has to differ changes the token here, with its reason.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WINDOWS = sorted((Path(__file__).resolve().parents[1] / "resources" / "skins" / "Default" / "1080i").glob("*.xml"))

# cenodude's palette for CrossWatch (issue #1). Positive and danger are reserved.
PALETTE = {
    "background": "FF090A0D",
    "dim": "E6090A0D",  # the screen behind a window, background at 90 %
    "border": "FF1C222C",
    "text": "FFF1F3F5",
    "muted": "FF9299A6",
    "accent": "FF7C5CFF",
}
SCREEN_HEIGHT = 1080
PANEL_LEFT = 340  # centres the panel on a 1920 wide screen
PANEL_WIDTH = 1240
GUTTER = 50  # panel edge to content, on both sides
BUTTON_HEIGHT = 72  # readable and hittable from the sofa
BUTTON_FONT = "font30_title"
BUTTON_TEXTURES = {
    "crosswatch/box.png",
    "crosswatch/box_focus.png",
    "crosswatch/box_accent.png",  # the primary action only
    "crosswatch/box_accent_focus.png",
}
ROW_LAYOUT_HEIGHT = 76  # a 66 high row plus a 10 gap
ROW_HEIGHT = 66
BRAND_FONT = "font25_title"
# Which font a piece of text uses, by the property or list field it shows.
ROLE_FONTS = {
    "Window.Property(CW.Title)": "font40_title",
    "Window.Property(CW.Question)": "font32_title",
    "Window.Property(CW.Heading)": "font32_title",
    "Window.Property(CW.Subtitle)": "font13",
    "Window.Property(CW.Message)": "font13",
    "Window.Property(CW.Count)": "font13",
    "Window.Property(CW.Footer)": "font12",
    "ListItem.Label": "font30_title",
    "ListItem.Property(detail)": "font13",
    "ListItem.Property(tag)": "font12",
}


def _root(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def _panel(root: ET.Element) -> ET.Element:
    panel = root.find("controls/control[@type='group']")
    assert panel is not None, "a window's first group is its panel"
    return panel


def _int(control: ET.Element, key: str) -> int | None:
    text = control.findtext(key)
    return int(text) if text is not None and text.strip().lstrip("-").isdigit() else None


def _placed(element: ET.Element):
    """Controls placed in panel coordinates: not inside a list's item layouts."""
    for child in element:
        if child.tag in ("itemlayout", "focusedlayout"):
            continue
        if child.tag == "control":
            yield child
        yield from _placed(child)


@pytest.fixture(params=WINDOWS, ids=lambda p: p.name)
def window(request) -> Path:
    return request.param


def test_there_are_windows_to_check():
    assert WINDOWS


def test_colours_come_from_the_palette(window):
    text = window.read_text(encoding="ascii")
    found = re.findall(r"<(?:textcolor|focusedcolor|disabledcolor|colordiffuse)>([0-9A-Fa-f]{8})<", text)
    found += re.findall(r'colordiffuse="([0-9A-Fa-f]{8})"', text)
    found += re.findall(r"\[COLOR ([0-9A-Fa-f]{8})\]", text)
    assert found
    for colour in found:
        assert colour.upper() in PALETTE.values(), colour


def test_the_panel_is_centred(window):
    panel = _panel(_root(window))
    height = _int(panel, "height")
    assert _int(panel, "left") == PANEL_LEFT and _int(panel, "width") == PANEL_WIDTH
    assert height is not None and _int(panel, "top") == (SCREEN_HEIGHT - height) // 2


def test_content_keeps_the_gutter(window):
    for control in _placed(_panel(_root(window))):
        left, width = _int(control, "left"), _int(control, "width")
        if left is None or width is None or control.get("type") == "scrollbar":
            continue  # a grouplist child, or a scrollbar, which sits in the gutter on purpose
        if left == 0 and width == PANEL_WIDTH:
            continue  # the panel's own background and bar
        assert left >= GUTTER and left + width <= PANEL_WIDTH - GUTTER, (control.get("id"), left, width)


def test_buttons_and_inputs_share_one_size_font_and_texture(window):
    for control in _root(window).iter("control"):
        if control.get("type") not in ("button", "edit"):
            continue
        assert _int(control, "height") == BUTTON_HEIGHT, control.get("id")
        assert control.findtext("font") == BUTTON_FONT, control.get("id")
        for tag in ("texturefocus", "texturenofocus"):
            assert control.findtext(tag) in BUTTON_TEXTURES, (control.get("id"), tag)


def test_list_rows_share_one_height(window):
    for layout in [*_root(window).iter("itemlayout"), *_root(window).iter("focusedlayout")]:
        assert int(layout.get("height") or 0) == ROW_LAYOUT_HEIGHT
        box = layout.find("control[@type='image']")
        assert box is not None and _int(box, "height") == ROW_HEIGHT


def test_text_uses_the_font_of_its_role(window):
    for control in _root(window).iter("control"):
        label = control.findtext("label") or ""
        font = control.findtext("font")
        if label == "CROSSWATCH":
            assert font == BRAND_FONT and control.findtext("textcolor") == PALETTE["muted"]
        for role, expected in ROLE_FONTS.items():
            if f"$INFO[{role}]" in label:
                assert font == expected, (role, font)


def test_every_window_has_a_default_control(window):
    default = _root(window).find("defaultcontrol")
    assert default is not None and (default.text or "").strip()


def test_every_button_can_be_reached(window):
    root = _root(window)
    targets = {(root.findtext("defaultcontrol") or "").strip()}
    for control in root.iter("control"):
        for key in ("onup", "ondown", "onleft", "onright"):
            targets.add((control.findtext(key) or "").strip())
    # A grouplist moves focus between its own children.
    for group in root.iter("control"):
        if group.get("type") == "grouplist":
            ids = {c.get("id") for c in group.findall("control")}
            if ids & targets or group.get("id") in targets:
                targets |= ids
    for control in root.iter("control"):
        if control.get("type") in ("button", "edit"):
            assert control.get("id") in targets, control.get("id")


def test_no_control_uses_an_id_kodi_keeps_for_itself(window):
    """WindowXML handles clicks on ids 2, 3 and 4 as its own view and sort buttons and never
    passes them to the script (xbmc/interfaces/legacy/WindowXML.cpp, GUI_MSG_CLICKED)."""
    for control in _root(window).iter("control"):
        assert control.get("id") not in {"2", "3", "4"}, control.get("id")
