"""Panelmodell (Fázis 4).

Specifikáció (brief 7. pont):
  yield_t_ha ~ vármegye-fixhatás + közös időtrend + standardizált időjárási mutatók

A technológiai trendet a fixhatás + trend választja le; az időjárási mutatók a
trendhez képesti eltérést magyarázzák. Becslés: OLS county-dummykkal, opcionális
ridge büntetéssel CSAK az időjárási blokkon (a fixhatást és a trendet nem büntetjük).
A tavaszi/nyári csapadékot a vízmérleg (precip - et0) képviseli, hogy ne legyen
majdnem-tökéletes kollinearitás.

Nem sklearn-pipeline: zárt alakú megoldás numpy-val, így a büntetés szelektív
és a modell teljesen átlátható marad (~500 megfigyelés, brief: nincs fekete doboz).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src import config

TREND_BASE_YEAR = 2000


@dataclass
class PanelModel:
    counties: list[str]          # dummy-sorrend
    feature_names: list[str]     # időjárási magyarázók
    trend_degree: int
    feat_mean: np.ndarray        # standardizálás a tanítómintából
    feat_std: np.ndarray
    beta: np.ndarray             # [county dummyk | trend fok(ok) | feature-ök]
    resid_std: float             # tanító reziduumok szórása (in-sample)
    wb_median: dict[str, float] | None = None  # vármegyénkénti wb_total medián
                                               # (tanítóból — look-ahead-mentes)

    def _with_derived(self, df: pd.DataFrame) -> pd.DataFrame:
        """Származtatott modell-feature-ök: wb_deficit = min(wb_total - medián, 0).

        Konvex aszályjelző: csak a vármegye szokásos vízmérlegéhez képesti HIÁNY
        számít, a többlet nem. A medián a tanítómintából jön, így a LOYO/backtest
        look-ahead-mentes marad.
        """
        if "wb_deficit" in self.feature_names and "wb_deficit" not in df.columns:
            df = df.copy()
            med = df["nuts_id"].map(self.wb_median)
            df["wb_deficit"] = np.minimum(df["wb_total"] - med, 0.0)
        return df

    def _design(self, df: pd.DataFrame) -> np.ndarray:
        df = self._with_derived(df)
        n = len(df)
        dummies = np.zeros((n, len(self.counties)))
        idx = {c: j for j, c in enumerate(self.counties)}
        for i, c in enumerate(df["nuts_id"]):
            dummies[i, idx[c]] = 1.0
        t = (df["crop_year"].to_numpy() - TREND_BASE_YEAR).astype(float)
        trend = np.column_stack([t ** d for d in range(1, self.trend_degree + 1)])
        feats = (df[self.feature_names].to_numpy() - self.feat_mean) / self.feat_std
        return np.hstack([dummies, trend, feats])

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self._design(df) @ self.beta

    @property
    def weather_coefs(self) -> pd.Series:
        k = len(self.counties) + self.trend_degree
        return pd.Series(self.beta[k:], index=self.feature_names)


def fit_panel_model(train: pd.DataFrame,
                    features: list[str] | None = None,
                    trend_degree: int | None = None,
                    ridge_alpha: float | None = None,
                    crop: str = config.DEFAULT_CROP) -> PanelModel:
    """Panelmodell tanítása. ridge_alpha=0 -> sima OLS."""
    features = (features if features is not None
                else config.CROPS[crop]["model_features"])
    trend_degree = trend_degree if trend_degree is not None else config.TREND_DEGREE
    ridge_alpha = ridge_alpha if ridge_alpha is not None else config.RIDGE_ALPHA

    counties = sorted(train["nuts_id"].unique())
    wb_median = (train.groupby("nuts_id")["wb_total"].median().to_dict()
                 if "wb_deficit" in features else None)

    model = PanelModel(counties=counties, feature_names=features,
                       trend_degree=trend_degree,
                       feat_mean=np.zeros(len(features)), feat_std=np.ones(len(features)),
                       beta=np.zeros(1), resid_std=0.0, wb_median=wb_median)
    if features:
        X_feats = model._with_derived(train)[features].to_numpy().astype(float)
        mean, std = X_feats.mean(axis=0), X_feats.std(axis=0)
        std[std == 0] = 1.0
        model.feat_mean, model.feat_std = mean, std
    X = model._design(train)
    y = train["yield_t_ha"].to_numpy().astype(float)

    # Szelektív ridge: csak az időjárási blokk kap büntetést
    k_unpen = len(counties) + trend_degree
    D = np.zeros(X.shape[1])
    D[k_unpen:] = ridge_alpha
    beta = np.linalg.solve(X.T @ X + np.diag(D), X.T @ y)

    model.beta = beta
    model.resid_std = float(np.std(y - X @ beta, ddof=X.shape[1]))
    return model


# ---------------------------------------------------------------------------- #
# Naiv alapmodellek a mérési kapuhoz (brief: vármegye-trend / előző 3 év átlaga)
# ---------------------------------------------------------------------------- #
def predict_naive_trend(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Naiv 1: vármegye-fixhatás + közös lineáris trend, időjárás NÉLKÜL."""
    m = fit_panel_model(train, features=[], trend_degree=1, ridge_alpha=0.0)
    return m.predict(test)


def predict_naive_last3(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Naiv 2: a vármegye előző 3 (elérhető) évének átlaga."""
    out = np.full(len(test), np.nan)
    for i, (c, y) in enumerate(zip(test["nuts_id"], test["crop_year"])):
        hist = train[(train["nuts_id"] == c) & (train["crop_year"] < y)]
        last3 = hist.sort_values("crop_year").tail(3)["yield_t_ha"]
        if len(last3):
            out[i] = last3.mean()
        else:  # év eleji évek: nincs korábbi adat -> vármegye-átlag a tanítóból
            out[i] = train[train["nuts_id"] == c]["yield_t_ha"].mean()
    return out


def load_model_data(crop: str = config.DEFAULT_CROP) -> pd.DataFrame:
    """Panel + feature-ök összekötve, Budapest kihagyva (config.BUDAPEST_HANDLING)."""
    panel = pd.read_parquet(config.panel_parquet(crop))
    feats = pd.read_parquet(config.features_parquet(crop))
    df = panel.merge(feats, on=["nuts_id", "crop_year"], how="inner")
    if config.BUDAPEST_HANDLING == "drop":
        df = df[df["nuts_id"] != config.BUDAPEST_NUTS_ID]
    return df.dropna(subset=["yield_t_ha"]).reset_index(drop=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", choices=list(config.CROPS), default=config.DEFAULT_CROP)
    crop = ap.parse_args().crop
    df = load_model_data(crop)
    m = fit_panel_model(df, crop=crop)
    print(f"Panelmodell ({config.CROPS[crop]['label']}): {len(df)} megfigyelés, "
          f"{len(m.counties)} vármegye, trend fok {m.trend_degree}, "
          f"ridge alpha {config.RIDGE_ALPHA}")
    print("Időjárási együtthatók (standardizált, t/ha):")
    print(m.weather_coefs.round(3).to_string())
    print(f"In-sample reziduum szórás: {m.resid_std:.3f} t/ha")
