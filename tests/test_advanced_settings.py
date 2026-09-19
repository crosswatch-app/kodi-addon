from resources.lib.advanced_settings import DEFAULTS, read_thresholds
from tests.fakes import FakeKodi

PROFILE = "special://profile/advancedsettings.xml"
MASTER = "special://masterprofile/advancedsettings.xml"

FULL = """<advancedsettings>
  <video>
    <playcountminimumpercent>75</playcountminimumpercent>
    <ignoresecondsatstart>60</ignoresecondsatstart>
    <ignorepercentatend>4</ignorepercentatend>
  </video>
</advancedsettings>"""


def test_defaults_match_kodi_when_no_file_exists():
    got = read_thresholds(FakeKodi())
    assert got == DEFAULTS
    assert (got.ignore_seconds_at_start, got.ignore_percent_at_end, got.playcount_minimum_percent) == (180, 8.0, 90.0)


def test_reads_values_from_the_profile_file():
    got = read_thresholds(FakeKodi(files={PROFILE: FULL}))
    assert (got.ignore_seconds_at_start, got.ignore_percent_at_end, got.playcount_minimum_percent) == (60, 4.0, 75.0)


def test_profile_file_wins_over_master():
    got = read_thresholds(FakeKodi(files={PROFILE: FULL, MASTER: FULL.replace("60", "600")}))
    assert got.ignore_seconds_at_start == 60


def test_falls_back_to_master_when_the_profile_has_no_file():
    assert read_thresholds(FakeKodi(files={MASTER: FULL})).ignore_seconds_at_start == 60


def test_missing_elements_fall_back_individually():
    partial = "<advancedsettings><video><ignoresecondsatstart>30</ignoresecondsatstart></video></advancedsettings>"
    got = read_thresholds(FakeKodi(files={PROFILE: partial}))
    assert got.ignore_seconds_at_start == 30
    assert got.playcount_minimum_percent == 90.0


def test_malformed_xml_falls_back_to_defaults():
    assert read_thresholds(FakeKodi(files={PROFILE: "<advancedsettings><video>"})) == DEFAULTS


def test_a_non_numeric_value_falls_back_to_its_default():
    bad = "<advancedsettings><video><ignoresecondsatstart>soon</ignoresecondsatstart></video></advancedsettings>"
    assert read_thresholds(FakeKodi(files={PROFILE: bad})).ignore_seconds_at_start == 180
