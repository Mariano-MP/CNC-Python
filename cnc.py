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
arduino    = None
posicion   = {"x": 0.0, "y": 0.0, "z": 0.0}
cero_offset= {"x": 0.0, "y": 0.0, "z": 0.0}
conectado  = False
abort_flag = threading.Event()   # se activa con PARAR
serial_lock = threading.Lock()   # ← AGREGAR ESTA LÍNEA

# ============================================================
# UTILIDAD: GENERAR PASADAS EN Z
# ============================================================
def calcular_pasadas(z_inicio, z_final, paso_z):
    """
    Devuelve lista de profundidades Z absolutas para cada pasada.
    z_inicio : posición Z actual (normalmente 0 = superficie)
    z_final  : profundidad máxima (negativo, ej. -5 para cortar 5 mm)
    paso_z   : incremento por pasada (positivo, ej. 0.5)
    """
    pasadas = []
    z = z_inicio - paso_z
    while z > z_final:
        pasadas.append(round(z, 4))
        z -= paso_z
    pasadas.append(round(z_final, 4))   # última pasada exacta
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
        btn_conectar.config(text="Desconectar", bg="#ffcccc")
        lbl_estado.config(text=f"Conectado a {puerto} @ {baud}", fg="green")
        log(f"✓ Conectado a {puerto} @ {baud} baudios")
    except Exception as e:
        messagebox.showerror("Error de conexión", str(e))

def desconectar():
    global arduino, conectado
    abort_flag.set()
    if arduino and arduino.is_open:
        arduino.close()
    conectado = False
    btn_conectar.config(text="Conectar", bg="#ccffcc")
    lbl_estado.config(text="Desconectado", fg="red")
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
        log(f">> {comando}")
        while not abort_flag.is_set():
            try:
                respuesta = arduino.readline().decode(errors="replace").strip()
            except Exception:
                return False
            if respuesta:
                log(f"   {respuesta}")
            if "ok" in respuesta.lower():
                return True
            if "error" in respuesta.lower() or "alarm" in respuesta.lower():
                log(f"⚠ GRBL: {respuesta}")
                return False
    return False

def jog_move(axis, direction):
    """Envía un comando de jog usando el protocolo $J de GRBL."""
    try:
        step = float(ent_jog_step.get())
        feed = int(ent_jog_feed.get())
    except ValueError:
        log("⚠ Paso o feed de jog inválido"); return

    dist = step * direction
    cmd = f"$J=G21 G91 {axis}{dist:.4f} F{feed}"
    enviar_lista([cmd])

def enviar_lista(cmds, on_done=None, on_progreso=None):
    """Ejecuta lista de comandos en hilo aparte. on_progreso(i, total)."""
    abort_flag.clear()
    def _run():
        total = len(cmds)
        for i, cmd in enumerate(cmds):
            if abort_flag.is_set():
                log("⚠ Operación abortada por el usuario")
                ventana.after(0, lambda: lbl_tray_info.config(text="⚠ Abortado"))
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
    lbl_pos_x.config(text=f"X: {posicion['x'] - cero_offset['x']:+.3f} mm")
    lbl_pos_y.config(text=f"Y: {posicion['y'] - cero_offset['y']:+.3f} mm")
    lbl_pos_z.config(text=f"Z: {posicion['z'] - cero_offset['z']:+.3f} mm")

def parsear_estado_grbl(linea):
    import re
    # $10=2 reporta WPos, también intentamos MPos por si acaso
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
    log(f"Cero establecido → {label}=0")

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
    log("⚠ PARADA DE EMERGENCIA")
    lbl_tray_info.config(text="⚠ PARADA DE EMERGENCIA")

# ============================================================
# LEER CAMPOS DE UI
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

def get_z_params():
    """Lee los parámetros de pasadas Z. Retorna dict o None si hay error."""
    try:
        modo         = var_z_modo.get()           # "superficie" | "profundidad" | "completo"
        paso         = float(ent_z_paso.get())
        z_seguro     = float(ent_z_seguro.get())
        feed_z       = int(ent_z_feed.get())

        if modo == "profundidad":
            prof = float(ent_z_prof.get())
            z_final = -abs(prof)
        elif modo == "completo":
            d = get_tabla()
            if not d: return None
            z_final = -abs(d["z"])
        else:   # "superficie" — solo una pasada en Z=0
            z_final = 0.0

        if paso <= 0:
            messagebox.showerror("Error","El paso Z debe ser > 0"); return None

        return {
            "modo": modo,
            "paso": paso,
            "z_final": z_final,
            "z_seguro": z_seguro,
            "feed_z": feed_z,
        }
    except ValueError:
        messagebox.showerror("Error","Parámetros Z inválidos"); return None

