"""Cached accessibility rows retain their labels and values without exposing passwords."""

from types import SimpleNamespace

from clickachu.automation.uia_snapshot import _capture


class Node:
    def __init__(self, kind, name, children=(), value="", password=False):
        self.props = {
            30003: kind,
            30005: name,
            30045: value,
            30019: password,
            30001: (0, 0, 100, 30),
            30010: True,
            30022: False,
        }
        self.children = children

    def BuildUpdatedCache(self, cache):
        return self

    def GetCachedPropertyValue(self, key):
        return self.props.get(key)

    def GetCachedChildren(self):
        return SimpleNamespace(Length=len(self.children), GetElement=lambda i: self.children[i])


def test_row_name_reaches_its_play_button_and_password_value_stays_hidden():
    play = Node(50000, "Play")
    row = Node(50029, "Example Song by Example Artist", [play])
    address = Node(50004, "Address and search bar", value="https://example.org/docs")
    password = Node(50004, "Password", value="do-not-expose", password=True)
    root = Node(50032, "Example App", [row, address, password])
    client = SimpleNamespace(
        CreateCacheRequest=lambda: SimpleNamespace(AddProperty=lambda key: None),
        RawViewCondition=None,
        ControlViewCondition=None,
    )
    rows, _ = _capture(client, root, None)
    button = next(r for r in rows if r.type == "Button")
    assert button.context == "Example Song by Example Artist"
    assert (
        next(r for r in rows if r.text == "Address and search bar").value
        == "https://example.org/docs"
    )
    hidden = next(r for r in rows if r.password)
    assert hidden.text == "[password field]" and hidden.value == ""
