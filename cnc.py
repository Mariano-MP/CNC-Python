import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import threading
import time
import math

# ============================================================
# ESTADO GLOBAL
# ============================================================
arduino     = None
posicion    = {"x": 0.0, "y": 0.0, "z": 0.0}
cero_offset = {"x": 0.0, "y": 0.0, "z": 0.0}
conectado   = False
abort_flag  = threading.Event()
serial_lock = threading.Lock()

# ============================================================
# TEMA / COLORES
# ============================================================
BG      = "#0b0e11"
BG2     = "#111519"
BG3     = "#181d23"
BG4     = "#1e252e"
FG      = "#c9d1d9"
FG2     = "#8b949e"
ACCENT  = "#00d4ff"
GREEN   = "#00ff88"
RED     = "#ff4444"
AMBER   = "#ffaa00"
PURPLE  = "#b060ff"
GRID_C  = "#1a2030"

FONT_TITLE = ("Courier New", 11, "bold")
FONT_MONO  = ("Courier New", 9)
FONT_SMALL = ("Courier New", 8)
FONT_BIG   = ("Courier New", 14, "bold")
FONT_NUM   = ("Courier New", 16, "bold")

# ============================================================
# UTILIDADES
# ============================================================
def calcular_pasadas(z_inicio, z_final, paso_z):
    pasadas = []
    z = z_inicio - paso_z
    while z > z_final:
        pasadas.append(round(z, 4))
        z -= paso_z
    pasadas.append(round(z_final, 4))
    return pasadas

def z_retract(z_seguro):
    return f"G0 Z{z_seguro:.3f}"

# ============================================================
# CONEXIÓN SERIAL
# ============================================================
def listar_puertos():
    return [p.device for p in serial.tools.list_ports.comports()]

def conectar():
    global arduino, conectado
    puerto = combo_puerto.get()
    baud   = int(combo_baud.get())
    try:
        arduino = serial.Serial(puerto, baud, timeout=2)
        time.sleep(2)
        arduino.write(b"\r\n\r\n")
        time.sleep(2)
        arduino.flushInput()
        conectado = True
        btn_conectar.config(text="⏏  DESCONECTAR", bg="#1a0000", fg=RED)
        lbl_estado.config(text=f"●  {puerto} @ {baud}", fg=GREEN)
        log(f"✓ Conectado a {puerto} @ {baud} baudios")
    except Exception as e:
        messagebox.showerror("Error de conexión", str(e))

def desconectar():
    global arduino, conectado
    abort_flag.set()
    if arduino and arduino.is_open:
        arduino.close()
    conectado = False
    btn_conectar.config(text="⏻  CONECTAR", bg="#001a0a", fg=GREEN)
    lbl_estado.config(text="●  Desconectado", fg=RED)
    log("Desconectado")

def toggle_conexion():
    if conectado: desconectar()
    else:         conectar()

# ============================================================
# ENVÍO DE GCODE
# ============================================================
def enviar_gcode(comando):
    if not conectado or arduino is None:
        log(f"[SIN CONEXIÓN] {comando}")
        return False
    if abort_flag.is_set():
        return False
    with serial_lock:
        arduino.write((comando + "\n").encode())
        log(f"→ {comando}")
        while not abort_flag.is_set():
            try:
                respuesta = arduino.readline().decode(errors="replace").strip()
            except Exception:
                return False
            if respuesta:
                log(f"  ← {respuesta}")
            if "ok" in respuesta.lower():
                return True
            if "error" in respuesta.lower() or "alarm" in respuesta.lower():
                log(f"⚠ GRBL: {respuesta}")
                return False
    return False

def jog_move(axis, direction):
    try:
        step = float(ent_jog_step.get())
        feed = int(ent_jog_feed.get())
    except ValueError:
        log("⚠ Paso o feed inválido"); return
    dist = step * direction
    cmd = f"$J=G21 G91 {axis}{dist:.4f} F{feed}"
    enviar_lista([cmd])

def enviar_lista(cmds, on_done=None, on_progreso=None):
    abort_flag.clear()
    def _run():
        total = len(cmds)
        for i, cmd in enumerate(cmds):
            if abort_flag.is_set():
                log("⚠ Operación abortada")
                ventana.after(0, lambda: lbl_tray_info.config(text="⚠ Abortado", fg=AMBER))
                return
            ok = enviar_gcode(cmd)
            if not ok and not abort_flag.is_set():
                log("⚠ Error en comando, deteniendo")
                return
            if on_progreso:
                ventana.after(0, lambda i=i, t=total: on_progreso(i+1, t))
        if on_done and not abort_flag.is_set():
            ventana.after(0, on_done)
    threading.Thread(target=_run, daemon=True).start()

def log(texto):
    def _u():
        txt_log.config(state="normal")
        txt_log.insert("end", texto + "\n")
        txt_log.see("end")
        txt_log.config(state="disabled")
    ventana.after(0, _u)

# ============================================================
# POSICIÓN / CERO
# ============================================================
def actualizar_display_pos():
    lbl_pos_x.config(text=f"{posicion['x'] - cero_offset['x']:+9.3f}")
    lbl_pos_y.config(text=f"{posicion['y'] - cero_offset['y']:+9.3f}")
    lbl_pos_z.config(text=f"{posicion['z'] - cero_offset['z']:+9.3f}")
    actualizar_cabezal()

def parsear_estado_grbl(linea):
    import re
    m = re.search(r"WPos:([-\d.]+),([-\d.]+),([-\d.]+)", linea)
    if not m:
        m = re.search(r"MPos:([-\d.]+),([-\d.]+),([-\d.]+)", linea)
    if m:
        posicion["x"] = float(m.group(1))
        posicion["y"] = float(m.group(2))
        posicion["z"] = float(m.group(3))
        ventana.after(0, actualizar_display_pos)

def hilo_posicion():
    while True:
        time.sleep(0.5)
        if not conectado or arduino is None or not arduino.is_open:
            continue
        if serial_lock.locked():
            continue
        try:
            with serial_lock:
                arduino.write(b"?")
                time.sleep(0.1)
                while arduino.in_waiting:
                    linea = arduino.readline().decode(errors="replace").strip()
                    if linea.startswith("<"):
                        parsear_estado_grbl(linea)
        except Exception:
            pass

def set_cero(eje=None):
    axes = ["x","y","z"] if eje is None else [eje]
    gcmd = "G92 " + " ".join(f"{a.upper()}0" for a in axes)
    for a in axes:
        cero_offset[a] = posicion[a]
    enviar_lista([gcmd])
    actualizar_display_pos()
    label = "XYZ" if eje is None else eje.upper()
    log(f"Cero → {label}=0")