def num_pasadas_label():
    """Actualiza el contador de pasadas en la UI."""
    zp = get_z_params()
    if not zp or zp["z_final"] == 0.0:
        lbl_pasadas.config(text="Pasadas: 1 (superficie)")
        return
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"])
    n = len(pasadas)
    lbl_pasadas.config(text=f"Pasadas: {n}  (hasta Z={zp['z_final']:.2f} mm)")

# ============================================================
# CONSTRUIR G-CODE CON PASADAS Z
# ============================================================
def build_gcode_recta(x1, y1, x2, y2, feed, zp):
    cmds = ["G21","G90"]
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0.0 else [0.0]
    n = len(pasadas)
    cmds.append(z_retract(zp["z_seguro"]))
    cmds.append(f"G0 X{x1:.3f} Y{y1:.3f}")   # mover a origen en XY (Z seguro)
    for i, z in enumerate(pasadas):
        cmds.append(f"; --- Pasada {i+1}/{n}  Z={z:.3f} mm ---")
        cmds.append(f"G1 Z{z:.3f} F{zp['feed_z']}")   # bajar Z
        cmds.append(f"G1 X{x2:.3f} Y{y2:.3f} F{feed}")  # cortar
        if i < n-1:
            cmds.append(z_retract(zp["z_seguro"]))        # subir entre pasadas
            cmds.append(f"G0 X{x1:.3f} Y{y1:.3f}")       # regresar al inicio
    cmds.append(z_retract(zp["z_seguro"]))
    return cmds

def build_gcode_arco(x1, y1, x2, y2, I, J, dire, feed, zp):
    cmds = ["G21","G90"]
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0.0 else [0.0]
    n = len(pasadas)
    cmds.append(z_retract(zp["z_seguro"]))
    cmds.append(f"G0 X{x1:.3f} Y{y1:.3f}")
    for i, z in enumerate(pasadas):
        cmds.append(f"; --- Pasada {i+1}/{n}  Z={z:.3f} mm ---")
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
        cmds.append(f"; --- Pasada {i+1}/{n}  Z={z:.3f} mm ---")
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
    lbl_tray_info.config(text=f"Ejecutando... {actual}/{total} comandos ({pct}%)")
    prog_bar["value"] = pct

def trayectoria_recta():
    d  = get_tabla(); zp = get_z_params()
    if not d or not zp: return
    try:
        x1   = float(ent_r_x1.get());  y1   = float(ent_r_y1.get())
        x2   = float(ent_r_x2.get());  y2   = float(ent_r_y2.get())
        feed = int(ent_r_feed.get())
    except ValueError:
        messagebox.showerror("Error","Valores inválidos en Recta"); return

    if not (0 <= x2 <= d["x"] and 0 <= y2 <= d["y"]):
        if not messagebox.askyesno("Advertencia",
            f"Destino ({x2},{y2}) fuera de la tabla ({d['x']}×{d['y']} mm).\n¿Continuar?"): return

    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0 else [0]
    dist = math.hypot(x2-x1, y2-y1)
    t_total = (dist * len(pasadas) / feed) * 60
    lbl_tray_info.config(text=f"Recta | {len(pasadas)} pasada(s) | {dist*len(pasadas):.1f} mm total | ~{t_total:.0f}s")
    cmds = build_gcode_recta(x1, y1, x2, y2, feed, zp)
    prog_bar["value"] = 0
    log(f"▶ Recta: {len(cmds)} comandos, {len(pasadas)} pasada(s) Z")
    enviar_lista(cmds, on_done=lambda: [
        lbl_tray_info.config(text="✓ Recta completada"),
        prog_bar.__setitem__("value", 100)
    ], on_progreso=on_progreso)

