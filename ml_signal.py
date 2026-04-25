"""
ml_signal.py — Señal de predicción ML basada en ExtraTreesClassifier.

Pipeline:
  1. Descarga hasta 1000 velas de 4h desde Bybit (≈166 días)
  2. Construye features técnicas: returns, EMAs, RSI, BB, volumen, momentum
  3. Target: 1 si el cierre de la siguiente vela > cierre actual, 0 si no
  4. Entrena ExtraTreesClassifier con validación cruzada temporal
  5. Persiste el modelo en disco (models/<ASSET>_model.joblib)
  6. Retorna señal: +1 (predicción de subida), -1 (bajada), 0 (neutral/incierto)

El modelo se reentrena automáticamente si tiene más de ML_RETRAIN_DAYS días.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pandas_ta as ta
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

import config
from exchange import get_ohlcv, OHLCVCandle

log = logging.getLogger(__name__)

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(exist_ok=True)

# Número de velas a pedir para entrenamiento (máx Bybit = 1000)
ML_TRAIN_LIMIT = 1000
# Días antes de reentrenar el modelo
ML_RETRAIN_DAYS = int(os.getenv("ML_RETRAIN_DAYS", "7"))
# Umbral de probabilidad para emitir señal (por debajo → neutral)
ML_CONFIDENCE_THRESHOLD = float(os.getenv("ML_CONFIDENCE_THRESHOLD", "0.58"))
# Mínimo de muestras para entrenar
ML_MIN_SAMPLES = 120


# ---------------------------------------------------------------------------
# Rutas de artefactos por activo
# ---------------------------------------------------------------------------

def _model_path(asset: str) -> Path:
    return MODELS_DIR / f"{asset.lower()}_model.joblib"


def _meta_path(asset: str) -> Path:
    return MODELS_DIR / f"{asset.lower()}_meta.joblib"


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _candles_to_df(candles: list[OHLCVCandle]) -> pd.DataFrame:
    df = pd.DataFrame([
        {"ts": c.ts, "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume}
        for c in candles
    ])
    df.sort_values("ts", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construye todas las features. Retorna DataFrame con columnas de features
    alineadas con el índice original (sin NaNs).
    """
    feat = pd.DataFrame(index=df.index)

    # --- Returns ---
    for period in [1, 3, 6, 12, 24]:
        feat[f"ret_{period}"] = df["close"].pct_change(period)

    # --- EMA ratios ---
    ema9  = ta.ema(df["close"], length=9)
    ema21 = ta.ema(df["close"], length=21)
    ema50 = ta.ema(df["close"], length=50)
    feat["close_ema9_ratio"]  = df["close"] / ema9  - 1
    feat["close_ema21_ratio"] = df["close"] / ema21 - 1
    feat["ema9_ema21_ratio"]  = ema9 / ema21 - 1
    feat["ema21_ema50_ratio"] = ema21 / ema50 - 1

    # --- RSI ---
    feat["rsi14"] = ta.rsi(df["close"], length=14) / 100.0
    feat["rsi7"]  = ta.rsi(df["close"], length=7)  / 100.0

    # --- Bollinger Band position [0,1] ---
    bb = ta.bbands(df["close"], length=20, std=2.0)
    if bb is not None:
        upper_col = [c for c in bb.columns if "BBU" in c][0]
        lower_col = [c for c in bb.columns if "BBL" in c][0]
        band_width = bb[upper_col] - bb[lower_col]
        feat["bb_position"] = (df["close"] - bb[lower_col]) / band_width.replace(0, np.nan)
        feat["bb_width"]    = band_width / df["close"]

    # --- Volumen ---
    vol_ma = df["volume"].rolling(20).mean()
    feat["vol_ratio"] = df["volume"] / vol_ma.replace(0, np.nan)
    feat["vol_ret1"]  = df["volume"].pct_change(1)

    # --- Momentum / rango ---
    feat["hl_range"]   = (df["high"] - df["low"]) / df["close"]
    feat["close_open"] = (df["close"] - df["open"]) / df["open"]

    # --- Patrón temporal ---
    ts_dt = pd.to_datetime(df["ts"], unit="ms", utc=True)
    feat["hour"]       = ts_dt.dt.hour / 23.0
    feat["dayofweek"]  = ts_dt.dt.dayofweek / 6.0

    # --- Volatilidad histórica ---
    log_ret = np.log(df["close"] / df["close"].shift(1))
    feat["vol_10"]  = log_ret.rolling(10).std()
    feat["vol_20"]  = log_ret.rolling(20).std()

    # Eliminar infinitos y valores extremos que rompen el scaler
    feat.replace([np.inf, -np.inf], np.nan, inplace=True)
    # Clipear percentiles extremos por si quedan outliers
    for col in feat.columns:
        if feat[col].dtype == float:
            q_low  = feat[col].quantile(0.001)
            q_high = feat[col].quantile(0.999)
            feat[col] = feat[col].clip(q_low, q_high)

    return feat


