"""Skills as folders with a SKILL.md, loaded when relevant (idea 62).

A skill is a folder under data/skills/ containing:
- SKILL.md: name, description, trigger patterns, the instructions the model follows
- Optionally: example files, templates, scripts

Skills are matched by keyword/intent and injected into the system prompt when relevant.
The model can also create new skills from recorded macros (idea 63).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _parse_skill_md(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta: dict[str, Any] = {"path": str(path.parent), "file": str(path)}
    for line in text.split("\n"):
        line = line.strip()
        if line.lower().startswith("# "):
            meta["name"] = line[2:].strip()
        elif line.lower().startswith("trigger:"):
            meta["triggers"] = [t.strip().lower() for t in line.split(":", 1)[1].split(",")]
        elif line.lower().startswith("description:"):
            meta["description"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("auto:"):
            meta["auto"] = line.split(":", 1)[1].strip().lower() in ("true", "yes", "1")
    meta["instructions"] = text
    return meta


class SkillRegistry:
    """Discovers and matches skills from the data/skills/ directory."""

    def __init__(self, skills_dir: Path | str) -> None:
        self.dir = Path(skills_dir) if not isinstance(skills_dir, Path) else skills_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._skills: list[dict[str, Any]] = []
        self.reload()

    def reload(self) -> None:
        self._skills = []
        if not self.dir.exists():
            return
        for skill_dir in sorted(self.dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            md = skill_dir / "SKILL.md"
            if md.exists():
                parsed = _parse_skill_md(md)
                if parsed:
                    self._skills.append(parsed)

    def list_skills(self) -> list[dict[str, Any]]:
        return [{"name": s.get("name", ""), "description": s.get("description", ""),
                 "triggers": s.get("triggers", []), "auto": s.get("auto", False),
                 "path": s.get("path", "")} for s in self._skills]

    def match(self, text: str) -> list[dict[str, Any]]:
        """Find skills whose triggers match the user's text."""
        lower = text.lower()
        matched = []
        for skill in self._skills:
            triggers = skill.get("triggers", [])
            if any(t in lower for t in triggers):
                matched.append(skill)
        return matched

    def get(self, name: str) -> dict[str, Any] | None:
        for skill in self._skills:
            if skill.get("name", "").lower() == name.lower():
                return skill
        return None

    def context_for_model(self, text: str) -> str:
        """Build skill context for the system prompt based on the user's request."""
        matched = self.match(text)
        if not matched:
            return ""
        parts = ["Active skills:"]
        for skill in matched[:3]:
            parts.append(f"\n--- Skill: {skill.get('name', 'unnamed')} ---")
            parts.append(skill.get("instructions", ""))
        return "\n".join(parts)

    def create(self, name: str, description: str, triggers: list[str], instructions: str) -> Path:
        """Create a new skill from a macro or user request."""
        safe = re.sub(r"[^a-z0-9_-]", "_", name.lower())[:32]
        skill_dir = self.dir / safe
        skill_dir.mkdir(parents=True, exist_ok=True)
        md = skill_dir / "SKILL.md"
        content = f"# {name}\nDescription: {description}\nTrigger: {', '.join(triggers)}\nAuto: false\n\n{instructions}"
        md.write_text(content, encoding="utf-8")
        self.reload()
        return skill_dir
