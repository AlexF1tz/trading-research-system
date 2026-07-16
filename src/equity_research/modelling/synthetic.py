"""Deterministic modelling fixtures; no row is a real market observation."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .contracts import FillStatus, ModelDataset, ModelRow


FEATURE_NAMES = (
    "gap_pct",
    "relative_volume",
    "float_rotation",
    "momentum_5m_pct",
    "distance_from_vwap_pct",
    "catalyst_materiality",
    "dilution_risk_score",
    "attention_acceleration",
    "independent_author_score",
    "promotional_score",
    "spread_pct",
    "realised_volatility",
    "unstable_sentiment_proxy",
)


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _business_days(start: datetime, end: datetime) -> tuple[datetime, ...]:
    values: list[datetime] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def _gap_category(value: float) -> str:
    if value < 0:
        return "negative"
    if value < 5:
        return "zero_to_5"
    if value < 10:
        return "5_to_10"
    return "10_plus"


def _rvol_category(value: float) -> str:
    if value < 1:
        return "below_1"
    if value < 2:
        return "1_to_2"
    if value < 5:
        return "2_to_5"
    return "5_plus"


class SyntheticModelFixtureProvider:
    """Generate a regime-shifting dataset solely for engineering verification."""

    @property
    def name(self) -> str:
        return "synthetic_modelling_fixture"

    def load(self) -> ModelDataset:
        generator = random.Random(20260716)
        dates = _business_days(
            datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
        )
        catalysts = (
            "earnings_and_guidance",
            "contracts_and_purchase_orders",
            "fda_or_clinical_events",
            "offerings_atm_and_dilution",
            "partnerships",
        )
        float_categories = ("under_10m", "10m_to_50m", "over_50m", "unavailable")
        market_caps = ("micro", "small", "mid")
        regimes = ("risk_on", "neutral", "risk_off")
        times = ("premarket", "opening_30m", "midday", "afternoon")
        stages = ("quiet", "early", "expanding", "crowded", "collapsing")
        rows: list[ModelRow] = []
        for index, prediction_at in enumerate(dates):
            catalyst = catalysts[index % len(catalysts)]
            float_category = float_categories[(index // 3) % len(float_categories)]
            market_cap = market_caps[(index // 5) % len(market_caps)]
            regime = regimes[(index // 17) % len(regimes)]
            time_of_day = times[index % len(times)]
            attention_stage = stages[(index // 4) % len(stages)]

            gap = _clip(generator.gauss(5.0, 8.0), -18.0, 32.0)
            relative_volume = _clip(generator.lognormvariate(0.35, 0.65), 0.2, 8.0)
            float_rotation = _clip(generator.lognormvariate(-1.0, 0.8), 0.01, 4.0)
            momentum = _clip(generator.gauss(1.0, 4.5), -12.0, 15.0)
            distance_vwap = _clip(generator.gauss(1.0, 4.0), -10.0, 14.0)
            materiality = _clip(generator.gauss(57.0, 23.0), 0.0, 100.0)
            dilution = _clip(
                generator.gauss(
                    70.0 if catalyst == "offerings_atm_and_dilution" else 28.0,
                    20.0,
                ),
                0.0,
                100.0,
            )
            attention_acceleration = _clip(generator.gauss(18.0, 42.0), -80.0, 150.0)
            independent = _clip(generator.gauss(58.0, 25.0), 0.0, 100.0)
            promotional = _clip(
                generator.gauss(65.0 if attention_stage == "crowded" else 22.0, 22.0),
                0.0,
                100.0,
            )
            spread = _clip(
                generator.lognormvariate(-0.4, 0.65)
                + (0.7 if float_category == "under_10m" else 0.0),
                0.05,
                4.5,
            )
            volatility = _clip(generator.gauss(5.5, 2.8), 0.5, 16.0)
            unstable = generator.gauss(0.0, 1.0)
            regime_effect = {"risk_on": 0.35, "neutral": 0.0, "risk_off": -0.35}[
                regime
            ]
            if prediction_at < datetime(2026, 2, 20, tzinfo=timezone.utc):
                unstable_coefficient = 0.75
            elif prediction_at < datetime(2026, 4, 1, tzinfo=timezone.utc):
                unstable_coefficient = -0.75
            else:
                unstable_coefficient = 0.0
            latent = (
                0.30 * gap / 10.0
                + 0.48 * (relative_volume - 1.0)
                + 0.35 * momentum / 5.0
                + 0.50 * (materiality - 50.0) / 50.0
                + 0.22 * attention_acceleration / 50.0
                + 0.18 * (independent - 50.0) / 50.0
                - 0.48 * promotional / 100.0
                - 0.60 * dilution / 100.0
                - 0.28 * spread / 3.0
                + regime_effect
                + unstable_coefficient * unstable
            )
            clinical_jump = (
                7.5
                if catalyst == "fda_or_clinical_events" and materiality > 75
                else 0.0
            )
            mfe = round(
                max(0.0, 4.5 + 3.2 * latent + clinical_jump + generator.gauss(0, 5.0)),
                6,
            )
            mae = round(
                -max(0.0, 3.5 - 1.8 * latent + generator.gauss(0, 3.0)),
                6,
            )
            touch_up_05 = mfe >= 5.0
            touch_up_10 = mfe >= 10.0
            touch_up_20 = mfe >= 20.0
            touch_down_05 = mae <= -5.0
            touch_down_10 = mae <= -10.0
            target_before_stop = touch_up_10 and (
                not touch_down_05 or generator.random() > 0.45
            )
            continuation = latent + generator.gauss(0.0, 0.9) > 0.15
            continuation_value = None if index % 23 == 0 else continuation

            unfilled = index % 19 == 0 or spread > 4.0
            fill_status = FillStatus.UNFILLED if unfilled else FillStatus.FILLED
            if target_before_stop:
                gross = min(13.0, max(4.0, mfe * 0.72))
            elif touch_down_05:
                gross = max(-13.0, mae * 0.82)
            elif continuation:
                gross = 1.5 + latent
            else:
                gross = -1.2 - 0.45 * abs(latent)
            spread_cost = round(spread, 8)
            slippage_cost = round(
                0.15
                + 0.25 * spread
                + (0.30 if float_category == "under_10m" else 0.0),
                8,
            )
            gross_value = None if unfilled else round(gross, 8)
            net_value = (
                None
                if unfilled
                else gross_value - spread_cost - slippage_cost  # type: ignore[operator]
            )
            feature_values: dict[str, float | None] = {
                "gap_pct": gap,
                "relative_volume": relative_volume,
                "float_rotation": (
                    None if float_category == "unavailable" else float_rotation
                ),
                "momentum_5m_pct": momentum,
                "distance_from_vwap_pct": distance_vwap,
                "catalyst_materiality": materiality,
                "dilution_risk_score": dilution,
                "attention_acceleration": (
                    None if index % 13 == 0 else attention_acceleration
                ),
                "independent_author_score": (
                    None if index % 11 == 0 else independent
                ),
                "promotional_score": None if index % 17 == 0 else promotional,
                "spread_pct": spread,
                "realised_volatility": volatility,
                "unstable_sentiment_proxy": unstable,
            }
            missing_count = sum(value is None for value in feature_values.values())
            rows.append(
                ModelRow(
                    observation_id=f"fixture-observation-{index:04d}",
                    event_id=f"fixture-event-{index // 2:04d}",
                    security_id=f"FIXTURE-SEC-{index % 12:02d}",
                    ticker=f"FX{index % 12:02d}",
                    prediction_as_of=prediction_at,
                    features_available_at=prediction_at,
                    outcome_available_at=prediction_at + timedelta(days=1),
                    source_url=f"fixture://modelling/observation/{index:04d}",
                    features=tuple(
                        (name, feature_values[name]) for name in FEATURE_NAMES
                    ),
                    target_before_stop=target_before_stop,
                    touch_up_05=touch_up_05,
                    touch_up_10=touch_up_10,
                    touch_up_20=touch_up_20,
                    touch_down_05=touch_down_05,
                    touch_down_10=touch_down_10,
                    mfe_pct=mfe,
                    mae_pct=mae,
                    continuation=continuation_value,
                    fill_status=fill_status,
                    gross_return_pct=gross_value,
                    spread_cost_pct=spread_cost,
                    slippage_cost_pct=slippage_cost,
                    net_return_after_cost_pct=net_value,
                    catalyst_category=catalyst,
                    float_category=float_category,
                    market_cap_category=market_cap,
                    market_regime=regime,
                    time_of_day=time_of_day,
                    gap_size_category=_gap_category(gap),
                    relative_volume_category=_rvol_category(relative_volume),
                    retail_attention_stage=attention_stage,
                    data_quality_score=max(60.0, 96.0 - 9.0 * missing_count),
                    label_policy_version="fixture-path-label-v1",
                    fill_policy_version="fixture-costed-fill-v1",
                )
            )
        return ModelDataset(
            provider=self.name,
            dataset_kind="synthetic_engineering_fixture_not_market_history",
            fetched_at=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            feature_names=FEATURE_NAMES,
            rows=tuple(rows),
            target_barrier_pct=10.0,
            stop_barrier_pct=-5.0,
            universe_survivorship_safe=False,
            notes=(
                "SYNTHETIC_FIXTURE: generated labels, prices, costs, and categories are not observations",
                "unstable_sentiment_proxy intentionally changes relationship inside training to exercise stability diagnostics",
                "limited universe flag is deliberate and cannot support survivorship-safe or profitability claims",
            ),
        )
