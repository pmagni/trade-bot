"""
Tests del núcleo puro de stops nativos (native_stops).

La garantía que protegen los invariantes: nunca colocar una orden que dispare
al instante y venda una posición sana. Quedarse sin red es preferible.
"""

import pytest

from native_stops import DesiredStop, desired_stops


def _pos(pos_id=1, symbol="BNBUSDT", qty=0.02, stop_loss=600.0):
    """Posición estilo fila de la tabla positions."""
    return {"id": pos_id, "symbol": symbol, "qty": qty, "stop_loss": stop_loss}


PRECIOS = {"BNBUSDT": 650.0, "ETHUSDT": 2400.0}


def test_emite_stop_bajo_el_stop_del_bot():
    """El trigger va margin_pct por debajo del stop de la posición."""
    d = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"])

    assert d == {"nsl-1": DesiredStop(
        link_id="nsl-1", symbol="BNBUSDT", qty=0.02,
        trigger_price=600.0 * (1 - 0.015))}


def test_ignora_simbolo_no_habilitado():
    """El flag por símbolo es lo que hace posible el canario."""
    d = desired_stops([_pos(symbol="ETHUSDT")], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"])

    assert d == {}


def test_ignora_posicion_sin_stop():
    """stop_loss=0 significa 'sin stop'; no hay nada de qué derivar el trigger."""
    d = desired_stops([_pos(stop_loss=0)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"])

    assert d == {}


def test_invariante_trigger_bajo_precio_de_mercado():
    """
    Un trigger sobre el mercado dispararía al instante y vendería una posición
    sana. No se emite: quedarse sin red es preferible a colocar una trampa.
    """
    # stop_loss por encima del precio actual (posición ya bajo agua)
    d = desired_stops([_pos(stop_loss=700.0)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"])

    assert d == {}


def test_invariante_trigger_estrictamente_bajo_el_stop_del_bot():
    """Con margen 0 el nativo empataría al stop del bot y abriría una carrera."""
    d = desired_stops([_pos()], PRECIOS, margin=0.0, enabled=["BNBUSDT"])

    assert d == {}


def test_ignora_simbolo_sin_precio_conocido():
    """Sin precio no se puede verificar el invariante de mercado."""
    d = desired_stops([_pos(symbol="XRPUSDT")], PRECIOS, margin=0.015,
                      enabled=["XRPUSDT"])

    assert d == {}


@pytest.mark.parametrize("stop_loss", [627.5, 633.75, 613.2])
def test_funciona_bajo_las_tres_reglas_de_stop(stop_loss):
    """
    El trigger se deriva del stop_loss de la posición, no de la entrada, para
    servir a las tres reglas: 3.5% normal, 2.5% uptrend, 5.6% downtrend.
    """
    d = desired_stops([_pos(stop_loss=stop_loss)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"])

    assert d["nsl-1"].trigger_price == pytest.approx(stop_loss * 0.985)


def test_varias_posiciones_del_mismo_simbolo():
    """Una orden por posición, vinculada por id. El exchange permite 30/símbolo."""
    d = desired_stops([_pos(pos_id=7), _pos(pos_id=9, stop_loss=590.0)],
                      PRECIOS, margin=0.015, enabled=["BNBUSDT"])

    assert set(d) == {"nsl-7", "nsl-9"}