def hacer_home():
    def _done():
        posicion.update({"x":0.0,"y":0.0,"z":0.0})
        cero_offset.update({"x":0.0,"y":0.0,"z":0.0})
        actualizar_display_pos()
        log("HOME completado ✓")
    enviar_lista(["$X","$H"], on_done=_done)

def parar():
    abort_flag.set()
    if arduino and arduino.is_open:
        arduino.write(b"!")
    log("⛔ PARADA DE EMERGENCIA")
    lbl_tray_info.config(text="⛔ PARADA DE EMERGENCIA", fg=RED)

# ============================================================
# LECTURA DE PARÁMETROS
# ============================================================
def get_tabla():
    try:
        return {
            "x": float(ent_tabla_x.get()),
            "y": float(ent_tabla_y.get()),
            "z": float(ent_tabla_z.get()),
        }
    except ValueError:
        messagebox.showerror("Error","Dimensiones de tabla inválidas"); return None

def get_maquina():
    """Retorna las dimensiones máximas de la máquina."""
    try:
        return {
            "x": float(ent_maq_x.get()),
            "y": float(ent_maq_y.get()),
        }
    except ValueError:
        return {"x": 370.0, "y": 170.0}

def get_z_params():
    try:
        modo     = var_z_modo.get()
        paso     = float(ent_z_paso.get())
        z_seguro = float(ent_z_seguro.get())
        feed_z   = int(ent_z_feed.get())
        if modo == "profundidad":
            prof   = float(ent_z_prof.get())
            z_final = -abs(prof)
        elif modo == "completo":
            d = get_tabla()
            if not d: return None
            z_final = -abs(d["z"])
        else:
            z_final = 0.0
        if paso <= 0:
            messagebox.showerror("Error","El paso Z debe ser > 0"); return None
        return {"modo": modo, "paso": paso, "z_final": z_final,
                "z_seguro": z_seguro, "feed_z": feed_z}
    except ValueError:
        messagebox.showerror("Error","Parámetros Z inválidos"); return None

def num_pasadas_label():
    zp = get_z_params()
    if not zp or zp["z_final"] == 0.0:
        lbl_pasadas.config(text="1 pasada  (Z=0 superficie)")
        return
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"])
    n = len(pasadas)
    lbl_pasadas.config(text=f"{n} pasadas  →  Z={zp['z_final']:.2f} mm")

# ============================================================
# CANVAS PREVIEW — NUEVO SISTEMA
# ============================================================
CANVAS_W = 800
CANVAS_H = 500

_preview_path = []

def calcular_transform(maq_x, maq_y, cw, ch):
    """
    Calcula la transformación mm → píxeles para el canvas.
    El área de la máquina ocupa el espacio útil del canvas.
    """
    PAD_LEFT   = 46   # espacio para etiquetas Y
    PAD_RIGHT  = 12
    PAD_TOP    = 12
    PAD_BOTTOM = 30   # espacio para etiquetas X

    usable_w = cw - PAD_LEFT - PAD_RIGHT
    usable_h = ch - PAD_TOP - PAD_BOTTOM

    scale = min(usable_w / maq_x, usable_h / maq_y)

    # Centrar dentro del espacio útil
    off_x = PAD_LEFT + (usable_w - maq_x * scale) / 2
    off_y = PAD_TOP  + (usable_h - maq_y * scale) / 2

    return scale, off_x, off_y

def mm_to_canvas_new(x_mm, y_mm, scale, off_x, off_y, maq_y):
    """Convierte mm → píxeles. Y se invierte (0 abajo, maq_y arriba)."""
    px = off_x + x_mm * scale
    py = off_y + (maq_y - y_mm) * scale
    return px, py

def calcular_paso_grid(scale):
    """
    Elige el paso de grilla (en mm) de modo que los píxeles entre
    líneas queden entre 30 y 80 px aproximadamente.
    """
    candidatos = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500]
    for paso in candidatos:
        if paso * scale >= 35:
            return paso
    return 500