def _build_target(df: pd.DataFrame) -> pd.Series:
    """Target: 1 si el precio de la SIGUIENTE vela sube, 0 si baja."""
    return (df["close"].shift(-1) > df["close"]).astype(int)


def _prepare_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    features = _build_features(df)
    target   = _build_target(df)

    # Alinear y eliminar NaNs + última fila (target desconocido)
    data = features.copy()
    data["_target"] = target
    data = data.dropna()
    data = data.iloc[:-1]  # última fila no tiene target futuro

    X = data.drop(columns=["_target"])
    y = data["_target"]
    return X, y


# ---------------------------------------------------------------------------
# Entrenamiento
# ---------------------------------------------------------------------------

def train(asset: str) -> dict:
    """
    Descarga datos, entrena el modelo y lo persiste en disco.
    Retorna métricas de validación.
    """
    log.info("%s — Iniciando entrenamiento ML (%d velas de 4h)...", asset, ML_TRAIN_LIMIT)

    candles = get_ohlcv(asset, interval=config.OHLCV_INTERVAL, limit=ML_TRAIN_LIMIT)
    df = _candles_to_df(candles)

    if len(df) < ML_MIN_SAMPLES:
        raise ValueError(
            f"{asset}: datos insuficientes para entrenar ({len(df)} < {ML_MIN_SAMPLES})"
        )

    X, y = _prepare_dataset(df)
    log.info("%s — Dataset: %d muestras, %d features", asset, len(X), X.shape[1])

    # Validación cruzada temporal (no shuffle — respeta orden cronológico)
    tscv = TimeSeriesSplit(n_splits=5)
    cv_scores = []
    for train_idx, val_idx in tscv.split(X):
        X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", ExtraTreesClassifier(
                n_estimators=300,
                max_depth=8,
                min_samples_split=10,
                min_samples_leaf=5,
                max_features="sqrt",
                class_weight="balanced",
                random_state=42,
                n_jobs=-1,
            )),
        ])
        pipe.fit(X_tr, y_tr)
        cv_scores.append(accuracy_score(y_val, pipe.predict(X_val)))

    mean_acc = float(np.mean(cv_scores))
    log.info("%s — CV accuracy: %.3f ± %.3f", asset, mean_acc, np.std(cv_scores))

    # Entrena modelo final con todos los datos
    final_model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", ExtraTreesClassifier(
            n_estimators=500,
            max_depth=8,
            min_samples_split=10,
            min_samples_leaf=5,
            max_features="sqrt",
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )),
    ])
    final_model.fit(X, y)

    # Feature importances (top 10 para logging)
    importances = final_model.named_steps["clf"].feature_importances_
    top_features = sorted(zip(X.columns, importances), key=lambda x: x[1], reverse=True)[:10]
    log.info("%s — Top features: %s", asset, [(f, round(i, 3)) for f, i in top_features])

    # Persistir
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "cv_accuracy": mean_acc,
        "cv_scores": cv_scores,
        "n_samples": len(X),
        "feature_names": list(X.columns),
        "top_features": top_features,
    }
    joblib.dump(final_model, _model_path(asset))
    joblib.dump(meta, _meta_path(asset))
    log.info("%s — Modelo guardado en %s (acc=%.3f)", asset, _model_path(asset), mean_acc)

    return meta


