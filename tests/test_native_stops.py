"""
Tests del núcleo puro de stops nativos (native_stops).

La garantía que protegen los invariantes: nunca colocar una orden que dispare
al instante y venda una posición sana. Quedarse sin red es preferible.
"""

import pytest

from native_stops import DesiredStop, desired_stops, ReconcilePlan, reconcile_plan


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


def _orden(link_id="nsl-1", order_id="abc123", qty="0.02", trigger="591.0"):
    """Orden condicional abierta, con el shape crudo que devuelve Bybit."""
    return {"orderId": order_id, "orderLinkId": link_id,
            "qty": qty, "triggerPrice": trigger}


def test_deseada_sin_orden_se_coloca():
    d = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"])

    plan = reconcile_plan(d, actual=[])

    assert plan.to_place == [d["nsl-1"]]
    assert plan.to_cancel == []


def test_orden_sin_deseada_se_cancela():
    """Posición ya cerrada: su orden quedó huérfana y hay que limpiarla."""
    plan = reconcile_plan({}, actual=[_orden(order_id="huerfana")])

    assert plan.to_place == []
    assert plan.to_cancel == ["huerfana"]


def test_estado_convergido_produce_plan_vacio():
    """
    LA propiedad que hace seguro correr esto cada 15 min. Si un estado ya
    convergido generara actividad, el reconciliador cancelaría y recolocaría
    en loop, gastando rate limit y dejando ventanas sin protección.
    """
    d = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"])
    stop = d["nsl-1"]

    plan = reconcile_plan(d, actual=[
        _orden(link_id="nsl-1", qty=str(stop.qty), trigger=str(stop.trigger_price))])

    assert plan == ReconcilePlan(to_place=[], to_cancel=[])


def test_qty_distinta_se_recoloca():
    """Tras una venta parcial la orden vieja cubre de más."""
    d = desired_stops([_pos(qty=0.01)], PRECIOS, margin=0.015, enabled=["BNBUSDT"])

    plan = reconcile_plan(d, actual=[
        _orden(link_id="nsl-1", order_id="vieja", qty="0.02", trigger="591.0")])

    assert plan.to_cancel == ["vieja"]
    assert plan.to_place == [d["nsl-1"]]


def test_trigger_distinto_se_recoloca():
    d = desired_stops([_pos(stop_loss=610.0)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"])

    plan = reconcile_plan(d, actual=[
        _orden(link_id="nsl-1", order_id="vieja", qty="0.02", trigger="591.0")])

    assert plan.to_cancel == ["vieja"]
    assert plan.to_place == [d["nsl-1"]]


def test_nunca_cancela_ordenes_ajenas():
    """
    El prefijo nsl- es la única marca de propiedad. Una orden colocada a mano
    desde la app de Bybit NO puede ser cancelada por el bot.
    """
    plan = reconcile_plan({}, actual=[
        _orden(link_id="mi-orden-manual", order_id="ajena"),
        _orden(link_id="", order_id="sin-link"),
    ])

    assert plan.to_cancel == []
    assert plan.to_place == []