def trayectoria_arco():
    d  = get_tabla(); zp = get_z_params()
    if not d or not zp: return
    try:
        cx   = float(ent_a_cx.get()); cy   = float(ent_a_cy.get())
        r    = float(ent_a_r.get());  feed = int(ent_a_feed.get())
        dire = var_dir.get()
    except ValueError:
        messagebox.showerror("Error","Valores inválidos en Arco"); return

    x1 = cx - r; y1 = cy; x2 = cx + r; y2 = cy; I = -r; J = 0
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0 else [0]
    dist = math.pi * r
    t_total = (dist * len(pasadas) / feed) * 60
    lbl_tray_info.config(text=f"Semiarco R={r:.1f}mm | {len(pasadas)} pasada(s) | ~{t_total:.0f}s")
    cmds = build_gcode_arco(x1, y1, x2, y2, I, J, dire, feed, zp)
    prog_bar["value"] = 0
    log(f"▶ Semiarco: {len(cmds)} comandos, {len(pasadas)} pasada(s) Z")
    enviar_lista(cmds, on_done=lambda: [
        lbl_tray_info.config(text="✓ Semiarco completado"),
        prog_bar.__setitem__("value", 100)
    ], on_progreso=on_progreso)

def trayectoria_perimetro():
    d  = get_tabla(); zp = get_z_params()
    if not d or not zp: return
    try:
        offset = float(ent_p_off.get()); feed = int(ent_p_feed.get())
    except ValueError:
        messagebox.showerror("Error","Valores inválidos en Perímetro"); return

    x0,y0 = offset,offset; xf,yf = d["x"]-offset, d["y"]-offset
    pasadas = calcular_pasadas(0, zp["z_final"], zp["paso"]) if zp["z_final"] != 0 else [0]
    dist = 2*((xf-x0)+(yf-y0))
    t_total = (dist * len(pasadas) / feed) * 60
    lbl_tray_info.config(text=f"Perímetro | {len(pasadas)} pasada(s) | ~{t_total:.0f}s")
    cmds = build_gcode_perimetro(x0, y0, xf, yf, feed, zp)
    prog_bar["value"] = 0
    log(f"▶ Perímetro: {len(cmds)} comandos, {len(pasadas)} pasada(s) Z")
    enviar_lista(cmds, on_done=lambda: [
        lbl_tray_info.config(text="✓ Perímetro completado"),
        prog_bar.__setitem__("value", 100)
    ], on_progreso=on_progreso)

def recta_ancho_tabla():
    d = get_tabla()
    if not d: return
    ent_r_x1.delete(0,"end"); ent_r_x1.insert(0,"0")
    ent_r_y1.delete(0,"end"); ent_r_y1.insert(0,"0")
    ent_r_x2.delete(0,"end"); ent_r_x2.insert(0,str(d["x"]))
    ent_r_y2.delete(0,"end"); ent_r_y2.insert(0,"0")
    num_pasadas_label()

def recta_diagonal():
    d = get_tabla()
    if not d: return
    ent_r_x1.delete(0,"end"); ent_r_x1.insert(0,"0")
    ent_r_y1.delete(0,"end"); ent_r_y1.insert(0,"0")
    ent_r_x2.delete(0,"end"); ent_r_x2.insert(0,str(d["x"]))
    ent_r_y2.delete(0,"end"); ent_r_y2.insert(0,str(d["y"]))
    num_pasadas_label()

def arco_desde_tabla():
    d = get_tabla()
    if not d: return
    r = d["x"] / 2
    ent_a_r.delete(0,"end");  ent_a_r.insert(0,f"{r:.1f}")
    ent_a_cx.delete(0,"end"); ent_a_cx.insert(0,f"{r:.1f}")
    ent_a_cy.delete(0,"end"); ent_a_cy.insert(0,"0")
    num_pasadas_label()

# ============================================================
# INTERFAZ GRÁFICA
# ============================================================
ventana = tk.Tk()
ventana.title("Control CNC — GRBL  (con pasadas Z)")
ventana.geometry("900x740")
ventana.configure(bg="#1a1a1a")

FONT_MONO  = ("Consolas", 10)
FONT_BIG   = ("Consolas", 13, "bold")
FONT_SMALL = ("Consolas", 9)
BG    = "#1a1a1a"
BG2   = "#252525"
BG3   = "#2e2e2e"
FG    = "#d4d4d4"
ACCENT= "#3B8BD4"
GREEN = "#1D9E75"
RED   = "#D85A30"
AMBER = "#EF9F27"
PURPLE= "#9B6DFF"

