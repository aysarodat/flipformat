"""FlipFormat - offline image converter, resizer and cropper for Windows.

Run:  python flipformat.py

Shift-drag menu: keep this window open (you can minimise it). In Explorer or
on the Desktop, select image(s), hold Shift, and drag. A format menu pops up
under the cursor; drop the files on a format to convert. Drop on "Edit..." to
open the first image in the Crop tab. Originals are never modified and
nothing is ever uploaded.
"""
import ctypes
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    sys.exit("Missing dependency. Run: pip install -r requirements.txt")

from flipformat_core import (
    CROP_PRESETS, FORMATS, INPUT_EXTS, RESAMPLE, RESIZE_MODES,
    anchored_box, available_formats, centered_box, convert, crop_and_resize,
    load_image, preset_ratio, save_image,
)

IS_WIN = sys.platform == "win32"
ACCENT = "#ff9f1c"
DARK = "#1f1f1f"
PILL = "#3a3a3a"

if IS_WIN:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    def key_down(vk):
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def cursor_pos():
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def root_class_at(x, y):
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return ""
        hwnd = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def virtual_screen():
        gm = user32.GetSystemMetrics
        return gm(76), gm(77), gm(78), gm(79)

    def make_noactivate(win):
        """Show the menu without stealing focus from Explorer mid-drag."""
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)


def to_int(text):
    try:
        return max(0, int(float(str(text).strip())))
    except ValueError:
        return 0


# ------------------------------------------------------------ Shift-drag menu