def dibujar_canvas(path_puntos=None, tipo="recta", cabezal=None):
    """Redibuja el canvas completo con ejes, cuadrícula, tabla y trayectoria."""
    global _preview_path

    d   = get_tabla()
    maq = get_maquina()
    if not d:
        return

    canvas_preview.delete("all")
    cw = CANVAS_W
    ch = CANVAS_H

    maq_x = maq["x"]
    maq_y = maq["y"]
    tab_x = d["x"]
    tab_y = d["y"]

    scale, off_x, off_y = calcular_transform(maq_x, maq_y, cw, ch)

    def to_c(x, y):
        return mm_to_canvas_new(x, y, scale, off_x, off_y, maq_y)

    # ── Fondo ────────────────────────────────────────────────
    canvas_preview.config(bg="#100909")

    # ── Cuadrícula con paso calculado ───────────────────────
    paso = calcular_paso_grid(scale)

    x_mm = 0
    while x_mm <= maq_x:
        px, _ = to_c(x_mm, 0)
        _, py_top = to_c(0, maq_y)
        _, py_bot = to_c(0, 0)
        canvas_preview.create_line(px, py_top, px, py_bot,
                                   fill="#18283a", width=1)
        x_mm += paso

    y_mm = 0
    while y_mm <= maq_y:
        px_left,  py = to_c(0, y_mm)
        px_right, _  = to_c(maq_x, y_mm)
        canvas_preview.create_line(px_left, py, px_right, py,
                                   fill="#18283a", width=1)
        y_mm += paso

    # ── Etiquetas del eje X ──────────────────────────────────
    x_mm = 0
    while x_mm <= maq_x:
        px, _ = to_c(x_mm, 0)
        _, py_bot = to_c(0, 0)
        canvas_preview.create_text(px, py_bot + 10,
                                   text=str(int(x_mm)),
                                   fill="#3a6080", font=FONT_SMALL,
                                   anchor="n")
        x_mm += paso

    # Título eje X
    px_mid, _ = to_c(maq_x / 2, 0)
    _, py_bot = to_c(0, -10)
    canvas_preview.create_text(px_mid, py_bot + 22,
                               text="X (mm)", fill="#2a5070",
                               font=FONT_SMALL)

    # ── Etiquetas del eje Y ──────────────────────────────────
    y_mm = 0
    while y_mm <= maq_y:
        px_left, py = to_c(0, y_mm)
        canvas_preview.create_text(px_left - 4, py,
                                   text=str(int(y_mm)),
                                   fill="#3a6080", font=FONT_SMALL,
                                   anchor="e")
        y_mm += paso

    # Título eje Y (vertical, simulado con texto corto)
    _, py_mid = to_c(0, maq_y / 2)
    px_left,  _ = to_c(0, 0)
    canvas_preview.create_text(px_left - 34, py_mid,
                               text="Y\n(mm)", fill="#2a5070",
                               font=FONT_SMALL, anchor="center")

    # ── Flechas de ejes ─────────────────────────────────────
    ox, oy = to_c(0, 0)
    ex, _  = to_c(maq_x, 0)
    _, ey  = to_c(0, maq_y)

    # Eje X
    canvas_preview.create_line(ox, oy, ex + 10, oy,
                               fill="#1a4a7a", width=1.5, arrow="last",
                               arrowshape=(7, 9, 3))
    # Eje Y
    canvas_preview.create_line(ox, oy, ox, ey - 10,
                               fill="#1a4a7a", width=1.5, arrow="last",
                               arrowshape=(7, 9, 3))

    # ── Borde área máquina (línea punteada azul tenue) ───────
    x0m, y0m = to_c(0, 0)
    xfm, yfm = to_c(maq_x, maq_y)
    canvas_preview.create_rectangle(x0m, yfm, xfm, y0m,
                                    outline="#1a5090", width=1,
                                    dash=(6, 4), fill="")
    canvas_preview.create_text(xfm - 2, yfm - 2,
                               text=f"Máq {maq_x:.0f}×{maq_y:.0f}",
                               fill="#1a5090", font=FONT_SMALL,
                               anchor="se")

    # ── Tabla de trabajo (borde cian sólido) ─────────────────
    x0t, y0t = to_c(0, 0)
    xft, yft = to_c(tab_x, tab_y)

    # Relleno semitransparente simulado con stipple
    canvas_preview.create_rectangle(x0t, yft, xft, y0t,
                                    outline=ACCENT, width=2,
                                    fill="#091830", stipple="gray12")
    canvas_preview.create_rectangle(x0t, yft, xft, y0t,
                                    outline=ACCENT, width=2, fill="")

    # Cotas de la tabla
    mid_x_t = (x0t + xft) / 2
    canvas_preview.create_text(mid_x_t, y0t - 10,
                               text=f"← {tab_x:.0f} mm →",
                               fill=ACCENT, font=FONT_SMALL)
    mid_y_t = (y0t + yft) / 2
    canvas_preview.create_text(x0t + 8, mid_y_t,
                               text=f"← {tab_y:.0f} mm →", fill=ACCENT,
                               font=FONT_SMALL, angle=90)

    # ── Origen 0,0 ───────────────────────────────────────────
    canvas_preview.create_oval(ox - 5, oy - 5, ox + 5, oy + 5,
                               fill=ACCENT, outline=ACCENT)
    canvas_preview.create_text(ox + 10, oy - 8,
                               text="0,0", fill=ACCENT, font=FONT_SMALL)

    # ── Trayectoria (línea punteada ámbar) ───────────────────
    if path_puntos and len(path_puntos) >= 2:
        _preview_path = path_puntos
        pts_canvas = [to_c(px, py) for (px, py) in path_puntos]

        # Línea punteada: se dibuja segmento a segmento con dash
        for i in range(len(pts_canvas) - 1):
            x1c, y1c = pts_canvas[i]
            x2c, y2c = pts_canvas[i + 1]
            canvas_preview.create_line(x1c, y1c, x2c, y2c,
                                       fill=AMBER, width=2,
                                       dash=(85, 5),
                                       capstyle="round")

        # Marcadores inicio / fin
        sx, sy = pts_canvas[0]
        ex2, ey2 = pts_canvas[-1]
        canvas_preview.create_oval(sx - 5, sy - 5, sx + 5, sy + 5,
                                   fill=GREEN, outline=GREEN)
        canvas_preview.create_text(sx + 10, sy - 8,
                                   text="S", fill=GREEN, font=FONT_SMALL)
        canvas_preview.create_oval(ex2 - 5, ey2 - 5, ex2 + 5, ey2 + 5,
                                   fill=AMBER, outline=AMBER)
        canvas_preview.create_text(ex2 + 10, ey2 + 8,
                                   text="E", fill=AMBER, font=FONT_SMALL)

    # ── Cabezal (posición actual) ─────────────────────────────
    hx = cabezal[0] if cabezal else (posicion["x"] - cero_offset["x"])
    hy = cabezal[1] if cabezal else (posicion["y"] - cero_offset["y"])
    hxc, hyc = to_c(hx, hy)

    size = 10
    canvas_preview.create_line(hxc - size, hyc, hxc + size, hyc,
                               fill=RED, width=2)
    canvas_preview.create_line(hxc, hyc - size, hxc, hyc + size,
                               fill=RED, width=2)
    canvas_preview.create_oval(hxc - 5, hyc - 5, hxc + 5, hyc + 5,
                               outline=RED, width=2, fill="")
    """canvas_preview.create_oval(hxc - 10, hyc - 10, hxc + 10, hyc + 10,
                               outline=RED, width=1, fill="", dash=(3, 3))"""

    # ── Leyenda ───────────────────────────────────────────────
    lx, ly = 6, ch - 20
    items = [
        ("━", "#1a5090", f"Máquina ({maq_x:.0f}×{maq_y:.0f})"),
        ("━", ACCENT,    "Tabla de trabajo"),
        ("--", AMBER,     "Trayectoria"),
        ("●", GREEN,     "Inicio (S)"),
        ("●", AMBER,     "Fin (E)"),
        ("✛", RED,       "Cabezal"),
    ]
    for sym, color, label in items:
        canvas_preview.create_text(lx, ly, text=sym,
                                   fill=color, font=FONT_SMALL, anchor="w")
        lx += 30
        canvas_preview.create_text(lx, ly, text=label,
                                   fill=FG2, font=FONT_SMALL, anchor="w")
        lx += len(label) * 6 + 30


def actualizar_cabezal():
    """Actualiza el canvas con la posición actual del cabezal."""
    try:
        dibujar_canvas(path_puntos=_preview_path if _preview_path else None,
                       tipo="recta")
    except Exception:
        pass

def preview_recta():
    try:
        x1 = float(ent_r_x1.get()); y1 = float(ent_r_y1.get())
        x2 = float(ent_r_x2.get()); y2 = float(ent_r_y2.get())
    except ValueError:
        return
    pts = [(x1, y1), (x2, y2)]
    dibujar_canvas(path_puntos=pts, tipo="recta")

def preview_arco():
    try:
        cx = float(ent_a_cx.get())
        cy = float(ent_a_cy.get())
        r  = float(ent_a_r.get())
    except ValueError:
        return
    pts = []
    for ang in range(0, 181, 3):
        rad = math.radians(ang)
        pts.append((cx + r * math.cos(math.pi - rad),
                    cy + r * math.sin(math.pi - rad)))
    dibujar_canvas(path_puntos=pts, tipo="arco")

