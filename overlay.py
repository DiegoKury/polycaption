import signal
import threading
import tkinter as tk
import ctypes
import win32gui
import win32con
from datetime import datetime


# Colours (GitHub-dark-ish, matching the original look).
BG = '#0d1117'
BORDER = '#21262d'
PANEL = '#161b22'
DIV = '#30363d'
BLUE = '#79c0ff'
WHITE = '#e6edf3'
TS = '#3d444d'
FOOT = '#484f58'

CELL_FONT = ('Segoe UI', 16)
HEAD_FONT = ('Segoe UI', 9, 'bold')


class Overlay:
    """Click-through, capture-hidden transcript overlay. The content area is split into
    one column per configured language (1, 2, or 3 columns) so each phrase's languages
    sit side by side instead of stacked. All columns share a single vertical scroll."""

    def __init__(self, labels=None):
        self._labels = list(labels) if labels else ['EN', 'ES']
        self._ncols = max(1, len(self._labels))

        self.root = tk.Tk()
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.88)
        self.root.overrideredirect(True)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        top, bottom_margin = 10, 50
        self._win_h = int((sh - top - bottom_margin) * 0.7)  # ~30% shorter than full height
        # Wider by default so multiple columns have room; ~250px per column.
        dw = min(sw - 36, 260 * self._ncols + 24)
        self._dialog_x = 18
        self.root.geometry(f'{dw}x{self._win_h}+{self._dialog_x}+{top}')
        self.root.configure(bg=BORDER, cursor='arrow')

        self._border = tk.Frame(self.root, bg=BORDER)
        self._border.pack(expand=True, fill='both', padx=1, pady=1)

        # ── Header bar ──
        header = tk.Frame(self._border, bg=PANEL, height=28, cursor='arrow')
        header.pack(fill='x')
        header.pack_propagate(False)
        tk.Label(header, text='●', fg=BLUE, bg=PANEL, font=('Segoe UI', 7), padx=6).pack(side='left')
        tk.Label(header, text='DIALOG', fg=BLUE, bg=PANEL, font=('Segoe UI', 8, 'bold')).pack(side='left')
        tk.Frame(self._border, bg=DIV, height=1).pack(fill='x')

        # ── Footer (packed bottom-up before the content) ──
        footer = tk.Frame(self._border, bg=PANEL, cursor='arrow')
        footer.pack(fill='x', side='bottom')
        tk.Frame(self._border, bg=DIV, height=1).pack(fill='x', side='bottom')
        hotkey_text = '↑↓: Scroll   ←→: Move/Resize   −/+: Opacity   Q: Quit'
        tk.Label(footer, text=hotkey_text, bg=PANEL, fg=FOOT,
                 font=('Segoe UI', 6), anchor='w', padx=8, pady=3).pack(side='left')

        # ── Content area: centered column headers + scrollable rows, divided by bars ──
        self._content = tk.Frame(self._border, bg=BG)
        self._content.pack(expand=True, fill='both')

        # Column header row (fixed, not scrolled): centered language codes.
        self._colhead = tk.Frame(self._content, bg=BG)
        self._colhead.pack(fill='x')
        for i, lab in enumerate(self._labels):
            self._colhead.grid_columnconfigure(i, weight=1, uniform='col')
            tk.Label(self._colhead, text=lab, fg=WHITE, bg=BG, font=HEAD_FONT,
                     anchor='center', pady=4).grid(row=0, column=i, sticky='ew')
        tk.Frame(self._content, bg=DIV, height=1).pack(fill='x')

        self.canvas = tk.Canvas(self._content, bg=BG, highlightthickness=0,
                                bd=0, yscrollincrement=24)
        self.canvas.pack(expand=True, fill='both')
        self._rows = tk.Frame(self.canvas, bg=BG)
        self._rows_id = self.canvas.create_window((0, 0), window=self._rows, anchor='nw')
        self._rows.bind('<Configure>', lambda e: self._sync_scrollregion())
        self.canvas.bind('<Configure>', self._on_canvas_resize)

        # Full-height vertical bars between columns. Placed (not gridded) so they span the
        # header and the whole scroll area as continuous dividers and track resizes.
        self._dividers = []
        for i in range(1, self._ncols):
            d = tk.Frame(self._content, bg=DIV, width=1)
            d.place(relx=i / self._ncols, rely=0.0, relheight=1.0, width=1)
            self._dividers.append(d)

        # All cell labels currently shown, so we can re-wrap them on resize.
        self._cells = []
        self._cell_wrap = 220
        self._at_bottom = True
        self._last_block = None  # 'you' | 'them' | 'response' — gap between differing turns

        # Live partial row. Worker threads only set _live_pending; the main-thread poller
        # (_live_poll) applies it. Tkinter isn't thread-safe, so workers never touch widgets.
        self._live_pending = None   # ('show', cells) | ('clear', None)
        self._live_row = None

        self.root.update()
        self._hwnd = self._make_clickthrough(self.root)
        self.root.after(2000, self._keep_on_top)
        self.root.after(150, self._live_poll)

    # ── Window plumbing (unchanged behaviour) ──

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

    def _on_canvas_resize(self, event):
        """Keep the inner frame as wide as the canvas and re-wrap every cell so the text
        fills its column at the new width."""
        self.canvas.itemconfigure(self._rows_id, width=event.width)
        self._cell_wrap = max(60, event.width // self._ncols - 22)
        for cell in self._cells:
            try:
                cell.configure(wraplength=self._cell_wrap)
            except Exception:
                pass

    # ── Row building ──

    @staticmethod
    def _block_of(cells):
        """Classify a row so consecutive turns from different sources get a gap. The
        speaker tag (if any) is carried on the first cell as a leading '[You]'/'[Them]'."""
        head = (cells[0] if cells else '') or ''
        if head.startswith('[You]'):
            return 'you'
        if head.startswith('[Them]'):
            return 'them'
        return 'other'

    def _norm(self, cells):
        """Force `cells` to exactly one entry per column."""
        cells = list(cells or [])
        if len(cells) < self._ncols:
            cells += [''] * (self._ncols - len(cells))
        return cells[:self._ncols]

    def _make_row(self, cells, *, live=False, timestamp=True):
        """Create one phrase row: a thin timestamp line spanning all columns, then one
        wrapped cell per language. Returns (row_frame, [cell_labels]). Main thread only."""
        cells = self._norm(cells)
        row = tk.Frame(self._rows, bg=BG)
        row.pack(fill='x', anchor='n')
        for i in range(self._ncols):
            row.grid_columnconfigure(i, weight=1, uniform='col')
        if timestamp and not live:
            ts = datetime.now().strftime('%H:%M')
            tk.Label(row, text=ts, fg=TS, bg=BG, font=('Segoe UI', 9),
                     anchor='e', padx=10).grid(row=0, column=0, columnspan=self._ncols, sticky='ew')
        # In-progress text looks identical to the final product (same blue, same font) —
        # only the missing timestamp and the fact that it gets replaced mark it as live.
        labels = []
        for i, text in enumerate(cells):
            lab = tk.Label(row, text=text, fg=BLUE, bg=BG, font=CELL_FONT, justify='left',
                           anchor='nw', wraplength=self._cell_wrap, padx=10, pady=2)
            lab.grid(row=1, column=i, sticky='nsew')
            labels.append(lab)
        return row, labels

    def _sync_scrollregion(self):
        self.canvas.configure(scrollregion=self.canvas.bbox('all') or (0, 0, 0, 0))

    def _scroll_to_bottom_if_following(self):
        if self._at_bottom:
            self.canvas.update_idletasks()
            self._sync_scrollregion()
            self.canvas.yview_moveto(1.0)

    # ── Public API (thread-safe) ──

    def post(self, cells, kind='response'):
        """Thread-safe: add a finalized phrase row, one cell per language column."""
        def update():
            self._drop_live_row()
            block = self._block_of(cells)
            if self._last_block is not None and self._last_block != block:
                spacer = tk.Frame(self._rows, bg=BG, height=8)
                spacer.pack(fill='x')
            self._last_block = block
            _, labels = self._make_row(cells)
            self._cells.extend(labels)
            self._scroll_to_bottom_if_following()
        self.root.after(0, update)

    def live_transcript(self, cells):
        """Thread-safe: queue a live-line update (does NOT touch Tkinter)."""
        self._live_pending = ('show', list(cells) if not isinstance(cells, str) else [cells])

    def clear_live_transcript(self):
        """Thread-safe: queue hiding the live line."""
        self._live_pending = ('clear', None)

    def _live_poll(self):
        """Main-thread loop: apply the latest queued live-line update."""
        pending = self._live_pending
        if pending is not None:
            action, cells = pending
            self._live_pending = None
            if action == 'clear':
                self._drop_live_row()
            elif cells and any((c or '').strip() for c in cells):
                self._render_live(cells)
        self.root.after(120, self._live_poll)

    def _render_live(self, cells):
        """Insert/replace the tentative live row at the bottom, in place to avoid flicker."""
        cells = self._norm(cells)
        if self._live_row is not None:
            row, labels = self._live_row
            for lab, text in zip(labels, cells):
                lab.configure(text=text)
        else:
            self._live_row = self._make_row(cells, live=True)
        self._scroll_to_bottom_if_following()

    def _drop_live_row(self):
        """Remove the live row if present. Main thread."""
        if self._live_row is not None:
            row, _ = self._live_row
            row.destroy()
            self._live_row = None

    # ── Scroll / move / opacity ──

    def scroll_up(self):
        def update():
            self._at_bottom = False
            self.canvas.yview_scroll(-3, 'units')
        self.root.after(0, update)

    def scroll_down(self):
        def update():
            self.canvas.yview_scroll(3, 'units')
            if self.canvas.yview()[1] >= 1.0:
                self._at_bottom = True
        self.root.after(0, update)

    def move(self, dx):
        def update():
            sw = self.root.winfo_screenwidth()
            w = self.root.winfo_width()
            x = max(0, min(sw - w, self.root.winfo_x() + dx))
            self.root.geometry(f'{w}x{self.root.winfo_height()}+{x}+{self.root.winfo_y()}')
        self.root.after(0, update)

    def resize_width(self, dw):
        def update():
            sw = self.root.winfo_screenwidth()
            right = self.root.winfo_x() + self.root.winfo_width()
            new_w = max(220, min(sw, self.root.winfo_width() + dw))
            x = max(0, min(sw - new_w, right - new_w))
            self.root.geometry(f'{new_w}x{self.root.winfo_height()}+{x}+{self.root.winfo_y()}')
        self.root.after(0, update)

    def adjust_opacity(self, delta):
        def update():
            try:
                current = float(self.root.attributes('-alpha'))
            except Exception:
                current = 0.88
            self.root.attributes('-alpha', max(0.1, min(0.9, current + delta)))
        self.root.after(0, update)

    def clear(self):
        def update():
            self._drop_live_row()
            for child in self._rows.winfo_children():
                child.destroy()
            self._cells = []
            self._last_block = None
        self.root.after(0, update)

    # ── Lifecycle ──

    def run(self, worker=None):
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        if worker:
            threading.Thread(target=worker, daemon=True).start()
        self.root.mainloop()

    def quit(self):
        self.root.quit()