def lbl(parent, text, **kw):
    return tk.Label(parent, text=text, bg=parent.cget("bg") if hasattr(parent,"cget") else BG2,
                    fg=FG, font=FONT_MONO, **kw)

def entry(parent, default="", width=8):
    e = tk.Entry(parent, font=FONT_MONO, width=width, bg="#111", fg=FG,
                 insertbackground=FG, relief="flat", bd=4)
    e.insert(0, default); return e

def btn(parent, text, cmd, color=BG3, fg=FG, width=16):
    return tk.Button(parent, text=text, command=cmd, font=FONT_MONO,
                     bg=color, fg=fg, activebackground="#444",
                     relief="flat", bd=0, padx=8, pady=4, width=width, cursor="hand2")

def field_row(parent, label_txt, default, unit="mm", width=9):
    f = tk.Frame(parent, bg=parent.cget("bg")); f.pack(fill="x", padx=8, pady=2)
    tk.Label(f, text=label_txt, bg=f.cget("bg"), fg=FG, font=FONT_MONO, width=16, anchor="w").pack(side="left")
    e = entry(f, default, width=width); e.pack(side="left")
    tk.Label(f, text=unit, bg=f.cget("bg"), fg="#555", font=FONT_SMALL).pack(side="left", padx=4)
    return e

# ─── TOP BAR ────────────────────────────────────────────────
frm_top = tk.Frame(ventana, bg=BG, pady=5)
frm_top.pack(fill="x", padx=10)

lbl(frm_top, "Puerto:").pack(side="left")
puertos = listar_puertos() or ["COM3"]
combo_puerto = ttk.Combobox(frm_top, values=puertos, width=9, font=FONT_MONO)
combo_puerto.set(puertos[0]); combo_puerto.pack(side="left", padx=3)

lbl(frm_top, "Baud:").pack(side="left")
combo_baud = ttk.Combobox(frm_top, values=["9600","115200","250000"], width=8, font=FONT_MONO)
combo_baud.set("115200"); combo_baud.pack(side="left", padx=3)

btn_conectar = btn(frm_top,"Conectar",toggle_conexion,color="#1a3a1a",fg="#88ff88",width=12)
btn_conectar.pack(side="left", padx=6)

lbl_estado = tk.Label(frm_top, text="Desconectado", bg=BG, fg=RED, font=FONT_MONO)
lbl_estado.pack(side="left")

btn(frm_top,"⚠ PARAR",parar,color=RED,fg="white",width=10).pack(side="right", padx=4)

# ─── BARRA DE PROGRESO ──────────────────────────────────────
prog_bar = ttk.Progressbar(ventana, mode="determinate", maximum=100)
prog_bar.pack(fill="x", padx=10, pady=(0,2))

lbl_tray_info = tk.Label(ventana, text="Ninguna trayectoria activa",
                          bg=BG, fg=AMBER, font=FONT_SMALL, anchor="w")
lbl_tray_info.pack(fill="x", padx=12)

# ─── CUERPO PRINCIPAL ───────────────────────────────────────
frm_main = tk.Frame(ventana, bg=BG)
frm_main.pack(fill="both", expand=True, padx=10, pady=4)

# ══ COLUMNA IZQUIERDA ═══════════════════════════════════════
frm_left = tk.Frame(frm_main, bg=BG)
frm_left.pack(side="left", fill="y", padx=(0,8))

# — Posición actual —
frm_pos = tk.LabelFrame(frm_left, text=" Posición actual ", bg=BG2, fg=ACCENT,
                         font=FONT_MONO, bd=1, relief="groove")
frm_pos.pack(fill="x", pady=3)

lbl_pos_x = tk.Label(frm_pos,text="X: +0.000 mm",bg=BG2,fg=GREEN,font=FONT_BIG)
lbl_pos_x.pack(anchor="w",padx=8,pady=1)
lbl_pos_y = tk.Label(frm_pos,text="Y: +0.000 mm",bg=BG2,fg=GREEN,font=FONT_BIG)
lbl_pos_y.pack(anchor="w",padx=8,pady=1)
lbl_pos_z = tk.Label(frm_pos,text="Z: +0.000 mm",bg=BG2,fg=GREEN,font=FONT_BIG)
lbl_pos_z.pack(anchor="w",padx=8,pady=1)

