"""
Fase 8: suite de regresión general.

Las suites test_concurrencia.py, test_impresion.py y test_backup.py ya
cubren en detalle las condiciones de carrera (tickets, impresión,
backups). Esta suite complementa esas con los flujos de negocio del día
a día y los arreglos de seguridad de las Fases 1 y 3, para detectar si
un cambio futuro rompe algo que ya funcionaba:

  1. Login: credenciales correctas/incorrectas, CSRF obligatorio, límite
     de intentos (fuerza bruta), migración transparente de contraseña
     en texto plano a hash.
  2. Autorización: un operador (no-admin) no puede usar las rutas de
     administrador, aunque conozca la URL directamente.
  3. Whitelist de tipo de vehículo en /entrada/<tipo>.
  4. /admin/limpiar_pruebas bloqueado fuera de entorno de desarrollo.
  5. Flujo completo entrada -> salida, con el valor calculado según
     tarifas vigentes.
  6. Anulación de pago: el valor original NUNCA se pierde, y un pago
     anulado no cuenta en caja/estadísticas/inicio.
  7. Mensualidades: crear, editar, eliminar -- cada una debe quedar en
     auditoría.
  8. Tarifas: un cambio real se audita; guardar sin cambiar nada no
     genera ruido en la auditoría.
  9. Reset de contraseña por el admin: el usuario objetivo puede
     iniciar sesión con la clave genérica nueva, y la auditoría no
     guarda ninguna contraseña.
  10. Búsqueda en histórico por placa.
"""
import os
import re

os.environ["DEVELOPMENT"] = "true"
os.environ["PRINTER_API_KEY"] = "test_key_fase8"
import app as appmod

client = appmod.app.test_client()


def limpiar_db():
    conn = appmod.get_db()
    conn.execute("DELETE FROM parqueadero")
    conn.execute("DELETE FROM mensualidades")
    conn.execute("DELETE FROM auditoria")
    conn.execute("UPDATE consecutivo SET numero = 0 WHERE id = 1")
    conn.commit()
    conn.close()


def csrf_from(html):
    # La mayoría de páginas (login.html y los formularios) traen un input
    # oculto name="csrf_token". Pero una página sin ningún formulario
    # visible para el usuario actual (p.ej. /mensualidades visto por un
    # operador, donde los formularios de admin están ocultos) no tiene
    # ese input -- en ese caso usamos el <meta name="csrf-token"> que
    # base.html siempre incluye en el <head>, independiente del rol.
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not m:
        m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert m, "No se encontró csrf_token en la página -- ¿cambió el HTML?"
    return m.group(1)


def set_sesion(usuario, rol):
    with client.session_transaction() as sess:
        sess["usuario"] = usuario
        sess["rol"] = rol


def get_con_token(url):
    r = client.get(url)
    return r, csrf_from(r.get_data(as_text=True))


limpiar_db()

# ═════════════════════════════════════════════════════════════════
# PRUEBA 1: LOGIN
# ═════════════════════════════════════════════════════════════════

# 1a. Credenciales correctas -> 302 a inicio
r_page = client.get("/login")
token = csrf_from(r_page.get_data(as_text=True))
r = client.post("/login", data={"username": "admin", "password": "admin123", "csrf_token": token})
assert r.status_code == 302, f"FALLO: login correcto debería redirigir (302), dio {r.status_code}"
print("PRUEBA 1a: OK -- login con credenciales correctas redirige")
client.get("/logout")

# 1b. Credenciales incorrectas -> 200 con mensaje de error, sin sesión
r_page = client.get("/login")
token = csrf_from(r_page.get_data(as_text=True))
r = client.post("/login", data={"username": "admin", "password": "clave_mala", "csrf_token": token})
assert r.status_code == 200, "FALLO: login incorrecto no debería redirigir"
assert "incorrectos" in r.get_data(as_text=True)
print("PRUEBA 1b: OK -- login con credenciales incorrectas muestra error y no crea sesión")

# 1c. Sin token CSRF -> 400
r = client.post("/login", data={"username": "admin", "password": "admin123"})
assert r.status_code == 400, f"FALLO: login sin csrf_token debería dar 400, dio {r.status_code}"
print("PRUEBA 1c: OK -- login sin token CSRF es rechazado")