def preview_perimetro():
    d = get_tabla()
    if not d: return
    try:
        offset = float(ent_p_off.get())
    except ValueError:
        offset = 0
    x0, y0 = offset, offset
    xf, yf = d["x"] - offset, d["y"] - offset
    pts = [(x0, y0), (xf, y0), (xf, yf), (x0, yf), (x0, y0)]
    dibujar_canvas(path_puntos=pts, tipo="perimetro")

# ============================================================
# BUILD GCODE
# ============================================================
def build_gcode_recta(x1, y1, x2, y2, feed, zp):
    cmds = ["G21","G90"]
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0.0 else [0.0]
    n = len(pasadas)
    cmds.append(z_retract(zp["z_seguro"]))
    cmds.append(f"G0 X{x1:.3f} Y{y1:.3f}")
    for i, z in enumerate(pasadas):
        cmds.append(f"; Pasada {i+1}/{n}  Z={z:.3f}")
        cmds.append(f"G1 Z{z:.3f} F{zp['feed_z']}")
        cmds.append(f"G1 X{x2:.3f} Y{y2:.3f} F{feed}")
        if i < n-1:
            cmds.append(z_retract(zp["z_seguro"]))
            cmds.append(f"G0 X{x1:.3f} Y{y1:.3f}")
    cmds.append(z_retract(zp["z_seguro"]))
    return cmds

def build_gcode_arco(x1, y1, x2, y2, I, J, dire, feed, zp):
    cmds = ["G21","G90"]
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0.0 else [0.0]
    n = len(pasadas)
    cmds.append(z_retract(zp["z_seguro"]))
    cmds.append(f"G0 X{x1:.3f} Y{y1:.3f}")
    for i, z in enumerate(pasadas):
        cmds.append(f"; Pasada {i+1}/{n}  Z={z:.3f}")
        cmds.append(f"G1 Z{z:.3f} F{zp['feed_z']}")
        cmds.append(f"{dire} X{x2:.3f} Y{y2:.3f} I{I:.3f} J{J:.3f} F{feed}")
        if i < n-1:
            cmds.append(z_retract(zp["z_seguro"]))
            cmds.append(f"G0 X{x1:.3f} Y{y1:.3f}")
    cmds.append(z_retract(zp["z_seguro"]))
    return cmds

def build_gcode_perimetro(x0, y0, xf, yf, feed, zp):
    cmds = ["G21","G90"]
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0.0 else [0.0]
    n = len(pasadas)
    cmds.append(z_retract(zp["z_seguro"]))
    cmds.append(f"G0 X{x0:.3f} Y{y0:.3f}")
    for i, z in enumerate(pasadas):
        cmds.append(f"; Pasada {i+1}/{n}  Z={z:.3f}")
        cmds.append(f"G1 Z{z:.3f} F{zp['feed_z']}")
        cmds.append(f"G1 X{xf:.3f} Y{y0:.3f} F{feed}")
        cmds.append(f"G1 X{xf:.3f} Y{yf:.3f}")
        cmds.append(f"G1 X{x0:.3f} Y{yf:.3f}")
        cmds.append(f"G1 X{x0:.3f} Y{y0:.3f}")
        if i < n-1:
            cmds.append(z_retract(zp["z_seguro"]))
    cmds.append(z_retract(zp["z_seguro"]))
    return cmds

# ============================================================
# ACCIONES DE TRAYECTORIA
# ============================================================
def on_progreso(actual, total):
    pct = int(actual / total * 100)
    lbl_tray_info.config(text=f"Ejecutando  {actual}/{total}  ({pct}%)", fg=ACCENT)
    prog_bar["value"] = pct

def trayectoria_recta():
    d  = get_tabla(); zp = get_z_params()
    if not d or not zp: return
    try:
        x1 = float(ent_r_x1.get()); y1 = float(ent_r_y1.get())
        x2 = float(ent_r_x2.get()); y2 = float(ent_r_y2.get())
        feed = int(ent_r_feed.get())
    except ValueError:
        messagebox.showerror("Error","Valores inválidos"); return
    preview_recta()
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0 else [0]
    dist = math.hypot(x2-x1, y2-y1)
    lbl_tray_info.config(text=f"Recta  {dist:.1f}mm  ×{len(pasadas)} pasada(s)", fg=ACCENT)
    cmds = build_gcode_recta(x1, y1, x2, y2, feed, zp)
    prog_bar["value"] = 0
    log(f"▶ Recta: {len(cmds)} comandos")
    enviar_lista(cmds,
        on_done=lambda: [lbl_tray_info.config(text="✓ Recta completada", fg=GREEN),
                         prog_bar.__setitem__("value", 100)],
        on_progreso=on_progreso)

def trayectoria_arco():
    d  = get_tabla(); zp = get_z_params()
    if not d or not zp: return
    try:
        cx = float(ent_a_cx.get()); cy = float(ent_a_cy.get())
        r  = float(ent_a_r.get()); feed = int(ent_a_feed.get())
        dire = var_dir.get()
    except ValueError:
        messagebox.showerror("Error","Valores inválidos"); return
    preview_arco()
    x1 = cx - r; y1 = cy; x2 = cx + r; y2 = cy; I = -r; J = 0
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0 else [0]
    lbl_tray_info.config(text=f"Semiarco R={r:.1f}mm  ×{len(pasadas)} pasada(s)", fg=ACCENT)
    cmds = build_gcode_arco(x1, y1, x2, y2, I, J, dire, feed, zp)
    prog_bar["value"] = 0
    log(f"▶ Semiarco R={r:.1f}: {len(cmds)} comandos")
    enviar_lista(cmds,
        on_done=lambda: [lbl_tray_info.config(text="✓ Semiarco completado", fg=GREEN),
                         prog_bar.__setitem__("value", 100)],
        on_progreso=on_progreso)

def trayectoria_perimetro():
    d  = get_tabla(); zp = get_z_params()
    if not d or not zp: return
    try:
        offset = float(ent_p_off.get()); feed = int(ent_p_feed.get())
    except ValueError:
        messagebox.showerror("Error","Valores inválidos"); return
    preview_perimetro()
    x0,y0 = offset,offset; xf,yf = d["x"]-offset, d["y"]-offset
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0 else [0]
    lbl_tray_info.config(text=f"Perímetro  ×{len(pasadas)} pasada(s)", fg=ACCENT)
    cmds = build_gcode_perimetro(x0, y0, xf, yf, feed, zp)
    prog_bar["value"] = 0
    log(f"▶ Perímetro: {len(cmds)} comandos")
    enviar_lista(cmds,
        on_done=lambda: [lbl_tray_info.config(text="✓ Perímetro completado", fg=GREEN),
                         prog_bar.__setitem__("value", 100)],
        on_progreso=on_progreso)

