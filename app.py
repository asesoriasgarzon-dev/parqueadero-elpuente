from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import sqlite3, os, math, shutil
import pytz
import calendar
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "parqueadero_el_puente_2026"

POLIZA_NUM = "C-250004843"
CERTIFICADO_NUM = "10402089"
VIGENCIA_POLIZA = "Marzo 14 de 2026 a Marzo 14 de 2027"

def get_colombia_time():
    # Creamos la zona horaria de Bogotá
    tz = pytz.timezone('America/Bogota')
    # Devolvemos la hora actual en esa zona
    return datetime.now(tz)

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

# ─── Base de datos ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # 1. Tabla Parqueadero
    c.execute("""CREATE TABLE IF NOT EXISTS parqueadero (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        placa TEXT, 
        tipo TEXT,
        hora_entrada TEXT, 
        hora_salida TEXT,
        valor REAL, 
        ticket_num INTEGER
    )""")
    
    # 2. Tabla Consecutivo
    c.execute("""CREATE TABLE IF NOT EXISTS consecutivo (
        id INTEGER PRIMARY KEY, numero INTEGER
    )""")
    c.execute("INSERT OR IGNORE INTO consecutivo (id, numero) VALUES (1, 0)")
    
    # 3. Tabla Mensualidades
    c.execute("""CREATE TABLE IF NOT EXISTS mensualidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT, 
        placa TEXT UNIQUE,
        tipo TEXT, 
        estado TEXT,
        fecha_inicio TEXT, 
        fecha_fin TEXT
    )""")
    
    # 4. Tabla Clientes Frecuentes
    c.execute("""CREATE TABLE IF NOT EXISTS clientes_frecuentes (
        placa TEXT PRIMARY KEY, marca TEXT, celular TEXT
    )""")
    
    # 5. Tabla Usuarios
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        rol TEXT DEFAULT 'operador'
    )""")
    
    # 6. Tabla Tarifas
    c.execute("""CREATE TABLE IF NOT EXISTS tarifas (
        id INTEGER PRIMARY KEY,
        tipo TEXT UNIQUE,
        valor_hora REAL,
        valor_dia REAL,
        minutos_cortesia INTEGER DEFAULT 5
    )""")

    # Datos iniciales (Usuarios y Tarifas 2026)
    c.execute("INSERT OR IGNORE INTO usuarios (username, password, rol) VALUES ('admin', 'admin123', 'admin')")
    c.execute("INSERT OR IGNORE INTO usuarios (username, password, rol) VALUES ('operador', 'op123', 'operador')")
    c.execute("INSERT OR IGNORE INTO tarifas (id, tipo, valor_hora, valor_dia, minutos_cortesia) VALUES (1, 'carro', 4000, 13000, 5)")
    c.execute("INSERT OR IGNORE INTO tarifas (id, tipo, valor_hora, valor_dia, minutos_cortesia) VALUES (2, 'moto', 2500, 7000, 5)")
    
    # --- BLOQUE DE ACTUALIZACIÓN SEGURO (Evolución de la BD) ---
    
    # Columnas adicionales para Parqueadero (Control de auditoría y anulaciones)
    columnas_parqueadero = [
        ("marca", "TEXT"),
        ("celular", "TEXT"),
        ("metodo_pago", "TEXT DEFAULT 'Efectivo'"),
        ("cajero", "TEXT DEFAULT 'Operador'"),
        ("observaciones", "TEXT"),
        ("anulado", "INTEGER DEFAULT 0"),
        ("motivo_anulacion", "TEXT"),
        ("valor_real", "REAL DEFAULT 0")
    ]
    
    for col in columnas_parqueadero:
        try:
            c.execute(f"ALTER TABLE parqueadero ADD COLUMN {col[0]} {col[1]}")
        except Exception:
            pass # La columna ya existe

    # Columnas adicionales para Mensualidades (Datos del propietario)
    columnas_mensualidades = [
        ("telefono", "TEXT"), 
        ("modelo", "TEXT"), 
        ("color", "TEXT"), 
        ("observaciones", "TEXT")
    ]
    
    for col in columnas_mensualidades:
        try:
            c.execute(f"ALTER TABLE mensualidades ADD COLUMN {col[0]} {col[1]}")
        except Exception:
            pass # La columna ya existe

    conn.commit()
    conn.close()
    print("Base de datos inicializada y actualizada correctamente.")
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
def get_tarifas():
    conn = get_db()
    regs = conn.execute("SELECT tipo, valor_hora, valor_dia, minutos_cortesia FROM tarifas").fetchall()
    conn.close()
    # Convertimos a un diccionario para que sea fácil de usar: {'carro': {...}, 'moto': {...}}
    return {r["tipo"]: dict(r) for r in regs}

def calcular_valor(tipo, minutos_totales, tarifas):
    t = tarifas[tipo]
    cortesia = t["minutos_cortesia"]
    
    # 1. Validación de cortesía inicial
    if minutos_totales <= cortesia:
        return 0
        
    if tipo == "carro":
        # Primera hora o fracción (hasta el minuto 60)
        if minutos_totales <= 60:
            total = t["valor_hora"]  # $4.000
        else:
            # Calculamos las horas adicionales (fracciones de 60 min)
            # math.ceil redondea hacia arriba cualquier minuto excedente
            fracciones = math.ceil((minutos_totales - 60) / 60)
            # Primera hora ($4.000) + Horas extras ($3.500 c/u)
            total = t["valor_hora"] + fracciones * (t["valor_hora"] - 500)
        
        # Eliminamos el min(total, t["valor_dia"]) para que NO tenga tope
        return total 

    else:
        # Lógica para motos: cobro lineal por hora o fracción
        total = math.ceil(minutos_totales / 60) * t["valor_hora"]
        
        # También eliminamos el tope para las motos
        return total

def get_consecutivo(conn):
    # Esta función maneja el número de ticket (1, 2, 3...)
    c = conn.cursor()
    c.execute("UPDATE consecutivo SET numero = numero + 1 WHERE id = 1")
    conn.commit()
    res = conn.execute("SELECT numero FROM consecutivo WHERE id = 1").fetchone()
    return res["numero"]

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
   # CAMBIO AQUÍ: Usamos get_colombia_time() en lugar de datetime.now()
    ahora_co = get_colombia_time() 
    hoy = ahora_co.strftime("%Y-%m-%d")
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
        obs = request.form.get("observaciones","").strip()

        if not placa:
            mensaje = ("error", "La placa es obligatoria.")
        else:
            conn = get_db()
            
            # --- LÍNEA SALVAVIDAS PARA EVITAR ERROR 500 ---
            try:
                conn.execute("ALTER TABLE parqueadero ADD COLUMN observaciones TEXT")
            except:
                pass # Si la columna ya existe, no hace nada
            # ----------------------------------------------

            existe = conn.execute("SELECT id FROM parqueadero WHERE placa=? AND hora_salida IS NULL", (placa,)).fetchone()
            if existe:
                conn.close()
                return redirect(url_for("salida_form", placa=placa))
            
            ticket_num = get_consecutivo(conn)
            ahora = get_colombia_time().strftime("%Y-%m-%d %H:%M:%S")
            
            # INSERT con los 8 campos
            conn.execute("""INSERT INTO parqueadero 
                (placa, tipo, hora_entrada, ticket_num, marca, celular, cajero, observaciones) 
                VALUES (?,?,?,?,?,?,?,?)""",
                (placa, tipo, ahora, ticket_num, marca, celular, session.get("usuario"), obs))
            
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
            # 1. Convertimos la hora de entrada y le asignamos la zona horaria de Bogotá
            entrada_dt = datetime.strptime(reg["hora_entrada"], "%Y-%m-%d %H:%M:%S")
            tz = pytz.timezone('America/Bogota')
            entrada_dt = tz.localize(entrada_dt)
            
            # 2. Obtenemos la hora actual de Colombia
            ahora_co = get_colombia_time()
            
            # 3. Calculamos la duración real (Colombia vs Colombia)
            dur = ahora_co - entrada_dt
            mins = int(max(0, dur.total_seconds() // 60))
            
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
            
        # 4. Usamos la hora de Colombia para la salida final
        ahora = get_colombia_time().strftime("%Y-%m-%d %H:%M:%S")
        
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
    # PASAMOS LOS DATOS DE LA PÓLIZA AQUÍ
    return render_template("ticket.html", 
                           reg=reg, 
                           tipo="entrada", 
                           tarifas=tarifas,
                           poliza=POLIZA_NUM, 
                           certificado=CERTIFICADO_NUM, 
                           vigencia=VIGENCIA_POLIZA)

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
        mins = int(max(0, dur.total_seconds() // 60))
        h, m = divmod(mins, 60)
        tiempo_str = f"{h}h {m}m"
    else:
        tiempo_str = ""
    
    # TAMBIÉN LOS PASAMOS EN LA SALIDA
    return render_template("ticket.html", 
                           reg=reg, 
                           tipo="salida", 
                           tiempo_str=tiempo_str,
                           poliza=POLIZA_NUM, 
                           certificado=CERTIFICADO_NUM, 
                           vigencia=VIGENCIA_POLIZA)

# ─── Clientes activos ─────────────────────────────────────────────
@app.route("/clientes")
@login_required
def clientes():
    conn = get_db()
    activos = conn.execute("SELECT * FROM parqueadero WHERE hora_salida IS NULL ORDER BY hora_entrada").fetchall()
    conn.close()
    
    tarifas = get_tarifas()
    # CAMBIO CRÍTICO: Usamos la hora de Colombia para comparar con los carros que están adentro
    ahora_co = get_colombia_time()
    
    lista = []
    for r in activos:
        entrada_dt = datetime.strptime(r["hora_entrada"], "%Y-%m-%d %H:%M:%S")
        
        # Localizamos la hora de entrada para que sea compatible con la de Colombia
        tz = pytz.timezone('America/Bogota')
        entrada_dt = tz.localize(entrada_dt)
        
        # Restamos Colombia vs Colombia
        mins = int(max(0, (ahora_co - entrada_dt).total_seconds() // 60))
        
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
    ahora_co = get_colombia_time()
    fecha_hoy = ahora_co.strftime("%Y-%m-%d")
    
    # Recibimos ambos parámetros, si no existen, por defecto es hoy
    fecha_inicio = request.args.get("fecha_inicio", fecha_hoy)
    fecha_fin = request.args.get("fecha_fin", fecha_hoy)
    
    conn = get_db()
    # Cambiamos la consulta SQL para que use un rango (BETWEEN)
    query = """
        SELECT * FROM parqueadero 
        WHERE date(hora_salida) BETWEEN ? AND ? 
        ORDER BY hora_salida DESC
    """
    regs = conn.execute(query, (fecha_inicio, fecha_fin)).fetchall()
    
    total = sum(r["valor"] for r in regs if r["valor"])
    por_metodo = {}
    for r in regs:
        m = r["metodo_pago"] or "Efectivo"
        metodo_base = m.split(" - ")[0]
        por_metodo[metodo_base] = por_metodo.get(metodo_base, 0) + (r["valor"] or 0)
    conn.close()
    
    return render_template("caja.html", regs=regs, total=int(total), 
                           fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, 
                           por_metodo=por_metodo)
# ─── Mensualidades ────────────────────────────────────────────────
@app.route("/mensualidades", methods=["GET", "POST"])
@login_required
def mensualidades():
    conn = get_db()
    
    # REPARACIÓN: Agrega las columnas de la dueña si no existen
    for col in ["telefono", "modelo", "color", "observaciones"]:
        try:
            conn.execute(f"ALTER TABLE mensualidades ADD COLUMN {col} TEXT")
            conn.commit()
        except:
            pass

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        placa = request.form.get("placa", "").strip().upper()
        tel = request.form.get("telefono", "").strip()
        mod = request.form.get("modelo", "").strip()
        col_v = request.form.get("color", "").strip()
        tipo = request.form.get("tipo", "")
        fi = request.form.get("fecha_inicio", "")
        ff = request.form.get("fecha_fin", "")
        est = request.form.get("estado", "Activo")
        obs = request.form.get("observaciones", "").strip()

        check = conn.execute("SELECT id FROM mensualidades WHERE placa = ?", (placa,)).fetchone()
        
        if check:
            conn.execute("""UPDATE mensualidades SET 
                nombre=?, telefono=?, modelo=?, color=?, tipo=?, 
                fecha_inicio=?, fecha_fin=?, estado=?, observaciones=? 
                WHERE placa=?""", (nombre, tel, mod, col_v, tipo, fi, ff, est, obs, placa))
        else:
            conn.execute("""INSERT INTO mensualidades 
                (nombre, placa, telefono, modelo, color, tipo, fecha_inicio, fecha_fin, estado, observaciones) 
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (nombre, placa, tel, mod, col_v, tipo, fi, ff, est, obs))
        conn.commit()
        return redirect(url_for('mensualidades'))

    regs = conn.execute("SELECT * FROM mensualidades ORDER BY nombre ASC").fetchall()
    hoy = get_colombia_time().date()
    lista = []
    for r in regs:
        fila = dict(r)
        alerta = "Activo"
        if fila.get('fecha_fin'):
            try:
                f_fin = datetime.strptime(fila['fecha_fin'], "%Y-%m-%d").date()
                if f_fin < hoy: alerta = "Vencida"
                elif f_fin <= hoy + timedelta(days=5): alerta = "Por vencer"
            except: pass
        lista.append({'reg': fila, 'alerta': alerta})
    conn.close()
    return render_template("mensualidades.html", lista=lista)