# ---------------------------------------------------------------------------
# Carga con reentrenamiento automático
# ---------------------------------------------------------------------------

def _needs_retrain(asset: str) -> bool:
    meta_file = _meta_path(asset)
    if not meta_file.exists() or not _model_path(asset).exists():
        return True
    try:
        meta = joblib.load(meta_file)
        trained_at = datetime.fromisoformat(meta["trained_at"])
        age = datetime.now(timezone.utc) - trained_at.replace(tzinfo=timezone.utc)
        return age >= timedelta(days=ML_RETRAIN_DAYS)
    except Exception:
        return True


def load_or_train(asset: str) -> tuple:
    """Carga el modelo desde disco; reentrena si es necesario o no existe."""
    if _needs_retrain(asset):
        try:
            train(asset)
        except Exception as exc:
            log.error("%s — Error entrenando modelo ML: %s", asset, exc)
            return None, None
    try:
        model = joblib.load(_model_path(asset))
        meta  = joblib.load(_meta_path(asset))
        return model, meta
    except Exception as exc:
        log.error("%s — Error cargando modelo ML: %s", asset, exc)
        return None, None


# ---------------------------------------------------------------------------
# Predicción — punto de entrada principal
# ---------------------------------------------------------------------------

def predict_signal(asset: str) -> int:
    """
    Retorna la señal ML para el activo:
      +1 → el modelo predice subida con confianza >= ML_CONFIDENCE_THRESHOLD
      -1 → el modelo predice bajada con confianza >= ML_CONFIDENCE_THRESHOLD
       0 → neutral / modelo no disponible / baja confianza
    """
    model, meta = load_or_train(asset)
    if model is None:
        log.warning("%s — Modelo ML no disponible, señal = 0", asset)
        return 0

    try:
        # Obtener las últimas velas para construir las features actuales
        candles = get_ohlcv(asset, interval=config.OHLCV_INTERVAL, limit=100)
        df = _candles_to_df(candles)
        features = _build_features(df).dropna()

        if features.empty:
            return 0

        # Alinear columnas con las usadas en entrenamiento
        trained_cols = meta.get("feature_names", [])
        for col in trained_cols:
            if col not in features.columns:
                features[col] = 0.0
        features = features[trained_cols]

        last_row = features.iloc[[-1]]
        proba = model.predict_proba(last_row)[0]  # [P(baja), P(subida)]
        p_up   = proba[1]
        p_down = proba[0]

        if p_up >= ML_CONFIDENCE_THRESHOLD:
            signal = +1
            log.info("%s — ML signal=+1 (P_up=%.3f)", asset, p_up)
        elif p_down >= ML_CONFIDENCE_THRESHOLD:
            signal = -1
            log.info("%s — ML signal=-1 (P_down=%.3f)", asset, p_down)
        else:
            signal = 0
            log.info("%s — ML signal=0 (P_up=%.3f, P_down=%.3f — baja confianza)", asset, p_up, p_down)

        return signal

    except Exception as exc:
        log.error("%s — Error en predicción ML: %s", asset, exc)
        return 0


def get_model_info(asset: str) -> dict | None:
    """Retorna metadata del modelo (para /signals en Telegram)."""
    meta_file = _meta_path(asset)
    if not meta_file.exists():
        return None
    try:
        return joblib.load(meta_file)
    except Exception:
        return None


def retrain_all(assets: list[str] = config.ASSETS) -> None:
    """Reentrena modelos de todos los activos. Llamado por el scheduler semanal."""
    log.info("Reentrenamiento ML programado para: %s", assets)
    for asset in assets:
        try:
            meta = train(asset)
            log.info("%s — Reentrenamiento completado. CV acc=%.3f", asset, meta["cv_accuracy"])
        except Exception as exc:
            log.error("%s — Reentrenamiento fallido: %s", asset, exc)