def recta_ancho_tabla():
    d = get_tabla()
    if not d: return
    ent_r_x1.delete(0,"end"); ent_r_x1.insert(0,"0")
    ent_r_y1.delete(0,"end"); ent_r_y1.insert(0,"0")
    ent_r_x2.delete(0,"end"); ent_r_x2.insert(0,str(d["x"]))
    ent_r_y2.delete(0,"end"); ent_r_y2.insert(0,"0")
    num_pasadas_label(); preview_recta()

def recta_diagonal():
    d = get_tabla()
    if not d: return
    ent_r_x1.delete(0,"end"); ent_r_x1.insert(0,"0")
    ent_r_y1.delete(0,"end"); ent_r_y1.insert(0,"0")
    ent_r_x2.delete(0,"end"); ent_r_x2.insert(0,str(d["x"]))
    ent_r_y2.delete(0,"end"); ent_r_y2.insert(0,str(d["y"]))
    num_pasadas_label(); preview_recta()

def arco_desde_tabla():
    d = get_tabla()
    if not d: return
    r = d["x"] / 2
    ent_a_r.delete(0,"end");  ent_a_r.insert(0,f"{r:.1f}")
    ent_a_cx.delete(0,"end"); ent_a_cx.insert(0,f"{r:.1f}")
    ent_a_cy.delete(0,"end"); ent_a_cy.insert(0,"0")
    num_pasadas_label(); preview_arco()

# ============================================================
# HELPERS DE ESTILO UI
# ============================================================
def make_label(parent, text, fg=FG, font=FONT_MONO, **kw):
    return tk.Label(parent, text=text, bg=parent.cget("bg"),
                    fg=fg, font=font, **kw)

def make_entry(parent, default="", width=8):
    e = tk.Entry(parent, font=FONT_MONO, width=width,
                 bg="#0d1520", fg=FG,
                 insertbackground=ACCENT,
                 relief="flat", bd=0,
                 highlightthickness=1,
                 highlightbackground="#1e3050",
                 highlightcolor=ACCENT)
    e.insert(0, default)
    return e

def make_btn(parent, text, cmd, color=BG4, fg=FG, width=14, accent=False):
    bg = ACCENT if accent else color
    fc = "#000" if accent else fg
    b = tk.Button(parent, text=text, command=cmd, font=FONT_MONO,
                  bg=bg, fg=fc,
                  activebackground=ACCENT if accent else "#2a3040",
                  activeforeground="#000" if accent else FG,
                  relief="flat", bd=0, padx=6, pady=5,
                  width=width, cursor="hand2")
    return b

def section_frame(parent, title, color=BG3):
    outer = tk.Frame(parent, bg=color, bd=0)
    bar = tk.Frame(outer, bg=ACCENT, height=2)
    bar.pack(fill="x")
    header = tk.Frame(outer, bg=color)
    header.pack(fill="x", padx=8, pady=(4,0))
    tk.Label(header, text=title.upper(), bg=color,
             fg=ACCENT, font=FONT_SMALL).pack(side="left")
    inner = tk.Frame(outer, bg=color)
    inner.pack(fill="x", padx=8, pady=(2,6))
    return outer, inner

def field_row(parent, label_txt, default, unit="mm", width=8):
    f = tk.Frame(parent, bg=parent.cget("bg"))
    f.pack(fill="x", pady=2)
    tk.Label(f, text=label_txt, bg=f.cget("bg"), fg=FG2,
             font=FONT_MONO, width=17, anchor="w").pack(side="left")
    e = make_entry(f, default, width=width)
    e.pack(side="left", padx=(0,4))
    tk.Label(f, text=unit, bg=f.cget("bg"),
             fg="#2a4060", font=FONT_SMALL).pack(side="left")
    return e

# ============================================================
# VENTANA PRINCIPAL
# ============================================================
ventana = tk.Tk()
ventana.title("CNC Control  ▸  GRBL  v3")
ventana.geometry("1800x1200")
ventana.configure(bg=BG)
ventana.resizable(True, True)

# ── HEADER BAR ──────────────────────────────────────────────
frm_header = tk.Frame(ventana, bg="#070a0d", pady=0)
frm_header.pack(fill="x")

tk.Frame(frm_header, bg=ACCENT, height=2).pack(fill="x")

frm_header_inner = tk.Frame(frm_header, bg="#070a0d", pady=6)
frm_header_inner.pack(fill="x", padx=12)

tk.Label(frm_header_inner, text="CNC", bg="#070a0d",
         fg=ACCENT, font=("Courier New", 18, "bold")).pack(side="left")
tk.Label(frm_header_inner, text=" CONTROL  ", bg="#070a0d",
         fg=FG, font=("Courier New", 18, "bold")).pack(side="left")
tk.Label(frm_header_inner, text="▸ GRBL", bg="#070a0d",
         fg=FG2, font=("Courier New", 11)).pack(side="left")

tk.Frame(frm_header_inner, bg="#1a2535", width=2).pack(side="left", fill="y", padx=14)

tk.Label(frm_header_inner, text="PORT", bg="#070a0d",
         fg=FG2, font=FONT_SMALL).pack(side="left")
puertos = listar_puertos() or ["COM3"]

style_combo = ttk.Style()
style_combo.theme_use("clam")
style_combo.configure("Dark.TCombobox",
    fieldbackground="#0d1520", background="#0d1520",
    foreground=FG, bordercolor="#1e3050",
    arrowcolor=ACCENT, selectbackground="#0d1520",
    selectforeground=FG)

combo_puerto = ttk.Combobox(frm_header_inner, values=puertos, width=9,
                             font=FONT_MONO, style="Dark.TCombobox")
combo_puerto.set(puertos[0])
combo_puerto.pack(side="left", padx=(4,8))

tk.Label(frm_header_inner, text="BAUD", bg="#070a0d",
         fg=FG2, font=FONT_SMALL).pack(side="left")
combo_baud = ttk.Combobox(frm_header_inner, values=["9600","115200","250000"],
                           width=8, font=FONT_MONO, style="Dark.TCombobox")
combo_baud.set("115200")
combo_baud.pack(side="left", padx=(4,12))

btn_conectar = make_btn(frm_header_inner, "⏻  CONECTAR", toggle_conexion,
                         color="#001a0a", fg=GREEN, width=14)