@app.route("/eliminar_mensualidad/<int:id>")
@login_required
def eliminar_mensualidad(id):
    conn = get_db()
    conn.execute("DELETE FROM mensualidades WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('mensualidades'))    
    
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
    # CAMBIO: Usamos get_colombia_time() para que el nombre del archivo
    # refleje la hora real en la que hiciste la copia.
    ahora_co = get_colombia_time()
    nombre = f"backup_{ahora_co.strftime('%Y%m%d_%H%M%S')}.db"
    
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
    
    # 1. Convertimos la entrada y le ponemos la zona horaria de Colombia
    entrada_dt = datetime.strptime(reg["hora_entrada"], "%Y-%m-%d %H:%M:%S")
    tz = pytz.timezone('America/Bogota')
    entrada_dt = tz.localize(entrada_dt)
    
    # 2. Obtenemos la hora actual de Colombia
    ahora_co = get_colombia_time()
    
   # 3. Calculamos los minutos reales (Colombia vs Colombia)
    mins = int(max(0, (ahora_co - entrada_dt).total_seconds() // 60))
    
    h, m = divmod(mins, 60)
    tarifas = get_tarifas()
    valor = int(calcular_valor(reg["tipo"], mins, tarifas))
    
    return jsonify({"tiempo": f"{h}h {m}m", "valor": valor, "mins": mins})

# ─── Perfil y Seguridad ──────────────────────────────────────────
@app.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    mensaje = None
    if request.method == "POST":
        nueva_pass = request.form.get("password").strip()
        confirmar_pass = request.form.get("confirmar_password").strip()
        
        if not nueva_pass:
            mensaje = ("error", "La contraseña no puede estar vacía")
        elif nueva_pass == confirmar_pass:
            conn = get_db()
            conn.execute("UPDATE usuarios SET password=? WHERE username=?", 
                         (nueva_pass, session["usuario"]))
            conn.commit()
            conn.close()
            mensaje = ("success", "¡Contraseña actualizada con éxito!")
        else:
            mensaje = ("error", "Las contraseñas no coinciden")
            
    return render_template("perfil.html", mensaje=mensaje)

# ─── Gestión de Usuarios (Solo Admin) ────────────────────────────
@app.route("/usuarios")
@login_required
@admin_required
def gestion_usuarios():
    conn = get_db()
    usuarios = conn.execute("SELECT id, username, rol FROM usuarios").fetchall()
    conn.close()
    return render_template("usuarios.html", usuarios=usuarios)

@app.route("/usuarios/reset/<int:id>")
@login_required
@admin_required
def reset_password(id):
    conn = get_db()
    # Reseteo a clave genérica para que el operador la cambie luego en su perfil
    conn.execute("UPDATE usuarios SET password=? WHERE id=?", ("cambiar123", id))
    conn.commit()
    conn.close()
    return redirect(url_for('gestion_usuarios'))

# ─── Estadísticas (Solo Admin) ───────────────────────────────────
@app.route("/estadisticas")
@login_required
@admin_required
def estadisticas():
    # 1. IMPORTANTE: Necesitamos importar calendar para el cálculo
    import calendar
    
    conn = get_db()
    
    # 2. CÁLCULO DEL SEMÁFORO (Días restantes del mes)
    ahora = get_colombia_time()
    ultimo_dia = calendar.monthrange(ahora.year, ahora.month)[1]
    dias_restantes = ultimo_dia - ahora.day
    
    # Ingresos últimos 7 días
    query_semana = """
        SELECT date(hora_salida) as fecha, SUM(valor) as total
        FROM parqueadero
        WHERE hora_salida IS NOT NULL 
        AND date(hora_salida) > date('now', '-7 days')
        GROUP BY date(hora_salida)
        ORDER BY fecha ASC
    """
    datos = conn.execute(query_semana).fetchall()
    
    # Ingresos por tipo
    por_tipo = conn.execute("""
        SELECT tipo, SUM(valor) as total 
        FROM parqueadero 
        WHERE hora_salida IS NOT NULL
        GROUP BY tipo
    """).fetchall()
    conn.close()
    
    labels = [d['fecha'] for d in datos]
    valores = [d['total'] for d in datos]
    
    # 3. PASAR dias_restantes AL TEMPLATE
    return render_template("estadisticas.html", 
                           labels=labels, 
                           valores=valores, 
                           por_tipo=por_tipo, 
                           dias_restantes=dias_restantes)    
    return render_template("estadisticas.html", labels=labels, valores=valores, por_tipo=por_tipo)

# ─── Limpiar Pruebas (Solo Admin) ────────────────────────────────
@app.route("/admin/limpiar_pruebas")
@login_required
@admin_required
def limpiar_pruebas():
    conn = get_db()
    conn.execute("DELETE FROM parqueadero")
    conn.execute("UPDATE consecutivo SET numero=0 WHERE id=1")
    conn.commit()
    conn.close()
    return redirect(url_for('inicio'))

@app.route("/imprimir_caja")
@login_required
def imprimir_caja():
    ahora_co = get_colombia_time()
    fecha_hoy = ahora_co.strftime("%Y-%m-%d")
    
    conn = get_db()
    # 1. Obtenemos todos los registros que salieron hoy
    regs = conn.execute("""
        SELECT metodo_pago, valor, tipo 
        FROM parqueadero 
        WHERE date(hora_salida) = ?
    """, (fecha_hoy,)).fetchall()
    
    # 2. Calculamos totales y desglose
    total_caja = 0
    total_entradas_hoy = conn.execute("SELECT COUNT(*) FROM parqueadero WHERE date(hora_entrada) = ?", (fecha_hoy,)).fetchone()[0]
    total_salidas_hoy = len(regs)
    
    desglose = {}
    for r in regs:
        valor = r["valor"] or 0
        total_caja += valor
        # Limpiamos el nombre del método (quitamos convenios si existen)
        m = (r["metodo_pago"] or "Efectivo").split(" - ")[0]
        desglose[m] = desglose.get(m, 0) + valor
        
    conn.close()
    
    return render_template("ticket_caja.html", 
                           fecha=ahora_co.strftime("%d/%m/%Y"),
                           hora=ahora_co.strftime("%I:%M %p"),
                           desglose=desglose,
                           total_caja=int(total_caja),
                           total_entradas=total_entradas_hoy,
                           total_salidas=total_salidas_hoy)

# ─── Ruta para Anular Pago (Versión Corregida) ──────────────────
@app.route("/anular_pago/<int:id>")
@login_required
@admin_required
def anular_pago(id):
    # Capturamos los datos desde la URL (?motivo=...&real=...)
    motivo = request.args.get("motivo", "No especificado")
    valor_real_raw = request.args.get("real", "0")
    
    # Limpiamos el valor numérico de posibles puntos o comas
    try:
        valor_real = float(str(valor_real_raw).replace(",", "").replace(".", ""))
    except:
        valor_real = 0
    
    conn = get_db()
    # Verificamos si el registro existe
    reg = conn.execute("SELECT id FROM parqueadero WHERE id = ?", (id,)).fetchone()
    
    if reg:
        # 1. Marcamos como anulado
        # 2. Guardamos el motivo y lo que se cobró realmente
        # 3. Seteamos 'valor' a 0 para que NO sume en el total de la caja
        conn.execute("""
            UPDATE parqueadero 
            SET anulado = 1, 
                motivo_anulacion = ?, 
                valor_real = ?, 
                valor = 0 
            WHERE id = ?
        """, (motivo, valor_real, id))
        conn.commit()
    
    conn.close()
    return redirect(url_for('historico'))
    
# Mueve la llamada afuera del if para que Railway la ejecute sí o sí
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
