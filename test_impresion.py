"""
Prueba de la Fase 4 (impresión robusta): reservas atómicas, expiración por
timeout, y confirmación idempotente. Usa el test client de Flask (no
levanta un servidor real) con varios hilos golpeando el mismo endpoint al
mismo tiempo, para reproducir el escenario real de dos polls del agente
de impresión solapándose.
"""
import os
import threading
from datetime import timedelta

os.environ["DEVELOPMENT"] = "true"
os.environ["PRINTER_API_KEY"] = "test_key_fase4"
import app as appmod

HEADERS = {"X-API-KEY": "test_key_fase4"}


def reset_db():
    conn = appmod.get_db()
    conn.execute("DELETE FROM parqueadero")
    conn.execute("UPDATE consecutivo SET numero = 0 WHERE id = 1")
    conn.commit()
    conn.close()


def crear_entrada(placa):
    conn = appmod.get_db()
    ticket = appmod.get_consecutivo(conn)
    conn.execute(
        "INSERT INTO parqueadero (placa, tipo, hora_entrada, ticket_num) VALUES (?,?,?,?)",
        (placa, "carro", appmod.get_colombia_time().strftime("%Y-%m-%d %H:%M:%S"), ticket),
    )
    conn.commit()
    conn.close()


reset_db()
client = appmod.app.test_client()

# ─────────────────────────────────────────────────────────────────
# PRUEBA 1: 5 polls concurrentes -> cada ticket se entrega a UNO solo
# ─────────────────────────────────────────────────────────────────
for i in range(10):
    crear_entrada(f"RACE{i:02d}")

resultados = []
lock = threading.Lock()

def poll():
    r = client.get("/api/pendientes_impresion", headers=HEADERS)
    placas = [e["placa"] for e in r.get_json()["entradas"]]
    with lock:
        resultados.append(placas)

hilos = [threading.Thread(target=poll) for _ in range(5)]
for h in hilos: h.start()
for h in hilos: h.join()

todas = [p for lote in resultados for p in lote]
print(f"PRUEBA 1 (polls concurrentes): repartos = {[len(r) for r in resultados]}, "
      f"total entregado = {len(todas)}, únicas = {len(set(todas))}")
assert len(todas) == 10, f"FALLO: se entregaron {len(todas)} tickets, esperaba 10"
assert len(todas) == len(set(todas)), "FALLO: el mismo ticket se entregó a más de un poll"
print("PRUEBA 1: OK -- cada ticket se entregó a exactamente un poll, sin duplicados")

# ─────────────────────────────────────────────────────────────────
# PRUEBA 2: reserva vencida (agente caído) se vuelve a ofrecer
# ─────────────────────────────────────────────────────────────────
reset_db()
crear_entrada("TIMEOUT01")
r = client.get("/api/pendientes_impresion", headers=HEADERS)
assert len(r.get_json()["entradas"]) == 1, "FALLO: no se reservó el ticket la primera vez"

# Sin confirmar, forzamos que la reserva quede "vieja" (más del timeout)
conn = appmod.get_db()
vieja = (appmod.get_colombia_time() - timedelta(seconds=appmod.IMPRESION_RESERVA_TIMEOUT_SEG + 10)).strftime("%Y-%m-%d %H:%M:%S")
conn.execute("UPDATE parqueadero SET impreso_entrada_reservado_en=? WHERE placa='TIMEOUT01'", (vieja,))
conn.commit()
conn.close()

r2 = client.get("/api/pendientes_impresion", headers=HEADERS)
recuperados = [e["placa"] for e in r2.get_json()["entradas"]]
print(f"PRUEBA 2 (timeout de reserva): {recuperados}")
assert recuperados == ["TIMEOUT01"], "FALLO: el ticket con reserva vencida no se liberó"
print("PRUEBA 2: OK -- una reserva vencida (agente caído) se vuelve a ofrecer automáticamente")

# ─────────────────────────────────────────────────────────────────
# PRUEBA 3: confirmar impreso es idempotente (reintento del agente)
# ─────────────────────────────────────────────────────────────────
conn = appmod.get_db()
row = conn.execute("SELECT id FROM parqueadero WHERE placa='TIMEOUT01'").fetchone()
conn.close()
rid = row["id"]

r3a = client.post(f"/api/marcar_impreso/{rid}/entrada", headers=HEADERS)
r3b = client.post(f"/api/marcar_impreso/{rid}/entrada", headers=HEADERS)  # reintento
print(f"PRUEBA 3 (confirmar dos veces): 1ra vez -> {r3a.get_json()}, 2da vez (reintento) -> {r3b.get_json()}")
assert r3a.get_json()["ok"] is True
assert r3b.get_json()["ok"] is True, "FALLO: reintentar confirmar un ticket ya impreso no debería fallar"
print("PRUEBA 3: OK -- confirmar impreso es idempotente ante reintentos del agente")

for f in os.listdir("."):
    if f.startswith(os.path.basename(appmod.DB_FILE)):
        os.remove(f)

print("\nTODAS LAS PRUEBAS DE IMPRESIÓN PASARON")
