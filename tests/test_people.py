import pytest

from resources.lib.models import Viewer
from resources.lib.people import crosswatch_name, exact_name, match_person, reconcile, same_name
from resources.lib.routes import Person

ANNA = Person("p-anna", "Anna B.")
ANNAB = Person("p-annab", "Annab")
BEN = Person("p-ben", "Ben")

# Written as code points, as CrossWatch's own tests do, so an editor cannot normalise them.
JOSE = "Jos" + chr(0xE9)
JOSE_DECOMPOSED = "Jose" + chr(0x301)
ELOISE = chr(0xC9) + "lo" + chr(0xEF) + "se"
WANG = chr(0x738B) + chr(0x660E)
LI = chr(0x674E) + chr(0x96F7)
KA = chr(0x915)
KI = chr(0x915) + chr(0x93F)
ZERO_WIDTH_SPACE = chr(0x200B)
ZERO_WIDTH_JOINER = chr(0x200D)
HEART = chr(0x2764) + chr(0xFE0F)
SUN = chr(0x2600) + chr(0xFE0F)


@pytest.mark.parametrize(
    ("name", "key"),
    [("Dad", "dad"), ("  Anna B. ", "annab"), ("john_doe-99", "johndoe99"), ("", ""), ("!!!", ""), ("Zoë", "zoë"), (WANG, WANG)],
)
def test_crosswatch_name_is_crosswatchs_own_comparison(name, key):
    assert crosswatch_name(name) == key


def test_crosswatch_name_drops_case_spaces_and_punctuation():
    assert crosswatch_name("Anna B.") == crosswatch_name("annab") == "annab"


def test_accented_names_are_kept_apart_from_their_stripped_form():
    assert same_name(ELOISE, ELOISE.lower())
    assert not same_name(ELOISE, "lose")
    assert not same_name(JOSE, "Jos")


def test_composed_and_decomposed_accents_are_the_same_name():
    assert JOSE != JOSE_DECOMPOSED
    assert same_name(JOSE, JOSE_DECOMPOSED) and same_name(JOSE_DECOMPOSED, JOSE.upper())


def test_non_latin_names_compare_loosely_and_only_with_themselves():
    assert same_name(WANG, f" {WANG} ")
    assert not same_name(WANG, LI)
    assert not same_name(WANG, "")


def test_marks_count_only_after_a_letter_or_digit():
    assert not same_name(KA, KI)
    assert crosswatch_name(chr(0x301) + "a") == "a"
    assert crosswatch_name("!" + chr(0x301)) == ""


def test_invisible_format_characters_are_ignored():
    for hidden in (ZERO_WIDTH_SPACE, ZERO_WIDTH_JOINER):
        assert crosswatch_name(KA + hidden + chr(0x93F)) == KI
        assert same_name(JOSE, "Jose" + hidden + chr(0x301))
    assert same_name("anna", "an" + ZERO_WIDTH_SPACE + "na")


def test_a_name_with_nothing_loose_compares_by_its_exact_form():
    assert same_name("!!!", " !!! ")
    assert not same_name("!!!", "???") and not same_name("!!!", "! ! !")
    assert not same_name(HEART, SUN) and same_name(HEART, HEART)
    assert exact_name(" Anna B. ") == "anna b."


def test_a_loose_match_links_when_only_one_person_fits():
    assert match_person("anna b", [ANNA, BEN], ()) == ANNA


def test_a_loose_pair_links_only_on_the_same_spelling():
    assert match_person("annab", [ANNA, ANNAB], ()) == ANNAB
    assert match_person("ANNA B.", [ANNA, ANNAB], ()) == ANNA
    assert match_person("anna-b", [ANNA, ANNAB], ()) is None


def test_a_person_already_linked_is_not_linked_twice():
    assert match_person("anna b", [ANNA], {"p-anna"}) is None


def test_a_non_latin_name_links_loosely():
    zoe = Person("p-z", "Зоя")
    assert match_person(" зоя!", [zoe], ()) == zoe
    assert match_person("Ηλίας", [zoe], ()) is None


def test_a_name_with_nothing_loose_links_only_on_its_exact_form():
    heart = Person("p-h", HEART)
    assert match_person(HEART, [heart], ()) == heart
    assert match_person(SUN, [heart], ()) is None


def test_reconcile_links_a_typed_viewer_and_keeps_its_spelling():
    result = reconcile([Viewer(name="anna b")], [ANNA])
    assert result.viewers == [Viewer(name="anna b", person_id="p-anna", person_name="Anna B.")]
    assert result.linked == 1 and result.renamed == ()


def test_a_loosely_linked_viewer_keeps_its_spelling_on_the_next_ping():
    first = reconcile([Viewer(name="anna-lee")], [Person("p1", "Anna Lee")])
    second = reconcile(first.viewers, [Person("p1", "Anna Lee")])
    assert second.viewers == first.viewers and second.viewers[0].name == "anna-lee"
    assert not second.changed and second.renamed == ()


def test_reconcile_renames_a_linked_viewer():
    old = Viewer(name="Anna", playlists=("A",), person_id="p-anna", person_name="Anna")
    result = reconcile([old], [ANNA])
    assert result.viewers == [Viewer(name="Anna B.", playlists=("A",), person_id="p-anna", person_name="Anna B.")]
    assert result.renamed == (("Anna", "Anna B."),)


def test_a_rename_onto_another_viewers_name_is_skipped():
    viewers = [Viewer(name="Anna", person_id="p-anna", person_name="Anna"), Viewer(name="anna b.")]
    result = reconcile(viewers, [ANNA])
    assert result.viewers == viewers and result.skipped == 1 and result.renamed == ()


def test_a_person_gone_from_the_reply_leaves_the_viewer_linked():
    viewers = [Viewer(name="Anna", person_id="p-anna")]
    assert reconcile(viewers, [BEN]).viewers == viewers


def test_nothing_to_do_reports_no_change():
    viewers = [Viewer(name="Ben", person_id="p-ben")]
    assert not reconcile(viewers, [BEN]).changed


def test_a_case_only_rename_is_taken_over():
    result = reconcile([Viewer(name="ben", person_id="p-ben", person_name="ben")], [BEN])
    assert result.renamed == (("ben", "Ben"),)


def test_a_link_without_a_last_seen_name_compares_against_the_viewer_name():
    viewers = [Viewer(name="Ben", person_id="p-ben")]
    assert reconcile(viewers, [BEN]).viewers == viewers


def test_a_taken_person_does_not_make_its_loose_twin_unambiguous():
    assert match_person("anna b", [ANNA, ANNAB], {"p-annab"}) is None
    assert match_person("anna b.", [ANNA, ANNAB], {"p-annab"}) == ANNA


def test_two_people_with_the_same_exact_name_match_nobody():
    assert match_person("Anna", [Person("p1", "Anna"), Person("p2", "anna ")], ()) is None


def test_two_viewers_link_in_one_pass():
    result = reconcile([Viewer(name="Anna B."), Viewer(name="Ben")], [ANNA, BEN])
    assert [v.person_id for v in result.viewers] == ["p-anna", "p-ben"]
    assert result.linked == 2 and result.renamed == () and result.changed


def test_every_clashing_rename_is_counted():
    viewers = [
        Viewer(name="Anna", person_id="p-anna"),
        Viewer(name="anna b."),
        Viewer(name="Bob", person_id="p-ben"),
        Viewer(name="ben"),
    ]
    result = reconcile(viewers, [ANNA, BEN])
    assert result.viewers == viewers and result.skipped == 2 and not result.changed