frm_z0 = tk.Frame(frm_pos, bg=BG2); frm_z0.pack(fill="x", padx=6, pady=4)
for txt,ax in [("X=0","x"),("Y=0","y"),("Z=0","z")]:
    btn(frm_z0,txt,lambda a=ax:set_cero(a),color="#002244",fg=FG,width=5).pack(side="left",padx=2)
btn(frm_z0,"XYZ=0",lambda:set_cero(None),color="#3a2a00",fg=AMBER,width=7).pack(side="left",padx=2)

# — Tabla —
frm_tabla = tk.LabelFrame(frm_left, text=" Dimensiones de tabla ", bg=BG2, fg=ACCENT,
                           font=FONT_MONO, bd=1, relief="groove")
frm_tabla.pack(fill="x", pady=3)

ent_tabla_x = field_row(frm_tabla,"Ancho X:","300")
ent_tabla_y = field_row(frm_tabla,"Alto  Y:","200")
ent_tabla_z = field_row(frm_tabla,"Espesor Z:","5")

# — HOME —
btn(frm_left,"⌂  HOME",hacer_home,color="#1a2244",fg="white",width=26).pack(fill="x",pady=4)

# ══ PANEL Z (pasadas) ═══════════════════════════════════════
frm_z = tk.LabelFrame(frm_left, text=" Pasadas en Z ", bg=BG2, fg=PURPLE,
                       font=FONT_MONO, bd=1, relief="groove")
frm_z.pack(fill="x", pady=3)

var_z_modo = tk.StringVar(value="superficie")

frm_zmodo = tk.Frame(frm_z, bg=BG2); frm_zmodo.pack(fill="x", padx=6, pady=4)

# entrada profundidad manual (se crea ANTES de toggle_z_ui y los radio buttons)
frm_zprof = tk.Frame(frm_z, bg=BG2); frm_zprof.pack(fill="x", padx=6)
tk.Label(frm_zprof,text="Profundidad:",bg=BG2,fg=FG,font=FONT_MONO,width=14,anchor="w").pack(side="left")
ent_z_prof = entry(frm_zprof,"5",width=7); ent_z_prof.pack(side="left")
tk.Label(frm_zprof,text="mm",bg=BG2,fg="#555",font=FONT_SMALL).pack(side="left",padx=3)

tk.Frame(frm_z,bg=BG2,height=4).pack()

ent_z_paso   = field_row(frm_z,"Paso por pasada:","0.5")
ent_z_feed   = field_row(frm_z,"Veloc. bajada Z:","100","mm/m")
ent_z_seguro = field_row(frm_z,"Z seguro (retract):","5")

lbl_pasadas = tk.Label(frm_z, text="Pasadas: 1 (superficie)", bg=BG2,
                        fg=PURPLE, font=FONT_SMALL)
lbl_pasadas.pack(anchor="w", padx=8, pady=2)

btn(frm_z,"↺ Calcular pasadas",num_pasadas_label,color="#2a1a44",fg=PURPLE,width=24).pack(
    padx=6, pady=4)

# toggle_z_ui se define ANTES de los radio buttons que la usan como command
def toggle_z_ui():
    estado = "normal" if var_z_modo.get() == "profundidad" else "disabled"
    ent_z_prof.config(state=estado)
    num_pasadas_label()

def radio_z(txt, val):
    tk.Radiobutton(frm_zmodo, text=txt, variable=var_z_modo, value=val,
                   bg=BG2, fg=FG, selectcolor="#333", activebackground=BG2,
                   font=FONT_SMALL, command=toggle_z_ui).pack(anchor="w")

radio_z("Solo superficie (Z=0, sin bajar)", "superficie")
radio_z("Profundidad manual:", "profundidad")
radio_z("Corte completo (usa espesor tabla)", "completo")

toggle_z_ui()

# ══ COLUMNA DERECHA: Trayectorias + Terminal ═════════════════
frm_right = tk.Frame(frm_main, bg=BG)
frm_right.pack(side="left", fill="both", expand=True)

nb = ttk.Notebook(frm_right)
nb.pack(fill="both", expand=False)

# ── Tab Jog ──────────────────────────────────────────────────
tab_j = tk.Frame(nb, bg=BG2); nb.add(tab_j, text="  Jog  ")

# — Paso y feed —
frm_jog_params = tk.Frame(tab_j, bg=BG2); frm_jog_params.pack(fill="x", padx=10, pady=(10,4))