class ShiftDragMenu:
    """Pops up a drop-target format menu when Shift + left-drag starts in
    Explorer or on the Desktop. Works by polling key/mouse state, so it needs
    no hooks and no extra dependencies."""

    ALLOWED = {"CabinetWClass", "Progman", "WorkerW"}  # Explorer, Desktop
    GRID = ["JPG", "PNG", "WEBP", "HEIC", "TIFF", "BMP", "AVIF", "PDF", "Edit"]

    def __init__(self, app):
        self.app, self.root, self.k = app, app.root, app.k
        self.state = "idle"
        self.start = (0, 0)
        self.visible = self.busy = self.dropped = self.styled = False
        self.hide_job = None

        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        body = tk.Frame(self.win, bg=DARK, padx=8, pady=8,
                        highlightthickness=2, highlightbackground=ACCENT)
        body.pack()
        self.header = tk.Label(body, text="", bg=DARK, fg="white",
                               font=("Segoe UI", 10, "bold"))
        self.header.grid(row=0, column=0, columnspan=3, pady=(0, 6))

        self.pills = {}
        for i, name in enumerate(self.GRID):
            ok = name == "Edit" or name in app.ready
            lbl = tk.Label(body, text="Edit..." if name == "Edit" else name,
                           width=9, pady=8, bg=PILL,
                           fg="white" if ok else "#777777",
                           font=("Segoe UI", 10, "bold"))
            lbl.grid(row=1 + i // 3, column=i % 3, padx=3, pady=3)
            self.pills[name] = lbl
            if ok:
                lbl.drop_target_register(DND_FILES)
                lbl.dnd_bind("<<DropEnter>>", lambda e, w=lbl: self.hover(w, True))
                lbl.dnd_bind("<<DropPosition>>", lambda e: "copy")
                lbl.dnd_bind("<<DropLeave>>", lambda e, w=lbl: self.hover(w, False))
                lbl.dnd_bind("<<Drop>>", lambda e, n=name: self.on_drop(e, n))

    # -- state machine, called every ~25 ms --------------------------------
    def step(self):
        left, shift = key_down(0x01), key_down(0x10)
        if self.state == "idle":
            if left and shift and self.app.shift_var.get():
                x, y = cursor_pos()
                if root_class_at(x, y) in self.ALLOWED:
                    self.start, self.state = (x, y), "pressed"
        elif self.state == "pressed":
            if not left:
                self.state = "idle"
            else:
                x, y = cursor_pos()
                sx, sy = self.start
                if abs(x - sx) + abs(y - sy) > 30 * self.k:
                    self.show(x, y)
                    self.state = "menu"
        elif self.state == "menu" and not left:
            self.state = "idle"
            self.root.after(600, self.release_check)

    # -- showing / hiding ----------------------------------------------------
    def show(self, x, y):
        self.dropped = self.busy = False
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        self.header.configure(text=self.app.menu_caption())
        for lbl in self.pills.values():
            lbl.configure(bg=PILL)
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        vx, vy, vw, vh = virtual_screen()
        gap = int(20 * self.k)
        px = min(max(x - w // 2, vx), vx + vw - w)
        py = y + gap
        if py + h > vy + vh:
            py = y - h - gap
        self.win.geometry(f"+{px}+{py}")
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.win.lift()
        if not self.styled:
            self.win.update_idletasks()
            make_noactivate(self.win)
            self.styled = True
        self.visible = True

    def hide(self):
        self.win.withdraw()
        self.visible = False
        self.hide_job = None

    def hide_later(self, ms):
        self.hide_job = self.root.after(ms, self.hide)

    def release_check(self):
        if self.visible and not self.dropped and not self.busy:
            self.hide()

    # -- drop handling -------------------------------------------------------
    def hover(self, widget, on):
        widget.configure(bg=ACCENT if on else PILL)
        return "copy"

    def on_drop(self, event, name):
        self.dropped = True
        paths = self.root.tk.splitlist(event.data)
        files = [Path(p) for p in paths if Path(p).suffix.lower() in INPUT_EXTS]
        if not files:
            self.header.configure(text="No supported images in that drop")
            self.hide_later(1800)
        elif name == "Edit":
            self.hide()
            self.app.open_in_editor(files)
        else:
            self.busy = True
            self.header.configure(text=f"Converting {len(files)} file(s)...")
            self.app.run_conversion(files, name, lambda ok, total: self.finished(ok, total, name))
        return "copy"

    def finished(self, ok, total, name):
        self.busy = False
        self.header.configure(text=f"Done: {ok}/{total} converted to {name}")
        self.hide_later(1600)


# ------------------------------------------------------------------ Crop tab

class CropTab(ttk.Frame):
    HANDLE = 6

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        k = app.k
        self.cw, self.ch = int(480 * k), int(330 * k)
        self.im = self.path = self.photo = self.box = None
        self.scale, self.ox, self.oy, self.dw, self.dh = 1, 0, 0, 1, 1
        self.mode, self.anchor, self.grab = None, (0, 0), (0, 0)

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Open image...", command=self.browse).pack(side="left")
        self.preset_cb = ttk.Combobox(
            top, state="readonly", width=34,
            values=[p["name"] for p in CROP_PRESETS])
        self.preset_cb.current(0)
        self.preset_cb.pack(side="left", padx=8)
        self.preset_cb.bind("<<ComboboxSelected>>", lambda e: self.on_preset(reset=True))

        self.canvas = tk.Canvas(self, width=self.cw, height=self.ch, bg="#2b2b2b",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(pady=8)
        self.canvas.create_text(self.cw // 2, self.ch // 2, fill="#aaaaaa",
                                font=("Segoe UI", 12),
                                text="Drop an image here or click Open image...")
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "mode", None))
        self.canvas.drop_target_register(DND_FILES)
        self.canvas.dnd_bind("<<Drop>>", self.on_drop)

        self.info = ttk.Label(self, text="Drag inside the box to move it; drag a "
                                         "corner to resize.", wraplength=int(480 * k))
        self.info.pack(fill="x")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, text="Save as").pack(side="left")
        self.fmt_var = tk.StringVar(value="JPG")
        ttk.Combobox(bottom, textvariable=self.fmt_var, state="readonly", width=6,
                     values=app.ready).pack(side="left", padx=(4, 12))
        ttk.Label(bottom, text="Max KB (0 = none)").pack(side="left")
        self.kb_var = tk.StringVar(value="0")
        ttk.Entry(bottom, textvariable=self.kb_var, width=7).pack(side="left", padx=4)
        ttk.Button(bottom, text="Crop and save", command=self.save).pack(side="right")

    # -- helpers -----------------------------------------------------------
    def preset(self):
        return CROP_PRESETS[self.preset_cb.current()]

    def ratio(self):
        return preset_ratio(self.preset())

    def to_img(self, cx, cy):
        x = min(max((cx - self.ox) / self.scale, 0), self.im.width)
        y = min(max((cy - self.oy) / self.scale, 0), self.im.height)
        return x, y

    def to_canvas(self, x, y):
        return self.ox + x * self.scale, self.oy + y * self.scale

    # -- loading -----------------------------------------------------------
    def browse(self):
        types = " ".join(f"*{e}" for e in sorted(INPUT_EXTS))
        picked = filedialog.askopenfilename(
            title="Choose an image", filetypes=[("Images", types)])
        if picked:
            self.load(picked)

    def on_drop(self, event):
        paths = [Path(p) for p in self.app.root.tk.splitlist(event.data)]
        paths = [p for p in paths if p.suffix.lower() in INPUT_EXTS]
        if paths:
            self.load(paths[0])
        return "copy"

    def load(self, path):
        try:
            im = load_image(path)
        except Exception as exc:
            self.info.configure(text=f"Cannot open that file: {exc}")
            return
        self.im, self.path = im, Path(path)
        self.scale = min(self.cw / im.width, self.ch / im.height)
        self.dw = max(1, round(im.width * self.scale))
        self.dh = max(1, round(im.height * self.scale))
        self.ox, self.oy = (self.cw - self.dw) // 2, (self.ch - self.dh) // 2
        thumb = im.convert("RGBA").resize((self.dw, self.dh), RESAMPLE)
        self.photo = ImageTk.PhotoImage(thumb)
        self.box = None
        self.on_preset(reset=True)

    def on_preset(self, reset=False):
        preset = self.preset()
        self.kb_var.set(str(preset.get("max_kb", 0)))
        if self.im is None:
            return
        if reset or self.box is None:
            self.box = centered_box(self.im.width, self.im.height, self.ratio())
        self.redraw()

    # -- drawing -----------------------------------------------------------
    def redraw(self):
        c = self.canvas
        c.delete("all")
        c.create_image(self.ox, self.oy, anchor="nw", image=self.photo)
        x0, y0 = self.to_canvas(*self.box[:2])
        x1, y1 = self.to_canvas(*self.box[2:])
        ix0, iy0, ix1, iy1 = self.ox, self.oy, self.ox + self.dw, self.oy + self.dh
        for rect in ((ix0, iy0, ix1, y0), (ix0, y1, ix1, iy1),
                     (ix0, y0, x0, y1), (x1, y0, ix1, y1)):
            c.create_rectangle(*rect, fill="black", stipple="gray50", outline="")
        c.create_rectangle(x0, y0, x1, y1, outline=ACCENT, width=2)
        for t in (1 / 3, 2 / 3):  # rule-of-thirds guides
            c.create_line(x0 + (x1 - x0) * t, y0, x0 + (x1 - x0) * t, y1,
                          fill=ACCENT, dash=(2, 4))
            c.create_line(x0, y0 + (y1 - y0) * t, x1, y0 + (y1 - y0) * t,
                          fill=ACCENT, dash=(2, 4))
        r = int(self.HANDLE * self.app.k)
        for hx, hy in self.corners_canvas():
            c.create_rectangle(hx - r, hy - r, hx + r, hy + r,
                               fill=ACCENT, outline="white")
        px = self.preset().get("px")
        w, h = round(self.box[2] - self.box[0]), round(self.box[3] - self.box[1])
        out = f"  ->  output {px[0]}x{px[1]} px" if px else ""
        self.info.configure(text=f"{self.path.name}: crop {w}x{h} px{out}")

    def corners_canvas(self):
        x0, y0, x1, y1 = self.box
        return [self.to_canvas(x, y) for x, y in
                ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]

    # -- mouse -------------------------------------------------------------
    def press(self, e):
        if self.im is None:
            return
        reach = self.HANDLE * 2 * self.app.k
        corners = self.corners_canvas()
        for i, (hx, hy) in enumerate(corners):
            if abs(e.x - hx) <= reach and abs(e.y - hy) <= reach:
                ox, oy = self.canvas_to_img_corner(i)
                self.mode, self.anchor = "anchor", (ox, oy)
                return
        ix, iy = self.to_img(e.x, e.y)
        x0, y0, x1, y1 = self.box
        if x0 <= ix <= x1 and y0 <= iy <= y1:
            self.mode, self.grab = "move", (ix - x0, iy - y0)
        else:
            self.mode, self.anchor = "anchor", (ix, iy)

    def canvas_to_img_corner(self, i):
        """Image coordinates of the corner opposite to handle i."""
        x0, y0, x1, y1 = self.box
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return pts[(i + 2) % 4]

    def drag(self, e):
        if self.im is None or self.mode is None:
            return
        ix, iy = self.to_img(e.x, e.y)
        W, H = self.im.size
        if self.mode == "anchor":
            ax, ay = self.anchor
            self.box = anchored_box(ax, ay, ix, iy, W, H, self.ratio())
        else:
            x0, y0, x1, y1 = self.box
            w, h = x1 - x0, y1 - y0
            nx = min(max(ix - self.grab[0], 0), W - w)
            ny = min(max(iy - self.grab[1], 0), H - h)
            self.box = (nx, ny, nx + w, ny + h)
        self.redraw()

    # -- saving ------------------------------------------------------------
    def save(self):
        if self.im is None:
            self.info.configure(text="Open or drop an image first.")
            return
        preset = self.preset()
        px = preset.get("px")
        try:
            cropped = crop_and_resize(self.im, self.box, px)
            out, size, note = save_image(
                cropped, self.path, self.fmt_var.get(), to_int(self.kb_var.get()),
                suffix="_crop", allow_downscale=not px)
            kb = out.stat().st_size / 1024
            text = f"Saved {out.name}  ({size[0]}x{size[1]} px, {kb:.0f} KB)"
            if note:
                text += f"  - {note}"
            self.info.configure(text=text)
        except Exception as exc:
            self.info.configure(text=f"Failed: {exc}")


# ---------------------------------------------------------------------- App

class App:
    def __init__(self, root):
        self.root = root
        self.k = root.winfo_fpixels("1i") / 96
        self.ready = available_formats()
        self.files = []
        self.busy = False
        self.q = queue.Queue()
        root.title("FlipFormat")
        root.minsize(int(540 * self.k), int(660 * self.k))

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.convert_tab = ttk.Frame(self.nb, padding=10)
        self.crop_tab = CropTab(self.nb, self)
        self.nb.add(self.convert_tab, text="Convert")
        self.nb.add(self.crop_tab, text="Crop")
        self.build_convert_tab()

        self.menu = ShiftDragMenu(self) if IS_WIN else None
        root.after(25, self.tick)

    # -- Convert tab -------------------------------------------------------
    def build_convert_tab(self):
        tab = self.convert_tab
        self.drop = tk.Label(tab, text="Drop images here\nor click to browse",
                             font=("Segoe UI", 14), relief="groove", bd=2,
                             height=5, cursor="hand2")
        self.drop.pack(fill="x")
        self.drop.drop_target_register(DND_FILES)
        self.drop.dnd_bind("<<Drop>>", self.on_drop)
        self.drop.bind("<Button-1>", self.browse)

        self.status = ttk.Label(tab, text="No files selected")
        self.status.pack(fill="x", pady=(8, 4))

        opts = ttk.LabelFrame(tab, text="Size options (also used by the Shift-drag menu)",
                              padding=8)
        opts.pack(fill="x", pady=4)
        row1 = ttk.Frame(opts)
        row1.pack(fill="x")
        ttk.Label(row1, text="Resize").pack(side="left")
        self.mode_var = tk.StringVar(value=RESIZE_MODES[0])
        ttk.Combobox(row1, textvariable=self.mode_var, values=RESIZE_MODES,
                     state="readonly", width=18).pack(side="left", padx=6)
        self.val_var = tk.StringVar(value="0")
        ttk.Entry(row1, textvariable=self.val_var, width=7).pack(side="left")
        row2 = ttk.Frame(opts)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="Max file size (KB, 0 = no limit)").pack(side="left")
        self.kb_var = tk.StringVar(value="0")
        ttk.Entry(row2, textvariable=self.kb_var, width=7).pack(side="left", padx=6)
        self.shift_var = tk.BooleanVar(value=IS_WIN)
        chk = ttk.Checkbutton(opts, text="Enable Shift-drag menu (Windows)",
                              variable=self.shift_var)
        chk.pack(anchor="w", pady=(6, 0))
        if not IS_WIN:
            chk.state(["disabled"])

        grid = ttk.Frame(tab)
        grid.pack(fill="x", pady=6)
        self.buttons = []
        for i, name in enumerate(FORMATS):
            btn = ttk.Button(grid, text=name, command=lambda n=name: self.start(n))
            btn.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="ew")
            if name not in self.ready:
                btn.state(["disabled"])
            self.buttons.append(btn)
        for col in range(4):
            grid.columnconfigure(col, weight=1)

        self.log = tk.Text(tab, height=8, state="disabled", wrap="word",
                           font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        missing = [n for n in FORMATS if n not in self.ready]
        if missing:
            self.write(["Unavailable (plugin missing): " + ", ".join(missing)])

    def write(self, lines):
        self.log.configure(state="normal")
        for line in lines:
            self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_files(self, paths):
        files = [Path(p) for p in paths if Path(p).suffix.lower() in INPUT_EXTS]
        self.files = files
        self.status.configure(text=f"{len(files)} file(s) ready. Pick a format below.")
        if len(files) < len(paths):
            self.write([f"Skipped {len(paths) - len(files)} unsupported file(s)."])

    def on_drop(self, event):
        self.set_files(self.root.tk.splitlist(event.data))
        return "copy"

    def browse(self, _event=None):
        types = " ".join(f"*{e}" for e in sorted(INPUT_EXTS))
        picked = filedialog.askopenfilenames(
            title="Choose images", filetypes=[("Images", types)])
        if picked:
            self.set_files(picked)

    def options(self):
        return {"resize_mode": self.mode_var.get(),
                "resize_value": to_int(self.val_var.get()),
                "max_kb": to_int(self.kb_var.get())}

    def menu_caption(self):
        o = self.options()
        bits = []
        if o["resize_mode"] != RESIZE_MODES[0] and o["resize_value"]:
            bits.append(f"{o['resize_mode'].lower()} {o['resize_value']}")
        if o["max_kb"]:
            bits.append(f"max {o['max_kb']} KB")
        return "Drop on a format" + (" - " + ", ".join(bits) if bits else "")

    # -- conversion --------------------------------------------------------
    def start(self, fmt):
        if self.busy:
            return
        if not self.files:
            self.write(["Drop or choose some images first."])
            return
        self.busy = True
        for b in self.buttons:
            b.state(["disabled"])
        self.status.configure(text=f"Converting to {fmt}...")
        self.run_conversion(self.files[:], fmt, self.finish_convert_tab)

    def finish_convert_tab(self, ok, total):
        self.busy = False
        self.files = []
        for b, name in zip(self.buttons, FORMATS):
            if name in self.ready:
                b.state(["!disabled"])
        self.status.configure(text=f"Done: {ok} of {total} converted.")

    def run_conversion(self, files, fmt, done):
        opts = self.options()

        def work():
            ok, lines = 0, []
            for f in files:
                try:
                    out, size, note = convert(f, fmt, **opts)
                    kb = out.stat().st_size / 1024
                    line = f"OK   {f.name} -> {out.name} ({size[0]}x{size[1]}, {kb:.0f} KB)"
                    if note:
                        line += f"  [{note}]"
                    lines.append(line)
                    ok += 1
                except Exception as exc:  # keep going if one file fails
                    lines.append(f"FAIL {f.name}: {exc}")
            self.post(self.write, lines)
            self.post(done, ok, len(files))

        threading.Thread(target=work, daemon=True).start()

    # -- plumbing ----------------------------------------------------------
    def post(self, fn, *args):
        self.q.put((fn, args))

    def open_in_editor(self, files):
        self.root.deiconify()
        self.root.lift()
        self.nb.select(self.crop_tab)
        self.crop_tab.load(files[0])

    def tick(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        except Exception as exc:
            print("UI error:", exc, file=sys.stderr)
        if self.menu:
            try:
                self.menu.step()
            except Exception as exc:
                print("Menu error:", exc, file=sys.stderr)
        self.root.after(25, self.tick)


def main():
    if IS_WIN:  # make Tk and the cursor/mouse APIs agree on pixel coordinates
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = TkinterDnD.Tk()
    App(root)
    if "--minimized" in sys.argv:  # used by the "start with Windows" option
        root.iconify()
    root.mainloop()


if __name__ == "__main__":
    main()
