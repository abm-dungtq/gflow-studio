"""``gflow run --cast cast.json``: name a character in a prompt, get its reference image.

A cast file maps each character name to one approved reference image::

    {"Ngan": "characters/ngan/design.png", "OngTu": "characters/tu-duc/design.png", "Lai": null}

Paths are relative to the cast file. For every ``@Name`` a row's text mentions, the
character's image is appended to the row's ``refs`` under that name, so the existing
reference-alias step turns ``@Name`` into ``reference image N`` and the image is uploaded
once per run. A row's own ``ref``/``refs`` keep precedence: a name they already carry is
left alone.

A mention the cast cannot serve is reported, never dropped: a character listed with
``null`` has no approved image yet, and a name missing from the cast is left for Flow's
own ``@`` mention lookup (a saved Character or a named asset in the project).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from gflow_cli.api.image import Model, reference_cap_for
from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import ALLOWED_MODELS, DEFAULT_MODEL
from gflow_cli.ref_aliases import (
    MENTION_TOKEN_RE,
    default_ref_name,
    is_valid_ref_name,
    parse_config_ref_entry,
)


@dataclass
class Cast:
    """Character name (case-folded) -> ``(name as written in the cast, image or None)``."""

    members: dict[str, tuple[str, str | None]]
    # (kind, name) -> rows; kept per name so a long run reports each name once.
    _missed: dict[tuple[str, str], list[int]] = field(
        default_factory=lambda: defaultdict(list[int])
    )

    @property
    def warnings(self) -> list[str]:
        """One line per name the cast could not serve, naming the rows that mention it."""
        lines: list[str] = []
        for (kind, name), rows in self._missed.items():
            where = f"prompts {', '.join(str(r) for r in rows)}"
            lines.append(
                {
                    "unknown": f"@{name} is not in the cast ({where}); "
                    "Flow will look it up as a saved character.",
                    "no_ref": f"@{name} has no reference image in the cast yet ({where}).",
                    "bare": f"{name} is named without @ ({where}); no reference image attached.",
                }[kind]
            )
        return lines

    @classmethod
    def load(cls, path: Path) -> Cast:
        """Read and check a cast file; any mistake is exit 11 before a browser opens."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(detail=f"--cast {path}: cannot read the file.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError(detail=f"--cast {path}: not valid JSON ({exc}).") from exc
        if not isinstance(data, dict) or not data:
            raise ConfigurationError(
                detail=f"--cast {path}: must be a JSON object of name -> image path.",
                remediation_hint='Example: {"Ngan": "characters/ngan/design.png"}',
            )
        members: dict[str, tuple[str, str | None]] = {}
        for name, raw in cast("dict[str, Any]", data).items():
            if not is_valid_ref_name(name):
                raise ConfigurationError(
                    detail=f"--cast {path}: name {name!r} must match ^[\\w-]+$.",
                    remediation_hint="Write the name as one word, as it appears after @.",
                )
            key = name.casefold()
            if key in members:
                raise ConfigurationError(detail=f"--cast {path}: {name!r} is listed twice.")
            if raw is None:
                members[key] = (name, None)
                continue
            if not isinstance(raw, str) or not raw.strip():
                raise ConfigurationError(
                    detail=f"--cast {path}: {name!r} must be an image path or null."
                )
            image = (path.parent / Path(raw).expanduser()).resolve()
            if not image.is_file():
                raise ConfigurationError(
                    detail=f"--cast {path}: {name!r} image {raw!r} does not exist.",
                    remediation_hint="Fix the path, or set it to null until the image exists.",
                )
            members[key] = (name, str(image))
        return cls(members=members)

    def apply(self, row: dict[str, Any], idx: int) -> dict[str, Any]:
        """Return ``row`` with the images of the characters its text mentions added."""
        text = row.get("text")
        if not isinstance(text, str):
            return row
        mentioned = {
            m.group(1).casefold(): m.group(1) for m in MENTION_TOKEN_RE.finditer(text) if m.group(1)
        }
        for key, (name, _) in self.members.items():
            if key not in mentioned and re.search(rf"(?<![@\w]){re.escape(name)}(?![\w-])", text):
                self._missed["bare", name].append(idx)
        refs = row.get("refs")
        if (refs is not None and row.get("ref") is not None) or (
            refs is not None and not isinstance(refs, list)
        ):
            return row  # malformed; the row parser refuses it with its own message
        entries = _row_refs(row)
        taken = {name.casefold() for name in _ref_names(entries, idx)}
        added: list[Any] = []
        for key, token in mentioned.items():
            if key in taken:
                continue
            member = self.members.get(key)
            if member is None:
                self._missed["unknown", token].append(idx)
            elif member[1] is None:
                self._missed["no_ref", member[0]].append(idx)
            else:
                added.append({"name": member[0], "path": member[1]})
        if not added:
            return row
        model = row.get("model", DEFAULT_MODEL)
        if model in ALLOWED_MODELS:
            cap = reference_cap_for(Model.from_cli(model))
            if len(entries) + len(added) > cap:
                raise ConfigurationError(
                    detail=(
                        f"prompts[{idx}] needs {len(entries) + len(added)} reference images "
                        f"({len(added)} from the cast), but model {model!r} takes at most {cap}."
                    ),
                    remediation_hint="Mention fewer characters, or use a model with a higher cap.",
                )
        out = {k: v for k, v in row.items() if k not in ("ref", "refs")}
        out["refs"] = [*entries, *added]
        return out


def _row_refs(row: dict[str, Any]) -> list[Any]:
    if row.get("refs") is not None:
        return list(cast("list[Any]", row["refs"]))
    return [] if row.get("ref") is None else [row["ref"]]


def _ref_names(entries: list[Any], idx: int) -> list[str]:
    names: list[str] = []
    for pos, entry in enumerate(entries):
        explicit, path = parse_config_ref_entry(entry, label=f"prompts[{idx}].refs[{pos}]")
        name = explicit or default_ref_name(path)
        if name:
            names.append(name)
    return names
