"""
Prueba de la Fase 7 (backup y recuperación):
  1. Un backup tomado con la API de SQLite (backup()) mientras hay
     escrituras concurrentes queda íntegro (PRAGMA integrity_check) y
     con todos los datos ya confirmados -- a diferencia de una copia
     "en crudo" con shutil.copy2(), que en WAL puede quedar incompleta.
  2. La retención automática conserva solo los backups más recientes.
  3. El flujo completo de restauración (subir/seleccionar un backup,
     reemplazar el archivo activo, limpiar el WAL viejo) deja la base
     de datos exactamente como estaba en el momento del backup.
"""
import os
import shutil
import sqlite3
import threading
import time

os.environ["DEVELOPMENT"] = "true"
os.environ["PRINTER_API_KEY"] = "test_key_fase7"
os.environ["BACKUP_RETENCION"] = "3"
import app as appmod

client = appmod.app.test_client()


def limpiar_todo():
    for f in os.listdir("."):
        if f.startswith(os.path.basename(appmod.DB_FILE)):
            os.remove(f)
    if os.path.isdir(appmod.BACKUP_DIR):
        shutil.rmtree(appmod.BACKUP_DIR)
    os.makedirs(appmod.BACKUP_DIR)


limpiar_todo()
appmod.init_db()

conn = appmod.get_db()
conn.execute("DELETE FROM parqueadero")
conn.commit()
conn.close()

# ─────────────────────────────────────────────────────────────────
# PRUEBA 1: backup íntegro mientras hay escrituras concurrentes
# ─────────────────────────────────────────────────────────────────
detener = threading.Event()
errores_escritura = []


def escritor_continuo():
    i = 0
    while not detener.is_set():
        try:
            c = appmod.get_db()
            ticket = appmod.get_consecutivo(c)
            c.execute(
                "INSERT INTO parqueadero (placa, tipo, hora_entrada, ticket_num) VALUES (?,?,?,?)",
                (f"BKP{i:04d}", "carro", appmod.get_colombia_time().strftime("%Y-%m-%d %H:%M:%S"), ticket),
            )
            c.commit()
            c.close()
            i += 1
        except Exception as e:
            errores_escritura.append(str(e))
        time.sleep(0.005)


hilo_escritor = threading.Thread(target=escritor_continuo)
hilo_escritor.start()
time.sleep(0.2)  # dejamos que arranquen algunas escrituras primero

destino_backup1 = os.path.join(appmod.BACKUP_DIR, "backup_prueba1.db")
appmod._crear_backup_sqlite(destino_backup1)

detener.set()
hilo_escritor.join()

assert not errores_escritura, f"FALLO: hubo errores durante las escrituras concurrentes: {errores_escritura}"

# El backup debe ser una base de datos SQLite válida y consistente,
# tomada mientras se escribía activamente.
chequeo = sqlite3.connect(destino_backup1)
integridad = chequeo.execute("PRAGMA integrity_check").fetchone()[0]
filas_backup = chequeo.execute("SELECT COUNT(*) FROM parqueadero").fetchone()[0]
chequeo.close()
print(f"PRUEBA 1 (backup con escrituras concurrentes): integrity_check={integridad}, filas capturadas={filas_backup}")
assert integridad == "ok", f"FALLO: el backup tomado durante escrituras concurrentes quedó corrupto: {integridad}"
assert filas_backup > 0, "FALLO: el backup no capturó ninguna fila (algo salió mal con el timing de la prueba)"
print("PRUEBA 1: OK -- el backup con la API de SQLite queda íntegro incluso con escrituras simultáneas")

# ─────────────────────────────────────────────────────────────────
# PRUEBA 2: retención automática (BACKUP_RETENCION=3 en esta prueba)
# ─────────────────────────────────────────────────────────────────
for f in os.listdir(appmod.BACKUP_DIR):
    os.remove(os.path.join(appmod.BACKUP_DIR, f))

nombres_creados = []
for i in range(6):
    nombre = f"backup_2026010{i}_000000.db"  # nombres ordenables cronológicamente
    ruta = os.path.join(appmod.BACKUP_DIR, nombre)
    appmod._crear_backup_sqlite(ruta)
    nombres_creados.append(nombre)

