from resources.lib import connect, remembered, scripts, viewer_config


def _spy(monkeypatch):
    opened: list[object] = []
    monkeypatch.setattr(viewer_config, "main", lambda: opened.append("viewers"))
    monkeypatch.setattr(remembered, "main", lambda: opened.append("remembered"))
    monkeypatch.setattr(connect, "pair_main", lambda: opened.append("pair"))
    monkeypatch.setattr(connect, "unpair_main", lambda: opened.append("unpair"))
    monkeypatch.setattr(connect, "link_main", lambda arguments: opened.append(("link", arguments)))
    return opened


def test_no_argument_opens_the_viewer_dialog(monkeypatch):
    opened = _spy(monkeypatch)
    scripts.dispatch(["configure.py"])
    assert opened == ["viewers"]


def test_the_remembered_argument_opens_the_remembered_answers_screen(monkeypatch):
    opened = _spy(monkeypatch)
    scripts.dispatch(["configure.py", "remembered"])
    assert opened == ["remembered"]


def test_pair_and_unpair_open_their_screens(monkeypatch):
    opened = _spy(monkeypatch)
    scripts.dispatch(["configure.py", "pair"])
    scripts.dispatch(["configure.py", "unpair"])
    assert opened == ["pair", "unpair"]


def test_a_link_from_execute_addon_is_handed_its_arguments(monkeypatch):
    opened = _spy(monkeypatch)
    scripts.dispatch(["configure.py", "action=link", "url=http://nas/webhook/kodiwatcher", "token=tok"])
    assert opened == [("link", {"action": "link", "url": "http://nas/webhook/kodiwatcher", "token": "tok"})]


def test_an_unknown_action_opens_nothing(monkeypatch):
    opened = _spy(monkeypatch)
    scripts.dispatch(["configure.py", "action=other"])
    assert opened == []
