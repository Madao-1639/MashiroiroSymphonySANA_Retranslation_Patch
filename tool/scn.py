"""*.scn (M2 MDF script) reader: message / choice text model.

A `.scn` file in this game is

    "mdf\\0" + u32(uncompressed length) + zlib(PSB v3)

The PSB object graph holds the script: `root["scenes"]` is a list of scenes,
each scene has a `label` (`*start`, `*normal`, ...), a command stream (`lines`)
and the message rows (`texts`) / choice rows (`selects`).

Message row layout (verified over all 297 scripts of the CN patch, row arity 5):

    [0] speaker display name (string | null)
    [1] pages[]                     -- one entry per message window page
    [2] per-page attributes[]       -- dict(name=voice char id, pan, type, voice=voice file)
    [3] flag (int, always 64 in this game)
    [4] command container {data, env, ...}  -- engine commands, no translatable text

Page layout, two variants:

    len 3: [voice gate | null, text, length]
    len 5: [voice gate | null, text, length, text_plain, text_plain]

* `text`        -- the string actually laid out in the message window; line breaks are
                    the two characters `\\` `n`, and inline control codes look like
                    `%205;` (char/voice marker), `#00ff0000;` (color), `#;` (reset).
* `length`      -- UTF-16 code-unit count of `text` (line-break characters included).
                    Must be recomputed when a text is replaced.
* `text_plain`  -- the same text with the `\\n` line breaks removed (p[3] and p[4]
                    normally share one string slot). The CN patch translated it too,
                    so it is part of the translatable payload.

Choice row (`scenes[].selects[]`) is a dict with `text` for plain choices; button
choices (`button: yes|no`) carry no text (the label comes from the UI layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import psb


def u16len(s: str) -> int:
    """Length of `s` in UTF-16 code units (what the engine stores as page length)."""
    return len(s.encode("utf-16-le")) // 2


@dataclass
class Page:
    index: int
    text_idx: int
    text: str
    length: int
    plain_idx: int | None = None
    plain: str | None = None
    alt_name_idx: int | None = None
    alt_name: str | None = None
    voice: str | None = None
    char_id: str | None = None

    @property
    def length_ok(self) -> bool:
        return self.length == u16len(self.text)


@dataclass
class Message:
    scene: int
    label: str
    row: int
    speaker: str | None
    speaker_idx: int | None
    flag: int
    pages: list[Page] = field(default_factory=list)


@dataclass
class Select:
    scene: int
    label: str
    index: int
    text_idx: int
    text: str


class ScnFile:
    """Read access to one `.scn` script."""

    def __init__(self, data: bytes):
        self.psb = psb.PsbFile(data)
        self.raw = data

    # ------------------------------------------------------------------ meta
    @property
    def script_name(self) -> str:
        name = self.psb.root.get("name")
        return name.value if isinstance(name, psb.PsbString) else ""

    @property
    def script_hash(self) -> str:
        h = self.psb.root.get("hash")
        return h.value if isinstance(h, psb.PsbString) else ""

    @property
    def scenes(self) -> list:
        return self.psb.root.get("scenes") or []

    @staticmethod
    def _str(node):
        return node.value if isinstance(node, psb.PsbString) else None

    @staticmethod
    def _idx(node):
        return node.index if isinstance(node, psb.PsbString) else None

    def scene_label(self, scene) -> str:
        return self._str(scene.get("label")) or ""

    # ------------------------------------------------------------------ text
    def iter_messages(self):
        for si, scene in enumerate(self.scenes):
            label = self.scene_label(scene)
            for ri, row in enumerate(scene.get("texts") or []):
                if not isinstance(row, list) or len(row) < 2:
                    continue
                attrs = row[2] if len(row) > 2 and isinstance(row[2], list) else []
                msg = Message(
                    scene=si,
                    label=label,
                    row=ri,
                    speaker=self._str(row[0]),
                    speaker_idx=self._idx(row[0]),
                    flag=row[3] if len(row) > 3 and isinstance(row[3], int) else 0,
                )
                for pi, page in enumerate(row[1] or []):
                    if not isinstance(page, list) or len(page) < 2:
                        continue
                    attr = attrs[pi] if pi < len(attrs) and isinstance(attrs[pi], dict) else {}
                    p = Page(
                        index=pi,
                        text_idx=self._idx(page[1]),
                        text=self._str(page[1]) or "",
                        length=page[2] if len(page) > 2 and isinstance(page[2], int) else -1,
                        alt_name_idx=self._idx(page[0]),
                        alt_name=self._str(page[0]),
                        voice=self._str(attr.get("voice")),
                        char_id=self._str(attr.get("name")),
                    )
                    if len(page) >= 5:
                        p.plain_idx = self._idx(page[3])
                        p.plain = self._str(page[3])
                    msg.pages.append(p)
                yield msg

    def iter_selects(self):
        for si, scene in enumerate(self.scenes):
            label = self.scene_label(scene)
            for ci, sel in enumerate(scene.get("selects") or []):
                if not isinstance(sel, dict):
                    continue
                text = sel.get("text")
                if isinstance(text, psb.PsbString):
                    yield Select(scene=si, label=label, index=ci, text_idx=text.index, text=text.value)

    # ----------------------------------------------------------------- write
    def rebuild(self, new_strings: dict[int, str]) -> bytes:
        """Re-emit the `.scn` with the given string slots replaced."""
        return self.psb.rebuild(new_strings)


def load(path: str) -> ScnFile:
    with open(path, "rb") as fp:
        return ScnFile(fp.read())


# ----------------------------------------------------------------- markup & length

import re as _re

MARKUP = _re.compile(r"%[0-9]+;|#[0-9A-Fa-f]{6,8};|#;|\\[kp]")


def page_length(text: str) -> int:
    """Value the `length` field is expected to hold (Steam/official convention).

    Measured on the Steam side: the field equals the UTF-16 length of the text with
    inline markup (`%NNN;`, `#RRGGBBAA;`, `#;`) removed -- markup characters are not
    counted, plain characters are. Line breaks written as the two characters
    backslash + n are counted as two, because the CN patch's scripts do it that way and the engine
    never complained.

    The CN patch itself stored the *raw* length (markup included) in 12,027 places,
    so this field is clearly tolerated either way; keep this helper as the single
    place that encodes the decision (see doc/text-model.md §2.2).
    """
    return u16len(MARKUP.sub("", text))