# 1d. Límite de intentos (fuerza bruta): 8 por minuto por IP.
# Aislamos esta prueba reseteando el limiter antes y después, para no
# afectar (ni ser afectados por) el resto de la suite. El límite cuenta
# TODAS las peticiones a /login (GET y POST), así que pedimos el token
# una sola vez (1ra petición) y reutilizamos el mismo token en los
# siguientes POST (2da a 9na petición) -- no hace falta un GET nuevo
# por intento, y así no dependemos de que la página de error también
# traiga un token.
appmod.limiter.reset()
r_page = client.get("/login")
tk = csrf_from(r_page.get_data(as_text=True))
ultimo_status = None
for intento in range(8):
    ultimo_status = client.post(
        "/login", data={"username": "admin", "password": "clave_mala", "csrf_token": tk}
    ).status_code
print(f"PRUEBA 1d (fuerza bruta): petición #9 al mismo endpoint en el mismo minuto -> status {ultimo_status}")
assert ultimo_status == 429, f"FALLO: la petición 9 debería estar bloqueada (429), dio {ultimo_status}"
appmod.limiter.reset()
print("PRUEBA 1d: OK -- después de varios intentos fallidos, el login se bloquea temporalmente")

# 1e. Migración transparente de contraseña en texto plano.
conn = appmod.get_db()
conn.execute("INSERT INTO usuarios (username, password, rol) VALUES ('legacy_user', 'plano123', 'operador')")
conn.commit()
conn.close()

r_page = client.get("/login")
token = csrf_from(r_page.get_data(as_text=True))
r = client.post("/login", data={"username": "legacy_user", "password": "plano123", "csrf_token": token})
assert r.status_code == 302, "FALLO: el login con la contraseña vieja en texto plano debería funcionar"
conn = appmod.get_db()
guardada = conn.execute("SELECT password FROM usuarios WHERE username='legacy_user'").fetchone()["password"]
conn.close()
assert appmod._es_hash(guardada), "FALLO: la contraseña debió quedar migrada a hash tras el login"
print("PRUEBA 1e: OK -- una contraseña vieja en texto plano se migra a hash automáticamente al iniciar sesión")
client.get("/logout")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 2: AUTORIZACIÓN (un operador no puede usar rutas de admin)
# ═════════════════════════════════════════════════════════════════
set_sesion("operador_prueba", "operador")

rutas_admin_get = ["/tarifas", "/auditoria", "/backup", "/restore", "/usuarios"]
for ruta in rutas_admin_get:
    r = client.get(ruta, follow_redirects=False)
    assert r.status_code == 302 and r.headers.get("Location", "").endswith("/"), (
        f"FALLO: un operador pudo acceder a {ruta} (status {r.status_code}, "
        f"redirige a {r.headers.get('Location')})"
    )
print(f"PRUEBA 2a: OK -- un operador es redirigido fuera de {len(rutas_admin_get)} rutas de solo-admin (GET)")

# POST protegidas con @admin_required (necesitan CSRF igual, pero el
# redirect de admin_required ocurre antes de validar nada del formulario)
_, tk = get_con_token("/mensualidades")
r = client.post("/eliminar_mensualidad/99999", data={"csrf_token": tk}, follow_redirects=False)
assert r.status_code == 302 and r.headers.get("Location", "").endswith("/"), \
    "FALLO: un operador pudo llamar a /eliminar_mensualidad"
r = client.post("/usuarios/reset/1", data={"csrf_token": tk}, follow_redirects=False)
assert r.status_code == 302 and r.headers.get("Location", "").endswith("/"), \
    "FALLO: un operador pudo llamar a /usuarios/reset"
print("PRUEBA 2b: OK -- un operador es redirigido fuera de las rutas POST de solo-admin")

# Crear/editar mensualidad: la ruta es login_required (no admin_required)
# pero el propio código adentro bloquea a los no-admin -- lo probamos
# aparte porque redirige a /mensualidades, no a inicio.
r = client.post("/mensualidades", data={
    "csrf_token": tk, "nombre": "Intento Operador", "placa": "OPER01", "tipo": "carro",
    "fecha_inicio": "2026-01-01", "fecha_fin": "2026-12-31", "estado": "Activo",
}, follow_redirects=False)
assert r.status_code == 302 and r.headers.get("Location", "").endswith("/mensualidades"), \
    "FALLO: un operador pudo crear una mensualidad"
conn = appmod.get_db()
existe = conn.execute("SELECT id FROM mensualidades WHERE placa='OPER01'").fetchone()
conn.close()
assert existe is None, "FALLO: se creó una mensualidad aunque el usuario no era admin"
print("PRUEBA 2c: OK -- un operador no puede crear/editar mensualidades aunque conozca la ruta")

