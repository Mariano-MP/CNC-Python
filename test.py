"""
CNC Control v3 — Jog + Simulación
Aplicación de escritorio Python con tkinter + matplotlib
Comunicación serial con GRBL via pyserial

Dependencias:
    pip install matplotlib pyserial

Uso:
    python cnc_control.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, font as tkfont
import threading
import math
import time
import queue

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# ─────────────────────────────────────────────
#  PALETA DE COLORES
# ─────────────────────────────────────────────
BG     = "#0f1117"
BG2    = "#161b24"
BG3    = "#1e2535"
BG4    = "#252d3d"
BORDER = "#2a3448"
BORDER2= "#3a4a66"
CYAN   = "#00e5ff"
GREEN  = "#00ff88"
AMBER  = "#ffb800"
RED    = "#ff4060"
TEXT   = "#c8d8f0"
TEXT2  = "#7a90b0"
TEXT3  = "#4a5870"


# ─────────────────────────────────────────────
#  CONTROLADOR GRBL (real + simulación)
# ─────────────────────────────────────────────
class GRBLController:
    def __init__(self, log_callback, pos_callback):
        self.ser       = None
        self.connected = False
        self.sim_mode  = True
        self.log_cb    = log_callback
        self.pos_cb    = pos_callback
        # Posición REAL (animada)
        self.x = self.y = self.z = 0.0
        # Posición OBJETIVO (a donde va / va a ir)
        self.tx = self.ty = self.tz = 0.0
        self.feed  = 500
        self.state = "Idle"
        self._stop_poll = threading.Event()
        self._move_seq  = 0
        self._lock      = threading.Lock()
        # Cola de posición para no saturar el hilo de UI
        self._pos_queue = queue.Queue(maxsize=4)

    # ── Conexión ──────────────────────────────
    def connect(self, port, baud):
        if not SERIAL_AVAILABLE:
            self._sim_connect(port, baud)
            return
        try:
            self.ser = serial.Serial(port, int(baud), timeout=1)
            time.sleep(2)
            self.ser.write(b"\r\n\r\n")
            time.sleep(0.1)
            self.ser.flushInput()
            self.connected = True
            self.sim_mode  = False
            self.log_cb("ok", f"Conectado a {port} @ {baud}")
            self._start_poll()
        except Exception as e:
            self.log_cb("err", f"Error serial: {e} — modo simulación")
            self._sim_connect(port, baud)

    def _sim_connect(self, port, baud):
        self.connected = True
        self.sim_mode  = True
        self.log_cb("ok", f"[SIM] Conectado a {port} @ {baud}")

    def disconnect(self):
        self._stop_poll.set()
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.connected = False
        self.log_cb("info", "Desconectado")

    # ── Envío de comandos ─────────────────────
    def send(self, cmd):
        self.log_cb("cmd", f">> {cmd}")
        if self.sim_mode:
            self._sim_process(cmd)
            return
        if self.ser and self.ser.is_open:
            self.ser.write((cmd + "\n").encode())

    def _sim_process(self, cmd):
        import re
        cmd_up = cmd.strip().upper()

        # Comandos de control
        if cmd_up == "$H":
            self.state = "Home"
            self.tx = self.ty = self.tz = 0.0
            threading.Thread(target=self._sim_home, daemon=True).start()
            return
        if cmd_up == "$X":
            self.log_cb("ok", "ALARM cleared")
            return
        # Ignorar G90/G91 solos (modos relativo/absoluto — no afectan simulación)
        if cmd_up in ("G90", "G91"):
            return

        nums = {m.group(1): float(m.group(2))
                for m in re.finditer(r'([XYZFR])([-\d.]+)', cmd_up)}
        if "F" in nums:
            self.feed = nums["F"]

        if re.match(r'G0\b|G00\b', cmd_up) or re.match(r'G1\b|G01\b', cmd_up):
            with self._lock:
                # Solo actualizar ejes que vengan en el comando
                if "X" in nums: self.tx = nums["X"]
                if "Y" in nums: self.ty = nums["Y"]
                if "Z" in nums: self.tz = nums["Z"]
                self._move_seq += 1
                seq  = self._move_seq
                dest = (self.tx, self.ty, self.tz)
            threading.Thread(target=self._sim_linear,
                             args=(*dest, seq), daemon=True).start()

        elif re.match(r'G2\b|G02\b', cmd_up) or re.match(r'G3\b|G03\b', cmd_up):
            with self._lock:
                if "X" in nums: self.tx = nums["X"]
                if "Y" in nums: self.ty = nums["Y"]
                r   = nums.get("R", 40)
                cw  = bool(re.match(r'G2\b|G02\b', cmd_up))
                self._move_seq += 1
                seq = self._move_seq
                dest_x, dest_y = self.tx, self.ty
            threading.Thread(target=self._sim_arc,
                             args=(dest_x, dest_y, r, cw, seq), daemon=True).start()

        elif re.match(r'G10\b', cmd_up):
            with self._lock:
                if "X" in nums and nums["X"] == 0: self.x = self.tx = 0.0
                if "Y" in nums and nums["Y"] == 0: self.y = self.ty = 0.0
                if "Z" in nums and nums["Z"] == 0: self.z = self.tz = 0.0
            self.pos_cb(self.x, self.y, self.z, self.feed, "Idle")
            self.log_cb("ok", "ok")

    def _sim_home(self):
        steps = 40
        sx, sy, sz = self.x, self.y, self.z
        for i in range(steps + 1):
            t = i / steps
            self.x = sx * (1 - t)
            self.y = sy * (1 - t)
            self.z = sz * (1 - t)
            self.pos_cb(self.x, self.y, self.z, self.feed, "Home")
            time.sleep(0.04)
        self.state = "Idle"
        self.pos_cb(0, 0, 0, self.feed, "Idle")
        self.log_cb("ok", "Homing completado")

    def _sim_linear(self, tx, ty, tz, seq=0):
        self.state = "Run"
        steps = 60
        sx, sy, sz = self.x, self.y, self.z
        for i in range(steps + 1):
            if self._move_seq != seq:
                return
            t = i / steps
            self.x = sx + (tx - sx) * t
            self.y = sy + (ty - sy) * t
            self.z = sz + (tz - sz) * t
            self.pos_cb(self.x, self.y, self.z, self.feed, "Run")
            time.sleep(0.02)
        with self._lock:
            self.x, self.y, self.z = tx, ty, tz
        self.state = "Idle"
        self.pos_cb(self.x, self.y, self.z, self.feed, "Idle")
        self.log_cb("ok", "ok")

    def _sim_arc(self, tx, ty, r, cw, seq=0):
        self.state = "Run"
        sx, sy = self.x, self.y
        chord = math.sqrt((tx - sx) ** 2 + (ty - sy) ** 2)
        if chord < 1e-6:
            return
        h  = math.sqrt(max(0, r * r - (chord / 2) ** 2))
        dx = (tx - sx) / chord
        dy = (ty - sy) / chord
        sign = -1 if cw else 1
        mx = (sx + tx) / 2 + sign * h * dy
        my = (sy + ty) / 2 - sign * h * dx
        a1 = math.atan2(sy - my, sx - mx)
        a2 = math.atan2(ty - my, tx - mx)
        da = a2 - a1
        if cw and da > 0:  da -= 2 * math.pi
        if not cw and da < 0: da += 2 * math.pi
        steps = 60
        for i in range(steps + 1):
            if self._move_seq != seq:
                return
            t = i / steps
            a = a1 + da * t
            self.x = mx + r * math.cos(a)
            self.y = my + r * math.sin(a)
            self.pos_cb(self.x, self.y, self.z, self.feed, "Run")
            time.sleep(0.02)
        with self._lock:
            self.x, self.y = tx, ty
        self.state = "Idle"
        self.pos_cb(self.x, self.y, self.z, self.feed, "Idle")
        self.log_cb("ok", "ok")

    def _start_poll(self):
        self._stop_poll.clear()
        if not self.sim_mode:
            threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        import re
        while not self._stop_poll.is_set():
            try:
                if self.ser and self.ser.is_open:
                    self.ser.write(b"?")
                    line = self.ser.readline().decode(errors="ignore").strip()
                    m = re.search(
                        r'<(\w+)\|MPos:([-\d.]+),([-\d.]+),([-\d.]+)', line)
                    if m:
                        self.state = m.group(1)
                        self.x = float(m.group(2))
                        self.y = float(m.group(3))
                        self.z = float(m.group(4))
                        self.tx, self.ty, self.tz = self.x, self.y, self.z
                        self.pos_cb(self.x, self.y, self.z,
                                    self.feed, self.state)
            except Exception:
                pass
            time.sleep(0.2)


# ─────────────────────────────────────────────
#  CANVAS DE SIMULACIÓN
# ─────────────────────────────────────────────
class SimCanvas:
    def __init__(self, parent):
        self.fig = Figure(figsize=(6, 4), dpi=96, facecolor=BG)
        self.ax  = self.fig.add_subplot(111)
        self._style_ax()
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.traj_line = None
        self.cut_line  = None
        self.tool_dot  = None
        self.pos_label = None
        self.cut_x = []
        self.cut_y = []
        self.table_w = 300
        self.table_h = 200
        self._draw_base()

    def _style_ax(self):
        ax = self.ax
        ax.set_facecolor(BG)
        ax.tick_params(colors=TEXT3, labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
        self.fig.tight_layout(pad=1.2)

    def _draw_base(self):
        ax = self.ax
        ax.cla()
        self._style_ax()
        W, H = int(self.table_w), int(self.table_h)
        for x in range(0, W + 1, 25):
            ax.axvline(x, color=BORDER, lw=0.4, zorder=0)
        for y in range(0, H + 1, 25):
            ax.axhline(y, color=BORDER, lw=0.4, zorder=0)
        ax.axvline(0, color=BORDER2, lw=0.8)
        ax.axhline(0, color=BORDER2, lw=0.8)
        ax.set_xlim(-10, W + 20)
        ax.set_ylim(-15, H + 15)
        ax.tick_params(colors=TEXT3, labelsize=8)
        ax.text(W * 0.5, H + 5,
                f"{W} × {H} mm  (Z=5.0mm)",
                color=CYAN, fontsize=8, alpha=0.55,
                ha="center")
        for x in range(0, W + 1, 50):
            ax.text(x, -12, str(x), color=TEXT3, fontsize=7,
                    ha="center", va="top")
        for y in range(0, H + 1, 50):
            ax.text(-8, y, str(y), color=TEXT3, fontsize=7,
                    ha="right", va="center")
        self.origin_dot, = ax.plot(0, 0, 'o', color=CYAN,
                                    ms=7, mfc="none", mew=2, zorder=5)
        self.tool_dot, = ax.plot(0, 0, 'o', color=AMBER,
                                  ms=9, mfc=AMBER + "55", mew=2.5, zorder=7)
        self.pos_label = ax.text(2, 2, "(0.0, 0.0)",
                                  color=AMBER, fontsize=8, zorder=8)
        self.canvas.draw_idle()

    def set_table(self, w, h):
        self.table_w = w
        self.table_h = h
        self._draw_base()
        self.cut_x = []
        self.cut_y = []
        self.cut_line = None
        self.traj_line = None

    def set_trajectory(self, xs, ys):
        ax = self.ax
        if self.traj_line:
            try: self.traj_line.remove()
            except: pass
        if xs:
            self.traj_line, = ax.plot(
                xs, ys, '--', color=CYAN, lw=1.4, alpha=0.4, zorder=3)
        self.cut_x = []
        self.cut_y = []
        if self.cut_line:
            try: self.cut_line.remove()
            except: pass
            self.cut_line = None
        self.canvas.draw_idle()

    def update_tool(self, x, y):
        if self.tool_dot:
            self.tool_dot.set_data([x], [y])
        if self.pos_label:
            self.pos_label.set_position((x + 3, y + 3))
            self.pos_label.set_text(f"({x:.1f}, {y:.1f})")
        self.cut_x.append(x)
        self.cut_y.append(y)
        ax = self.ax
        if self.cut_line:
            self.cut_line.set_data(self.cut_x, self.cut_y)
        else:
            self.cut_line, = ax.plot(
                self.cut_x, self.cut_y,
                '-', color=GREEN, lw=2.5, alpha=0.9, zorder=6)
        self.canvas.draw_idle()

    def reset_cut(self):
        self.cut_x = []
        self.cut_y = []
        if self.cut_line:
            try: self.cut_line.remove()
            except: pass
            self.cut_line = None
        self.canvas.draw_idle()


# ─────────────────────────────────────────────
#  APLICACIÓN PRINCIPAL
# ─────────────────────────────────────────────
class CNCApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CNC Control v3 — Jog + Simulación")
        self.configure(bg=BG)
        self.geometry("1280x800")
        self.minsize(960, 640)

        self.fn_mono  = tkfont.Font(family="Courier New", size=10)
        self.fn_small = tkfont.Font(family="Courier New", size=9)
        self.fn_ui    = tkfont.Font(family="Arial", size=10)

        self.current_traj = tk.StringVar(value="Recta")
        self.step_var     = tk.DoubleVar(value=1.0)
        self.port_var     = tk.StringVar(value="COM3")
        self.baud_var     = tk.StringVar(value="115200")
        self._connected   = False
        # Throttle para no saturar el hilo UI con pos callbacks
        self._last_pos_update = 0.0

        self.grbl = GRBLController(
            log_callback=self._log,
            pos_callback=self._on_pos
        )

        self._apply_style()
        self._build_ui()
        self._refresh_ports()
        self._connect_preview_traces()
        self._preview()

    # ─────────────────────────────────────────
    #  Traces para preview automático
    # ─────────────────────────────────────────
    def _connect_preview_traces(self):
        self._preview_after_id = None

        def _schedule_preview(*_):
            if self._preview_after_id is not None:
                self.after_cancel(self._preview_after_id)
            self._preview_after_id = self.after(200, self._preview)

        for var in (self.x1, self.y1, self.x2, self.y2,
                    self.rad_var, self.dir_var,
                    self.table_w, self.table_h, self.table_z):
            var.trace_add("write", _schedule_preview)

    # ─────────────────────────────────────────
    #  ESTILO TTK
    # ─────────────────────────────────────────
    def _apply_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox",
                         fieldbackground=BG3, background=BG3,
                         foreground=TEXT, bordercolor=BORDER,
                         arrowcolor=TEXT2, selectbackground=BG4,
                         selectforeground=TEXT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", BG3)],
                  selectbackground=[("readonly", BG3)],
                  foreground=[("readonly", TEXT)])

    # ─────────────────────────────────────────
    #  CONSTRUCCIÓN UI
    # ─────────────────────────────────────────
    def _build_ui(self):
        self._build_topbar()
        content = tk.Frame(self, bg=BG)
        content.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(content, bg=BG2, width=225,
                        highlightbackground=BORDER, highlightthickness=1)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        self._build_left(left)
        center = tk.Frame(content, bg=BG)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_center(center)

    # ── Top bar ───────────────────────────────
    def _build_topbar(self):
        bar = tk.Frame(self, bg=BG2, height=40,
                       highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        tk.Label(bar, text="⬡  CNC Control v3", bg=BG2,
                 fg=CYAN, font=("Arial", 12, "bold")).pack(
            side=tk.LEFT, padx=(10, 16))

        for lbl, var, vals, w in [
            ("Puerto:", self.port_var, [], 8),
            ("Baud:", self.baud_var, ["9600", "115200"], 8)
        ]:
            tk.Label(bar, text=lbl, bg=BG2, fg=TEXT3,
                     font=self.fn_small).pack(side=tk.LEFT, padx=(4, 1))
            cb = ttk.Combobox(bar, textvariable=var,
                               values=vals, width=w, state="readonly")
            cb.pack(side=tk.LEFT, padx=(0, 4))
            if lbl.startswith("P"):
                self.port_cb = cb

        self.btn_conn = tk.Button(
            bar, text="Conectar", bg=BG3, fg=CYAN,
            font=self.fn_small, relief="flat", bd=0,
            padx=10, cursor="hand2", command=self._toggle_connect)
        self.btn_conn.pack(side=tk.LEFT, padx=6)

        self.lbl_conn = tk.Label(bar, text="●  Desconectado",
                                  bg=BG2, fg=RED, font=self.fn_small)
        self.lbl_conn.pack(side=tk.LEFT, padx=4)

        self.prog_lbl = tk.Label(bar, text="Listo",
                                  bg=BG2, fg=TEXT2, font=self.fn_small)
        self.prog_lbl.pack(side=tk.LEFT, padx=(20, 4))
        pf = tk.Frame(bar, bg=BORDER, height=5, width=200)
        pf.pack(side=tk.LEFT)
        pf.pack_propagate(False)
        self.prog_fill = tk.Frame(pf, bg=CYAN, height=5, width=0)
        self.prog_fill.place(x=0, y=0, relheight=1)
        self._prog_w = 200

        tk.Button(bar, text="⬛  PARAR",
                  bg=RED, fg="white", font=("Arial", 10, "bold"),
                  relief="flat", padx=12, cursor="hand2",
                  command=self._stop).pack(side=tk.RIGHT, padx=10, pady=5)

    # ── Panel izquierdo ───────────────────────
    def _build_left(self, parent):
        canvas_scroll = tk.Canvas(parent, bg=BG2, highlightthickness=0)
        canvas_scroll.pack(fill=tk.BOTH, expand=True)
        frame = tk.Frame(canvas_scroll, bg=BG2)
        canvas_scroll.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>",
                   lambda e: canvas_scroll.config(
                       scrollregion=canvas_scroll.bbox("all")))

        def _section(title):
            f = tk.Frame(frame, bg=BG2)
            f.pack(fill=tk.X, padx=6, pady=(8, 0))
            tk.Label(f, text=title.upper(), bg=BG2,
                     fg=TEXT3, font=("Arial", 7, "bold")).pack(anchor="w")
            tk.Frame(f, bg=BORDER, height=1).pack(fill=tk.X, pady=(2, 4))
            return f

        # ── Posición ──
        s = _section("Posición")
        self.lbl_x = tk.Label(s, text="X: +0.000 mm", bg=BG2,
                               fg=RED, font=("Courier New", 14, "bold"))
        self.lbl_x.pack(anchor="w", padx=4, pady=1)
        self.lbl_y = tk.Label(s, text="Y: +0.000 mm", bg=BG2,
                               fg=GREEN, font=("Courier New", 14, "bold"))
        self.lbl_y.pack(anchor="w", padx=4, pady=1)
        self.lbl_z = tk.Label(s, text="Z: +0.000 mm", bg=BG2,
                               fg=CYAN, font=("Courier New", 14, "bold"))
        self.lbl_z.pack(anchor="w", padx=4, pady=1)
        zr = tk.Frame(s, bg=BG2); zr.pack(fill=tk.X, padx=4, pady=3)
        for txt, axis, color in [
            ("X=0", "x", TEXT2), ("Y=0", "y", TEXT2),
            ("Z=0", "z", TEXT2), ("XYZ=0", "all", AMBER)
        ]:
            tk.Button(zr, text=txt, bg=BG3, fg=color,
                      font=self.fn_small, relief="flat", bd=0,
                      padx=5, pady=3, cursor="hand2",
                      command=lambda a=axis: self._zero(a)
                      ).pack(side=tk.LEFT, padx=2)

        # ── Tabla ──
        s2 = _section("Tabla")
        self.table_w = tk.StringVar(value="300")
        self.table_h = tk.StringVar(value="200")
        self.table_z = tk.StringVar(value="5")
        for lbl, var, unit in [("Ancho X:", self.table_w, "mm"),
                                ("Alto Y:",  self.table_h, "mm"),
                                ("Espesor Z:", self.table_z, "mm")]:
            r = tk.Frame(s2, bg=BG2); r.pack(fill=tk.X, padx=4, pady=1)
            tk.Label(r, text=lbl, bg=BG2, fg=TEXT2,
                     font=self.fn_small, width=10, anchor="w").pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, width=6, bg=BG3, fg=TEXT,
                     insertbackground=CYAN, font=self.fn_mono,
                     relief="flat", bd=2).pack(side=tk.LEFT)
            tk.Label(r, text=unit, bg=BG2, fg=TEXT3,
                     font=self.fn_small).pack(side=tk.LEFT, padx=2)

        tk.Button(s2, text="⌂   HOME", bg=BG3, fg=CYAN,
                  font=("Arial", 11, "bold"), relief="flat", bd=0,
                  pady=7, cursor="hand2",
                  command=self._home).pack(fill=tk.X, padx=4, pady=6)

        # ── Jog manual ──
        s3 = _section("Jog manual")
        sr = tk.Frame(s3, bg=BG2); sr.pack(fill=tk.X, padx=4, pady=(0, 4))
        tk.Label(sr, text="Paso:", bg=BG2, fg=TEXT2,
                 font=self.fn_small).pack(side=tk.LEFT)
        for v in [0.1, 1, 10, 50]:
            tk.Radiobutton(sr, text=str(v), variable=self.step_var, value=v,
                           bg=BG2, fg=TEXT2, selectcolor=BG3,
                           activebackground=BG2,
                           font=self.fn_small).pack(side=tk.LEFT, padx=1)

        jog = tk.Frame(s3, bg=BG2); jog.pack(pady=4)
        bcfg = dict(bg=BG3, fg=TEXT, font=("Arial", 11, "bold"),
                    relief="flat", bd=0, width=4, height=2,
                    cursor="hand2", activebackground=BG4,
                    activeforeground=CYAN)
        tk.Label(jog, bg=BG2, width=4, height=2).grid(row=0, column=0, padx=2, pady=2)
        tk.Button(jog, text="Y+", **bcfg,
                  command=lambda: self._jog(0, 1)).grid(row=0, column=1, padx=2, pady=2)
        tk.Label(jog, bg=BG2, width=4, height=2).grid(row=0, column=2, padx=2, pady=2)
        tk.Button(jog, text="X−", **bcfg,
                  command=lambda: self._jog(-1, 0)).grid(row=1, column=0, padx=2, pady=2)
        tk.Label(jog, text="✛", bg=BG4, fg=TEXT3,
                 font=("Arial", 12), width=4, height=2).grid(row=1, column=1, padx=2, pady=2)
        tk.Button(jog, text="X+", **bcfg,
                  command=lambda: self._jog(1, 0)).grid(row=1, column=2, padx=2, pady=2)
        tk.Label(jog, bg=BG2, width=4, height=2).grid(row=2, column=0, padx=2, pady=2)
        tk.Button(jog, text="Y−", **bcfg,
                  command=lambda: self._jog(0, -1)).grid(row=2, column=1, padx=2, pady=2)

        zr2 = tk.Frame(s3, bg=BG2); zr2.pack(pady=3)
        zcfg = dict(bg=BG3, fg=CYAN, font=("Arial", 10, "bold"),
                    relief="flat", bd=0, padx=10, pady=4, cursor="hand2")
        tk.Button(zr2, text="Z+", **zcfg,
                  command=lambda: self._jog_z(1)).pack(side=tk.LEFT, padx=3)
        tk.Label(zr2, text="Z", bg=BG4, fg=TEXT3,
                 font=self.fn_small, padx=8, pady=4).pack(side=tk.LEFT)
        tk.Button(zr2, text="Z−", **zcfg,
                  command=lambda: self._jog_z(-1)).pack(side=tk.LEFT, padx=3)

        # ── Pasadas Z ──
        s4 = _section("Pasadas Z")
        self.zp_depth = tk.StringVar(value="5")
        self.zp_step  = tk.StringVar(value="0.5")
        self.zp_vel   = tk.StringVar(value="100")
        self.zp_safe  = tk.StringVar(value="5")
        for lbl, var, unit in [
            ("Profundidad:", self.zp_depth, "mm"),
            ("Paso/pasada:", self.zp_step, "mm"),
            ("Vel. bajada Z:", self.zp_vel, "mm/m"),
            ("Z seguro:", self.zp_safe, "mm")
        ]:
            r = tk.Frame(s4, bg=BG2); r.pack(fill=tk.X, padx=4, pady=1)
            tk.Label(r, text=lbl, bg=BG2, fg=TEXT2,
                     font=self.fn_small, width=13, anchor="w").pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, width=5, bg=BG3, fg=TEXT,
                     insertbackground=CYAN, font=self.fn_mono,
                     relief="flat", bd=2).pack(side=tk.LEFT)
            tk.Label(r, text=unit, bg=BG2, fg=TEXT3,
                     font=self.fn_small).pack(side=tk.LEFT, padx=2)
        self.lbl_passes = tk.Label(s4, text="Pasadas: 1 (superficie)",
                                    bg=BG2, fg=GREEN, font=self.fn_small)
        self.lbl_passes.pack(anchor="w", padx=4, pady=2)
        self.zmode = tk.StringVar(value="superficie")
        for txt, val in [("Solo superficie", "superficie"),
                         ("Profundidad manual", "manual"),
                         ("Corte completo (tabla)", "completo")]:
            tk.Radiobutton(s4, text=txt, variable=self.zmode, value=val,
                           bg=BG2, fg=TEXT2, selectcolor=BG3,
                           activebackground=BG2, font=self.fn_small,
                           command=self._update_passes).pack(anchor="w", padx=6)

    # ── Centro ────────────────────────────────
    def _build_center(self, parent):
        hdr = tk.Frame(parent, bg=BG2, height=36,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="◈  Simulación", bg=BG2,
                 fg=CYAN, font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)
        self.badge = tk.Label(hdr, text="— Recta", bg=BG3,
                               fg=CYAN, font=self.fn_mono, padx=8, pady=1)
        self.badge.pack(side=tk.LEFT, padx=6)
        for txt, cmd in [("▷ Animar", "animate"), ("◉ Preview", "preview")]:
            tk.Button(hdr, text=txt, bg=BG3, fg=TEXT2,
                      font=self.fn_small, relief="flat", bd=0,
                      padx=8, cursor="hand2",
                      command=lambda c=cmd: self._sim_action(c)
                      ).pack(side=tk.RIGHT, padx=3, pady=5)

        sbar = tk.Frame(parent, bg=BG2, height=22,
                        highlightbackground=BORDER, highlightthickness=1)
        sbar.pack(fill=tk.X)
        sbar.pack_propagate(False)
        self.sv_x    = tk.StringVar(value="X: +0.000 mm")
        self.sv_y    = tk.StringVar(value="Y: +0.000 mm")
        self.sv_z    = tk.StringVar(value="Z: +0.000 mm")
        self.sv_feed = tk.StringVar(value="Feed: 500 mm/min")
        self.sv_mode = tk.StringVar(value="G21 Métrico")
        self.sv_st   = tk.StringVar(value="Idle")
        for var, color in [(self.sv_x, RED), (self.sv_y, GREEN),
                           (self.sv_z, CYAN), (self.sv_feed, AMBER),
                           (self.sv_mode, CYAN)]:
            tk.Label(sbar, textvariable=var, bg=BG2,
                     fg=color, font=self.fn_small).pack(side=tk.LEFT, padx=8)
        self.lbl_state = tk.Label(sbar, textvariable=self.sv_st,
                                   bg=BG2, fg=TEXT2, font=self.fn_small)
        self.lbl_state.pack(side=tk.RIGHT, padx=12)

        sim_frame = tk.Frame(parent, bg=BG)
        sim_frame.pack(fill=tk.BOTH, expand=True)
        self.sim = SimCanvas(sim_frame)

        bot = tk.Frame(parent, bg=BG2, height=195,
                       highlightbackground=BORDER, highlightthickness=1)
        bot.pack(fill=tk.X)
        bot.pack_propagate(False)

        tp = tk.Frame(bot, bg=BG2)
        tp.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=6)
        self._build_traj_panel(tp)

        term = tk.Frame(bot, bg=BG2, width=310,
                        highlightbackground=BORDER, highlightthickness=1)
        term.pack(side=tk.RIGHT, fill=tk.Y)
        term.pack_propagate(False)
        self._build_terminal(term)

    def _build_traj_panel(self, parent):
        tabs = tk.Frame(parent, bg=BG2)
        tabs.pack(fill=tk.X, pady=(0, 6))
        self.traj_btns = {}
        for t in ["Recta", "Semiarco", "Perímetro"]:
            b = tk.Button(tabs, text=t, bg=BG3, fg=TEXT2,
                          font=self.fn_small, relief="flat", bd=0,
                          padx=10, pady=4, cursor="hand2",
                          command=lambda x=t: self._set_traj(x))
            b.pack(side=tk.LEFT, padx=2)
            self.traj_btns[t] = b
        self.traj_btns["Recta"].config(bg=BG4, fg=CYAN)

        row = tk.Frame(parent, bg=BG2)
        row.pack(fill=tk.X)

        og = tk.LabelFrame(row, text="Origen", bg=BG2, fg=TEXT3,
                            font=self.fn_small, padx=4, pady=3)
        og.pack(side=tk.LEFT, padx=(0, 8))
        self.x1 = tk.StringVar(value="0")
        self.y1 = tk.StringVar(value="0")
        for lbl, var in [("X1:", self.x1), ("Y1:", self.y1)]:
            r = tk.Frame(og, bg=BG2); r.pack(pady=2)
            tk.Label(r, text=lbl, bg=BG2, fg=TEXT2,
                     font=self.fn_small, width=3).pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, width=7, bg=BG3, fg=TEXT,
                     insertbackground=CYAN, font=self.fn_mono,
                     relief="flat", bd=2).pack(side=tk.LEFT)
            tk.Label(r, text="mm", bg=BG2, fg=TEXT3,
                     font=self.fn_small).pack(side=tk.LEFT, padx=2)

        self.arc_frame = tk.LabelFrame(row, text="Arco", bg=BG2,
                                        fg=TEXT3, font=self.fn_small,
                                        padx=4, pady=3)
        self.rad_var = tk.StringVar(value="40")
        self.dir_var = tk.StringVar(value="CW ↻")
        for lbl, var, opts in [("R:", self.rad_var, None),
                                ("Dir:", self.dir_var, ["CW ↻", "CCW ↺"])]:
            r = tk.Frame(self.arc_frame, bg=BG2); r.pack(pady=2)
            tk.Label(r, text=lbl, bg=BG2, fg=TEXT2,
                     font=self.fn_small, width=3).pack(side=tk.LEFT)
            if opts:
                ttk.Combobox(r, textvariable=var, values=opts,
                              width=7, state="readonly").pack(side=tk.LEFT)
            else:
                tk.Entry(r, textvariable=var, width=7, bg=BG3, fg=TEXT,
                         insertbackground=CYAN, font=self.fn_mono,
                         relief="flat", bd=2).pack(side=tk.LEFT)
                tk.Label(r, text="mm", bg=BG2, fg=TEXT3,
                         font=self.fn_small).pack(side=tk.LEFT, padx=2)

        dg = tk.LabelFrame(row, text="Destino", bg=BG2, fg=TEXT3,
                            font=self.fn_small, padx=4, pady=3)
        dg.pack(side=tk.LEFT, padx=(0, 8))
        self.x2   = tk.StringVar(value="30")
        self.y2   = tk.StringVar(value="0")
        self.feed = tk.StringVar(value="500")
        for lbl, var, unit in [("X2:", self.x2, "mm"),
                                ("Y2:", self.y2, "mm"),
                                ("Feed:", self.feed, "mm/m")]:
            r = tk.Frame(dg, bg=BG2); r.pack(pady=2)
            tk.Label(r, text=lbl, bg=BG2, fg=TEXT2,
                     font=self.fn_small, width=5).pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, width=7, bg=BG3, fg=TEXT,
                     insertbackground=CYAN, font=self.fn_mono,
                     relief="flat", bd=2).pack(side=tk.LEFT)
            tk.Label(r, text=unit, bg=BG2, fg=TEXT3,
                     font=self.fn_small).pack(side=tk.LEFT, padx=2)

        bc = tk.Frame(row, bg=BG2); bc.pack(side=tk.RIGHT, padx=6)
        for txt, cmd in [("= Ancho tabla", self._fill_w),
                         ("= Diagonal",    self._fill_diag)]:
            tk.Button(bc, text=txt, bg=BG3, fg=TEXT2,
                      font=self.fn_small, relief="flat", bd=0,
                      padx=6, pady=3, cursor="hand2",
                      command=cmd).pack(fill=tk.X, pady=1)
        tk.Button(bc, text="▶  Ejecutar", bg="#00b860", fg="#001a0e",
                  font=("Arial", 12, "bold"), relief="flat",
                  bd=0, padx=14, pady=7, cursor="hand2",
                  command=self._execute).pack(fill=tk.X, pady=(8, 0))

    def _build_terminal(self, parent):
        tk.Label(parent, text="⬢  Terminal GRBL", bg=BG2,
                 fg=CYAN, font=("Arial", 9, "bold"), pady=5
                 ).pack(fill=tk.X, padx=6)
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X)
        self.term = scrolledtext.ScrolledText(
            parent, bg=BG, fg=TEXT2, font=self.fn_small,
            relief="flat", bd=0, state="disabled", wrap=tk.WORD)
        self.term.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.term.tag_config("cmd",  foreground=CYAN)
        self.term.tag_config("info", foreground=TEXT2)
        self.term.tag_config("ok",   foreground=GREEN)
        self.term.tag_config("err",  foreground=RED)
        inp = tk.Frame(parent, bg=BG2)
        inp.pack(fill=tk.X, padx=4, pady=(0, 4))
        self.term_inp = tk.Entry(inp, bg=BG3, fg=CYAN,
                                  insertbackground=CYAN,
                                  font=self.fn_mono, relief="flat", bd=2)
        self.term_inp.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.term_inp.bind("<Return>", lambda e: self._send_cmd())
        tk.Button(inp, text="Enviar", bg=BG3, fg=TEXT2,
                  font=self.fn_small, relief="flat", bd=0,
                  padx=8, cursor="hand2",
                  command=self._send_cmd).pack(side=tk.LEFT, padx=3)
        for cls, msg in [("info", "Cero → XYZ=0"), ("cmd", ">> $X"),
                         ("info", "Cero → XYZ=0"), ("cmd", ">> G21"),
                         ("cmd", ">> G90")]:
            self._log(cls, msg)

    # ─────────────────────────────────────────
    #  LÓGICA
    # ─────────────────────────────────────────
    def _log(self, cls, msg):
        def _do():
            self.term.config(state="normal")
            self.term.insert(tk.END, msg + "\n", cls)
            self.term.see(tk.END)
            self.term.config(state="disabled")
        self.after(0, _do)

    def _on_pos(self, x, y, z, feed, state):
        """
        Llamado desde hilos secundarios.
        Throttle: no actualiza la UI más de ~30 veces/seg para evitar
        que los after() se acumulen y congelen el mainloop.
        """
        now = time.monotonic()
        if now - self._last_pos_update < 0.033:   # ~30 fps
            return
        self._last_pos_update = now

        def _do():
            def fmt(v):
                return ("+{:.3f}".format(v) if v >= 0 else "{:.3f}".format(v))
            self.lbl_x.config(text=f"X: {fmt(x)} mm")
            self.lbl_y.config(text=f"Y: {fmt(y)} mm")
            self.lbl_z.config(text=f"Z: {fmt(z)} mm")
            self.sv_x.set(f"X: {fmt(x)} mm")
            self.sv_y.set(f"Y: {fmt(y)} mm")
            self.sv_z.set(f"Z: {fmt(z)} mm")
            self.sv_feed.set(f"Feed: {int(feed)} mm/min")
            self.sv_st.set(state)
            c = GREEN if state == "Run" else (AMBER if state == "Home" else TEXT2)
            self.lbl_state.config(fg=c)
            self.sim.update_tool(x, y)
        self.after(0, _do)

    def _toggle_connect(self):
        if not self._connected:
            self.grbl.connect(self.port_var.get(), self.baud_var.get())
            self._connected = True
            self.btn_conn.config(text="Desconectar", fg=RED)
            self.lbl_conn.config(text=f"●  {self.port_var.get()}", fg=GREEN)
        else:
            self.grbl.disconnect()
            self._connected = False
            self.btn_conn.config(text="Conectar", fg=CYAN)
            self.lbl_conn.config(text="●  Desconectado", fg=RED)

    def _home(self):
        self.sim.reset_cut()
        self.grbl.send("$H")
        self._set_prog(0, "Homing...")

    def _zero(self, axis):
        cmds = {
            "x":   "G10 L20 P1 X0",
            "y":   "G10 L20 P1 Y0",
            "z":   "G10 L20 P1 Z0",
            "all": "G10 L20 P1 X0 Y0 Z0"
        }
        self.grbl.send(cmds[axis])
        lbl = {"x": "X", "y": "Y", "z": "Z", "all": "XYZ"}
        self._log("info", f"Cero → {lbl[axis]}=0")

    # ─── JOG CORREGIDO ────────────────────────
    # Calcula el destino absoluto desde tx/ty (posición objetivo),
    # aplica límites de tabla y envía UN solo G0 absoluto.
    # No usa G91/G90 en simulación para evitar el problema de
    # comandos ignorados.
    def _jog(self, dx, dy):
        step = self.step_var.get()
        try:
            W = float(self.table_w.get())
            H = float(self.table_h.get())
        except ValueError:
            W, H = 300.0, 200.0

        with self.grbl._lock:
            nx = max(0.0, min(W, self.grbl.tx + dx * step))
            ny = max(0.0, min(H, self.grbl.ty + dy * step))

        # Sin movimiento real → no enviar
        if abs(nx - self.grbl.tx) < 1e-9 and abs(ny - self.grbl.ty) < 1e-9:
            return

        # En hardware real GRBL usa $J=G90X...F... para jog cancelable.
        # En simulación (y como fallback) usamos G0 absoluto directamente.
        if not self.grbl.sim_mode:
            f = float(self.feed.get()) if self.feed.get() else 1000
            self.grbl.send(f"$J=G90X{nx:.3f}Y{ny:.3f}F{f:.0f}")
        else:
            self.grbl.send(f"G0 X{nx:.3f} Y{ny:.3f}")

    def _jog_z(self, dz):
        step = self.step_var.get()
        with self.grbl._lock:
            nz = self.grbl.tz + dz * step

        if not self.grbl.sim_mode:
            f = float(self.feed.get()) if self.feed.get() else 500
            self.grbl.send(f"$J=G90Z{nz:.3f}F{f:.0f}")
        else:
            self.grbl.send(f"G0 Z{nz:.3f}")

    def _set_traj(self, t):
        self.current_traj.set(t)
        for name, btn in self.traj_btns.items():
            btn.config(bg=(BG4 if name == t else BG3),
                       fg=(CYAN if name == t else TEXT2))
        badges = {"Recta": "— Recta",
                  "Semiarco": "~ Semiarco",
                  "Perímetro": "□ Perímetro"}
        self.badge.config(text=badges[t])
        if t == "Semiarco":
            self.arc_frame.pack(side=tk.LEFT, padx=(0, 8),
                                after=self.arc_frame.master.winfo_children()[0])
        else:
            self.arc_frame.pack_forget()
        self._preview()

    def _build_path(self):
        t = self.current_traj.get()
        try:
            x1 = float(self.x1.get()); y1 = float(self.y1.get())
            x2 = float(self.x2.get()); y2 = float(self.y2.get())
        except ValueError:
            return [], []
        xs, ys = [], []
        if t == "Recta":
            for i in range(61):
                s = i / 60
                xs.append(x1 + (x2 - x1) * s)
                ys.append(y1 + (y2 - y1) * s)
        elif t == "Semiarco":
            try: r = float(self.rad_var.get())
            except: r = 40.0
            cw = "CW" in self.dir_var.get()
            chord = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            if chord < 1e-6:
                return [x1], [y1]
            h  = math.sqrt(max(0, r * r - (chord / 2) ** 2))
            dx = (x2 - x1) / chord; dy = (y2 - y1) / chord
            sign = -1 if cw else 1
            mx = (x1 + x2) / 2 + sign * h * dy
            my = (y1 + y2) / 2 - sign * h * dx
            a1 = math.atan2(y1 - my, x1 - mx)
            a2 = math.atan2(y2 - my, x2 - mx)
            da = a2 - a1
            if cw and da > 0:  da -= 2 * math.pi
            if not cw and da < 0: da += 2 * math.pi
            for i in range(61):
                s = i / 60; a = a1 + da * s
                xs.append(mx + r * math.cos(a))
                ys.append(my + r * math.sin(a))
        elif t == "Perímetro":
            try:
                W = float(self.table_w.get())
                H = float(self.table_h.get())
            except ValueError:
                W, H = 300.0, 200.0
            corners = [(0, 0), (W, 0), (W, H), (0, H), (0, 0)]
            for s in range(4):
                for i in range(21):
                    tt = i / 20
                    xs.append(corners[s][0] + (corners[s+1][0] - corners[s][0]) * tt)
                    ys.append(corners[s][1] + (corners[s+1][1] - corners[s][1]) * tt)
        return xs, ys

    def _preview(self):
        try:
            W = float(self.table_w.get() or 300)
            H = float(self.table_h.get() or 200)
        except ValueError:
            return
        self.sim.set_table(W, H)
        xs, ys = self._build_path()
        self.sim.set_trajectory(xs, ys)

    def _execute(self):
        self._preview()
        self.sim.reset_cut()
        t = self.current_traj.get()
        try:
            x1 = float(self.x1.get()); y1 = float(self.y1.get())
            x2 = float(self.x2.get()); y2 = float(self.y2.get())
            f  = float(self.feed.get())
        except ValueError:
            self._log("err", "Error: valores inválidos")
            return
        self.grbl.feed = f
        if t == "Recta":
            self.grbl.send(f"G0 X{x1} Y{y1}")
            self.grbl.send(f"G1 X{x2} Y{y2} F{f}")
        elif t == "Semiarco":
            try: r = float(self.rad_var.get())
            except: r = 40.0
            cw = "CW" in self.dir_var.get()
            gc = "G2" if cw else "G3"
            self.grbl.send(f"G0 X{x1} Y{y1}")
            self.grbl.send(f"{gc} X{x2} Y{y2} R{r} F{f}")
        elif t == "Perímetro":
            try:
                W = float(self.table_w.get())
                H = float(self.table_h.get())
            except ValueError:
                W, H = 300.0, 200.0
            self.grbl.send("G0 X0 Y0")
            self.grbl.send(f"G1 X{W} Y0 F{f}")
            self.grbl.send(f"G1 X{W} Y{H} F{f}")
            self.grbl.send(f"G1 X0 Y{H} F{f}")
            self.grbl.send("G1 X0 Y0")
        self._set_prog(12, "Ejecutando…")
        self.sv_mode.set("G2/G3 Arco" if t == "Semiarco" else "G21 Métrico")

    def _sim_action(self, mode):
        self._preview()
        if mode == "animate":
            self.sim.reset_cut()

    def _send_cmd(self):
        cmd = self.term_inp.get().strip()
        if not cmd:
            return
        self.grbl.send(cmd)
        self.term_inp.delete(0, tk.END)

    def _stop(self):
        self._log("err", "! PARADA DE EMERGENCIA")
        if self._connected and not self.grbl.sim_mode:
            if self.grbl.ser and self.grbl.ser.is_open:
                self.grbl.ser.write(b"\x18")
        # Cancelar animación en curso
        with self.grbl._lock:
            self.grbl._move_seq += 1
        self.grbl.state = "Idle"
        self.sv_st.set("Idle")
        self.lbl_state.config(fg=TEXT2)

    def _fill_w(self):
        self.x2.set(self.table_w.get())
        self._preview()

    def _fill_diag(self):
        self.x2.set(self.table_w.get())
        self.y2.set(self.table_h.get())
        self._preview()

    def _update_passes(self):
        mode = self.zmode.get()
        if mode == "superficie":
            self.lbl_passes.config(text="Pasadas: 1 (superficie)")
        elif mode == "manual":
            try:
                d = float(self.zp_depth.get())
                s = float(self.zp_step.get())
                n = max(1, math.ceil(d / s))
                self.lbl_passes.config(text=f"Pasadas: {n}")
            except Exception:
                pass
        else:
            self.lbl_passes.config(text="Pasadas: Corte completo")

    def _set_prog(self, pct, label):
        self.prog_lbl.config(text=label)
        w = int(self._prog_w * min(1.0, pct / 100))
        self.prog_fill.place(x=0, y=0, relheight=1, width=w)

    def _refresh_ports(self):
        if SERIAL_AVAILABLE:
            ports = [p.device for p in serial.tools.list_ports.comports()]
        else:
            ports = ["COM3", "COM4", "/dev/ttyUSB0", "/dev/ttyACM0"]
        if not ports:
            ports = ["COM3"]
        self.port_cb["values"] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0])


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = CNCApp()
    app.after(600, app._toggle_connect)
    app.mainloop()