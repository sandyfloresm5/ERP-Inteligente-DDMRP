"""
revertir_override.py
Cargill Ecuador - Aquanutrition

Borra el/los ajuste(s) manual(es) vigente(s) de un SKU en
ddmrp_overrides_auditoria, para que en la próxima corrida de
clasificar_mts_mto.py ese SKU vuelva a seguir la sugerencia automática
del sistema (newsvendor + racionamiento por capacidad), sin ningún
override "congelado" por encima.

Uso (PowerShell, con el venv activado):
    python revertir_override.py 107714391
Pide confirmación antes de borrar. No toca ningún otro SKU ni ninguna
otra tabla.
"""

import sys
import sqlite3
import pandas as pd

from config import DB_PATH


def main():
    sku = sys.argv[1].strip() if len(sys.argv) > 1 else input("SKU a revertir: ").strip()

    conn = sqlite3.connect(DB_PATH)

    actual = pd.read_sql(
        "SELECT * FROM ddmrp_overrides_auditoria WHERE sku = ? ORDER BY actualizado_en",
        conn, params=(sku,),
    )

    if actual.empty:
        print(f"[INFO] No hay ningún ajuste manual vigente para el SKU '{sku}' -- nada que revertir.")
        conn.close()
        return

    print(f"=== Ajuste(s) manual(es) que se van a borrar -- SKU {sku} ===\n")
    print(actual.to_string(index=False))

    resp = input(f"\n¿Confirmás borrar {len(actual)} fila(s) de ddmrp_overrides_auditoria "
                 f"para el SKU {sku}? (s/n): ").strip().lower()
    if resp != "s":
        print("[CANCELADO] No se borró nada.")
        conn.close()
        return

    conn.execute("DELETE FROM ddmrp_overrides_auditoria WHERE sku = ?", (sku,))
    conn.commit()
    conn.close()
    print(f"[OK] Se borraron {len(actual)} fila(s). En la próxima corrida de "
          f"clasificar_mts_mto.py, el SKU {sku} va a seguir la sugerencia automática "
          f"del sistema (newsvendor + racionamiento), sin ningún ajuste manual encima.")


if __name__ == "__main__":
    main()