from pathlib import Path
from xml.etree import ElementTree

from resources.lib import config
from resources.lib.constants import DEFAULT_INDEX_TTL_SECONDS, DEFAULT_PROGRESS_STEP

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
        config.KEY_DEVICE_ID,
        config.KEY_PROGRESS_STEP,
        config.KEY_MOVIE_PROMPTS,
        config.KEY_INDEX_TTL,
        config.KEY_DEBUG,
    ):
        assert key in ids, key


def test_defaults_match_the_constants():
    settings = _settings()

    def default_of(key: str) -> str:
        node = settings[key].find("default")
        assert node is not None, key
        return (node.text or "").strip()

    assert default_of(config.KEY_PROGRESS_STEP) == str(DEFAULT_PROGRESS_STEP)
    assert default_of(config.KEY_INDEX_TTL) == str(DEFAULT_INDEX_TTL_SECONDS // 60)