ent_jog_step = field_row(tab_j, "Paso:", "1")
ent_jog_feed = field_row(tab_j, "Feed jog:", "500", "mm/m")

# — Presets de paso —
frm_steps = tk.Frame(tab_j, bg=BG2); frm_steps.pack(fill="x", padx=10, pady=4)
lbl(frm_steps, "Paso rápido:").pack(side="left")
for s in ["0.01", "0.1", "1", "5", "10"]:
    btn(frm_steps, s,
        lambda v=s: [ent_jog_step.delete(0,"end"), ent_jog_step.insert(0,v)],
        width=5).pack(side="left", padx=2)

# — Cruz XY —
frm_jog = tk.Frame(tab_j, bg=BG2); frm_jog.pack(pady=10)

# Fila 1: vacío | Y+ | vacío
tk.Frame(frm_jog, bg=BG2, width=60, height=60).grid(row=0, column=0, padx=3, pady=3)
btn(frm_jog,"Y+", lambda:jog_move("Y", 1), color=ACCENT, fg="white", width=4).grid(row=0, column=1, padx=3, pady=3)
tk.Frame(frm_jog, bg=BG2, width=60, height=60).grid(row=0, column=2, padx=3, pady=3)

# Fila 2: X- | · | X+
btn(frm_jog,"X-", lambda:jog_move("X",-1), color=ACCENT, fg="white", width=4).grid(row=1, column=0, padx=3, pady=3)
lbl(frm_jog, "XY").grid(row=1, column=1)
btn(frm_jog,"X+", lambda:jog_move("X", 1), color=ACCENT, fg="white", width=4).grid(row=1, column=2, padx=3, pady=3)

# Fila 3: vacío | Y- | vacío
tk.Frame(frm_jog, bg=BG2, width=60, height=60).grid(row=2, column=0, padx=3, pady=3)
btn(frm_jog,"Y-", lambda:jog_move("Y",-1), color=ACCENT, fg="white", width=4).grid(row=2, column=1, padx=3, pady=3)
tk.Frame(frm_jog, bg=BG2, width=60, height=60).grid(row=2, column=2, padx=3, pady=3)

# — Eje Z —
frm_z_jog = tk.Frame(tab_j, bg=BG2); frm_z_jog.pack(pady=4)
btn(frm_z_jog,"Z+", lambda:jog_move("Z", 1), color=PURPLE, fg="white", width=6).pack(side="left", padx=6)
btn(frm_z_jog,"Z-", lambda:jog_move("Z",-1), color=PURPLE, fg="white", width=6).pack(side="left", padx=6)

# — Cancelar jog —
btn(tab_j, "⏹ Cancelar jog",
    lambda: enviar_lista(["\x85"]),   # 0x85 = jog cancel GRBL
    color=RED, fg="white", width=20).pack(pady=6)

style = ttk.Style()
style.theme_use("clam")
style.configure("TNotebook",    background=BG,  borderwidth=0)
style.configure("TNotebook.Tab",background=BG2, foreground=FG, font=FONT_MONO, padding=(12,4))
style.map("TNotebook.Tab", background=[("selected",ACCENT)], foreground=[("selected","white")])

# ── Tab Recta ────────────────────────────────────────────────
tab_r = tk.Frame(nb, bg=BG2); nb.add(tab_r, text="  Recta  ")

tk.Label(tab_r,text="Origen:",bg=BG2,fg=ACCENT,font=FONT_MONO).pack(anchor="w",padx=10,pady=(8,0))
ent_r_x1 = field_row(tab_r,"Origen X:","0")
ent_r_y1 = field_row(tab_r,"Origen Y:","0")
tk.Label(tab_r,text="Destino:",bg=BG2,fg=ACCENT,font=FONT_MONO).pack(anchor="w",padx=10,pady=(6,0))
ent_r_x2 = field_row(tab_r,"Destino X:","300")
ent_r_y2 = field_row(tab_r,"Destino Y:","0")
ent_r_feed = field_row(tab_r,"Feed XY:","500","mm/m")

frm_rb = tk.Frame(tab_r, bg=BG2); frm_rb.pack(fill="x", padx=8, pady=6)
btn(frm_rb,"= Ancho tabla", recta_ancho_tabla,width=14).pack(side="left",padx=2)
btn(frm_rb,"= Diagonal",    recta_diagonal,   width=12).pack(side="left",padx=2)
btn(frm_rb,"▶ Ejecutar",    trayectoria_recta,color=GREEN,fg="white",width=12).pack(side="right",padx=2)

