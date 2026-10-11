import pytest

from resources.lib.models import Viewer
from resources.lib.people import crosswatch_name, match_person, reconcile
from resources.lib.routes import Person

ANNA = Person("p-anna", "Anna B.")
ANNAB = Person("p-annab", "Annab")
BEN = Person("p-ben", "Ben")


@pytest.mark.parametrize(("name", "key"), [("Anna-Lee", "annalee"), ("anna lee!", "annalee"), ("Zoë", "zo"), ("Ægir", "gir"), ("李", "")])
def test_crosswatch_name_is_crosswatchs_own_comparison(name, key):
    assert crosswatch_name(name) == key


def test_crosswatch_name_drops_case_spaces_and_punctuation():
    assert crosswatch_name("Anna B.") == crosswatch_name("annab") == "annab"


def test_a_loose_match_links_when_only_one_person_fits():
    assert match_person("anna b", [ANNA, BEN], ()) == ANNA


def test_a_loose_pair_links_only_on_the_same_spelling():
    assert match_person("annab", [ANNA, ANNAB], ()) == ANNAB
    assert match_person("ANNA B.", [ANNA, ANNAB], ()) == ANNA
    assert match_person("anna-b", [ANNA, ANNAB], ()) is None


def test_a_person_already_linked_is_not_linked_twice():
    assert match_person("anna b", [ANNA], {"p-anna"}) is None


def test_a_name_with_nothing_crosswatch_reads_matches_by_case_only():
    zoe = Person("p-z", "Зоя")
    assert match_person("зоя", [zoe], ()) == zoe
    assert match_person("Ηλίας", [zoe], ()) is None


def test_reconcile_links_a_typed_viewer_and_keeps_its_spelling():
    result = reconcile([Viewer(name="anna b")], [ANNA])
    assert result.viewers == [Viewer(name="anna b", person_id="p-anna")]
    assert result.linked == 1 and result.renamed == ()


def test_reconcile_renames_a_linked_viewer():
    result = reconcile([Viewer(name="Anna", playlists=("A",), person_id="p-anna")], [ANNA])
    assert result.viewers == [Viewer(name="Anna B.", playlists=("A",), person_id="p-anna")]
    assert result.renamed == (("Anna", "Anna B."),)


def test_a_rename_onto_another_viewers_name_is_skipped():
    viewers = [Viewer(name="Anna", person_id="p-anna"), Viewer(name="anna b.")]
    result = reconcile(viewers, [ANNA])
    assert result.viewers == viewers and result.skipped == 1 and result.renamed == ()


def test_a_person_gone_from_the_reply_leaves_the_viewer_linked():
    viewers = [Viewer(name="Anna", person_id="p-anna")]
    assert reconcile(viewers, [BEN]).viewers == viewers


def test_nothing_to_do_reports_no_change():
    viewers = [Viewer(name="Ben", person_id="p-ben")]
    assert not reconcile(viewers, [BEN]).changed


def test_a_case_only_rename_is_taken_over():
    result = reconcile([Viewer(name="ben", person_id="p-ben")], [BEN])
    assert result.renamed == (("ben", "Ben"),)
