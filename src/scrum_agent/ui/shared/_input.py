"""Terminal input reading — raw keypress handling and bracketed paste mode.

# See README: "Architecture" — shared UI utility for reading raw keypresses.
# Used by mode_select, session, and provider_select screens. Reads single
# keypresses in cbreak mode, handles escape sequences (arrows, paste), and
# returns standardised key names ("up", "down", "enter", "esc", etc.).
"""

from __future__ import annotations

import os
import sys
import termios
import tty


def read_key(stdin=None, timeout: float | None = None) -> str:
    """Read a single keypress from the terminal in raw mode.

    If timeout is given, returns "" if no key is pressed within that time.

    Returns standardised key names:
      - "up", "down", "left", "right" — arrow keys
      - "scroll_up", "scroll_down" — mouse wheel events
      - "enter", "tab", "esc", "backspace", "clear" — special keys
      - "paste:<content>" — bracketed paste payload
      - single character — printable input

    Uses os.read() instead of file-object read() to bypass Python's internal
    read buffer.  Python's buffered I/O can pull extra bytes (e.g. the "[B"
    of an arrow-key escape sequence) into its own buffer where they become
    invisible to select(), causing escape-sequence detection to fail.
    """
    import select as _select

    fd = (stdin or sys.stdin).fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        # Disable XON/XOFF flow control so Ctrl+S (\x13) reaches the app
        # instead of freezing the terminal (XOFF). Restored by tcsetattr below.
        new_settings = termios.tcgetattr(fd)
        new_settings[0] &= ~termios.IXON  # input flags
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        if timeout is not None:
            try:
                ready, _, _ = _select.select([fd], [], [], timeout)
            except KeyboardInterrupt:
                raise
            if not ready:
                return ""

        def _read1() -> str:
            """Read exactly 1 byte from fd, bypassing Python's buffer."""
            return os.read(fd, 1).decode("utf-8", errors="replace")

        def _read_available(wait: float = 0.05) -> str:
            """Read all immediately available bytes from fd."""
            buf = ""
            while _select.select([fd], [], [], wait)[0]:
                buf += _read1()
                wait = 0.01  # shorter timeout for subsequent chars
            return buf

        ch = _read1()
        if ch == "\x1b":
            # Non-blocking check for the second byte — if nothing arrives
            # within 100ms, this is a bare Escape keypress (not an arrow
            # key or other escape sequence, which always sends \x1b[...
            # within microseconds). 100ms is imperceptible to a human
            # but safe for slow terminals / SSH connections.
            if not _select.select([fd], [], [], 0.1)[0]:
                return "esc"
            ch2 = _read1()
            if ch2 == "\x7f":
                # Alt+Backspace → delete word backward
                return "word_backspace"
            # Alt+Enter (Option+Enter on macOS) → newline
            if ch2 in ("\r", "\n"):
                return "alt+enter"
            # Alt+b / Alt+f — word-level navigation (emacs-style)
            if ch2 == "b":
                return "shift+left"
            if ch2 == "f":
                return "shift+right"
            if ch2 == "[":
                # Third byte: blocking read
                ch3 = _read1()
                if ch3 == "A":
                    return "up"
                if ch3 == "B":
                    return "down"
                if ch3 == "C":
                    return "right"
                if ch3 == "D":
                    return "left"
                # SGR mouse events: \x1b[<button;x;yM or \x1b[<button;x;ym
                # Enabled by enable_mouse_tracking(). Button 64 = scroll up,
                # 65 = scroll down. Other mouse events (clicks, motion) are
                # consumed and discarded so they don't leak to the terminal.
                # Modified keys: \x1b[1;{mod}{dir} where mod 2=Shift, 3=Alt, 5=Ctrl
                # Shift+Left/Right are used for word-level navigation.
                # CSI u (kitty keyboard protocol): \x1b[13;2u = Shift+Enter
                if ch3 == "1":
                    rest = _read_available(0.05)
                    if rest.startswith("3;2u"):
                        return "alt+enter"
                    if rest.startswith(";2D"):
                        return "shift+left"
                    if rest.startswith(";2C"):
                        return "shift+right"
                    if rest.startswith(";2A"):
                        return "shift+up"
                    if rest.startswith(";2B"):
                        return "shift+down"
                    # Alt+arrow: \x1b[1;3{dir}
                    if rest.startswith(";3D"):
                        return "shift+left"  # treat Alt+arrow same as Shift+arrow
                    if rest.startswith(";3C"):
                        return "shift+right"
                    # Ctrl+arrow: \x1b[1;5{dir}
                    if rest.startswith(";5D"):
                        return "shift+left"
                    if rest.startswith(";5C"):
                        return "shift+right"
                    return ""
                if ch3 == "3":
                    ch4 = _read1()
                    if ch4 == "~":
                        return "delete"
                    if ch4 == ";":
                        # Shift+Delete: \x1b[3;2~
                        rest = _read_available(0.05)
                        if rest.startswith("2~"):
                            return "word_delete"
                    _read_available()
                    return ""
                if ch3 == "H":
                    return "home"
                if ch3 == "F":
                    return "end"
                if ch3 == "<":
                    # Read until 'M' or 'm' (SGR terminator).
                    # 'M' = button press, 'm' = button release.
                    sgr_buf = ""
                    is_press = True
                    while True:
                        c = _read1()
                        if c == "M":
                            is_press = True
                            break
                        if c == "m":
                            is_press = False
                            break
                        sgr_buf += c
                        if len(sgr_buf) > 20:
                            break  # safety limit
                    parts = sgr_buf.split(";")
                    # Only act on press events — release events ('m') for
                    # scroll wheel would double-count each tick, causing jumps.
                    if is_press and len(parts) >= 1:
                        try:
                            button = int(parts[0])
                        except ValueError:
                            return ""
                        if button == 64:
                            return "scroll_up"
                        if button == 65:
                            return "scroll_down"
                    return ""  # consume releases & other mouse events silently
                # Legacy mouse: \x1b[M followed by 3 raw bytes (button, x, y).
                # Button byte 96 = scroll up (64+32), 97 = scroll down (65+32).
                if ch3 == "M":
                    btn = ord(_read1())
                    _read1()  # x
                    _read1()  # y
                    if btn == 96:
                        return "scroll_up"
                    if btn == 97:
                        return "scroll_down"
                    return ""  # consume other mouse events silently
                # Bracketed paste starts with \x1b[200~
                if ch3 == "2":
                    # Read remaining 3 chars of the start marker: "00~"
                    marker_rest = _read1() + _read1() + _read1()
                    if marker_rest == "00~":
                        # Blocking reads until end marker \x1b[201~
                        chars: list[str] = []
                        max_len = 10000  # safety limit
                        while len(chars) < max_len:
                            c = _read1()
                            if c == "\x1b":
                                # Potential end marker: \x1b[201~
                                m1 = _read1()
                                if m1 == "[":
                                    m2 = _read1() + _read1() + _read1() + _read1()
                                    if m2 == "201~":
                                        break  # end of paste
                                    chars.append("\x1b[" + m2)
                                else:
                                    chars.append("\x1b" + m1)
                            else:
                                chars.append(c)
                        content = "".join(chars)
                        content = content.replace("\r", "").replace("\n", "")
                        content = "".join(c for c in content if c.isprintable())
                        if content:
                            return f"paste:{content}"
                    else:
                        _read_available()
                    return ""
                # Unknown CSI sequence — drain and ignore
                _read_available()
                return ""
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\t":
            return "tab"
        if ch in ("\x7f", "\x08"):
            return "backspace"
        if ch == "\x15":
            # Ctrl+U (kill line) → clear all
            return "clear"
        if ch == "\x17":
            # Ctrl+W → delete word backward
            return "word_backspace"
        if ch == "\x0e":
            # Ctrl+N → new line (works in all terminals)
            return "alt+enter"
        if ch == "\x13":
            return "ctrl+s"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch.isprintable():
            return ch
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_settings)


