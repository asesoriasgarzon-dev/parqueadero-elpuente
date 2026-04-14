from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import sqlite3, os, math, shutil
from datetime import datetime, timedelta
from functools import wraps
import pytz

app = Flask(__name__)
app.secret_key = "parqueadero_el_puente_2026"

# ─── CONFIGURACIÓN DE RUTA PARA RAILWAY VOLUME ───
# Si la carpeta /data existe (en Railway), usamos esa ruta. 
# Si no (en tu PC), usa la ruta local.
if os.path.exists('/data'):
    DB_FILE = "/data/parqueadero.db"
    BACKUP_DIR = "/data/backups"
else:
    DB_FILE = "parqueadero.db"
    BACKUP_DIR = "backups"

if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

# ─── CONFIGURACIÓN DE ZONA HORARIA (PUNTO 2) ───
def get_colombia_time():
    tz = pytz.timezone('America/Bogota')
    return datetime.now(tz)

# ─── Base de datos ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS parqueadero (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        placa TEXT, tipo TEXT,
        hora_entrada TEXT, hora_salida TEXT,
        valor REAL, ticket_num INTEGER,
        marca TEXT, celular TEXT,
        metodo_pago TEXT DEFAULT 'Efectivo',
        cajero TEXT DEFAULT 'Operador'
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS consecutivo (
        id INTEGER PRIMARY KEY, numero INTEGER
    )""")
    c.execute("INSERT OR IGNORE INTO consecutivo (id, numero) VALUES (1, 0)")
    c.execute("""CREATE TABLE IF NOT EXISTS mensualidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT, placa TEXT UNIQUE,
        tipo TEXT, estado TEXT,
        fecha_inicio TEXT, fecha_fin TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS clientes_frecuentes (
        placa TEXT PRIMARY KEY, marca TEXT, celular TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        rol TEXT DEFAULT 'operador'
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tarifas (
        id INTEGER PRIMARY KEY,
        tipo TEXT UNIQUE,
        valor_hora REAL,
        valor_dia REAL,
        minutos_cortesia INTEGER DEFAULT 5
    )""")
    # Usuarios por defecto
    c.execute("INSERT OR IGNORE INTO usuarios (username, password, rol) VALUES ('admin', 'admin123', 'admin')")
    c.execute("INSERT OR IGNORE INTO usuarios (username, password, rol) VALUES ('operador', 'op123', 'operador')")
    # Tarifas por defecto 2026
    c.execute("INSERT OR IGNORE INTO tarifas (id, tipo, valor_hora, valor_dia, minutos_cortesia) VALUES (1, 'carro', 4000, 13000, 5)")
    c.execute("INSERT OR IGNORE INTO tarifas (id, tipo, valor_hora, valor_dia, minutos_cortesia) VALUES (2, 'moto', 2500, 7000, 5)")
    # Columnas opcionales para DBs antiguas
    for col, tipo in [("metodo_pago", "TEXT"), ("cajero", "TEXT")]:
        try:
            c.execute(f"ALTER TABLE parqueadero ADD COLUMN {col} {tipo}")
        except Exception:
            pass
    # Agrega esto antes de conn.commit() para actualizar precios existentes
    c.execute("UPDATE tarifas SET valor_dia = 13000 WHERE tipo = 'carro'")
    c.execute("UPDATE tarifas SET valor_dia = 7000 WHERE tipo = 'moto'")
    conn.commit()
    conn.close()

init_db()

# ─── Auth ────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "usuario" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("rol") != "admin":
            return redirect(url_for("inicio"))
        return f(*args, **kwargs)
    return decorated

# ─── Helpers ─────────────────────────────────────────────────────
def calcular_valor(tipo, minutos_totales, tarifas):
    t = tarifas[tipo]
    cortesia = t["minutos_cortesia"]
    if minutos_totales <= cortesia:
        return 0
    if tipo == "carro":
        if minutos_totales <= 60:
            total = t["valor_hora"]
        else:
            fracciones = math.ceil((minutos_totales - 60) / 60)
            total = t["valor_hora"] + fracciones * (t["valor_hora"] - 500)
        return min(total, t["valor_dia"])
    else:
        total = math.ceil(minutos_totales / 60) * t["valor_hora"]
        return min(total, t["valor_dia"])

def get_tarifas():
    conn = get_db()
    rows = conn.execute("SELECT tipo, valor_hora, valor_dia, minutos_cortesia FROM tarifas").fetchall()
    conn.close()
    return {r["tipo"]: dict(r) for r in rows}

def get_consecutivo(conn):
    row = conn.execute("SELECT numero FROM consecutivo WHERE id=1").fetchone()
    num = (row["numero"] if row else 0) + 1
    conn.execute("UPDATE consecutivo SET numero=? WHERE id=1", (num,))
    return num

# ─── Rutas ───────────────────────────────────────────────────────

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form["username"].strip()
        p = request.form["password"].strip()
        conn = get_db()
        user = conn.execute("SELECT * FROM usuarios WHERE username=? AND password=?", (u, p)).fetchone()
        conn.close()
        if user:
            session["usuario"] = user["username"]
            session["rol"] = user["rol"]
            return redirect(url_for("inicio"))
        error = "Usuario o contraseña incorrectos"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def inicio():
    conn = get_db()
    activos = conn.execute("SELECT COUNT(*) as c FROM parqueadero WHERE hora_salida IS NULL").fetchone()["c"]
    hoy = datetime.now().strftime("%Y-%m-%d")
    caja_hoy = conn.execute("SELECT COALESCE(SUM(valor),0) as t FROM parqueadero WHERE date(hora_salida)=?", (hoy,)).fetchone()["t"]
    conn.close()
    return render_template("inicio.html", activos=activos, caja_hoy=int(caja_hoy))

# ─── Entrada ─────────────────────────────────────────────────────
@app.route("/entrada/<tipo>", methods=["GET","POST"])
@login_required
def entrada(tipo):
    mensaje = None
    cliente = None
    if request.method == "POST":
        placa = request.form.get("placa","").strip().upper()
        marca = request.form.get("marca","").strip()
        celular = request.form.get("celular","").strip()
        if not placa:
            mensaje = ("error", "La placa es obligatoria.")
        else:
            conn = get_db()
            existe = conn.execute("SELECT id FROM parqueadero WHERE placa=? AND hora_salida IS NULL", (placa,)).fetchone()
            if existe:
                conn.close()
                return redirect(url_for("salida_form", placa=placa))
            ticket_num = get_consecutivo(conn)
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO parqueadero (placa, tipo, hora_entrada, ticket_num, marca, celular, cajero) VALUES (?,?,?,?,?,?,?)",
                         (placa, tipo, ahora, ticket_num, marca, celular, session.get("usuario")))
            conn.execute("INSERT OR REPLACE INTO clientes_frecuentes (placa, marca, celular) VALUES (?,?,?)",
                         (placa, marca, celular))
            conn.commit()
            conn.close()
            return redirect(url_for("ticket_entrada", ticket=ticket_num))
    else:
        placa_pre = request.args.get("placa","")
        if placa_pre:
            conn = get_db()
            cliente = conn.execute("SELECT * FROM clientes_frecuentes WHERE placa=?", (placa_pre,)).fetchone()
            conn.close()
    return render_template("entrada.html", tipo=tipo, mensaje=mensaje, cliente=cliente,
                           placa_pre=request.args.get("placa",""))

@app.route("/buscar_placa")
@login_required
def buscar_placa():
    placa = request.args.get("placa","").strip().upper()
    if not placa:
        return jsonify({"status": "vacio"})
    conn = get_db()
    activo = conn.execute("SELECT id FROM parqueadero WHERE placa=? AND hora_salida IS NULL", (placa,)).fetchone()
    if activo:
        conn.close()
        return jsonify({"status": "activo", "placa": placa})
    cliente = conn.execute("SELECT * FROM clientes_frecuentes WHERE placa=?", (placa,)).fetchone()
    conn.close()
    if cliente:
        return jsonify({"status": "conocido", "marca": cliente["marca"], "celular": cliente["celular"]})
    return jsonify({"status": "nuevo"})

# ─── Salida ──────────────────────────────────────────────────────
@app.route("/salida", methods=["GET","POST"])
@login_required
def salida_form():
    placa = request.args.get("placa","").strip().upper()
    reg = None
    valor_sugerido = 0
    tiempo_str = ""
    if placa:
        conn = get_db()
        reg = conn.execute("SELECT * FROM parqueadero WHERE placa=? AND hora_salida IS NULL ORDER BY id DESC LIMIT 1", (placa,)).fetchone()
        conn.close()
        if reg:
            entrada_dt = datetime.strptime(reg["hora_entrada"], "%Y-%m-%d %H:%M:%S")
            dur = datetime.now() - entrada_dt
            mins = int(dur.total_seconds() // 60)
            h, m = divmod(mins, 60)
            tiempo_str = f"{h}h {m}m"
            tarifas = get_tarifas()
            valor_sugerido = int(calcular_valor(reg["tipo"], mins, tarifas))
    if request.method == "POST":
        placa = request.form.get("placa","").strip().upper()
        valor = request.form.get("valor", "0").replace(",","").strip()
        metodo = request.form.get("metodo_pago", "Efectivo")
        convenio = request.form.get("convenio", "").strip()
        try:
            valor_pago = float(valor)
        except ValueError:
            valor_pago = 0
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        reg2 = conn.execute("SELECT * FROM parqueadero WHERE placa=? AND hora_salida IS NULL ORDER BY id DESC LIMIT 1", (placa,)).fetchone()
        if reg2:
            conn.execute("UPDATE parqueadero SET hora_salida=?, valor=?, metodo_pago=?, cajero=? WHERE id=?",
                         (ahora, valor_pago, f"{metodo}{' - '+convenio if convenio else ''}", session.get("usuario"), reg2["id"]))
            conn.commit()
            ticket = reg2["ticket_num"]
            conn.close()
            return redirect(url_for("ticket_salida", ticket=ticket))
        conn.close()
    return render_template("salida.html", placa=placa, reg=reg,
                           valor_sugerido=valor_sugerido, tiempo_str=tiempo_str)

# ─── Tickets (vista imprimible) ───────────────────────────────────
@app.route("/ticket/entrada/<int:ticket>")
@login_required
def ticket_entrada(ticket):
    conn = get_db()
    reg = conn.execute("SELECT * FROM parqueadero WHERE ticket_num=?", (ticket,)).fetchone()
    conn.close()
    tarifas = get_tarifas()
    return render_template("ticket.html", reg=reg, tipo="entrada", tarifas=tarifas)

@app.route("/ticket/salida/<int:ticket>")
@login_required
def ticket_salida(ticket):
    conn = get_db()
    reg = conn.execute("SELECT * FROM parqueadero WHERE ticket_num=?", (ticket,)).fetchone()
    conn.close()
    if reg and reg["hora_entrada"] and reg["hora_salida"]:
        entrada_dt = datetime.strptime(reg["hora_entrada"], "%Y-%m-%d %H:%M:%S")
        salida_dt = datetime.strptime(reg["hora_salida"], "%Y-%m-%d %H:%M:%S")
        dur = salida_dt - entrada_dt
        mins = int(dur.total_seconds() // 60)
        h, m = divmod(mins, 60)
        tiempo_str = f"{h}h {m}m"
    else:
        tiempo_str = ""
    return render_template("ticket.html", reg=reg, tipo="salida", tiempo_str=tiempo_str)

# ─── Clientes activos ─────────────────────────────────────────────
@app.route("/clientes")
@login_required
def clientes():
    conn = get_db()
    activos = conn.execute("SELECT * FROM parqueadero WHERE hora_salida IS NULL ORDER BY hora_entrada").fetchall()
    conn.close()
    tarifas = get_tarifas()
    ahora = datetime.now()
    lista = []
    for r in activos:
        entrada_dt = datetime.strptime(r["hora_entrada"], "%Y-%m-%d %H:%M:%S")
        mins = int((ahora - entrada_dt).total_seconds() // 60)
        h, m = divmod(mins, 60)
        valor_est = int(calcular_valor(r["tipo"], mins, tarifas))
        lista.append({"reg": r, "tiempo": f"{h}h {m}m", "valor_est": valor_est})
    return render_template("clientes.html", lista=lista)

# ─── Histórico ───────────────────────────────────────────────────
@app.route("/historico")
@login_required
def historico():
    placa = request.args.get("placa","").strip().upper()
    conn = get_db()
    if placa:
        regs = conn.execute("SELECT * FROM parqueadero WHERE placa LIKE ? ORDER BY id DESC LIMIT 100", (f"%{placa}%",)).fetchall()
    else:
        regs = conn.execute("SELECT * FROM parqueadero ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return render_template("historico.html", regs=regs, placa=placa)

# ─── Caja ────────────────────────────────────────────────────────
@app.route("/caja")
@login_required
def caja():
    fecha = request.args.get("fecha", datetime.now().strftime("%Y-%m-%d"))
    conn = get_db()
    regs = conn.execute("SELECT * FROM parqueadero WHERE date(hora_salida)=? ORDER BY hora_salida DESC", (fecha,)).fetchall()
    total = sum(r["valor"] for r in regs if r["valor"])
    por_metodo = {}
    for r in regs:
        m = r["metodo_pago"] or "Efectivo"
        metodo_base = m.split(" - ")[0]
        por_metodo[metodo_base] = por_metodo.get(metodo_base, 0) + (r["valor"] or 0)
    conn.close()
    return render_template("caja.html", regs=regs, total=int(total), fecha=fecha, por_metodo=por_metodo)

# ─── Mensualidades ────────────────────────────────────────────────
@app.route("/mensualidades", methods=["GET","POST"])
@login_required
def mensualidades():
    conn = get_db()
    if request.method == "POST":
        nombre = request.form.get("nombre","").strip()
        placa = request.form.get("placa","").strip().upper()
        tipo = request.form.get("tipo","")
        estado = request.form.get("estado","")
        fi = request.form.get("fecha_inicio","")
        ff = request.form.get("fecha_fin","")
        conn.execute("INSERT OR REPLACE INTO mensualidades (nombre,placa,tipo,estado,fecha_inicio,fecha_fin) VALUES (?,?,?,?,?,?)",
                     (nombre, placa, tipo, estado, fi, ff))
        conn.commit()
    regs = conn.execute("SELECT * FROM mensualidades ORDER BY nombre").fetchall()
    conn.close()
    hoy = datetime.now().date()
    lista = []
    for r in regs:
        alerta = r["estado"]
        if r["fecha_fin"]:
            try:
                ff = datetime.strptime(r["fecha_fin"], "%Y-%m-%d").date()
                if ff < hoy: alerta = "Vencida"
                elif ff <= hoy + timedelta(days=5): alerta = "Por vencer"
            except: pass
        lista.append({"reg": r, "alerta": alerta})
    return render_template("mensualidades.html", lista=lista)

@app.route("/mensualidades/eliminar/<int:id>")
@login_required
def eliminar_mensualidad(id):
    conn = get_db()
    conn.execute("DELETE FROM mensualidades WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("mensualidades"))

# ─── Tarifas (solo admin) ─────────────────────────────────────────
@app.route("/tarifas", methods=["GET","POST"])
@login_required
@admin_required
def tarifas():
    conn = get_db()
    if request.method == "POST":
        for tipo in ["carro", "moto"]:
            vh = float(request.form.get(f"vh_{tipo}", 0))
            vd = float(request.form.get(f"vd_{tipo}", 0))
            mc = int(request.form.get(f"mc_{tipo}", 5))
            conn.execute("UPDATE tarifas SET valor_hora=?, valor_dia=?, minutos_cortesia=? WHERE tipo=?",
                         (vh, vd, mc, tipo))
        conn.commit()
    regs = conn.execute("SELECT * FROM tarifas ORDER BY tipo").fetchall()
    conn.close()
    return render_template("tarifas.html", tarifas=regs)

# ─── Backup ──────────────────────────────────────────────────────
@app.route("/backup")
@login_required
@admin_required
def backup():
    nombre = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    destino = os.path.join(BACKUP_DIR, nombre)
    shutil.copy2(DB_FILE, destino)
    return send_file(destino, as_attachment=True, download_name=nombre)

# ─── API tiempo real ──────────────────────────────────────────────
@app.route("/api/valor_estimado")
@login_required
def api_valor_estimado():
    placa = request.args.get("placa","").upper()
    conn = get_db()
    reg = conn.execute("SELECT tipo, hora_entrada FROM parqueadero WHERE placa=? AND hora_salida IS NULL ORDER BY id DESC LIMIT 1", (placa,)).fetchone()
    conn.close()
    if not reg:
        return jsonify({"error": "No encontrado"})
    mins = int((datetime.now() - datetime.strptime(reg["hora_entrada"], "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
    h, m = divmod(mins, 60)
    tarifas = get_tarifas()
    valor = int(calcular_valor(reg["tipo"], mins, tarifas))
    return jsonify({"tiempo": f"{h}h {m}m", "valor": valor, "mins": mins})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