btn_conectar.pack(side="left", padx=4)

lbl_estado = tk.Label(frm_header_inner, text="●  Desconectado",
                       bg="#070a0d", fg=RED, font=FONT_MONO)
lbl_estado.pack(side="left", padx=8)

frm_parar = tk.Frame(frm_header_inner, bg=RED, bd=0)
frm_parar.pack(side="right", padx=8)
tk.Button(frm_parar, text="  ⛔  PARAR  ", command=parar,
          font=("Courier New", 10, "bold"),
          bg=RED, fg="white", activebackground="#cc0000",
          relief="flat", bd=0, padx=8, pady=6, cursor="hand2").pack()

tk.Frame(frm_header, bg="#0d1520", height=1).pack(fill="x")

# ── CUERPO ──────────────────────────────────────────────────
frm_body = tk.Frame(ventana, bg=BG)
frm_body.pack(fill="both", expand=True, padx=0, pady=0)

# ══ COLUMNA A: Config + Jog ══════════════════════════════════
frm_colA = tk.Frame(frm_body, bg=BG2, width=270)
frm_colA.pack(side="left", fill="y", padx=(0,1))
frm_colA.pack_propagate(False)

# — Posición DRO —
frm_dro, inner_dro = section_frame(frm_colA, "▸ Posición actual", BG2)
frm_dro.pack(fill="x", pady=(0,1))

for axis, color in [("X", GREEN), ("Y", RED), ("Z", ACCENT)]:
    row = tk.Frame(inner_dro, bg=BG2)
    row.pack(fill="x", pady=1)
    tk.Label(row, text=axis, bg=BG2, fg=color,
             font=("Courier New", 12, "bold"), width=2).pack(side="left")
    lbl_name = tk.Label(row, text="+0000.000", bg="#0a0e14", fg=color,
                        font=("Courier New", 14, "bold"),
                        relief="flat", bd=0, padx=6, pady=2,
                        highlightthickness=1, highlightbackground="#1a2535")
    lbl_name.pack(side="left", fill="x", expand=True)
    tk.Label(row, text="mm", bg=BG2, fg="#1a3050",
             font=FONT_SMALL).pack(side="left", padx=4)
    if axis == "X": lbl_pos_x = lbl_name
    elif axis == "Y": lbl_pos_y = lbl_name
    else: lbl_pos_z = lbl_name

frm_ceros = tk.Frame(inner_dro, bg=BG2)
frm_ceros.pack(fill="x", pady=(4,0))
for txt, ax in [("X=0","x"),("Y=0","y"),("Z=0","z")]:
    make_btn(frm_ceros, txt, lambda a=ax: set_cero(a),
             color="#0d1520", fg=ACCENT, width=5).pack(side="left", padx=1)
make_btn(frm_ceros, "XYZ=0", lambda: set_cero(None),
         color="#1a1500", fg=AMBER, width=7).pack(side="left", padx=1)

# — Dimensiones tabla —
frm_tab, inner_tab = section_frame(frm_colA, "▸ Tabla de trabajo", BG2)
frm_tab.pack(fill="x", pady=(1,1))

ent_tabla_x = field_row(inner_tab, "Ancho  X :", "300")
ent_tabla_y = field_row(inner_tab, "Alto   Y :", "170")
ent_tabla_z = field_row(inner_tab, "Espesor Z:", "2")

# — Dimensiones máquina (NUEVO) —
frm_maq, inner_maq = section_frame(frm_colA, "▸ Área máxima máquina", BG2)
frm_maq.pack(fill="x", pady=(1,1))

ent_maq_x = field_row(inner_maq, "Máquina X :", "370")
ent_maq_y = field_row(inner_maq, "Máquina Y :", "170")

def on_dim_change(*a):
    try: dibujar_canvas()
    except: pass

for e in [ent_tabla_x, ent_tabla_y, ent_maq_x, ent_maq_y]:
    e.bind("<FocusOut>", on_dim_change)
    e.bind("<Return>",   on_dim_change)

# — HOME —
make_btn(frm_colA, "⌂  IR A HOME", hacer_home,
         color="#1a0040", fg=PURPLE, width=28).pack(fill="x", pady=(2,1), padx=0)

# — Pasadas Z —
frm_z, inner_z = section_frame(frm_colA, "▸ Pasadas en Z", BG2)
frm_z.pack(fill="x", pady=(1,1))

var_z_modo = tk.StringVar(value="superficie")

for txt, val in [("Superficie (Z=0)", "superficie"),
                 ("Profundidad manual", "profundidad"),
                 ("Corte completo (espesor)", "completo")]:
    tk.Radiobutton(inner_z, text=txt, variable=var_z_modo, value=val,
                   bg=BG2, fg=FG2, selectcolor="#0d1520",
                   activebackground=BG2, font=FONT_SMALL,
                   command=lambda: [toggle_z_ui(), num_pasadas_label()]
                   ).pack(anchor="w", pady=1)

frm_zp = tk.Frame(inner_z, bg=BG2); frm_zp.pack(fill="x", pady=2)
tk.Label(frm_zp, text="Profundidad:", bg=BG2, fg=FG2,
         font=FONT_MONO, width=14, anchor="w").pack(side="left")
ent_z_prof = make_entry(frm_zp, "5", width=7); ent_z_prof.pack(side="left")
tk.Label(frm_zp, text="mm", bg=BG2, fg="#1a3050", font=FONT_SMALL).pack(side="left", padx=3)

tk.Frame(inner_z, bg="#1a2535", height=1).pack(fill="x", pady=3)

ent_z_paso   = field_row(inner_z, "Paso por pasada:", "0.5")
ent_z_feed   = field_row(inner_z, "Vel. bajada Z:", "100", "mm/m")
ent_z_seguro = field_row(inner_z, "Z seguro:", "5")

lbl_pasadas = tk.Label(inner_z, text="1 pasada  (Z=0 superficie)",
                        bg=BG2, fg=ACCENT, font=FONT_SMALL)
lbl_pasadas.pack(anchor="w", pady=(4,0))

make_btn(inner_z, "↺  Calcular pasadas", num_pasadas_label,
         color="#0d1520", fg=ACCENT, width=24).pack(pady=(4,0))

def toggle_z_ui():
    estado = "normal" if var_z_modo.get() == "profundidad" else "disabled"
    ent_z_prof.config(state=estado)
    num_pasadas_label()

toggle_z_ui()

# ══ COLUMNA B: Canvas Preview ════════════════════════════════
frm_colB = tk.Frame(frm_body, bg=BG3)
frm_colB.pack(side="left", fill="both", expand=True, padx=(0,1))

