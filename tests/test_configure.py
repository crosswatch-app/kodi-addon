import configure
from resources.lib import remembered, viewer_config


def _spy(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(viewer_config, "main", lambda: opened.append("viewers"))
    monkeypatch.setattr(remembered, "main", lambda: opened.append("remembered"))
    return opened


def test_no_argument_opens_the_viewer_dialog(monkeypatch):
    opened = _spy(monkeypatch)
    configure.dispatch(["configure.py"])
    assert opened == ["viewers"]


def test_the_remembered_argument_opens_the_remembered_answers_screen(monkeypatch):
    opened = _spy(monkeypatch)
    configure.dispatch(["configure.py", "remembered"])
    assert opened == ["remembered"]
