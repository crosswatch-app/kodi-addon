from resources.lib.ui.panel import PanelLine, fit, slot_properties

SLOTS = 8
HEAD_P = PanelLine("Playlists", heading=True)
HEAD_Q = PanelLine("Profiles", heading=True)


def test_lines_that_fit_are_kept_as_they_are():
    lines = (HEAD_P, PanelLine("Anna TV"), HEAD_Q, PanelLine("Kids"))
    assert fit(lines, "and %s more", SLOTS) == lines


def test_lines_that_do_not_fit_end_in_and_n_more():
    lines = (HEAD_P, *(PanelLine(f"P{i}") for i in range(10)), HEAD_Q, PanelLine("Kids"))
    kept = fit(lines, "and %s more", SLOTS)
    assert len(kept) == SLOTS
    assert kept[:-1] == lines[: SLOTS - 1]
    # P6 to P9 and Kids are left out; the profiles heading is not counted.
    assert kept[-1] == PanelLine("and 5 more")


def test_a_heading_is_never_the_last_line_before_and_n_more():
    lines = (HEAD_P, *(PanelLine(f"P{i}") for i in range(5)), HEAD_Q, *(PanelLine(f"Q{i}") for i in range(4)))
    kept = fit(lines, "and %s more", SLOTS)
    assert not kept[-2].heading
    assert kept[-1] == PanelLine("and 4 more")
    assert len(kept) == SLOTS - 1


def test_more_text_without_a_placeholder_does_not_raise():
    lines = tuple(PanelLine(f"P{i}") for i in range(SLOTS + 2))
    assert fit(lines, "more", SLOTS)[-1] == PanelLine("more")


def test_a_warning_cut_off_the_panel_shows_on_and_n_more():
    """The row's icon says something needs fixing; the panel must not hide where."""
    lines = (HEAD_P, *(PanelLine(f"P{i}") for i in range(6)), HEAD_Q, PanelLine("Old", tag="missing", warn=True))
    kept = fit(lines, "and %s more", SLOTS)
    assert kept[-1] == PanelLine("and 1 more", warn=True)
    assert not fit((*lines[:-1], PanelLine("Kids")), "and %s more", SLOTS)[-1].warn


def test_slot_properties_fill_every_slot_and_follow_the_slot_count():
    props = slot_properties((HEAD_P, PanelLine("Gone", tag="missing", warn=True)), "and %s more", 3)
    assert (props["slot1_head"], props["slot1"]) == ("Playlists", "")
    assert (props["slot2"], props["slot2_tag"], props["slot2_warn"]) == ("Gone", "missing", "true")
    assert props["slot3"] == props["slot3_head"] == props["slot3_tag"] == props["slot3_warn"] == ""
    assert "slot4" not in props
