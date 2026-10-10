from resources.lib.ui.panel import PanelLine
from resources.lib.ui.viewers_window import (
    PANEL_SLOTS,
    ROUTE_ACCEPTED,
    ROUTE_REFUSED,
    ViewerRow,
    ViewersRequest,
    properties,
    start_position,
)

HEAD_P = PanelLine("Playlists", heading=True)
HEAD_Q = PanelLine("Profiles", heading=True)


def _request(rows=(), more="and %s more") -> ViewersRequest:
    return ViewersRequest(
        heading="Viewers", count="2 viewers", rows=tuple(rows), more=more,
        route_ok="CrossWatch route", route_missing="No CrossWatch route",
    )


def test_a_row_warns_for_a_flagged_line_or_a_refused_route():
    assert ViewerRow("anna", (PanelLine("Gone", warn=True),)).warn
    assert ViewerRow("anna", route=ROUTE_REFUSED).warn
    assert not ViewerRow("anna", (PanelLine("Anna TV"),), route=ROUTE_ACCEPTED).warn
    assert not ViewerRow("anna").warn


def test_properties_put_headings_and_lines_in_their_own_labels():
    row = ViewerRow("anna", (HEAD_P, PanelLine("Gone", tag="missing", warn=True)), route=ROUTE_ACCEPTED)
    props = properties(row, _request([row]))
    assert props["slot1_head"] == "Playlists" and props["slot1"] == ""
    assert props["slot2"] == "Gone" and props["slot2_head"] == ""
    assert props["slot2_tag"] == "missing" and props["slot2_warn"] == "true"
    assert props["slot1_warn"] == ""
    assert props["warn"] == "true"
    assert props["route"] == ROUTE_ACCEPTED and props["route_text"] == "CrossWatch route"


def test_properties_clear_every_unused_slot():
    props = properties(ViewerRow("anna"), _request())
    for n in range(1, PANEL_SLOTS + 1):
        assert props[f"slot{n}"] == props[f"slot{n}_head"] == props[f"slot{n}_tag"] == props[f"slot{n}_warn"] == ""


def test_route_text_follows_the_route_state():
    assert properties(ViewerRow("a", route=ROUTE_REFUSED), _request())["route_text"] == "No CrossWatch route"
    no_claim = properties(ViewerRow("a"), _request())
    assert no_claim["route"] == "" and no_claim["route_text"] == ""


def test_the_window_starts_on_the_key_or_the_first_row():
    rows = (ViewerRow("anna"), ViewerRow("bob"), ViewerRow("chloe"))
    assert start_position(rows, "bob") == 1
    assert start_position(rows, "dan") == 0
    assert start_position(rows, "") == 0
    assert start_position((), "anna") == 0
