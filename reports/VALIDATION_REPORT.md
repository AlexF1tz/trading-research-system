# Independent Model Validation and Red-Team Report

Audit date: 16 July 2026  
Scope: synthetic chronological modelling engineering fixture  
Disposition: **REJECT ALL MODELS FOR PROMOTION OR PERFORMANCE CLAIMS**

## Executive conclusion

The evaluation report is arithmetically reproducible: an independent implementation matched all 2,247 checked scalar, calibration-bin, ranking, bootstrap, regression, and subgroup values within `1.1e-6`. The repository also uses explicit chronological partitions and expanding walk-forward folds, and the supplied fixture has no detected row-level feature/outcome look-ahead violation.

Those passes do not validate a trading result. All 36 model/target combinations are rejected. The data are synthetic, the universe is not survivorship safe, primary announcement times cannot be independently corroborated, raw revisions cannot be distinguished, and realistic low-float fills and trading halts cannot be reproduced. No profitability claim is made.

## Findings

| Check | Result | Consequence |
| --- | --- | --- |
| Independent metric reproduction | Pass | 2,247/2,247 values match; report arithmetic is reproducible |
| Row-level look-ahead controls | Pass for fixture | Upstream source timestamp truth remains unverified |
| Chronological split/random-split scan | Pass | Final-test reuse across prior research iterations remains uncontrolled |
| Survivorship-safe universe | Fail, blocker | Inactive/delisted security coverage is absent |
| First-public announcement time | Not verifiable, blocker | Fixture clocks cannot corroborate real primary-source availability |
| Original/revised data lineage | Fail, blocker | Model rows lack raw response hash, revision ID, and as-of snapshot ID |
| Declared spread/slippage arithmetic | Pass | Inputs are internally reconciled but not empirically sourced |
| Per-model cost stress | Fail for multiple pairs | Failing pairs are explicitly rejected; passing fixture scenarios confer no clearance |
| Fill/capacity realism | Fail, blocker | No quote size, participation, latency, capacity, or same-bar policy evidence |
| Trading halts/reopenings | Fail, blocker | No halt intervals, reopening prints, or gap-through-stop evidence |
| Candidate/rejection ledger | Fail | Selection bias cannot be ruled out |
| Catalyst discovery completeness | Fail | Cherry-picking cannot be ruled out |
| Social duplication lineage | Not verifiable | Aggregate attention stage loses post-level audit evidence |
| Repeated final-test access | Fail | No immutable holdout access registry or hypothesis registration exists |
| One-security dependence | Warning | Fixture top-10 share is 20%; real-market diversification is untested |
| Empirical eligibility | Fail, blocker | Synthetic output is engineering evidence only |

## Required work before revalidation

1. Add a point-in-time security master with inactive and delisted listings.
2. Preserve content-addressed raw source objects, original/revision lineage, source-native IDs, and conservative availability timestamps.
3. Link every model row to complete candidate, rejection, catalyst-discovery, and attention-deduplication manifests.
4. Produce path labels through a halt-aware, quote/bar-resolution-aware backtester with participation limits, latency, capacity, conservative ambiguous-bar handling, and gap-through-stop fills.
5. Register the hypothesis and code/config/data hashes, lock a new chronological final period, and record each access immutably.
6. Rerun independent metrics, per-model cost stress, security/event concentration, calibration, and uncertainty checks on the locked real sample.

No model should be described as profitable or promoted to live shadow performance tracking until these gates pass. The system must remain decision support only and must never execute a trade.
