"""Tests for the startup splash animation.

Covers frame rendering at various opacity states and verifies
the full show_splash() animation runs to completion with mocked Live.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rich.panel import Panel

from scrum_agent.ui.shared._ascii_font import render_ascii_text
from scrum_agent.ui.splash import _build_splash_frame, show_splash


class TestBuildSplashFrame:
    """Tests for _build_splash_frame rendering."""

    def test_returns_panel(self):
        """Frame builder returns a Rich Panel."""
        lines = render_ascii_text("SCRUM AGENT")
        frame = _build_splash_frame(lines, width=80, height=24)
        assert isinstance(frame, Panel)

    def test_full_opacity_brand_blue(self):
        """At opacity=1.0, the brand blue colour appears in the rendered output."""
        lines = render_ascii_text("SCRUM AGENT")
        frame = _build_splash_frame(lines, width=80, height=24, opacity=1.0)
        assert isinstance(frame, Panel)
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80, color_system="truecolor")
        console.print(frame)
        output = buf.getvalue()
        # Brand blue (70,100,180) should appear in escape codes
        assert "70" in output and "100" in output and "180" in output

    def test_zero_opacity_invisible(self):
        """At opacity=0, text chars are replaced with spaces — nothing visible."""
        lines = render_ascii_text("TEST")
        frame = _build_splash_frame(lines, width=80, height=24, opacity=0.0)
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80, color_system="truecolor")
        console.print(frame)
        output = buf.getvalue()
        # No brand blue and no block characters should appear
        assert "rgb(70,100,180)" not in output
        assert "█" not in output

    def test_empty_text(self):
        """Empty text lines produce a valid panel without errors."""
        frame = _build_splash_frame(["", ""], width=80, height=24)
        assert isinstance(frame, Panel)

    def test_text_centered(self):
        """The rendered Text object uses center justification."""
        lines = render_ascii_text("HI")
        frame = _build_splash_frame(lines, width=80, height=24, opacity=1.0)
        # Dig into the Group to find the Text renderable
        group = frame.renderable
        # Group stores renderables in _renderables
        text_items = [r for r in group.renderables if isinstance(r, from_text_cls())]
        # At least one Text should be center-justified (the ASCII art)
        assert any(t.justify == "center" for t in text_items if hasattr(t, "justify"))


def from_text_cls():
    """Return the Text class for isinstance checks."""
    from rich.text import Text

    return Text


class TestShowSplash:
    """Tests for the full show_splash() animation."""

    @patch("scrum_agent.ui.splash.time.sleep")
    @patch("scrum_agent.ui.splash.Live")
    def test_completes_without_error(self, mock_live_cls, mock_sleep):
        """show_splash runs the full animation loop and exits cleanly."""
        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)
        mock_live_cls.return_value = mock_live

        console = MagicMock()
        console.size = (80, 24)

        show_splash(console)

        mock_live.__enter__.assert_called_once()
        mock_live.__exit__.assert_called_once()
        assert mock_live.update.call_count > 0