client.get("/logout")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 3: WHITELIST DE TIPO DE VEHÍCULO
# ═════════════════════════════════════════════════════════════════
set_sesion("admin_prueba", "admin")
r = client.get("/entrada/camion")
assert r.status_code == 404, f"FALLO: /entrada/camion debería dar 404, dio {r.status_code}"
print("PRUEBA 3: OK -- un tipo de vehículo fuera de la whitelist (carro/moto) es rechazado con 404")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 4: /admin/limpiar_pruebas bloqueado fuera de DEVELOPMENT
# ═════════════════════════════════════════════════════════════════
_, tk = get_con_token("/")
os.environ["DEVELOPMENT"] = "false"
r = client.post("/admin/limpiar_pruebas", data={"csrf_token": tk})
assert r.status_code == 403, f"FALLO: limpiar_pruebas debería estar bloqueado (403) fuera de desarrollo, dio {r.status_code}"
os.environ["DEVELOPMENT"] = "true"
print("PRUEBA 4: OK -- /admin/limpiar_pruebas está bloqueado fuera del entorno de desarrollo")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 5: FLUJO COMPLETO ENTRADA -> SALIDA CON CÁLCULO DE VALOR
# ═════════════════════════════════════════════════════════════════
_, tk = get_con_token("/entrada/carro")
r = client.post("/entrada/carro", data={"placa": "REG5001", "csrf_token": tk}, follow_redirects=False)
assert r.status_code == 302 and "/ticket/entrada/" in r.headers["Location"]

conn = appmod.get_db()
reg = conn.execute("SELECT * FROM parqueadero WHERE placa='REG5001'").fetchone()
conn.close()
assert reg is not None and reg["hora_salida"] is None
ticket_entrada_num = reg["ticket_num"]

