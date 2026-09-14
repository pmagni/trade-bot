"""
Tests del núcleo puro de decisión de venta (sell_rules).

Por qué existe este módulo: `bot.py` y `backtest.py` implementaban la misma
decisión de venta por separado, y divergieron. El backtest validaba un sistema
distinto al que corre en producción. Estos tests fijan la semántica de
PRODUCCIÓN como la verdad; el harness debe conformarse a ella, no al revés.

No se mockea nada: `sell_rules` es puro (entra estado, sale una decisión).
"""

import pytest

from config import config
from sell_rules import SellPlan, calc_sell_qty, near_support_block, plan_sell, runner_is_viable


@pytest.fixture
def gate_enabled():
    """Activa el near_support_gate solo durante el test, y lo restaura."""
    previo = config.sell_guard.near_support_gate_enabled
    config.sell_guard.near_support_gate_enabled = True
    yield
    config.sell_guard.near_support_gate_enabled = previo


# ── Dimensionamiento ──────────────────────────────────────────────────────

def test_score_bajo_umbral_no_vende():
    """Bajo sell_partial no hay venta: el plan es None, no una venta de qty 0."""
    assert plan_sell(sell_score=4, sell_details={}, position_qty=1.0, price=100.0) is None


def test_venta_moderada_sin_resistencia():
    """Score 6 → 50% de la posición, cierre normal, sin runner."""
    plan = plan_sell(sell_score=6, sell_details={}, position_qty=1.0, price=100.0)

    assert plan == SellPlan(qty=0.50, reason="signal_sell", runner_qty=0.0, runner_trailing=0.0)


# ── Venta parcial en resistencia (ausente en el backtest hasta v2.19) ─────

def test_resistencia_con_score_debil_deja_runner_con_trailing():
    """
    En resistencia y sin señal fuerte: vende partial_sell_pct y deja el resto
    como runner con trailing. Es el mecanismo que el harness no reproducía.
    """
    plan = plan_sell(
        sell_score=6,
        sell_details={"key_resistance": "R1 $100"},
        position_qty=1.0,
        price=100.0,
    )

    assert plan.reason == "partial_resistance"
    assert plan.qty == pytest.approx(0.50)
    assert plan.runner_qty == pytest.approx(0.50)
    assert plan.runner_trailing == pytest.approx(100.0 * (1 - config.risk.trailing_stop_distance))


def test_resistencia_con_score_fuerte_vende_normal():
    """Con señal fuerte (>= sell_strong) la resistencia no cambia nada: salida normal."""
    plan = plan_sell(
        sell_score=8,
        sell_details={"key_resistance": "R1 $100"},
        position_qty=1.0,
        price=100.0,
    )

    assert plan.reason == "signal_sell"
    assert plan.runner_qty == 0.0


# ── Mínimo de orden ───────────────────────────────────────────────────────

def test_venta_bajo_minimo_cierra_la_posicion_entera():
    """
    Si el parcial no alcanza min_order_usdt, no se manda una orden que el
    exchange rechazaría: se cierra todo.
    """
    # Score 6 → 50% de $12 = $6, bajo el mínimo de $10
    plan = plan_sell(sell_score=6, sell_details={}, position_qty=0.12, price=100.0)

    assert plan.qty == pytest.approx(0.12)
    assert plan.reason == "signal_sell"
    assert plan.runner_qty == 0.0


def test_runner_bajo_minimo_no_es_viable():
    """Un runner que no llega al mínimo de orden no puede sostenerse como posición."""
    assert runner_is_viable(0.05, 100.0) is False   # $5
    assert runner_is_viable(0.50, 100.0) is True    # $50


# ── Compuerta de soporte cercano ──────────────────────────────────────────

def test_gate_apagado_nunca_bloquea():
    """Con el flag en False el gate es inerte, aunque el precio esté sobre el soporte."""
    soportes = [{"price": 100.0, "label": "S1"}]

    bloqueado, _ = near_support_block(price=100.0, supports=soportes)

    assert bloqueado is False


def test_gate_bloquea_venta_sobre_soporte(gate_enabled):
    """Dentro de la tolerancia de un soporte, la venta se bloquea y se dice cuál."""
    soportes = [{"price": 100.0, "label": "S1"}]

    bloqueado, razon = near_support_block(price=100.2, supports=soportes)

    assert bloqueado is True
    assert "S1" in razon


def test_gate_permite_venta_lejos_del_soporte(gate_enabled):
    """Fuera de la banda de proximidad el gate no interfiere."""
    soportes = [{"price": 100.0, "label": "S1"}]

    bloqueado, _ = near_support_block(price=110.0, supports=soportes)

    assert bloqueado is False


# ── Compatibilidad del sizing ─────────────────────────────────────────────

def test_calc_sell_qty_respeta_los_tramos_de_score():
    """El sizing por tramos es el mismo que usaba strategy.calc_sell_qty."""
    assert calc_sell_qty(4, 1.0) == 0
    assert calc_sell_qty(5, 1.0) == pytest.approx(config.scoring.sell_partial_pct)
    assert calc_sell_qty(6, 1.0) == pytest.approx(config.scoring.sell_moderate_pct)
    assert calc_sell_qty(8, 1.0) == pytest.approx(config.scoring.sell_strong_pct)
    assert calc_sell_qty(10, 1.0) == pytest.approx(config.scoring.sell_total_pct)