frm_canvas_header = tk.Frame(frm_colB, bg="#0d0707", pady=4)
frm_canvas_header.pack(fill="x")
tk.Frame(frm_canvas_header, bg=GREEN, height=2).pack(fill="x")
frm_ch_inner = tk.Frame(frm_canvas_header, bg="#070a0d")
frm_ch_inner.pack(fill="x", padx=10, pady=4)
tk.Label(frm_ch_inner, text="Simulación", bg="#070a0d",
         fg=GREEN, font=FONT_TITLE).pack(side="left")
tk.Label(frm_ch_inner, text="  — vista de trayectoria", bg="#070a0d",
         fg=FG2, font=FONT_SMALL).pack(side="left")

make_btn(frm_ch_inner, "✕ Limpiar",
         lambda: [canvas_preview.delete("all"), dibujar_canvas()],
         color="#1a0505", fg=RED, width=10).pack(side="right")

canvas_frame = tk.Frame(frm_colB, bg=BG3, pady=4)
canvas_frame.pack(fill="both", expand=True, padx=8)

canvas_preview = tk.Canvas(canvas_frame, width=CANVAS_W, height=CANVAS_H,
                             bg="#090c10", highlightthickness=1,
                             highlightbackground="#1a2535",
                             relief="flat")
canvas_preview.pack(expand=True)



frm_tray_bar = tk.Frame(frm_colB, bg="#070a0d", pady=4)
frm_tray_bar.pack(fill="x")
tk.Frame(frm_tray_bar, bg="#1a2535", height=1).pack(fill="x")
frm_tb_inner = tk.Frame(frm_tray_bar, bg="#070a0d")
frm_tb_inner.pack(fill="x", padx=8, pady=4)

lbl_tray_info = tk.Label(frm_tb_inner, text="Esperando operación...",
                          bg="#070a0d", fg=FG2, font=FONT_MONO)
lbl_tray_info.pack(side="left")

prog_bar = ttk.Progressbar(frm_tb_inner, length=200, mode="determinate",
                            style="Custom.Horizontal.TProgressbar")
prog_bar.pack(side="right", padx=8)

style_pb = ttk.Style()
style_pb.configure("Custom.Horizontal.TProgressbar",
                   troughcolor="#0d1520", background=ACCENT,
                   bordercolor="#1a2535", lightcolor=ACCENT, darkcolor=ACCENT)

# ══ COLUMNA C: Tabs operaciones + Terminal ════════════════════
frm_colC = tk.Frame(frm_body, bg=BG2, width=310)
frm_colC.pack(side="left", fill="y", padx=(0,0))
frm_colC.pack_propagate(False)

style_nb = ttk.Style()
style_nb.configure("Dark.TNotebook",
                   background=BG2, borderwidth=0,
                   tabmargins=[0,0,0,0])
style_nb.configure("Dark.TNotebook.Tab",
                   background=BG3, foreground=FG2,
                   font=FONT_MONO, padding=(10,5),
                   borderwidth=0)
style_nb.map("Dark.TNotebook.Tab",
             background=[("selected", ACCENT)],
             foreground=[("selected","#000")])

nb = ttk.Notebook(frm_colC, style="Dark.TNotebook")
nb.pack(fill="x")

# ── Tab Jog ──────────────────────────────────────────────────
tab_j = tk.Frame(nb, bg=BG2); nb.add(tab_j, text="  Jog  ")

ent_jog_step = field_row(tab_j, "Paso:", "1")
ent_jog_feed = field_row(tab_j, "Feed jog:", "300", "mm/m")

frm_steps = tk.Frame(tab_j, bg=BG2); frm_steps.pack(fill="x", padx=8, pady=4)
tk.Label(frm_steps, text="Paso rápido:", bg=BG2, fg=FG2, font=FONT_SMALL).pack(side="left")
for s in ["0.1","1","5","10"]:
    make_btn(frm_steps, s,
             lambda v=s: [ent_jog_step.delete(0,"end"), ent_jog_step.insert(0,v)],
             width=4).pack(side="left", padx=1)

frm_jog = tk.Frame(tab_j, bg=BG2); frm_jog.pack(pady=8)
jog_cfg = [
    (0,1, "Y+", "Y",  1, RED),
    (1,0, "X-", "X", -1, GREEN),
    (1,2, "X+", "X",  1, GREEN),
    (2,1, "Y-", "Y", -1, RED),
]
for r,c,txt,ax,dr,col in jog_cfg:
    make_btn(frm_jog, txt, lambda a=ax,d=dr: jog_move(a,d),
             color=col if col==RED else "#0a200a",
             fg="white", width=5).grid(row=r, column=c, padx=3, pady=3)

tk.Label(frm_jog, text="◎", bg=BG2, fg=FG2,
         font=("Courier New",16)).grid(row=1, column=1)

frm_zj = tk.Frame(tab_j, bg=BG2); frm_zj.pack(pady=4)
make_btn(frm_zj, "Z+", lambda: jog_move("Z", 1), color="#001a2a", fg=ACCENT, width=6).pack(side="left", padx=4)
make_btn(frm_zj, "Z-", lambda: jog_move("Z",-1), color="#001a2a", fg=ACCENT, width=6).pack(side="left", padx=4)

make_btn(tab_j, "⏹ Cancelar jog",
         lambda: enviar_lista(["\x85"]),
         color="#1a0505", fg=RED, width=22).pack(pady=6)

# ── Tab Recta ────────────────────────────────────────────────
tab_r = tk.Frame(nb, bg=BG2); nb.add(tab_r, text=" Recta ")

tk.Label(tab_r, text="ORIGEN", bg=BG2, fg=ACCENT, font=FONT_SMALL).pack(anchor="w", padx=10, pady=(8,0))
ent_r_x1 = field_row(tab_r, "X₁:", "0")
ent_r_y1 = field_row(tab_r, "Y₁:", "0")
tk.Label(tab_r, text="DESTINO", bg=BG2, fg=AMBER, font=FONT_SMALL).pack(anchor="w", padx=10, pady=(6,0))
ent_r_x2 = field_row(tab_r, "X₂:", "300")
ent_r_y2 = field_row(tab_r, "Y₂:", "0")
ent_r_feed = field_row(tab_r, "Feed XY:", "500", "mm/m")

frm_rb = tk.Frame(tab_r, bg=BG2); frm_rb.pack(fill="x", padx=8, pady=6)
make_btn(frm_rb, "= Ancho tabla", recta_ancho_tabla, width=13).pack(side="left", padx=2)
make_btn(frm_rb, "= Diagonal",    recta_diagonal,    width=11).pack(side="left", padx=2)