def enable_bracketed_paste() -> None:
    """Enable bracketed paste mode on the terminal."""
    sys.stdout.write("\x1b[?2004h")
    sys.stdout.flush()


def disable_bracketed_paste() -> None:
    """Disable bracketed paste mode on the terminal."""
    sys.stdout.write("\x1b[?2004l")
    sys.stdout.flush()


def enable_mouse_tracking() -> None:
    """Enable mouse event reporting so scrolling stays within the app.

    Enables SGR extended mouse mode (\x1b[?1006h) on top of basic button
    tracking (\x1b[?1000h). This causes the terminal to send mouse wheel
    events as escape sequences that read_key() parses into "scroll_up" /
    "scroll_down" instead of letting the terminal scroll its own buffer.

    Also enables bracketed paste mode (\x1b[?2004h) so Cmd+V / Ctrl+V
    sends paste content as an escape sequence that read_key() can parse,
    rather than being swallowed by mouse tracking.
    """
    sys.stdout.write("\x1b[?1000h")  # enable basic mouse tracking
    sys.stdout.write("\x1b[?1006h")  # enable SGR extended mode
    sys.stdout.write("\x1b[?2004h")  # enable bracketed paste mode
    sys.stdout.flush()


def disable_mouse_tracking() -> None:
    """Disable mouse event reporting — restore normal terminal behaviour."""
    sys.stdout.write("\x1b[?2004l")  # disable bracketed paste mode
    sys.stdout.write("\x1b[?1006l")
    sys.stdout.write("\x1b[?1000l")
    sys.stdout.flush()
