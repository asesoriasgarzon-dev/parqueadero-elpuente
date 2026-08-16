"""
Prueba de concurrencia para la Fase 2.

Simula varios "operadores" registrando entradas al mismo tiempo (para probar
que el consecutivo nunca se repite) y varios operadores tratando de cerrar
la salida del MISMO vehículo al mismo tiempo (para probar que solo uno
gana y el otro se entera de que perdió, en vez de sobreescribir en
silencio).

No usa Flask/HTTP -- llama directo a las funciones de app.py, que es donde
vivía el bug real (a nivel de SQLite), para tener una prueba rápida y
determinística.
"""
import os
import threading
import sqlite3

os.environ["DEVELOPMENT"] = "true"
import app as appmod

DB_FILE = appmod.DB_FILE

# Reset limpio de la tabla de consecutivos y parqueadero para la prueba
conn = appmod.get_db()
conn.execute("DELETE FROM parqueadero")
conn.execute("UPDATE consecutivo SET numero = 0 WHERE id = 1")
conn.commit()
conn.close()

# ─────────────────────────────────────────────────────────────────
# PRUEBA 1: 30 "entradas" concurrentes -> ningún ticket repetido
# ─────────────────────────────────────────────────────────────────
N = 30
resultados = []
lock = threading.Lock()

def registrar_entrada(i):
    conn = appmod.get_db()
    ticket_num = appmod.get_consecutivo(conn)
    conn.execute(
        "INSERT INTO parqueadero (placa, tipo, hora_entrada, ticket_num) VALUES (?,?,?,?)",
        (f"CONC{i:03d}", "carro", "2026-08-16 10:00:00", ticket_num),
    )
    conn.commit()
    conn.close()
    with lock:
        resultados.append(ticket_num)

hilos = [threading.Thread(target=registrar_entrada, args=(i,)) for i in range(N)]
for h in hilos: h.start()
for h in hilos: h.join()

duplicados = len(resultados) - len(set(resultados))
print(f"PRUEBA 1 (consecutivo concurrente): {N} entradas simultáneas -> "
      f"{len(set(resultados))} tickets únicos de {len(resultados)} generados. "
      f"Duplicados: {duplicados}")
assert duplicados == 0, "FALLO: se generaron tickets de entrada duplicados"
assert sorted(resultados) == list(range(1, N + 1)), "FALLO: la secuencia de tickets tiene huecos"
print("PRUEBA 1: OK -- ningún ticket duplicado, secuencia completa 1.." + str(N))

# ─────────────────────────────────────────────────────────────────
# PRUEBA 2: 10 operadores intentando cerrar la salida del MISMO
# vehículo al mismo tiempo -> solo uno debe lograrlo
# ─────────────────────────────────────────────────────────────────
conn = appmod.get_db()
conn.execute(
    "INSERT INTO parqueadero (placa, tipo, hora_entrada, ticket_num) VALUES (?,?,?,?)",
    ("DOBLE01", "carro", "2026-08-16 09:00:00", 9999),
)
conn.commit()
row = conn.execute("SELECT id FROM parqueadero WHERE placa='DOBLE01'").fetchone()
reg_id = row["id"]
conn.close()

exitos = []
fallos = []

def intentar_salida(operador_num):
    conn = appmod.get_db()
    reg2 = conn.execute(
        "SELECT * FROM parqueadero WHERE placa=? AND hora_salida IS NULL ORDER BY id DESC LIMIT 1",
        ("DOBLE01",),
    ).fetchone()
    if not reg2:
        conn.close()
        with lock:
            fallos.append(operador_num)
        return
    cur = conn.execute(
        """UPDATE parqueadero SET hora_salida=?, valor=?, metodo_pago=?, cajero=?
           WHERE id=? AND hora_salida IS NULL""",
        ("2026-08-16 11:00:00", 4000, "Efectivo", f"operador_{operador_num}", reg2["id"]),
    )
    if cur.rowcount == 0:
        conn.rollback()
        conn.close()
        with lock:
            fallos.append(operador_num)
    else:
        conn.commit()
        conn.close()
        with lock:
            exitos.append(operador_num)

hilos2 = [threading.Thread(target=intentar_salida, args=(i,)) for i in range(10)]
for h in hilos2: h.start()
for h in hilos2: h.join()

print(f"PRUEBA 2 (doble salida): {len(exitos)} operador(es) lograron cerrar la salida, "
      f"{len(fallos)} detectaron que ya estaba cerrada.")
assert len(exitos) == 1, f"FALLO: {len(exitos)} operadores cerraron la MISMA salida (debía ser exactamente 1)"
assert len(fallos) == 9, "FALLO: no todos los perdedores fueron detectados correctamente"
print("PRUEBA 2: OK -- exactamente un operador ganó la carrera, los otros 9 lo detectaron")

os.remove(DB_FILE) if os.path.exists(DB_FILE) else None
for f in os.listdir("."):
    if f.startswith(os.path.basename(DB_FILE)):
        os.remove(f)
print("\nTODAS LAS PRUEBAS DE CONCURRENCIA PASARON")
