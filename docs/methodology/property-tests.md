# Property-based tests — Black-Scholes invariants

> Companion document to [research-paper.md §2.5](research-paper.md).
> Source: [`backend/scripts/validation/property_tests/`](../../backend/scripts/validation/property_tests/)

## What this is

Eleven universal Black-Scholes properties checked against the scipy reference (`bs_reference.py`) using `hypothesis` to generate random inputs.

This is **stronger than parity testing**:
- Parity (108-day batch in [parity-9day.md](parity-9day.md)) says "two implementations agree".
- Properties say "**any correct implementation must satisfy this theorem**".

If a property fails, either the reference is wrong, the test scope is too broad (theorem doesn't actually apply), or both. Failures here trip a bug **regardless of which side** of the parity comparison was right.

## Configuration

```python
SETTINGS = settings(max_examples=200, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow,
                                           HealthCheck.filter_too_much])
```

- **n=200 random examples per property** (deterministic via hypothesis seeds when failures occur)
- `deadline=None` so slow scipy.brentq solves don't kill tests
- Spot range `[100, 10000]` covers SPX/NDX scale
- Moneyness `[0.5, 2.0]` for general properties; tighter ranges for sign-sensitive properties (see notes below)
- Rates `[0, 8%]`, dividends `[0, 5%]`, sigmas `[5%, 200%]`

## Properties tested

| # | Property | Formula | Status |
|---|---|---|---|
| 1 | Put-call parity | `C − P = S·e^(−qT) − K·e^(−rT)` | **PASS** |
| 2 | Gamma symmetry | `Γ_call(K) = Γ_put(K)` | **PASS** |
| 3 | Vega symmetry | `ν_call(K) = ν_put(K)` | **PASS** |
| 4a | Theta sign (OTM call) | `Θ < 0` for `K ≥ S` | **PASS** |
| 4b | Theta sign (OTM put) | `Θ < 0` for `K ≤ S` | **PASS** |
| 5 | Delta bounds | `Δ_c ∈ [0, e^(−qT)]`, `Δ_p ∈ [−e^(−qT), 0]` | **PASS** |
| 6 | Gamma positive | `Γ > 0` | **PASS** |
| 7 | Vega positive | `ν > 0` for `T > 0` | **PASS** |
| 8 | IV round-trip | `BS(IV(price)) ≈ price` within solver tol | **PASS** |
| 9 | Vega monotone in T (ATM) | `dν/dT > 0` for `T < 4/σ²` at `S = K` | **PASS** |
| 10 | Real-data smile pass rate | per `test_real_data.py` | **PASS** |

**Verdict: 11 of 11 properties hold.**

## Scope notes (where theorems were tightened)

Some BS theorems are universal in textbooks (Hull §13, §15) but only hold within specific regimes once you account for European-option specifics or float64 precision. Where applicable, test ranges were tightened:

### Theta sign — OTM only

For European options, **theta is not universally negative**:
- ITM puts at high `r` can have `Θ > 0` because the strike payoff `K·e^(−rT)` appreciates as T shrinks.
- ITM calls at high `q` can have `Θ > 0` because the spot payoff `S·e^(−qT)` appreciates as T shrinks.

This is documented in Hull §15.6 — not a bug. Test scope is restricted to OTM regions where `Θ < 0` is universal:
- OTM call: moneyness `m ∈ [1.00, 1.15]`
- OTM put: moneyness `m ∈ [0.85, 1.00]`
- T ∈ `[7d, 0.25y]`, σ ∈ `[10%, 100%]`

### Greeks positivity — representable regime

Gamma and vega are mathematically positive, but at extreme moneyness × short T × low σ, `φ(d1)` underflows float64 below ~1e-300. The property is true but unmeasurable in this corner. Bounded to representable cases:
- Moneyness `m ∈ [0.7, 1.5]`
- T `∈ [7d, 2y]`
- σ `∈ [10%, 200%]`

### IV round-trip — solver bracket coverage

The Brent bracket is `[1e-3, 5.0]`. If a generated price corresponds to σ outside this range, `implied_vol_one` returns NaN and the test skips (not a failure). Otherwise: `BS(IV(price))` must equal the original price within `1e-4·max(price, 1) + 1e-5`.

### Vega monotone in T — ATM with r = q = 0

`dν/dT` flips sign at `T = 4/σ²` for OTM/ITM strikes. The universal monotone-up claim only holds at-the-money with no carry. Test restricted to:
- `S = K` (ATM)
- `r = q = 0`
- `T ∈ [1d, 0.5y]`

These restrictions don't weaken the validation — they just ensure the test asserts a property that's actually theorematic.

## Real-data smile property

`test_real_data.py` loads a sample of `iv_ref_*.csv` files from the parity batch outputs, then checks that across all snapshots, the global proportion of strikes obeying the typical smile shape (deep-OTM put IV ≥ ATM IV ≥ deep-OTM call IV, allowing 5% slack) is ≥ 80%.

This is qualitative ("the smile generally looks right"), not a per-strike assertion. 0DTE markets routinely show call-wing skew on rally days that breaks strict OTM-put-dominant ordering — so the test is designed to flag system-wide breakage, not catch every individual inversion.

## Reproducibility

```bash
cd backend/scripts/validation
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest property_tests/ -v
```

Expected output: `11 passed in ~15s`.

If hypothesis flags a falsifying example, the test output prints exact reproduction parameters (e.g. `Falsifying example: test_X(s=100.0, m=1.0625, ...)`). Re-run with those exact values to investigate.

## What this does not test

- **Numerical edge cases at extreme moneyness** (`|moneyness| > 1.15`) — those are excluded from theta-sign and Greeks-positivity scopes by design.
- **Vanna sign / vanna monotonicity** — vanna's sign depends on whether option is OTM or ITM; no clean universal theorem to assert. Vanna is also intentionally excluded from the parity batch (see [parity-9day.md](parity-9day.md)).
- **Greek consistency under finite-difference perturbation** — theoretically Δ should match `(P(S+h) − P(S−h)) / 2h` for small h. This adds noise from second-order terms; not currently tested.

These pendings are tracked in [research-paper.md §4](research-paper.md).
