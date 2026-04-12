# 🅿️ Parqueadero El Puente – App Web v2.0

## Instalación (Windows)

### 1. Instalar dependencias
Abrir CMD en la carpeta del proyecto y ejecutar:
```
pip install flask
```

### 2. Ejecutar la app
```
python app.py
```

### 3. Acceder desde el PC
Abrir navegador en: http://localhost:5000

### 4. Acceder desde el celular del operador
- El celular debe estar en la misma red WiFi que el PC
- Ver la IP del PC: en CMD escribir `ipconfig` → buscar "Dirección IPv4"
- Desde el celular abrir: http://IP_DEL_PC:5000
  Ejemplo: http://192.168.1.10:5000

---

## Usuarios por defecto
| Usuario   | Contraseña | Rol       |
|-----------|-----------|-----------|
| admin     | admin123  | Admin     |
| operador  | op123     | Operador  |

> ⚠️ Cambia las contraseñas después del primer acceso (edita directamente en la BD con DB Browser for SQLite)

---

## Funcionalidades
- ✅ Login admin / operador
- ✅ Registro de entrada (carro / moto)
- ✅ Registro de salida con valor en tiempo real
- ✅ Métodos de pago: Efectivo, Nequi, Daviplata, Transferencia
- ✅ Convenios / descuentos manuales
- ✅ Ticket imprimible desde el navegador
- ✅ Clientes activos con tiempo y valor estimado
- ✅ Histórico con búsqueda por placa
- ✅ Cierre de caja por fecha con desglose por método de pago
- ✅ Gestión de mensualidades con alertas de vencimiento
- ✅ Panel de tarifas configurable (solo admin)
- ✅ Backup de la base de datos (solo admin)
- ✅ Compatible con impresora térmica (imprime desde el navegador con Ctrl+P)

---

## Impresora térmica
Al mostrar el ticket, el botón "Imprimir" abre el diálogo de impresión del navegador.
Selecciona tu impresora térmica y usa papel de 80mm.

---

## Base de datos
El archivo `parqueadero.db` es compatible con el sistema anterior (borrador.py).
Puedes copiar tu BD existente a esta carpeta y los datos históricos estarán disponibles.
