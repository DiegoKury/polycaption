import signal
import threading
import tkinter as tk
import ctypes
import win32gui
import win32con
from datetime import datetime


class Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.88)
        self.root.overrideredirect(True)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        top, bottom_margin = 10, 50
        self._win_h = sh - top - bottom_margin
        self._win_top = top
        dw = 520
        self._dialog_x = 18
        self.root.geometry(f'{dw}x{self._win_h}+{self._dialog_x}+{top}')
        self.root.configure(bg='#21262d', cursor='arrow')

        # Outer border (1px border effect via bg color bleed)
        self._border = tk.Frame(self.root, bg='#21262d')
        self._border.pack(expand=True, fill='both', padx=1, pady=1)

        # ── Header bar ──
        header = tk.Frame(self._border, bg='#161b22', height=28, cursor='arrow')
        header.pack(fill='x')
        header.pack_propagate(False)

        tk.Label(
            header, text='●', fg='#79c0ff', bg='#161b22',
            font=('Segoe UI', 7), padx=6
        ).pack(side='left')
        tk.Label(
            header, text='DIALOG', fg='#79c0ff', bg='#161b22',
            font=('Segoe UI', 8, 'bold')
        ).pack(side='left')

        # Divider under header
        tk.Frame(self._border, bg='#30363d', height=1).pack(fill='x')

        # ── Footer (pack bottom-up before chat) ──

        # Hotkey bar at very bottom
        footer = tk.Frame(self._border, bg='#161b22', cursor='arrow')
        footer.pack(fill='x', side='bottom')

        # Divider above footer
        tk.Frame(self._border, bg='#30363d', height=1).pack(fill='x', side='bottom')

        hotkey_text = '↑↓: Scroll   ←→: Move/Resize   −/+: Opacity   Q: Quit'
        tk.Label(
            footer,
            text=hotkey_text,
            bg='#161b22', fg='#484f58',
            font=('Segoe UI', 6), anchor='w', padx=8, pady=3
        ).pack(side='left')

        self.cost_label = tk.Label(
            footer, text='',
            bg='#161b22', fg='#484f58',
            font=('Segoe UI', 6), anchor='e', padx=8, pady=3
        )
        self.cost_label.pack(side='right')

        # Hidden text input for manual questions
        self._input_frame = tk.Frame(self._border, bg='#161b22')
        self._input_entry = tk.Entry(
            self._input_frame, bg='#1c2128', fg='#c9d1d9',
            insertbackground='#c9d1d9', font=('Segoe UI', 9),
            relief='flat', bd=4
        )
        self._input_entry.pack(fill='x', padx=8, pady=4)
        self._input_entry.bind('<Return>', self._on_input_submit)
        self._input_entry.bind('<Escape>', lambda e: self.unfocus_input())
        self._input_visible = False
        self._input_callback = None
        self._prev_hwnd = None

        # ── Chat area ──
        self.chat = tk.Text(
            self._border, bg='#0d1117', fg='#c9d1d9',
            font=('Segoe UI', 16), wrap='word',
            state='disabled', cursor='arrow',
            relief='flat', bd=0, padx=12, pady=6,
            spacing3=2
        )
        self.chat.pack(expand=True, fill='both')

        # Transcript text is the whole point here, so everything is rendered large.
        self.chat.tag_configure('time',
            foreground='#3d444d', font=('Segoe UI', 12), justify='right')
        self.chat.tag_configure('msg',
            foreground='#79c0ff', font=('Segoe UI', 16), justify='right',
            spacing1=1, spacing3=5)
        # Streaming text (same as msg but no spacing — gets re-rendered on end)
        self.chat.tag_configure('stream',
            foreground='#79c0ff', font=('Segoe UI', 16), justify='right')
        # Section headers (# / ## / ###)
        self.chat.tag_configure('header',
            foreground='#e6edf3', font=('Segoe UI', 18, 'bold'),
            justify='right', spacing1=5, spacing3=2)
        # Bullet / numbered list items
        self.chat.tag_configure('bullet',
            foreground='#79c0ff', font=('Segoe UI', 16),
            justify='right', spacing3=1)
        # Inline bold within msg/bullet lines
        self.chat.tag_configure('bold',
            foreground='#e6edf3', font=('Segoe UI', 16, 'bold'),
            justify='right')
        # Audio transcript / heard text ([You]:)
        self.chat.tag_configure('audio',
            foreground='#484f58', font=('Segoe UI', 14, 'italic'),
            justify='right', spacing3=2)
        # Speaker heard text
        self.chat.tag_configure('speaker',
            foreground='#484f58', font=('Segoe UI', 14, 'italic'),
            justify='right', spacing3=2)
        # Status messages (speaker stopped, etc.)
        self.chat.tag_configure('status',
            foreground='#f0883e', font=('Segoe UI', 14, 'bold'),
            justify='center', spacing1=4, spacing3=4)
        # Live partial transcript (tentative, refined in place)
        self.chat.tag_configure('live',
            foreground='#6e7681', font=('Segoe UI', 14, 'italic'),
            justify='right', spacing3=2)

        # Streaming state
        self._stream_mark = None
        self._stream_active = False
        self._at_bottom = True
        self._last_block = None  # 'response' | 'you' | 'them' — separate turns with a blank line

        # Store footer ref for input packing
        self._footer = footer

        # Live partial-transcript state. Worker threads only set _live_pending; the
        # main-thread poller (_live_poll) renders it inline at the bottom of the chat.
        # Tkinter isn't thread-safe, so worker threads must never touch widgets.
        self._live_pending = None   # ('show', text) | ('clear', None)
        self._live_mark = None

        self.root.update()
        self._hwnd = self._make_clickthrough(self.root)
        self.root.after(2000, self._keep_on_top)
        self.root.after(150, self._live_poll)

    def _make_clickthrough(self, win):
        """Make a window click-through, top-most, and hidden from screen capture."""
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                               win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT)
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
        win32gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        return hwnd

    def _keep_on_top(self):
        try:
            win32gui.SetWindowPos(self._hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
            self.root.attributes('-topmost', True)
            self.root.lift()
        except Exception:
            pass
        self.root.after(2000, self._keep_on_top)

    @staticmethod
    def _block_of(text):
        """Classify a chat line so consecutive turns from different sources get a gap."""
        if text.startswith('[You]'):
            return 'you'
        if text.startswith('[Them]'):
            return 'them'
        return 'other'

    def _separate(self, block):
        """Insert a blank line if this block differs from the previous one. Main thread."""
        if self._last_block is not None and self._last_block != block:
            self.chat.insert('end', '\n', 'msg')
        self._last_block = block

    def post(self, text, kind='response'):
        """Thread-safe: add a message. kind='response' (blue) or kind='audio' (gray italic)."""
        def update(t=text):
            self.chat.configure(state='normal')
            self._drop_live_line()  # a finalized message replaces any tentative line
            if kind in ('audio', 'speaker'):
                self._separate(self._block_of(t))
            if kind == 'status':
                self.chat.insert('end', f'{t}\n', 'status')
            elif kind == 'audio':
                self.chat.insert('end', f'{t}\n', 'audio')
            elif kind == 'speaker':
                self.chat.insert('end', f'{t}\n', 'speaker')
            else:
                self._render_response(t)
            self.chat.configure(state='disabled')
            if self._at_bottom:
                self.chat.see('end')
        self.root.after(0, update)

    def _insert_bold(self, text, base_tag):
        """Insert a line, rendering **bold** spans."""
        for j, b in enumerate(text.split('**')):
            if b:
                self.chat.insert('end', b, 'bold' if j % 2 == 1 else base_tag)
        self.chat.insert('end', '\n', base_tag)

    def _render_response(self, text):
        """Render a complete response: parses headers, bullets, and bold text."""
        ts = datetime.now().strftime('%H:%M')
        self.chat.insert('end', f'{ts}\n', 'time')
        for line in text.split('\n'):
            s = line.strip()
            if not s:
                self.chat.insert('end', '\n', 'msg')
            elif s.startswith('#'):
                self.chat.insert('end', s.lstrip('#').strip() + '\n', 'header')
            elif s.startswith(('- ', '* ', '• ')):
                self._insert_bold('• ' + s[2:], 'bullet')
            elif len(s) > 2 and s[0].isdigit() and s[1] in '.):' and s[2] == ' ':
                self._insert_bold(s[0] + '. ' + s[3:], 'bullet')
            else:
                self._insert_bold(s, 'msg')
        self._last_block = 'response'

    def begin_stream(self):
        """Start a new streaming response. Insert timestamp, mark position."""
        self._stream_active = True  # set now so the live poller stops rendering immediately
        def update():
            self.chat.configure(state='normal')
            self._drop_live_line()
            ts = datetime.now().strftime('%H:%M')
            self.chat.insert('end', f'{ts}\n', 'time')
            self.chat.mark_set('stream_start', 'end-1c')
            self.chat.mark_gravity('stream_start', 'left')
            self._stream_mark = 'stream_start'
            self.chat.configure(state='disabled')
            if self._at_bottom:
                self.chat.see('end')
        self.root.after(0, update)

    def append_stream(self, chunk):
        """Append a text chunk during streaming (raw, no formatting)."""
        def update():
            self.chat.configure(state='normal')
            self.chat.insert('end', chunk, 'stream')
            self.chat.configure(state='disabled')
            if self._at_bottom:
                self.chat.see('end')
        self.root.after(0, update)

    def end_stream(self, full_text):
        """Finalize streaming: clear raw text, re-render with proper formatting."""
        def update():
            if self._stream_mark:
                self.chat.configure(state='normal')
                self.chat.delete(self._stream_mark, 'end')
                self._render_response(full_text)
                self.chat.configure(state='disabled')
                if self._at_bottom:
                    self.chat.see('end')
                self._stream_mark = None
            self._stream_active = False
        self.root.after(0, update)

    def update_cost(self, total_cost):
        """Thread-safe: update the cost display."""
        def update():
            self.cost_label.configure(text=f'${total_cost:.4f}')
        self.root.after(0, update)

    def live_transcript(self, text):
        """Thread-safe: queue a live-line update. Does NOT touch Tkinter — only sets a
        flag the main-thread poller reads. Calling Tk from worker threads is unreliable."""
        self._live_pending = ('show', text)

    def clear_live_transcript(self):
        """Thread-safe: queue hiding the live line (read by the main-thread poller)."""
        self._live_pending = ('clear', None)

    def _live_poll(self):
        """Main-thread loop: applies the latest queued live-line update. While a reply
        is streaming, 'show' updates are held back so they can't corrupt the stream."""
        pending = self._live_pending
        if pending is not None:
            action, text = pending
            if action == 'clear':
                self._live_pending = None
                self._clear_live()
            elif not self._stream_active:
                self._live_pending = None
                if text:
                    self._render_live(text)
            # else: a reply is streaming — leave pending, render once it finishes
        self.root.after(120, self._live_poll)

    def _render_live(self, text):
        """Insert/replace the inline tentative live line at the bottom of the chat.
        Replaces up to 'end-1c' (not 'end') and inserts at the mark, so the live line's
        trailing newline survives each cycle — otherwise repeated partials chew through
        the blank-line separators above them."""
        self.chat.configure(state='normal')
        if self._live_mark is None:
            self._separate(self._block_of(text))
            self.chat.mark_set('live_start', 'end-1c')
            self.chat.mark_gravity('live_start', 'left')
            self._live_mark = 'live_start'
        else:
            self.chat.delete('live_start', 'end-1c')
        # Make the in-progress line look like the final product: white bold EN/ES
        # labels, blue body (same tags as a committed response), not tentative gray.
        for line in text.split('\n'):
            self._insert_bold(line, 'msg')
        self.chat.configure(state='disabled')
        if self._at_bottom:
            self.chat.see('end')

    def _clear_live(self):
        """Remove the inline live line, managing the chat's read-only state."""
        if self._live_mark is not None:
            self.chat.configure(state='normal')
            self._drop_live_line()
            self.chat.configure(state='disabled')

    def _drop_live_line(self):
        """Delete the live line if present. Assumes chat is already in 'normal' state."""
        if self._live_mark is not None:
            self.chat.delete('live_start', 'end-1c')
            self._live_mark = None

    def move(self, dx):
        """Move the overlay horizontally by dx pixels, clamped on screen."""
        def update():
            sw = self.root.winfo_screenwidth()
            w = self.root.winfo_width()
            x = max(0, min(sw - w, self.root.winfo_x() + dx))
            self.root.geometry(f'{w}x{self.root.winfo_height()}+{x}+{self.root.winfo_y()}')
        self.root.after(0, update)

    def resize_width(self, dw):
        """Resize the overlay's width, keeping its right edge anchored."""
        def update():
            sw = self.root.winfo_screenwidth()
            d_right = self.root.winfo_x() + self.root.winfo_width()
            new_dw = max(200, min(sw, self.root.winfo_width() + dw))
            d_x = max(0, min(sw - new_dw, d_right - new_dw))
            self.root.geometry(f'{new_dw}x{self.root.winfo_height()}+{d_x}+{self.root.winfo_y()}')
        self.root.after(0, update)

    def adjust_opacity(self, delta):
        """Bump the window's alpha by delta, clamped to [0.1, 0.9]."""
        def update():
            try:
                current = float(self.root.attributes('-alpha'))
            except Exception:
                current = 0.88
            new = max(0.1, min(0.9, current + delta))
            self.root.attributes('-alpha', new)
        self.root.after(0, update)

    def scroll_up(self):
        """Scroll up through dialog history."""
        def update():
            self._at_bottom = False
            self.chat.yview_scroll(-5, 'units')
        self.root.after(0, update)

    def scroll_down(self):
        """Scroll down. If at bottom, re-enable auto-scroll."""
        def update():
            self.chat.yview_scroll(5, 'units')
            if self.chat.yview()[1] >= 1.0:
                self._at_bottom = True
        self.root.after(0, update)

    def focus_input(self, callback):
        """Show text input, remove click-through, focus. callback(text) called on Enter."""
        self._input_callback = callback
        def update():
            # Remember which window had focus before
            self._prev_hwnd = win32gui.GetForegroundWindow()
            # Remove WS_EX_TRANSPARENT so we can type
            ex_style = win32gui.GetWindowLong(self._hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(self._hwnd, win32con.GWL_EXSTYLE,
                                   ex_style & ~win32con.WS_EX_TRANSPARENT)
            # Show input above footer
            self._input_frame.pack(fill='x', side='bottom', before=self._footer)
            self._input_entry.delete(0, 'end')
            self._input_visible = True
            # Force window to front (Alt trick bypasses Windows restriction)
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
            win32gui.SetForegroundWindow(self._hwnd)
            self._input_entry.focus_force()
        self.root.after(0, update)

    def unfocus_input(self):
        """Hide input, re-enable click-through, return focus to previous window."""
        def update():
            self._input_frame.pack_forget()
            self._input_visible = False
            self._input_callback = None
            # Re-add WS_EX_TRANSPARENT
            ex_style = win32gui.GetWindowLong(self._hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(self._hwnd, win32con.GWL_EXSTYLE,
                                   ex_style | win32con.WS_EX_TRANSPARENT)
            # Return focus to previous window
            if self._prev_hwnd and win32gui.IsWindow(self._prev_hwnd):
                try:
                    win32gui.SetForegroundWindow(self._prev_hwnd)
                except Exception:
                    pass
                self._prev_hwnd = None
        self.root.after(0, update)

    def _on_input_submit(self, event):
        """Called when Enter is pressed in the input field."""
        text = self._input_entry.get().strip()
        callback = self._input_callback
        self.unfocus_input()
        if text and callback:
            threading.Thread(target=callback, args=(text,), daemon=True).start()

    def clear(self):
        """Clear all messages."""
        def update():
            self.chat.configure(state='normal')
            self.chat.delete('1.0', 'end')
            self.chat.configure(state='disabled')
        self.root.after(0, update)

    def run(self, worker=None):
        """Run mainloop. If worker provided, run it in a background thread."""
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        if worker:
            threading.Thread(target=worker, daemon=True).start()
        self.root.mainloop()

    def quit(self):
        self.root.quit()