# BOTÓN PREVISUALIZAR (nuevo, explícito)
make_btn(tab_r, "👁  PREVISUALIZAR", preview_recta,
         color="#001a0a", fg=GREEN, width=26).pack(fill="x", padx=8, pady=(0,2))

make_btn(tab_r, "▶  EJECUTAR RECTA", trayectoria_recta,
         color="#001a33", fg=ACCENT, width=26).pack(fill="x", padx=8, pady=4)

# ── Tab Semiarco ─────────────────────────────────────────────
tab_a = tk.Frame(nb, bg=BG2); nb.add(tab_a, text=" Semiarco ")

tk.Label(tab_a, text="GEOMETRÍA", bg=BG2, fg=ACCENT, font=FONT_SMALL).pack(anchor="w", padx=10, pady=(8,0))
ent_a_cx  = field_row(tab_a, "Centro X:", "150")
ent_a_cy  = field_row(tab_a, "Centro Y:", "0")
ent_a_r   = field_row(tab_a, "Radio:", "150")
ent_a_feed= field_row(tab_a, "Feed XY:", "300", "mm/m")

frm_adir = tk.Frame(tab_a, bg=BG2); frm_adir.pack(fill="x", padx=10, pady=3)
tk.Label(frm_adir, text="Dirección:", bg=BG2, fg=FG2,
         font=FONT_MONO, width=12, anchor="w").pack(side="left")
var_dir = tk.StringVar(value="G2")
for txt, val in [("G2 Horario","G2"),("G3 Anti-h.","G3")]:
    tk.Radiobutton(frm_adir, text=txt, variable=var_dir, value=val,
                   bg=BG2, fg=FG2, selectcolor="#0d1520",
                   activebackground=BG2, font=FONT_SMALL).pack(side="left", padx=4)

frm_ab = tk.Frame(tab_a, bg=BG2); frm_ab.pack(fill="x", padx=8, pady=4)
make_btn(frm_ab, "Radio=Ancho/2", arco_desde_tabla, width=15).pack(side="left", padx=2)

# BOTÓN PREVISUALIZAR (nuevo, explícito)
make_btn(tab_a, "👁  PREVISUALIZAR", preview_arco,
         color="#001a0a", fg=GREEN, width=26).pack(fill="x", padx=8, pady=(0,2))

make_btn(tab_a, "▶  EJECUTAR ARCO", trayectoria_arco,
         color="#001a33", fg=ACCENT, width=26).pack(fill="x", padx=8, pady=4)

# ── Tab Perímetro ─────────────────────────────────────────────
tab_p = tk.Frame(nb, bg=BG2); nb.add(tab_p, text=" Perímetro ")

tk.Label(tab_p, text="Recorre el borde de la tabla completo",
         bg=BG2, fg=FG2, font=FONT_SMALL).pack(anchor="w", padx=10, pady=(10,4))
ent_p_off  = field_row(tab_p, "Offset (margen):", "0")
ent_p_feed = field_row(tab_p, "Feed XY:", "400", "mm/m")

# BOTÓN PREVISUALIZAR (mantenido + ahora también explícito sin auto-bind)
make_btn(tab_p, "👁  PREVISUALIZAR", preview_perimetro,
         color="#001a0a", fg=GREEN, width=26).pack(fill="x", padx=8, pady=(6,2))
make_btn(tab_p, "▶  EJECUTAR PERÍMETRO", trayectoria_perimetro,
         color="#001a33", fg=ACCENT, width=26).pack(fill="x", padx=8, pady=4)

# ── Terminal ──────────────────────────────────────────────────
frm_term_hdr = tk.Frame(frm_colC, bg="#070a0d", pady=0)
frm_term_hdr.pack(fill="x", pady=(4,0))
tk.Frame(frm_term_hdr, bg=PURPLE, height=2).pack(fill="x")
tk.Label(frm_term_hdr, text="  TERMINAL GRBL", bg="#070a0d",
         fg=PURPLE, font=FONT_SMALL, pady=3).pack(anchor="w")

txt_log = tk.Text(frm_colC, bg="#060a0e", fg="#00cc66",
                  font=FONT_SMALL, state="disabled",
                  relief="flat", bd=0, wrap="word",
                  insertbackground=ACCENT,
                  selectbackground="#1a2535")
sb_log = tk.Scrollbar(frm_colC, command=txt_log.yview,
                       bg="#0d1520", troughcolor="#0d1520",
                       activebackground=ACCENT)
txt_log.configure(yscrollcommand=sb_log.set)
sb_log.pack(side="right", fill="y")
txt_log.pack(fill="both", expand=True)

frm_cmd = tk.Frame(frm_colC, bg="#070a0d", pady=4)
frm_cmd.pack(fill="x")
tk.Frame(frm_cmd, bg="#1a2535", height=1).pack(fill="x")
frm_cmd_inner = tk.Frame(frm_cmd, bg="#070a0d")
frm_cmd_inner.pack(fill="x", padx=6, pady=4)

ent_cmd = tk.Entry(frm_cmd_inner, font=FONT_MONO,
                   bg="#0a0e14", fg=GREEN,
                   insertbackground=GREEN,
                   relief="flat", bd=0,
                   highlightthickness=1,
                   highlightbackground="#1a2535",
                   highlightcolor=ACCENT)
ent_cmd.pack(side="left", fill="x", expand=True, padx=(0,4))

def enviar_manual():
    c = ent_cmd.get().strip()
    if c:
        enviar_lista([c])
        ent_cmd.delete(0,"end")

ent_cmd.bind("<Return>", lambda e: enviar_manual())
make_btn(frm_cmd_inner, "ENVIAR", enviar_manual,
         color=ACCENT, fg="#000", width=8).pack(side="right")

# ── Pie de página ─────────────────────────────────────────────
frm_footer = tk.Frame(ventana, bg="#070a0d", pady=3)
frm_footer.pack(fill="x", side="bottom")
tk.Frame(frm_footer, bg="#1a2535", height=1).pack(fill="x")
tk.Label(frm_footer, text="  CNC Control v3  ▸  GRBL  ▸  Tkinter",
         bg="#070a0d", fg="#1a3050", font=FONT_SMALL).pack(side="left")
tk.Label(frm_footer, text="⚠ Verifique límites antes de ejecutar  ",
         bg="#070a0d", fg="#3a1a00", font=FONT_SMALL).pack(side="right")

# ============================================================
# INICIO
# ============================================================
log("CNC Control v3  ▸  con pasadas Z + previsualización mejorada")
log(f"Puertos disponibles: {listar_puertos()}")
num_pasadas_label()
dibujar_canvas()

threading.Thread(target=hilo_posicion, daemon=True).start()
ventana.mainloop()