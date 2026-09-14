"""
Tests del núcleo puro de stops nativos (native_stops).

La garantía que protegen los invariantes: nunca colocar una orden que dispare
al instante y venda una posición sana. Quedarse sin red es preferible.
"""

import pytest

from native_stops import (DesiredStop, desired_stops, ReconcilePlan, reconcile_plan,
                          falta_balance, parse_position_id, fill_incompleto)


def _pos(pos_id=1, symbol="BNBUSDT", qty=0.02, stop_loss=600.0):
    """Posición estilo fila de la tabla positions."""
    return {"id": pos_id, "symbol": symbol, "qty": qty, "stop_loss": stop_loss}


PRECIOS = {"BNBUSDT": 650.0, "ETHUSDT": 2400.0}
NOW_MS = 1000000000000  # Timestamp para tests


def test_emite_stop_bajo_el_stop_del_bot():
    """El trigger va margin_pct por debajo del stop de la posición."""
    d = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"], now_ms=NOW_MS)

    assert d == {1: DesiredStop(
        link_id=f"nsl-1-{NOW_MS}", symbol="BNBUSDT", qty=0.02,
        trigger_price=600.0 * (1 - 0.015))}


def test_ignora_simbolo_no_habilitado():
    """El flag por símbolo es lo que hace posible el canario."""
    d = desired_stops([_pos(symbol="ETHUSDT")], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"], now_ms=NOW_MS)

    assert d == {}


def test_ignora_posicion_sin_stop():
    """stop_loss=0 significa 'sin stop'; no hay nada de qué derivar el trigger."""
    d = desired_stops([_pos(stop_loss=0)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"], now_ms=NOW_MS)

    assert d == {}


def test_invariante_trigger_bajo_precio_de_mercado():
    """
    Un trigger sobre el mercado dispararía al instante y vendería una posición
    sana. No se emite: quedarse sin red es preferible a colocar una trampa.
    """
    # stop_loss por encima del precio actual (posición ya bajo agua)
    d = desired_stops([_pos(stop_loss=700.0)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"], now_ms=NOW_MS)

    assert d == {}


def test_invariante_trigger_estrictamente_bajo_el_stop_del_bot():
    """Con margen 0 el nativo empataría al stop del bot y abriría una carrera."""
    d = desired_stops([_pos()], PRECIOS, margin=0.0, enabled=["BNBUSDT"], now_ms=NOW_MS)

    assert d == {}


def test_ignora_simbolo_sin_precio_conocido():
    """Sin precio no se puede verificar el invariante de mercado."""
    d = desired_stops([_pos(symbol="XRPUSDT")], PRECIOS, margin=0.015,
                      enabled=["XRPUSDT"], now_ms=NOW_MS)

    assert d == {}


@pytest.mark.parametrize("stop_loss", [627.5, 633.75, 613.2])
def test_funciona_bajo_las_tres_reglas_de_stop(stop_loss):
    """
    El trigger se deriva del stop_loss de la posición, no de la entrada, para
    servir a las tres reglas: 3.5% normal, 2.5% uptrend, 5.6% downtrend.
    """
    d = desired_stops([_pos(stop_loss=stop_loss)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"], now_ms=NOW_MS)

    assert d[1].trigger_price == pytest.approx(stop_loss * 0.985)


def test_varias_posiciones_del_mismo_simbolo():
    """Una orden por posición, vinculada por id. El exchange permite 30/símbolo."""
    d = desired_stops([_pos(pos_id=7), _pos(pos_id=9, stop_loss=590.0)],
                      PRECIOS, margin=0.015, enabled=["BNBUSDT"], now_ms=NOW_MS)

    assert set(d) == {7, 9}


def _orden(pos_id=1, order_id="abc123", qty="0.02", trigger="591.0", now_ms=NOW_MS):
    """Orden condicional abierta, con el shape crudo que devuelve Bybit."""
    link_id = f"nsl-{pos_id}-{now_ms}"
    return {"orderId": order_id, "orderLinkId": link_id,
            "qty": qty, "triggerPrice": trigger}


def test_deseada_sin_orden_se_coloca():
    d = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"], now_ms=NOW_MS)

    plan = reconcile_plan(d, actual=[])

    assert plan.to_place == [d[1]]
    assert plan.to_cancel == []


def test_orden_sin_deseada_se_cancela():
    """Posición ya cerrada: su orden quedó huérfana y hay que limpiarla."""
    plan = reconcile_plan({}, actual=[_orden(pos_id=1, order_id="huerfana")])

    assert plan.to_place == []
    assert plan.to_cancel == ["huerfana"]


def test_estado_convergido_produce_plan_vacio():
    """
    LA propiedad que hace seguro correr esto cada 15 min. Si un estado ya
    convergido generara actividad, el reconciliador cancelaría y recolocaría
    en loop, gastando rate limit y dejando ventanas sin protección.
    """
    d = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"], now_ms=NOW_MS)
    stop = d[1]

    plan = reconcile_plan(d, actual=[
        _orden(pos_id=1, qty=str(stop.qty), trigger=str(stop.trigger_price), now_ms=NOW_MS)])

    assert plan == ReconcilePlan(to_place=[], to_cancel=[])


def test_qty_distinta_se_recoloca():
    """Tras una venta parcial la orden vieja cubre de más."""
    d = desired_stops([_pos(qty=0.01)], PRECIOS, margin=0.015, enabled=["BNBUSDT"], now_ms=NOW_MS)

    plan = reconcile_plan(d, actual=[
        _orden(pos_id=1, order_id="vieja", qty="0.02", trigger="591.0", now_ms=NOW_MS)])

    assert plan.to_cancel == ["vieja"]
    assert plan.to_place == [d[1]]


def test_trigger_distinto_se_recoloca():
    d = desired_stops([_pos(stop_loss=610.0)], PRECIOS, margin=0.015,
                      enabled=["BNBUSDT"], now_ms=NOW_MS)

    plan = reconcile_plan(d, actual=[
        _orden(pos_id=1, order_id="vieja", qty="0.02", trigger="591.0", now_ms=NOW_MS)])

    assert plan.to_cancel == ["vieja"]
    assert plan.to_place == [d[1]]


def test_nunca_cancela_ordenes_ajenas():
    """
    El prefijo nsl- es la única marca de propiedad. Una orden colocada a mano
    desde la app de Bybit NO puede ser cancelada por el bot.
    """
    # Órdenes sin nsl- prefix o con posición_id no parseable
    plan = reconcile_plan({}, actual=[
        {"orderId": "ajena", "orderLinkId": "mi-orden-manual", "qty": "0.02", "triggerPrice": "591.0"},
        {"orderId": "sin-link", "orderLinkId": "", "qty": "0.02", "triggerPrice": "591.0"},
    ])

    assert plan.to_cancel == []
    assert plan.to_place == []


def test_idempotencia_con_diferentes_now_ms():
    """
    Propiedad de idempotencia: un estado convergido produce plan vacío
    INCLUSO si now_ms es diferente. Esto es crítico porque el link_id ahora
    incluye el timestamp y cada llamada genera un nuevo timestamp.

    Si esto falla (matching regresa a link_id literal), el bot cancelaría y
    recolocaría cada orden en cada scan, gastando rate limit y dejando brechas
    sin protección.
    """
    d1 = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"], now_ms=1000)
    stop1 = d1[1]

    # Orden actual con el MISMO position_id pero DIFERENTE timestamp en link_id.
    # Construir literalmente con diferente now_ms para probar que matching es
    # por position_id, no por link_id literal.
    actual_link_id = "nsl-1-999"  # mismo position_id (1), diferente timestamp

    plan = reconcile_plan(d1, actual=[
        {"orderId": "abc", "orderLinkId": actual_link_id, "qty": str(stop1.qty),
         "triggerPrice": str(stop1.trigger_price)}])

    # El plan debe estar vacío porque position_id coincide y qty/trigger son iguales
    # Si falla aquí, significa que matching regresó a link_id literal.
    assert plan == ReconcilePlan(to_place=[], to_cancel=[])


def test_duplicados_position_id_se_cancelan():
    """
    Si el exchange devuelve dos órdenes para el mismo position_id, mantener
    la primera y cancelar la segunda como huérfana (colisión).
    """
    d = desired_stops([_pos()], PRECIOS, margin=0.015, enabled=["BNBUSDT"], now_ms=NOW_MS)

    plan = reconcile_plan(d, actual=[
        {"orderId": "primera", "orderLinkId": f"nsl-1-{NOW_MS}", "qty": "0.02",
         "triggerPrice": "591.0"},
        {"orderId": "duplicada", "orderLinkId": f"nsl-1-{NOW_MS + 1000}", "qty": "0.02",
         "triggerPrice": "591.0"},
    ])

    # Primera se mantiene (match), duplicada se cancela
    assert plan.to_cancel == ["duplicada"]
    assert plan.to_place == []


def test_posicion_id_no_parseable_no_se_toca():
    """
    Si una orden tiene el prefijo nsl- pero position_id no es un int válido
    (ej: nsl-abc-123), se trata como NO-NUESTRA y nunca se cancela.
    """
    plan = reconcile_plan({}, actual=[
        {"orderId": "invalid", "orderLinkId": "nsl-abc-123", "qty": "0.02",
         "triggerPrice": "591.0"},
    ])

    assert plan.to_cancel == []
    assert plan.to_place == []


def test_falta_balance_detecta_cierre_externo():
    """Falta medio BNB a $650 = $325, muy por encima del umbral de polvo."""
    assert falta_balance(tracked_qty=1.0, balance=0.5, price=650.0,
                         dust_threshold=5.0) is True


def test_falta_balance_tolera_fees_en_activo_base():
    """
    Bybit cobra fees en el activo base, así que el balance SIEMPRE queda un
    poco por debajo de lo registrado. Sin tolerancia esto sería un falso
    positivo en cada scan.
    """
    assert falta_balance(tracked_qty=1.0, balance=0.999, price=650.0,
                         dust_threshold=5.0) is False


def test_falta_balance_compara_en_usdt_no_en_cantidad():
    """
    La misma cantidad faltante significa cosas distintas según el activo.
    0.005 de BTC a $77000 son $385 (cierre real); 0.005 de BNB a $650 son
    $3.25 (polvo de fees).
    """
    assert falta_balance(1.0, 0.995, price=77000.0, dust_threshold=5.0) is True
    assert falta_balance(1.0, 0.995, price=650.0, dust_threshold=5.0) is False


def test_falta_balance_ignora_balance_de_mas():
    """Sobrante es asunto de _sweep_dust, no de esta detección."""
    assert falta_balance(1.0, 1.5, price=650.0, dust_threshold=5.0) is False


def test_parse_position_id():
    assert parse_position_id("nsl-42-1699999999000") == 42
    assert parse_position_id("nsl-abc-1699999999000") is None
    assert parse_position_id("nsl-42") is None        # formato viejo, ya no válido
    assert parse_position_id("otra-cosa-123") is None
    assert parse_position_id("") is None


def test_fill_incompleto_detecta_ejecucion_parcial():
    """Se vendió la mitad de lo registrado: fill parcial."""
    assert fill_incompleto(recorded_qty=1.0, executed_qty=0.5) is True


def test_fill_incompleto_tolera_diferencias_de_redondeo():
    """
    recorded_qty (DB) y executed_qty (fill de Bybit) vienen de fuentes
    distintas y difieren en el último decimal. No es un fill parcial real.
    """
    assert fill_incompleto(recorded_qty=1.0, executed_qty=0.9999) is False


def test_fill_incompleto_ejecutado_ausente_no_cuenta():
    """
    executed_qty=0 significa que Bybit no reportó cumExecQty (dato ausente),
    no un fill parcial de tamaño cero. La cáscara asume cierre completo en
    ese caso, así que acá no debe marcarse como incompleto.
    """
    assert fill_incompleto(recorded_qty=1.0, executed_qty=0.0) is False


def test_fill_incompleto_ejecucion_completa():
    assert fill_incompleto(recorded_qty=0.5, executed_qty=0.5) is False