# ── Tab Semiarco ─────────────────────────────────────────────
tab_a = tk.Frame(nb, bg=BG2); nb.add(tab_a, text="  Semiarco  ")

tk.Label(tab_a,text="Geometría:",bg=BG2,fg=ACCENT,font=FONT_MONO).pack(anchor="w",padx=10,pady=(8,0))
ent_a_cx  = field_row(tab_a,"Centro X (I):","150")
ent_a_cy  = field_row(tab_a,"Centro Y (J):","0")
ent_a_r   = field_row(tab_a,"Radio:","150")
ent_a_feed= field_row(tab_a,"Feed XY:","300","mm/m")

frm_adir = tk.Frame(tab_a, bg=BG2); frm_adir.pack(fill="x",padx=10,pady=3)
tk.Label(frm_adir,text="Dirección:",bg=BG2,fg=FG,font=FONT_MONO,width=14,anchor="w").pack(side="left")
var_dir = tk.StringVar(value="G2")
tk.Radiobutton(frm_adir,text="G2 Horario",   variable=var_dir,value="G2",
               bg=BG2,fg=FG,selectcolor="#333",activebackground=BG2,font=FONT_MONO).pack(side="left",padx=4)
tk.Radiobutton(frm_adir,text="G3 Anti-horario",variable=var_dir,value="G3",
               bg=BG2,fg=FG,selectcolor="#333",activebackground=BG2,font=FONT_MONO).pack(side="left")

frm_ab = tk.Frame(tab_a, bg=BG2); frm_ab.pack(fill="x",padx=8,pady=6)
btn(frm_ab,"Radio=Ancho/2",arco_desde_tabla,width=16).pack(side="left",padx=2)
btn(frm_ab,"▶ Ejecutar",trayectoria_arco,color=GREEN,fg="white",width=12).pack(side="right",padx=2)

# ── Tab Perímetro ─────────────────────────────────────────────
tab_p = tk.Frame(nb, bg=BG2); nb.add(tab_p, text="  Perímetro  ")

tk.Label(tab_p,text="Recorre el borde completo de la tabla",
         bg=BG2,fg="#777",font=FONT_SMALL).pack(anchor="w",padx=10,pady=(10,4))
ent_p_off  = field_row(tab_p,"Offset (margen):","0")
ent_p_feed = field_row(tab_p,"Feed XY:","400","mm/m")

btn(tab_p,"▶ Ejecutar perímetro",trayectoria_perimetro,
    color=GREEN,fg="white",width=24).pack(padx=10,pady=10)

# ── Terminal ──────────────────────────────────────────────────
frm_log = tk.LabelFrame(frm_right, text=" Terminal GRBL ", bg=BG, fg=ACCENT,
                          font=FONT_MONO, bd=1, relief="groove")
frm_log.pack(fill="both", expand=True, pady=4)

txt_log = tk.Text(frm_log, height=12, bg="#0d0d0d", fg="#a8ff78", font=FONT_SMALL,
                  state="disabled", relief="flat", bd=4, wrap="word")
sb_log = tk.Scrollbar(frm_log, command=txt_log.yview)
txt_log.configure(yscrollcommand=sb_log.set)
sb_log.pack(side="right", fill="y")
txt_log.pack(fill="both", expand=True)

frm_cmd = tk.Frame(frm_log, bg=BG); frm_cmd.pack(fill="x", padx=4, pady=4)
ent_cmd = tk.Entry(frm_cmd, font=FONT_MONO, bg="#111", fg=FG,
                   insertbackground=FG, relief="flat", bd=4)
ent_cmd.pack(side="left", fill="x", expand=True, padx=(0,4))

def enviar_manual():
    c = ent_cmd.get().strip()
    if c:
        enviar_lista([c])
        ent_cmd.delete(0,"end")

ent_cmd.bind("<Return>", lambda e: enviar_manual())
btn(frm_cmd,"Enviar",enviar_manual,color=ACCENT,fg="white",width=10).pack(side="right")

# ─── INICIO ─────────────────────────────────────────────────
log("CNC Control v2 — con pasadas Z")
log(f"Puertos disponibles: {listar_puertos()}")
num_pasadas_label()

threading.Thread(target=hilo_posicion, daemon=True).start()

ventana.mainloop()