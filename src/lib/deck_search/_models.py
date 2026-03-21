"""Data models for online deck search results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeckSearchResult:
    """Compact metadata for a deck found via online search."""

    name: str
    author: str
    url: str
    source: str
    format: str = ""
    colors: list[str] = field(default_factory=list)
    date: str = ""
    views: int = 0

    def format_text(self) -> str:
        """Single-line summary for tool output."""
        colors_str: str = "".join(self.colors) if self.colors else "?"
        parts: list[str] = [
            f"[{self.source}]",
            f'"{self.name}"',
            f"by {self.author}" if self.author else "",
            f"| {self.format}" if self.format else "",
            f"| {colors_str}",
        ]
        if self.date:
            parts.append(f"| {self.date}")
        parts.append(f"| {self.url}")
        return " ".join(p for p in parts if p)


@dataclass
class DeckDetails:
    """Full card list for a single online deck."""

    name: str
    author: str
    url: str
    source: str
    format: str = ""
    colors: list[str] = field(default_factory=list)
    mainboard: dict[str, int] = field(default_factory=dict)
    sideboard: dict[str, int] = field(default_factory=dict)
    commander: list[str] = field(default_factory=list)

    def format_text(self) -> str:
        """Multi-line deck listing for tool output."""
        lines: list[str] = [
            f"Deck: {self.name}",
            f"Source: {self.source} | {self.url}",
        ]
        if self.author:
            lines.append(f"Author: {self.author}")
        if self.format:
            lines.append(f"Format: {self.format}")
        if self.colors:
            lines.append(f"Colors: {''.join(self.colors)}")
        if self.commander:
            lines.append(f"Commander: {', '.join(self.commander)}")

        total_main: int = sum(self.mainboard.values())
        lines.append(f"\nMainboard ({total_main} cards):")
        for card_name, qty in sorted(self.mainboard.items()):
            lines.append(f"  {qty} {card_name}")

        if self.sideboard:
            total_sb: int = sum(self.sideboard.values())
            lines.append(f"\nSideboard ({total_sb} cards):")
            for card_name, qty in sorted(self.sideboard.items()):
                lines.append(f"  {qty} {card_name}")

        return "\n".join(lines)
