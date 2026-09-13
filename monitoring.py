"""
Monitoring — señal de liveness hacia afuera (v2.18).

Deadman switch invertido: el bot EMPUJA un ping periódico a un servicio externo.
Si los pings paran —proceso muerto, droplet caído, red cortada, scan_cycle
colgado— el servicio alerta. La señal vive fuera del proceso que vigila, que es
lo que la hace sobrevivir a su muerte.

Contexto: el 2026-06-04 el bot estuvo 55 horas caído. Dos posiciones ETHUSDT
quedaron sin vigilancia mientras el precio atravesaba sus stops; se liquidaron
a -13.0% y -10.9% (-$4.53, ~80% del PnL neto histórico). Todo el monitoreo de
entonces vivía dentro del bot, así que murió con él. Ver
docs/superpowers/specs/2026-09-13-v2.18-deadman-alert-design.md

Este módulo importa SOLO `requests` a propósito: sin acoplamiento a config ni a
la lógica de trading, se puede testear sin levantar el stack del bot.
"""

import logging

import requests

logger = logging.getLogger("monitoring")

DEFAULT_TIMEOUT = 5.0


def heartbeat(url: str, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """
    Envía un ping de liveness. Devuelve True si se entregó.

    NUNCA lanza excepción. Es la garantía central: se llama desde scan_cycle,
    y una feature de monitoreo que tumba el trading es peor que no tenerla.
    Cualquier fallo (timeout, DNS, red, 5xx) se loguea y se traga.

    Un `url` vacío es un no-op silencioso — así el bot corre igual sin
    configurar nada.
    """
    if not url:
        return False

    try:
        response = requests.get(url, timeout=timeout)
        if response.ok:
            return True
        logger.warning(f"Heartbeat rechazado: HTTP {response.status_code}")
        return False
    except Exception as e:
        # Deliberadamente amplio: ninguna falla de monitoreo puede escalar
        # hasta interrumpir un scan cycle.
        logger.warning(f"Heartbeat falló: {type(e).__name__}: {e}")
        return False
