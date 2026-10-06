from pathlib import Path
from xml.etree import ElementTree

from resources.lib import config, constants
from resources.lib.constants import DEFAULT_INDEX_TTL_SECONDS, DEFAULT_PROGRESS_INTERVAL_SECONDS

SETTINGS = Path(__file__).resolve().parents[1] / "resources" / "settings.xml"
STRINGS = (
    Path(__file__).resolve().parents[1]
    / "resources" / "language" / "resource.language.en_gb" / "strings.po"
)


def _settings():
    return {s.get("id"): s for s in ElementTree.parse(SETTINGS).getroot().iter("setting")}


def test_every_empty_default_string_setting_allows_empty():
    """Without allowempty Kodi refuses to register the setting and it reads as '' for ever."""
    for setting_id, node in _settings().items():
        if node.get("type") != "string":
            continue
        default = node.find("default")
        if default is None or (default.text or "").strip():
            continue
        allow = node.find("constraints/allowempty")
        assert allow is not None and allow.text == "true", setting_id


def test_every_label_is_a_positive_integer_string_id():
    """Kodi parses label with QueryIntAttribute; plain text renders as a blank row."""
    root = ElementTree.parse(SETTINGS).getroot()
    for node in list(root.iter("setting")) + list(root.iter("category")):
        label = node.get("label")
        assert label is not None and label.isdigit() and int(label) > 0, node.get("id")


def test_every_label_id_exists_in_the_language_file():
    text = STRINGS.read_text(encoding="utf-8")
    root = ElementTree.parse(SETTINGS).getroot()
    for node in list(root.iter("setting")) + list(root.iter("category")):
        assert f'msgctxt "#{node.get("label")}"' in text, node.get("id")


def test_setting_ids_match_the_config_keys():
    ids = set(_settings())
    for key in (
        config.KEY_BASE_URL,
        config.KEY_TOKEN,
        config.KEY_STATUS,
        config.KEY_DEVICE_ID,
        config.KEY_PROGRESS_INTERVAL,
        config.KEY_MOVIE_PROMPTS,
        config.KEY_SKIP_PKC,
        config.KEY_INDEX_TTL,
        config.KEY_DEBUG,
    ):
        assert key in ids, key


def test_defaults_match_the_constants():
    settings = _settings()

    def default_of(key: str) -> str:
        # find() is Optional, so the assertion has to happen before .text is read.
        node = settings[key].find("default")
        assert node is not None, key
        return (node.text or "").strip()

    assert default_of(config.KEY_PROGRESS_INTERVAL) == str(DEFAULT_PROGRESS_INTERVAL_SECONDS)
    assert default_of(config.KEY_INDEX_TTL) == str(DEFAULT_INDEX_TTL_SECONDS // 60)


def test_every_string_id_the_code_shows_exists_in_the_language_file():
    """A missing id renders as an empty string, so the dialog or toast silently loses its text."""
    text = STRINGS.read_text(encoding="utf-8")
    ids = {name: value for name, value in vars(constants).items() if name.startswith(("NOTIFY_", "PROMPT_", "PAIR_", "LINK_", "STATUS_", "UNPAIR_")) and isinstance(value, int) and value >= 30000}
    assert ids
    for name, value in ids.items():
        assert f'msgctxt "#{value}"' in text, name



def test_the_connection_actions_run_their_scripts():
    settings = _settings()
    assert settings["pair"].findtext("data") == "RunScript(service.crosswatch,pair)"
    assert settings["unpair"].findtext("data") == "RunScript(service.crosswatch,unpair)"


def test_the_connection_actions_close_the_settings_screen_first():
    """Kodi saves and closes the screen before running the script (AddonSettings.cpp).

    Left open, the screen only stages the script's writes, and Back or Cancel then reloads
    the settings from disk and silently undoes a pairing the user was shown as done.
    """
    settings = _settings()
    for key in ("pair", "unpair"):
        assert settings[key].findtext("control/close") == "true", key


def test_the_status_line_cannot_be_edited():
    assert _settings()[config.KEY_STATUS].findtext("enable") == "false"


def test_unpair_shows_only_while_a_token_is_set():
    dependency = _settings()["unpair"].find("dependencies/dependency")
    assert dependency is not None
    assert dependency.attrib == {"type": "visible", "setting": config.KEY_TOKEN, "operator": "!is"}
    assert not (dependency.text or "").strip()


def test_pairing_comes_first_and_the_manual_fields_are_advanced():
    settings = _settings()
    order = [s.get("id") for s in ElementTree.parse(SETTINGS).getroot().iter("setting")]
    assert order[:3] == ["pair", config.KEY_STATUS, "unpair"]
    for key in (config.KEY_BASE_URL, config.KEY_TOKEN):
        assert settings[key].findtext("level") == "2", key
