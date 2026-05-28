"""Pydantic schemas for request/response payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing_extensions import TypeVar

from app.config import get_settings

# ── Auth ────────────────────────────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    # Cap field lengths so a malicious caller can't ship megabytes of JSON
    # that we'd parse before bcrypt truncates the password to 72 bytes.
    # The /admin/login route is rate-limited to 5/min/IP — combined with
    # these caps the memory-DoS vector is closed.
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


# ── API key management ──────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    allowed_symbols: list[str]
    expires_at: datetime | None = None

    @field_validator("allowed_symbols")
    @classmethod
    def _normalize_symbols(cls, v: list[str]) -> list[str]:
        # Normalise + reject anything outside ``SUPPORTED_SYMBOLS`` (Rev 8
        # SEC-12). Storing an ACL entry that the pipeline doesn't compute
        # for is silently broken — every request against that symbol
        # will 401 with "unauthorized" because no per-symbol data
        # exists, but the admin UI shows the key as "configured for
        # FOO". Validate at intake instead.
        normalised = [s.strip().upper() for s in v if s.strip()]
        supported = {s.upper() for s in get_settings().supported_symbols}
        unknown = [s for s in normalised if s not in supported]
        if unknown:
            raise ValueError(
                "allowed_symbols contains unsupported symbols: "
                f"{sorted(unknown)} (allowed: {sorted(supported)})"
            )
        return normalised


class ApiKeyUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    allowed_symbols: list[str] | None = None
    expires_at: datetime | None = None
    is_active: bool | None = None

    @field_validator("allowed_symbols")
    @classmethod
    def _normalize_symbols(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        normalised = [s.strip().upper() for s in v if s.strip()]
        supported = {s.upper() for s in get_settings().supported_symbols}
        unknown = [s for s in normalised if s not in supported]
        if unknown:
            raise ValueError(
                "allowed_symbols contains unsupported symbols: "
                f"{sorted(unknown)} (allowed: {sorted(supported)})"
            )
        return normalised


class ApiKeySummary(BaseModel):
    id: UUID
    key_prefix: str
    label: str
    allowed_symbols: list[str]
    created_at: datetime
    expires_at: datetime | None
    is_active: bool
    last_used_at: datetime | None
    usage_count: int


class ApiKeyCreateResponse(BaseModel):
    key: ApiKeySummary
    plaintext_key: str = Field(
        description="Plaintext API key. Shown ONCE — store it securely."
    )


# ── Data endpoint envelopes ─────────────────────────────────────────────────

# TypeVar with a default lets us keep ``DataEnvelope(...)`` working as a
# concrete dict envelope while ALSO supporting parametric ``DataEnvelope[T]``
# typed responses for the data endpoints.
T = TypeVar("T", default=Any)


class DataEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    computed_at: datetime | None
    next_update_in_seconds: int
    data: T


# ── Typed data payloads ─────────────────────────────────────────────────────

class GexResponse(BaseModel):
    # ``extra="allow"`` is intentional — the GEX emitter forwards the full
    # ``extra_json`` from ``ComputedMetric`` rows (provenance fields
    # like ``regime_label``) and we don't want to drop them. Explicit
    # field declarations exist so OpenAPI codegen sees the actually-emitted
    # payload (Rev 9 CT-14, Rev 10 BC-7). Codegen consumers MUST configure
    # ``additionalProperties: true`` per ``API_POLICY.md`` § 2.
    model_config = ConfigDict(extra="allow")

    net_total: float = Field(
        description="Sum of per-strike net GEX (call_gex - |put_gex|) across the chain.",
    )
    curve: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-strike GEX entries (`strike`, `net_gex`, `call_gex`, `put_gex`).",
    )
    top_positive: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Top-N strikes with the largest positive net GEX (call-dominant).",
    )
    top_negative: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Top-N strikes with the largest negative net GEX (put-dominant).",
    )
    underlying_price: float | None = Field(
        default=None,
        description="Underlying spot used for the GEX computation.",
    )
    zero_gamma: float | None = Field(
        default=None,
        description="Zero-gamma flip strike (chain-vol-weighted by default).",
    )
    weight_source: str | None = Field(
        default=None,
        description=(
            "Provenance of the ranking weight — `oi`, `volume_fallback`, "
            "`premium_fallback`, or `uniform_fallback`."
        ),
    )
    weight_col: str | None = Field(
        default=None,
        description="Underlying weight column requested by the caller (`oi` or `volume`).",
    )


class MaxPainExpiryEntry(BaseModel):
    expiration: str
    strike: float
    pain: float


class MaxPainAggregate(BaseModel):
    strike: float
    value: float


class MaxPainResponse(BaseModel):
    per_expiry: list[MaxPainExpiryEntry] = Field(default_factory=list)
    aggregate: MaxPainAggregate | None = None


class WallEntry(BaseModel):
    # Permissive on purpose — older clients populate extra provenance keys
    # (``label``, ``oi``, ``volume``) on each entry. Rev 9 CT-14 / Rev 10
    # BC-7: surface the per-row ``weight_source`` so OpenAPI codegen sees
    # it.
    model_config = ConfigDict(extra="allow")

    rank: int = Field(
        description="1-based rank within the wall (1 = strongest).",
    )
    strike: float = Field(description="Wall strike.")
    value: float = Field(
        description="Wall magnitude in the active weight unit (OI contracts or volume contracts).",
    )
    weight_source: str | None = Field(
        default=None,
        description=(
            "Per-row provenance — `oi`, `volume_fallback`, `premium_fallback`, "
            "`uniform_fallback`."
        ),
    )


class WallsResponse(BaseModel):
    # ``extra="allow"`` retained for back-compat (legacy ``walls`` shape
    # also folds OI + volume into one envelope). Rev 9 CT-14 / Rev 10 BC-7:
    # declare ``weight_source_oi`` / ``weight_source_volume`` and the
    # full per-mode wall lists explicitly so the generated OpenAPI client
    # carries them.
    model_config = ConfigDict(extra="allow")

    call_wall_oi: list[WallEntry] = Field(
        default_factory=list,
        description="Top-N call walls ranked by Open Interest.",
    )
    put_wall_oi: list[WallEntry] = Field(
        default_factory=list,
        description="Top-N put walls ranked by Open Interest.",
    )
    call_wall_volume: list[WallEntry] = Field(
        default_factory=list,
        description="Top-N call walls ranked by today's traded volume.",
    )
    put_wall_volume: list[WallEntry] = Field(
        default_factory=list,
        description="Top-N put walls ranked by today's traded volume.",
    )
    weight_source_oi: str | None = Field(
        default=None,
        description="Provenance of the OI ranking — `oi` or one of the fallback labels.",
    )
    weight_source_volume: str | None = Field(
        default=None,
        description="Provenance of the volume ranking — `volume` or a fallback label.",
    )


class IvResponse(BaseModel):
    # Rev 9 CT-14 / Rev 10 BC-7: explicit ``skew_per_expiry`` + ``surface``
    # declarations so codegen lines up with what the emitter actually
    # returns. Keep ``extra="allow"`` so any future enrichment passes
    # through silently rather than 500-ing.
    model_config = ConfigDict(extra="allow")

    atm_iv: float | None = Field(
        default=None,
        description="At-the-money implied vol on the nearest non-0DTE expiry.",
    )
    skew: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "DEPRECATED (Rev 10 BC-8): use `skew_per_expiry`. Identical "
            "content; the legacy name is preserved for v1 back-compat and "
            "will be removed in /v2/. The endpoint emits `Deprecation:` and "
            "`Sunset:` headers when this field is populated."
        ),
    )
    skew_per_expiry: dict[str, float] = Field(
        default_factory=dict,
        description="Per-expiration ATM-skew (canonical name).",
    )
    surface: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Flattened IV surface — one entry per (expiration, strike, option_type).",
    )


# ── System status ───────────────────────────────────────────────────────────

class PipelineRunSummary(BaseModel):
    """Most recent pipeline run for one symbol."""

    symbol: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float = 0.0
    status: str = "unknown"
    rows_read: int = 0
    metric_rows_written: int = 0
    missing_metric_types: list[str] = Field(default_factory=list)
    error: str | None = None


class SystemStatus(BaseModel):
    """Operational telemetry returned by ``GET /admin/system/status``.

    Pre-existing fields (``rows_per_symbol``, ``active_api_keys``,
    ``last_compute_per_symbol`` ...) remain for backward compatibility;
    the Rev 3 fields below are additive.
    """

    pipeline_running: bool
    last_databento_event: datetime | None
    last_compute_per_symbol: dict[str, datetime | None]
    last_compute_duration_ms: dict[str, float]
    rows_per_symbol: dict[str, int]
    metric_rows_per_symbol: dict[str, int]
    active_api_keys: int

    # ── Rev 3 operational telemetry ─────────────────────────────────────────
    futures_lag_ms: float | None = None
    """`now() - max(futures_ticks.ts)` in milliseconds, or null if no rows."""
    opra_lag_ms: float | None = None
    """`now() - max(options_chain.ts)` in milliseconds, or null if no rows."""
    dlq_pending: int = 0
    """`dead_letter_queue` row count."""
    flow_events_last_hour: int = 0
    """Count of ``flow_events`` rows inserted in the last 1 hour."""
    last_pipeline_runs: list[PipelineRunSummary] = Field(default_factory=list)
    """Last row per symbol from ``pipeline_runs``."""
    live_ingester: dict[str, Any] = Field(default_factory=dict)
    """Diagnostics from :meth:`DatabentoLiveIngester.diagnostics`."""


# ── DLQ inspector ───────────────────────────────────────────────────────────

class DlqEntry(BaseModel):
    id: UUID
    ts: datetime
    source: str
    reason: str
    payload: dict[str, Any] | None = None


class DlqPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DlqEntry] = Field(default_factory=list)


# ── Databento API key pool (Rev 4) ───────────────────────────────────────────


_DATASET_ALLOWED = {"OPRA.PILLAR", "GLBX.MDP3", "BOTH"}


class DatabentoKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    dataset: str
    api_key: str = Field(min_length=8, max_length=512)
    priority: int = Field(default=100, ge=0, le=10_000)
    is_active: bool = True

    @field_validator("dataset")
    @classmethod
    def _normalize_dataset(cls, v: str) -> str:
        s = v.strip().upper()
        if s not in _DATASET_ALLOWED:
            raise ValueError(
                f"dataset must be one of {sorted(_DATASET_ALLOWED)}, got {v!r}"
            )
        return s


class DatabentoKeyUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    priority: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None

    # We deliberately do NOT allow rotating the api_key/dataset via PATCH —
    # the operator should delete + re-create. This keeps the audit story
    # cleaner (one row = one secret).


class DatabentoKeySummary(BaseModel):
    id: int
    label: str
    dataset: str
    api_key_prefix: str
    priority: int
    is_active: bool
    last_used_at: datetime | None
    last_error_at: datetime | None
    last_error_msg: str | None
    error_count: int
    created_at: datetime


class DatabentoKeyTestResult(BaseModel):
    ok: bool
    message: str
    """Human-readable description of the test outcome."""


# ── Time-series history (Rev 13 FE-2) ───────────────────────────────────────


class HistoryPoint(BaseModel):
    """One bucketed time-series sample.

    ``ts`` is the bucket-end timestamp (inclusive). ``value`` is the last
    persisted ``computed_metrics.value`` for the (symbol, metric_type) within
    the bucket; null when the metric exists in the registry but no row
    landed in the bucket window. The ``last`` aggregation matches what the
    realtime stream emits — earlier samples in the bucket are superseded.
    """

    ts: datetime
    value: float | None = None


class HistoryResponse(BaseModel):
    """Response for ``GET /v1/{symbol}/history``.

    ``points`` is sorted ascending by ``ts``. Empty when no rows match
    (a metric_type that exists in the registry but has not been written
    yet, or a window prior to the configured retention horizon).
    """

    symbol: str
    metric: str
    interval_seconds: int
    since: datetime
    until: datetime
    points: list[HistoryPoint] = Field(default_factory=list)


# ── Admin JWT refresh (Rev 13 FE-3) ─────────────────────────────────────────


class RefreshTokenResponse(BaseModel):
    """Response for ``POST /admin/refresh-token``.

    Mirrors :class:`AdminLoginResponse` so consumers can swap the
    refresh result into the same auth-state slot they use on login.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