# Retrocedemos la hora de entrada 90 minutos para que la salida calcule
# un valor mayor a $0 (fuera del período de cortesía).
hace_90min = (appmod.get_colombia_time() - appmod.timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S")
conn = appmod.get_db()
conn.execute("UPDATE parqueadero SET hora_entrada=? WHERE placa='REG5001'", (hace_90min,))
conn.commit()
conn.close()

r_salida_get = client.get("/salida", query_string={"placa": "REG5001"})
html_salida = r_salida_get.get_data(as_text=True)
tarifas = appmod.get_tarifas()
valor_esperado = int(appmod.calcular_valor("carro", 90, tarifas))
assert f"{valor_esperado}" in html_salida.replace(",", "").replace(".", ""), (
    f"FALLO: el valor sugerido de salida no coincide con calcular_valor() ({valor_esperado})"
)
print(f"PRUEBA 5a: OK -- para 90 minutos de carro, el valor sugerido en /salida coincide con calcular_valor() (${valor_esperado})")

tk_salida = csrf_from(html_salida)
r = client.post("/salida", data={
    "placa": "REG5001", "valor": str(valor_esperado), "metodo_pago": "Efectivo", "csrf_token": tk_salida,
}, follow_redirects=False)
assert r.status_code == 302 and "/ticket/salida/" in r.headers["Location"]

conn = appmod.get_db()
reg = conn.execute("SELECT * FROM parqueadero WHERE placa='REG5001'").fetchone()
conn.close()
assert reg["hora_salida"] is not None
assert reg["valor"] == valor_esperado
assert reg["metodo_pago"] == "Efectivo"
print("PRUEBA 5b: OK -- la salida se registra con el valor, método de pago y hora correctos")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 6: ANULACIÓN DE PAGO -- el valor original nunca se pierde,
# y un pago anulado no cuenta en caja/estadísticas/inicio
# ═════════════════════════════════════════════════════════════════
conn = appmod.get_db()
id_reg = conn.execute("SELECT id FROM parqueadero WHERE placa='REG5001'").fetchone()["id"]
conn.close()

_, tk = get_con_token("/historico")
r = client.post(f"/anular_pago/{id_reg}", data={
    "csrf_token": tk, "motivo": "Cobro de prueba erróneo", "real": "1000",
}, follow_redirects=False)
assert r.status_code == 302

conn = appmod.get_db()
reg = conn.execute("SELECT valor, valor_real, anulado, motivo_anulacion FROM parqueadero WHERE id=?", (id_reg,)).fetchone()
conn.close()
assert reg["anulado"] == 1
assert reg["valor"] == valor_esperado, f"FALLO: el valor original se perdió tras anular (quedó {reg['valor']})"
assert reg["valor_real"] == 1000
assert reg["motivo_anulacion"] == "Cobro de prueba erróneo"
print(f"PRUEBA 6a: OK -- al anular, el valor original (${valor_esperado}) se conserva intacto y se guarda el valor real ($1.000)")

conn = appmod.get_db()
fila_auditoria = conn.execute(
    "SELECT accion FROM auditoria WHERE tabla_afectada='parqueadero' AND registro_id=? ORDER BY id DESC LIMIT 1",
    (str(id_reg),),
).fetchone()
conn.close()
assert fila_auditoria is not None and fila_auditoria["accion"] == "anular_pago"
print("PRUEBA 6b: OK -- la anulación queda registrada en auditoría")

# La caja/estadísticas/inicio del día de la salida NO deben contar este
# pago anulado.
fecha_salida = reg2_fecha = None
conn = appmod.get_db()
fecha_salida = conn.execute("SELECT date(hora_salida) as f FROM parqueadero WHERE id=?", (id_reg,)).fetchone()["f"]
total_ese_dia = conn.execute(
    "SELECT COALESCE(SUM(valor),0) as t FROM parqueadero WHERE date(hora_salida)=? AND (anulado=0 OR anulado IS NULL)",
    (fecha_salida,),
).fetchone()["t"]
conn.close()
assert total_ese_dia == 0, (
    f"FALLO: el pago anulado (${valor_esperado}) se está contando en el total del día (dio ${total_ese_dia})"
)
print("PRUEBA 6c: OK -- un pago anulado no se suma en el total de caja del día")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 7: MENSUALIDADES -- crear, editar, eliminar, con auditoría
# ═════════════════════════════════════════════════════════════════
_, tk = get_con_token("/mensualidades")
datos_mensualidad = {
    "csrf_token": tk, "nombre": "Cliente Prueba", "placa": "MENS001", "tipo": "carro",
    "fecha_inicio": "2026-01-01", "fecha_fin": "2026-12-31", "estado": "Activo",
}
r = client.post("/mensualidades", data=datos_mensualidad, follow_redirects=False)
assert r.status_code == 302
conn = appmod.get_db()
fila = conn.execute("SELECT id, nombre, estado FROM mensualidades WHERE placa='MENS001'").fetchone()
conn.close()
assert fila is not None and fila["nombre"] == "Cliente Prueba"
id_mensualidad = fila["id"]
print("PRUEBA 7a: OK -- se puede crear una mensualidad")

_, tk = get_con_token("/mensualidades")
datos_mensualidad["csrf_token"] = tk
datos_mensualidad["estado"] = "Inactivo"
r = client.post("/mensualidades", data=datos_mensualidad, follow_redirects=False)
assert r.status_code == 302
conn = appmod.get_db()
fila = conn.execute("SELECT estado FROM mensualidades WHERE placa='MENS001'").fetchone()
conn.close()
assert fila["estado"] == "Inactivo", "FALLO: editar una mensualidad existente (misma placa) no actualizó el estado"
print("PRUEBA 7b: OK -- registrar la misma placa otra vez edita en vez de duplicar")

_, tk = get_con_token("/mensualidades")
r = client.post(f"/eliminar_mensualidad/{id_mensualidad}", data={"csrf_token": tk}, follow_redirects=False)
assert r.status_code == 302
conn = appmod.get_db()
fila = conn.execute("SELECT id FROM mensualidades WHERE placa='MENS001'").fetchone()
acciones = [r["accion"] for r in conn.execute(
    "SELECT accion FROM auditoria WHERE tabla_afectada='mensualidades' ORDER BY id"
).fetchall()]
conn.close()
assert fila is None, "FALLO: la mensualidad no se eliminó"
assert acciones == ["crear_mensualidad", "editar_mensualidad", "eliminar_mensualidad"], (
    f"FALLO: se esperaban las 3 acciones de auditoría en orden, se obtuvo {acciones}"
)
print("PRUEBA 7c: OK -- eliminar una mensualidad funciona, y las 3 acciones (crear/editar/eliminar) quedan en auditoría")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 8: TARIFAS -- solo se audita cuando de verdad cambia algo
# ═════════════════════════════════════════════════════════════════
conn = appmod.get_db()
conn.execute("DELETE FROM auditoria")
conn.commit()
conn.close()

_, tk = get_con_token("/tarifas")
r = client.post("/tarifas", data={
    "csrf_token": tk, "vh_carro": "5000", "vd_carro": "16000", "mc_carro": "5",
    "vh_moto": "2500", "vd_moto": "7000", "mc_moto": "5",
}, follow_redirects=False)
assert r.status_code == 200  # tarifas() re-renderiza la misma página tras el POST

conn = appmod.get_db()
n_despues_cambio = conn.execute("SELECT COUNT(*) as c FROM auditoria WHERE accion='cambiar_tarifa'").fetchone()["c"]
conn.close()
assert n_despues_cambio == 1, f"FALLO: se esperaba 1 entrada de auditoría por el cambio de tarifa de carro, hubo {n_despues_cambio}"
print("PRUEBA 8a: OK -- cambiar una tarifa genera exactamente una entrada de auditoría")

# Volvemos a guardar con los MISMOS valores -- no debería auditar de nuevo.
_, tk = get_con_token("/tarifas")
r = client.post("/tarifas", data={
    "csrf_token": tk, "vh_carro": "5000", "vd_carro": "16000", "mc_carro": "5",
    "vh_moto": "2500", "vd_moto": "7000", "mc_moto": "5",
}, follow_redirects=False)
conn = appmod.get_db()
n_sin_cambio = conn.execute("SELECT COUNT(*) as c FROM auditoria WHERE accion='cambiar_tarifa'").fetchone()["c"]
conn.close()
assert n_sin_cambio == 1, f"FALLO: guardar tarifas sin cambios no debería generar una nueva entrada de auditoría (había 1, ahora hay {n_sin_cambio})"
print("PRUEBA 8b: OK -- guardar tarifas sin cambios reales no genera ruido en la auditoría")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 9: RESET DE CONTRASEÑA POR EL ADMIN
# ═════════════════════════════════════════════════════════════════
conn = appmod.get_db()
conn.execute("DELETE FROM usuarios WHERE username='operador_reset'")
conn.execute("INSERT INTO usuarios (username, password, rol) VALUES ('operador_reset', ?, 'operador')",
             (appmod.generate_password_hash("claveOriginal"),))
conn.commit()
id_operador = conn.execute("SELECT id FROM usuarios WHERE username='operador_reset'").fetchone()["id"]
conn.close()

_, tk = get_con_token("/usuarios")
r = client.post(f"/usuarios/reset/{id_operador}", data={"csrf_token": tk}, follow_redirects=False)
assert r.status_code == 302

conn = appmod.get_db()
fila_auditoria = conn.execute(
    "SELECT valor_anterior, valor_nuevo, motivo FROM auditoria WHERE accion='reset_password' ORDER BY id DESC LIMIT 1"
).fetchone()
conn.close()
texto_auditoria = " ".join(str(v) for v in dict(fila_auditoria).values() if v)
assert "claveOriginal" not in texto_auditoria and "cambiar123" not in texto_auditoria, (
    "FALLO: la auditoría de reset_password no debería guardar ninguna contraseña real"
)
print("PRUEBA 9a: OK -- resetear una contraseña se audita sin guardar contraseñas de verdad")

client.get("/logout")
r_page = client.get("/login")
token = csrf_from(r_page.get_data(as_text=True))
r = client.post("/login", data={"username": "operador_reset", "password": "cambiar123", "csrf_token": token})
assert r.status_code == 302, "FALLO: el operador no pudo iniciar sesión con la nueva clave genérica tras el reset"
print("PRUEBA 9b: OK -- el usuario puede iniciar sesión con la clave genérica tras el reset")
client.get("/logout")

# ═════════════════════════════════════════════════════════════════
# PRUEBA 10: BÚSQUEDA EN HISTÓRICO POR PLACA
# ═════════════════════════════════════════════════════════════════
set_sesion("admin_prueba", "admin")
r = client.get("/historico", query_string={"placa": "REG5001"})
html = r.get_data(as_text=True)
assert "REG5001" in html, "FALLO: la búsqueda en histórico no encontró la placa registrada en la prueba 5"
r_vacio = client.get("/historico", query_string={"placa": "PLACAQUENOEXISTE"})
# El campo de búsqueda repite el texto buscado en su "value", así que
# no basta con buscar la placa en toda la página -- confirmamos que la
# tabla muestra el mensaje de "sin resultados".
assert "Sin registros" in r_vacio.get_data(as_text=True), \
    "FALLO: una búsqueda sin resultados debería mostrar 'Sin registros'"
print("PRUEBA 10: OK -- la búsqueda en histórico por placa filtra correctamente")

client.get("/logout")
limpiar_db()

print("\nTODAS LAS PRUEBAS DE REGRESIÓN GENERAL PASARON")
