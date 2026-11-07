"""Per-level notes the model writes for itself: one lesson per failed attempt,
kept in agent/notes/level_NN.md and shown in the prompt on later attempts."""
from __future__ import annotations

from pathlib import Path

NOTES_DIR = Path(__file__).resolve().parent / "notes"
MAX_LESSONS = 8


class LevelNotes:
    def __init__(self, level: int, notes_dir: str | Path = NOTES_DIR):
        self.path = Path(notes_dir) / f"level_{level:02d}.md"

    def lessons(self) -> list[str]:
        if not self.path.exists():
            return []
        return [line[2:].strip() for line in self.path.read_text().splitlines() if line.startswith("- ")]

    def text(self) -> str:
        return "\n".join(f"- {lesson}" for lesson in self.lessons())

    def add(self, lesson: str) -> None:
        lesson = " ".join(lesson.split())[:300]
        if not lesson:
            return
        lessons = (self.lessons() + [lesson])[-MAX_LESSONS:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        level = self.path.stem.removeprefix("level_")
        self.path.write_text(f"# Level {int(level)} notes (written by the model)\n\n"
                             + "\n".join(f"- {l}" for l in lessons) + "\n")