appmod._limpiar_backups_antiguos()

restantes = sorted(os.listdir(appmod.BACKUP_DIR))
print(f"PRUEBA 2 (retención): se crearon 6 backups, quedaron {len(restantes)} -> {restantes}")
assert len(restantes) == 3, f"FALLO: debían quedar 3 backups (BACKUP_RETENCION=3), quedaron {len(restantes)}"
assert restantes == sorted(nombres_creados)[-3:], "FALLO: no se conservaron los 3 backups más recientes"
print("PRUEBA 2: OK -- la retención automática conserva solo los backups más recientes")

# ─────────────────────────────────────────────────────────────────
# PRUEBA 3: flujo completo de restauración vía HTTP (POST /restore)
# ─────────────────────────────────────────────────────────────────
for f in os.listdir(appmod.BACKUP_DIR):
    os.remove(os.path.join(appmod.BACKUP_DIR, f))

conn = appmod.get_db()
conn.execute("DELETE FROM parqueadero")
conn.execute(
    "INSERT INTO parqueadero (placa, tipo, hora_entrada, ticket_num) VALUES (?,?,?,?)",
    ("ANTESDEL", "carro", appmod.get_colombia_time().strftime("%Y-%m-%d %H:%M:%S"), 501),
)
conn.commit()
conn.close()

# Tomamos un backup del estado "bueno" que luego vamos a restaurar.
nombre_bueno = "backup_estado_bueno.db"
appmod._crear_backup_sqlite(os.path.join(appmod.BACKUP_DIR, nombre_bueno))

# Ahora "dañamos" el estado actual (simulando algo que salió mal después).
conn = appmod.get_db()
conn.execute("DELETE FROM parqueadero")
conn.execute(
    "INSERT INTO parqueadero (placa, tipo, hora_entrada, ticket_num) VALUES (?,?,?,?)",
    ("ERRORDESPUES", "carro", appmod.get_colombia_time().strftime("%Y-%m-%d %H:%M:%S"), 999),
)
conn.commit()
conn.close()

with client.session_transaction() as sess:
    sess["usuario"] = "admin_prueba"
    sess["rol"] = "admin"

# Simulamos: el admin entra a /restore, elige el backup del estado bueno.
csrf_resp = client.get("/restore")
import re
m = re.search(r'name="csrf_token" value="([^"]+)"', csrf_resp.get_data(as_text=True))
token = m.group(1)

r = client.post("/restore", data={"csrf_token": token, "backup_existente": nombre_bueno}, follow_redirects=True)
assert r.status_code == 200, f"FALLO: /restore devolvió {r.status_code}"

conn = appmod.get_db()
placas = [row["placa"] for row in conn.execute("SELECT placa FROM parqueadero").fetchall()]
conn.close()
print(f"PRUEBA 3 (restaurar vía web): placas después de restaurar = {placas}")
assert placas == ["ANTESDEL"], f"FALLO: se esperaba solo ['ANTESDEL'] después de restaurar, se obtuvo {placas}"

# Y debe haber quedado guardado un backup "pre_restore" con el estado
# dañado, por si hubiera que deshacer la restauración.
pre_restore = [f for f in os.listdir(appmod.BACKUP_DIR) if "pre_restore" in f]
assert len(pre_restore) == 1, f"FALLO: se esperaba exactamente 1 backup pre_restore, se encontraron {len(pre_restore)}"
print("PRUEBA 3: OK -- restaurar desde la web deja los datos exactamente como estaban en el backup, "
      "y guarda automáticamente una copia del estado anterior")

# Confirmamos que la restauración quedó en el log de auditoría.
conn = appmod.get_db()
fila_auditoria = conn.execute(
    "SELECT accion, usuario FROM auditoria WHERE accion='restore_backup' ORDER BY id DESC LIMIT 1"
).fetchone()
conn.close()
assert fila_auditoria is not None, "FALLO: la restauración no quedó registrada en auditoria"
assert fila_auditoria["usuario"] == "admin_prueba"
print("PRUEBA 3b: OK -- la restauración queda registrada en /auditoria con el usuario que la hizo")

limpiar_todo()
print("\nTODAS LAS PRUEBAS DE BACKUP Y RECUPERACIÓN PASARON")
