#!/usr/bin/env bash
# Corre toda la suite de pruebas automáticas del proyecto, una por una,
# limpiando los archivos de base de datos de prueba entre cada una
# (cada script crea su propia base de datos temporal en este mismo
# directorio y la borra al terminar, pero si una prueba falla a mitad
# de camino puede dejar restos -- por eso se limpia también antes de
# cada una).
#
# Uso:
#   ./run_tests.sh
#
# Sale con código 0 si todas las pruebas pasaron, o 1 si alguna falló
# (útil para usarlo en un pipeline de CI más adelante).

set -uo pipefail
cd "$(dirname "$0")"

PRUEBAS=(
  "test_concurrencia.py"
  "test_impresion.py"
  "test_backup.py"
  "test_regresion.py"
  "test_caja_pdf_crm.py"
)

fallo_general=0

for prueba in "${PRUEBAS[@]}"; do
  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  $prueba"
  echo "════════════════════════════════════════════════════════════"
  rm -f parqueadero.db*
  rm -rf backups

  if python "$prueba"; then
    echo "-> $prueba: OK"
  else
    echo "-> $prueba: FALLÓ"
    fallo_general=1
  fi
done

rm -f parqueadero.db*
rm -rf backups

echo ""
if [ "$fallo_general" -eq 0 ]; then
  echo "✅ TODAS LAS SUITES PASARON"
else
  echo "❌ AL MENOS UNA SUITE FALLÓ -- revisa el detalle arriba"
fi
exit $fallo_general
