"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Databento ────────────────────────────────────────────────────────────
    # ``DATABENTO_API_KEY`` is the legacy single-key fallback used when the
    # dataset-specific keys below are not set. New deployments should set
    # ``DATABENTO_API_KEY_OPRA`` (OPRA Pillar — options) and
    # ``DATABENTO_API_KEY_GLOBEX`` (GLBX.MDP3 — CME futures) explicitly so each
    # ingester authenticates with the correct subscription.
    databento_api_key: str = Field(default="", alias="DATABENTO_API_KEY")
    databento_api_key_opra: str = Field(default="", alias="DATABENTO_API_KEY_OPRA")
    databento_api_key_globex: str = Field(default="", alias="DATABENTO_API_KEY_GLOBEX")

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://options:options@db:5432/options_db",
        alias="DATABASE_URL",
    )
    # Connection-pool sizing for the async SQLAlchemy engine. Bumped from
    # 5/5 → 20/20 because the OPRA writer + 4 bulk writers + scheduler +
    # API handlers + WS streams contend for the same engine; the prior
    # 10-conn ceiling head-of-line-blocked the live ingest path behind
    # API reads. ``pool_pre_ping`` defaults to False — under high churn
    # (every flush opens a session) the per-checkout SELECT 1 was a real
    # round-trip cost; rely on ``pool_recycle`` instead. Operators behind
    # a flaky connection can re-enable it via ``DB_POOL_PRE_PING=true``.
    db_pool_size: int = Field(default=20, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_recycle_seconds: int = Field(default=3600, alias="DB_POOL_RECYCLE_SECONDS")
    db_pool_pre_ping: bool = Field(default=False, alias="DB_POOL_PRE_PING")

    # ── Admin auth ───────────────────────────────────────────────────────────
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="changeme", alias="ADMIN_PASSWORD")
    jwt_secret: str = Field(default="dev-only-change-me", alias="JWT_SECRET")
    # Default trimmed from 480 → 60 (Rev 8 SEC-2). Server-side revocation
    # via the ``jwt_revocations`` table closes the leak window completely
    # for ``/admin/logout``; the shorter TTL further bounds blast-radius
    # for tokens that leak before logout. Operators wanting longer-lived
    # tokens for headless tooling can override via env.
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")
    jwt_algorithm: str = "HS256"

    # ── Admin JWT refresh window (Rev 13 FE-3) ───────────────────────────────
    # ``POST /admin/refresh-token`` accepts the OLD token if it is either
    # still within its ``exp`` window OR expired within the last
    # ``jwt_refresh_grace_seconds`` seconds. The grace window absorbs
    # clock drift between client and server and lets a consumer that
    # was idle across the expiry boundary refresh without forcing a
    # re-login. Set to 0 to disable the grace path entirely (refresh
    # then requires a non-expired token).
    jwt_refresh_grace_seconds: int = Field(
        default=300, alias="JWT_REFRESH_GRACE_SECONDS", ge=0, le=3600
    )

    # ── DB-at-rest encryption ────────────────────────────────────────────────
    # Independent of ``JWT_SECRET`` so the admin can rotate the JWT signing
    # key without invalidating every encrypted Databento key in the pool.
    # Empty string falls back to ``JWT_SECRET`` for backwards compatibility
    # with deployments that pre-date this split — operators are expected to
    # set ``DB_ENCRYPTION_KEY`` explicitly and run the re-encryption job
    # before rotating ``JWT_SECRET``.
    db_encryption_key: str = Field(default="", alias="DB_ENCRYPTION_KEY")

    # ── Options config ───────────────────────────────────────────────────────
    supported_symbols_raw: str = Field(default="SPXW,NDXP", alias="SUPPORTED_SYMBOLS")
    risk_free_rate: float = Field(default=0.05, alias="RISK_FREE_RATE")
    data_retention_days: int = Field(default=7, alias="DATA_RETENTION_DAYS")
    compute_interval_seconds: int = Field(default=60, alias="COMPUTE_INTERVAL_SECONDS")
    historical_backfill_days: int = Field(default=7, alias="HISTORICAL_BACKFILL_DAYS")

    # ── Loader behavior ──────────────────────────────────────────────────────
    # Window (in hours) the chain loader scans for the latest snapshot per
    # contract. Tighter = less data scanned per pipeline tick. Default 6h
    # gives a comfortable safety margin: during RTH every liquid contract
    # updates sub-second, so anything older than 6h would be a contract
    # that hasn't traded all session — its OI is already covered by the
    # EOD-OI fallback table. Operators with extended-hours feeds may want
    # to leave at the legacy 2-day value.
    loader_snapshot_window_hours: int = Field(
        default=6, alias="LOADER_SNAPSHOT_WINDOW_HOURS"
    )

    # ── Ingestion behavior ───────────────────────────────────────────────────
    disable_live_ingestion: bool = Field(default=False, alias="DISABLE_LIVE_INGESTION")
    disable_historical_backfill: bool = Field(default=False, alias="DISABLE_HISTORICAL_BACKFILL")

    # ── Regime / processing thresholds ───────────────────────────────────────
    # Score threshold (absolute value) below which the regime is reported as
    # "neutral". Increase to add hysteresis around the zero-crossing and
    # prevent flickering when GEX_NET_TOTAL is small and noisy.
    gex_regime_threshold: float = Field(default=0.2, alias="GEX_REGIME_THRESHOLD")

    # ── Flow event detection thresholds (Agent 3) ────────────────────────────
    flow_sweep_min_premium: float = Field(
        default=50_000.0, alias="FLOW_SWEEP_MIN_PREMIUM"
    )
    """Minimum dollar premium (size × price × 100) for a multi-leg cluster
    to be flagged as a SWEEP. Sweeps are aggressive multi-venue prints."""

    flow_block_min_size: int = Field(default=100, alias="FLOW_BLOCK_MIN_SIZE")
    """Minimum single-print size (contracts) to be flagged as a BLOCK."""

    flow_uoa_vol_oi_ratio: float = Field(
        default=2.0, alias="FLOW_UOA_VOL_OI_RATIO"
    )
    """volume/OI ratio threshold for UOA classification when OI is known."""

    # ── Ingestion / DB write tuning (Agent 4 / 6) ────────────────────────────
    upsert_batch_size: int = Field(default=1000, alias="UPSERT_BATCH_SIZE")
    """Batch size used by ``BulkUpsertWriter`` / ``OptionsChainWriter``."""

    ingestion_max_pending_rows: int = Field(
        default=10_000, alias="INGESTION_MAX_PENDING_ROWS"
    )
    """Hard cap on rows in any single writer's pending buffer. Past this we
    log a WARNING and flush synchronously to apply backpressure."""

    ingestion_dlq_max_size: int = Field(
        default=1000, alias="INGESTION_DLQ_MAX_SIZE"
    )
    """Maximum dead-letter queue entries retained per ingester."""

    ingestion_registry_refresh_seconds: int = Field(
        default=4 * 60 * 60, alias="INGESTION_REGISTRY_REFRESH_SECONDS"
    )
    """How often the OPRA live ingester re-bootstraps its instrument registry
    to pick up new intraday contracts. Default 4 hours during RTH."""

    ingestion_quote_staleness_seconds: float = Field(
        default=5.0, alias="INGESTION_QUOTE_STALENESS_SECONDS"
    )
    """Maximum age of a cached NBBO quote before the inline quote-rule
    classifier in the OPRA trade path treats the book as stale and refuses
    to compute side / signed_premium. Tighter values reduce stale-book
    contamination at the cost of dropping classification on slow off-hours
    feeds.

    Related to but distinct from ``SPOT_STALE_CACHE_MAX_AGE_SECONDS``: this
    governs *trade-classification* freshness inside the ingester, while the
    spot knob governs *spot-resolution* freshness inside the pipeline. A
    stale quote drops a single trade's side; a stale spot can disable an
    entire pipeline tick's parity-fallback path.
    """

    # ── Live-ingester reconnect/recovery (REV8 OPS-1) ────────────────────────
    ingestion_max_reconnects: int = Field(
        default=30, alias="INGESTION_MAX_RECONNECTS"
    )
    """Maximum reconnect attempts before the live ingester gives up and
    enters the auto cold-restart loop. With a 300s backoff cap this gives
    roughly 30 minutes of routed-outage tolerance before a cold-restart
    is attempted."""

    ingestion_reconnect_max_backoff_seconds: float = Field(
        default=300.0, alias="INGESTION_RECONNECT_MAX_BACKOFF_SECONDS"
    )
    """Cap on the exponential backoff between reconnect attempts. Bumped
    from 60s to 300s so OPRA outages > 3min don't burn through the
    reconnect budget in a tight loop."""

    ingestion_terminal_reset_seconds: float = Field(
        default=600.0, alias="INGESTION_TERMINAL_RESET_SECONDS"
    )
    """Sleep before auto-resetting the live ingester after the reconnect
    budget is exhausted. The legacy ``reset_after_terminal()`` admin path
    is preserved for forced cycles; this knob controls the unattended
    recovery cadence."""

    ingestion_dlq_retention_days: int = Field(
        default=14, alias="INGESTION_DLQ_RETENTION_DAYS"
    )
    """Retention window for ``dead_letter_queue`` rows. Entries older than
    this are eligible for cleanup via ``cleanup_dlq_older_than``. The
    lifespan-side scheduling is wired by ``app.main._dlq_cleanup_loop``
    which runs the cleanup every 6 hours (Rev 9 DT-3)."""

    admin_audit_retention_days: int = Field(
        default=365, alias="ADMIN_AUDIT_RETENTION_DAYS"
    )
    """Retention window for ``admin_audit_events`` rows. Default 365 days
    — admin actions (key rotation, deletion) are typically retained for
    a full audit cycle, but the table grows monotonically and would
    otherwise need manual pruning. The lifespan-side scheduling is wired
    by ``app.main._admin_audit_prune_loop`` which runs daily (Rev 9
    DT-4)."""

    # ── Background loop intervals (Rev 12 SRE-24) ────────────────────────────
    # The two background lifespan loops below were module-level constants in
    # ``app.main`` (``_DLQ_CLEANUP_INTERVAL_S`` / ``_ADMIN_AUDIT_PRUNE_INTERVAL_S``)
    # — surfacing them as env-configurable Settings makes the cadence
    # operator-tunable without a code change. Defaults preserve the prior
    # constants. ``app.main`` should read these via ``get_settings()``;
    # REV12-MAIN: wire ``app.main._dlq_cleanup_loop`` and
    # ``app.main._admin_audit_prune_loop`` to read from settings instead of
    # the module constants.
    dlq_cleanup_interval_seconds: int = Field(
        default=6 * 60 * 60, alias="DLQ_CLEANUP_INTERVAL_SECONDS"
    )
    """How often the DLQ retention loop sweeps the ``dead_letter_queue``
    table. Default 6 hours. Tighten only if the DLQ grows unexpectedly."""

    admin_audit_prune_interval_seconds: int = Field(
        default=24 * 60 * 60, alias="ADMIN_AUDIT_PRUNE_INTERVAL_SECONDS"
    )
    """How often the admin-audit retention loop runs. Default 24 hours;
    audit rows are typically kept for ``ADMIN_AUDIT_RETENTION_DAYS``
    (default 365) so a daily prune is plenty."""

    futures_feed_lag_warn_ms: int = Field(
        default=5_000, alias="FUTURES_FEED_LAG_WARN_MS"
    )
    """Log a WARNING when the freshest futures tick is older than this."""

    # ── Streaming API (Agent 5) ──────────────────────────────────────────────
    max_ws_connections_per_key: int = Field(
        default=5, alias="MAX_WS_CONNECTIONS_PER_KEY"
    )
    """Cap on simultaneous WebSocket connections per API key."""

    ws_revocation_check_interval_seconds: float = Field(
        default=5.0, alias="WS_REVOCATION_CHECK_INTERVAL_SECONDS"
    )
    """How often the WS handlers re-poll ``api_keys`` to enforce mid-stream
    revocation. Default trimmed from 30s → 5s in Rev 8 (SEC-6) so a
    revoked key stops streaming within seconds. Trade-off: one DB
    round-trip per active connection per interval — paired with the
    Lane A single-watcher consolidation (ARCH-6) the load stays bounded.
    """

    # ── Request hardening (Rev 8 SEC-4) ──────────────────────────────────────
    max_request_body_bytes: int = Field(
        default=64 * 1024, alias="MAX_REQUEST_BODY_BYTES"
    )
    """Hard cap on request-body size enforced by ``BodySizeLimitMiddleware``.
    Returns 413 when exceeded. Default 64 KiB — every JSON-bodied route in
    this app (admin login, key creation, alert rules) is well under that.
    Bump only if a route legitimately needs a larger payload."""

    # ── Rev 4: RTH / 0DTE / spot resolver ────────────────────────────────────
    rth_open_time: str = Field(default="09:30", alias="RTH_OPEN_TIME")
    """RTH session open in America/New_York. Format ``HH:MM``."""

    rth_close_time: str = Field(default="16:15", alias="RTH_CLOSE_TIME")
    """RTH session close in America/New_York. SPX/NDX cash options stop
    trading at 16:00 ET; we keep a 15-minute buffer so the last pipeline
    tick still emits."""

    spot_parity_deviation_warn_pct: float = Field(
        default=0.5, alias="SPOT_PARITY_DEVIATION_WARN_PCT"
    )
    """Log a WARNING when the futures-basis spot vs. parity spot differ by
    more than this percent. Helps detect feed problems."""

    spot_stale_cache_max_age_seconds: float = Field(
        default=300.0, alias="SPOT_STALE_CACHE_MAX_AGE_SECONDS"
    )
    """Reject a stale-cache spot fallback older than this. Default 5 min.

    Related to but distinct from ``INGESTION_QUOTE_STALENESS_SECONDS``: this
    governs *spot-resolution* freshness inside the pipeline (the third
    fallback after futures-basis EMA and put-call parity), while the
    ingestion knob governs *trade-classification* freshness inside the
    OPRA trade path. A stale spot disables the spot-fallback chain;
    a stale quote drops a single trade's classification.
    """

    spot_basis_ema_alpha: float = Field(
        default=0.1, alias="SPOT_BASIS_EMA_ALPHA"
    )
    """Smoothing factor (0–1) for the cash-minus-futures basis EMA."""

    basis_ema_deviation_threshold: float = Field(
        default=0.005, alias="BASIS_EMA_DEVIATION_THRESHOLD"
    )
    """Maximum relative deviation (fraction) between an instantaneous
    cash-minus-futures basis observation and the prior EMA value before the
    update is rejected (DR-2). A single crossed/locked ATM pair can drive a
    wild ``parity_spot`` and contaminate the EMA for ~30 ticks (~30 min);
    rejecting outliers above 0.5% protects the smoother. The deviation is
    measured as ``|new_basis - prev_ema| / max(|prev_ema|, 1.0)``."""

    eod_oi_max_age_days: int = Field(
        default=3, alias="EOD_OI_MAX_AGE_DAYS"
    )
    """Maximum age (days) of an ``eod_open_interest`` row before the loader
    refuses to use it as the live-OI fallback (DR-13/14). Older rows are
    treated as missing and ``oi`` propagates as NaN — GEX-by-OI then falls
    through its existing weight-source chain (volume → premium → uniform)
    instead of pinning weights on multi-day-stale numbers. The merged OI's
    ``oi_date`` is surfaced in ``extra_json.eod_oi_age_days`` so consumers
    can inspect provenance."""

    last_price_max_age_seconds: int = Field(
        default=60, alias="LAST_PRICE_MAX_AGE_SECONDS"
    )
    """Maximum age (seconds) of a chain row's ``last_event_ts`` before the
    IV inversion path treats ``last_price`` as stale and refuses to use it
    as the inversion reference (DR-6). When the chain DataFrame doesn't
    carry a per-row last-price timestamp, ``_row_price`` only consults
    ``last_price`` if BOTH ``bid`` and ``ask`` are absent and ``last_price``
    is non-null and non-zero — the staleness gate then degrades to an
    age-unaware best-effort."""

    halt_threshold_seconds: int = Field(
        default=60, alias="HALT_THRESHOLD_SECONDS"
    )
    """Inter-trade gap (seconds) above which the Lee-Ready tick-rule
    treats the previous reference price as severed by a halt (DR-19).
    Without this guard the first post-halt print's tick rule references a
    stale pre-halt price; flipping the halt threshold severs the
    continuity so the first post-gap trade is left unclassified (or quote-
    classified if the NBBO is fresh)."""

    iv_lower_bound: float = Field(default=0.01, alias="IV_LOWER_BOUND")
    """Floor used when clipping inverted IV for non-0DTE rows. The Brent
    / Newton root-finders also bracket on this lower bound so any σ that
    would round below it is rejected and IV propagates as NaN. The
    historical default 0.01 reflects index-option practice — equity-index
    σ rarely rounds below 1 % annualised outside the close-to-expiry
    intrinsic regime, which is governed by ``IV_LOWER_BOUND_0DTE``."""

    iv_lower_bound_0dte: float = Field(
        default=0.005, alias="IV_LOWER_BOUND_0DTE"
    )
    """Floor used when clipping inverted IV for 0DTE rows (DR-22). Deep-
    ITM 0DTE prints can be intrinsic-only — their inverted σ legitimately
    rounds below the 0.01 non-0DTE floor and would otherwise be rejected,
    leaving the row IV-less and dropping it out of GEX / pin computations.
    BSM survives σ=0.005 at the 15-minute τ floor: σ√τ ≈ 2.7e-5 puts
    |d1| safely under the underflow horizon for ATM strikes (verified
    analytically in REVIEW_REV10 DR-22). Operators on instruments where
    intrinsic-only prints are not expected can lift this back to 0.01 by
    setting ``IV_LOWER_BOUND_0DTE=0.01``."""

    drop_zero_size_trades: bool = Field(
        default=True, alias="DROP_ZERO_SIZE_TRADES"
    )
    """When True (default), the OPRA live ingester drops trades with
    ``size <= 0`` at the entry of ``_handle_trade`` (DR-27). The drop is
    counted via ``dropped_zero_size_trades_total`` on the ingester
    diagnostics surface. ``size == 0`` rows are no-op semantically — they
    contribute nothing to volume, signed_premium or HIRO — but each still
    consumes a hypertable row and a sequence number. Set to False only if
    a particular publisher is observed to emit meaningful zero-size events
    (e.g. CANC corrections) that the pipeline should retain for audit."""

    enable_opra_auction_detection: bool = Field(
        default=False, alias="ENABLE_OPRA_AUCTION_DETECTION"
    )
    """Feature flag for ingest-side auction-print detection (DR-10).

    Auction prints (session open, halt resume) have no NBBO at trade time
    and the Lee-Ready quote rule has no meaningful sign for them. Standard
    DBN ``TradeMsg.flags`` for OPRA does not expose a documented
    ``is_auction`` bit at the time of writing — auction discrimination
    would require parsing OPRA sale-condition codes which the
    ``databento`` SDK does not surface. When this flag is True the
    ingester runs a best-effort heuristic (``flags`` bit inspection via
    ``hasattr``) and skips inline quote-rule classification on any
    detected auction print, leaving ``side=None`` and incrementing
    ``auction_prints_unclassified_total``. Default False because the
    heuristic is not validated end-to-end against a real OPRA auction
    fixture — see REVIEW_REV10 DR-10."""

    enable_opra_multileg_detection: bool = Field(
        default=False, alias="ENABLE_OPRA_MULTILEG_DETECTION"
    )
    """Feature flag for ingest-side multi-leg / spread-print detection
    (DR-11).

    OPRA tags multi-leg (combo) trades via sale-condition codes that are
    not surfaced by the standard DBN public API. Without per-leg
    unwinding, a spread print's ``signed_premium`` carries the leg-tagged
    net price instead of the true single-contract directional flow —
    including these in HIRO's delta-notional sum overstates dealer hedge
    pressure on the headline contract. When this flag is True the
    ingester runs a best-effort ``flags`` heuristic and increments
    ``multileg_prints_excluded_total`` for any detected combo print,
    suppressing inline classification (``side=None``,
    ``signed_premium=None``). Persistence of an ``is_multileg`` column to
    ``options_trades`` is deferred pending a confirmed SDK-surfaced flag
    (would require a migration). Default False — see REVIEW_REV10 DR-11
    for the documented limitation."""

    atm_band_pct_0dte: float = Field(default=0.005, alias="ATM_BAND_PCT_0DTE")
    """Half-width of the ATM band used by 0DTE charm-rate computation.
    0.005 ⇒ ±0.5% of spot (so a 10-pt window at SPX ≈ 5000)."""

    override_rth_gate: bool = Field(default=False, alias="OVERRIDE_RTH_GATE")
    """Dev/testing only — when true, the scheduler skips the RTH gate and
    runs the chain pipeline regardless of session state. Useful for
    smoke-testing the analytics off-hours when the chain is stale but
    still queryable. Never set in production."""

    # ── Misc ─────────────────────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=120, alias="RATE_LIMIT_PER_MINUTE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    trust_proxy_headers: bool = Field(default=False, alias="TRUST_PROXY_HEADERS")
    """When True, the rate limiter and audit logging treat the first
    ``X-Forwarded-For`` entry as the real client IP. Only enable this
    behind a trusted reverse proxy (Cloudflare, an in-cluster ingress)
    that strips client-supplied ``X-Forwarded-For`` headers — otherwise
    a client can spoof their IP and bypass per-IP rate limits."""

    admin_cors_origins: str = Field(
        default="http://localhost:3000", alias="ADMIN_CORS_ORIGINS"
    )
    """Comma-separated list of allowed origins for the admin dashboard.

    Set to a wildcard (``*``) only for local dev — production deployments
    should pin this to the exact origins that should be able to call
    the API.
    """

    enable_openapi_docs: bool = Field(
        default=True, alias="ENABLE_OPENAPI_DOCS"
    )
    """When False, FastAPI's ``/docs``, ``/redoc`` and ``/openapi.json``
    endpoints are disabled entirely. Recommended in production where
    the schema does not need to be publicly browsable."""

    # ── Rev 10 SRE-7: single-instance assertion ──────────────────────────────
    allow_multi_instance: bool = Field(
        default=False, alias="ALLOW_MULTI_INSTANCE"
    )
    """When True, the boot-time Postgres advisory-lock guard in
    ``app.main`` is bypassed and multiple replicas of the backend can
    run simultaneously. Default False — in-process state (deferred
    usage buffer, HIRO incremental cache, basis EMA, snapshot prime
    cache) is **not** consistent across replicas, so two backends
    talking to the same DB will silently produce divergent results.
    Set to True only for advanced operators who have externalised the
    relevant state (e.g. an L7 proxy that pins each WS connection to a
    single replica) and accept the operational consequences."""

    @field_validator("supported_symbols_raw")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def supported_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.supported_symbols_raw.split(",") if s.strip()]

    @property
    def opra_api_key(self) -> str:
        """API key used to authenticate against OPRA.PILLAR (options).

        Falls back to the legacy ``DATABENTO_API_KEY`` so existing single-key
        deployments keep working.
        """
        return self.databento_api_key_opra or self.databento_api_key

    @property
    def globex_api_key(self) -> str:
        """API key used to authenticate against GLBX.MDP3 (CME futures).

        Falls back to the legacy ``DATABENTO_API_KEY``.
        """
        return self.databento_api_key_globex or self.databento_api_key

    @property
    def admin_cors_origin_list(self) -> list[str]:
        return [
            o.strip()
            for o in (self.admin_cors_origins or "").split(",")
            if o.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
