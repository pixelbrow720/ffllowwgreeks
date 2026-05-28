"""SQLAlchemy ORM models for the options analytics platform."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    ARRAY,
    CHAR,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OptionsChain(Base):
    """Time-series options chain snapshots from the OPRA feed.

    Promoted to a TimescaleDB hypertable in the initial migration.
    """

    __tablename__ = "options_chain"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    expiration: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(20, 6), primary_key=True, nullable=False)
    option_type: Mapped[str] = mapped_column(CHAR(1), primary_key=True, nullable=False)

    oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    iv: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    delta: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    gamma: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    last_price: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    bid: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    ask: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    underlying_price: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)

    __table_args__ = (
        Index("ix_options_chain_symbol_ts", "symbol", "ts"),
        Index("ix_options_chain_symbol_expiry", "symbol", "expiration"),
    )


class ComputedMetric(Base):
    """Time-series storage for computed metrics (GEX, max pain, walls, IV, etc.).

    Promoted to a TimescaleDB hypertable in the initial migration.
    """

    __tablename__ = "computed_metrics"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    metric_type: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    strike: Mapped[float | None] = mapped_column(
        Numeric(20, 6), primary_key=True, nullable=False, default=0
    )
    expiration: Mapped[date] = mapped_column(
        Date, primary_key=True, nullable=False, default=date(1970, 1, 1)
    )

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    value: Mapped[float | None] = mapped_column(Numeric(30, 8), nullable=True)
    extra_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # ``ix_computed_metrics_symbol_type_ts`` (symbol, metric_type, ts) was
        # dropped: every "latest per (symbol, metric_type)" lookup also
        # filters/serves expiration through the wider index below. Keeping
        # both doubled write amplification on metric upserts (~36 metric_types
        # × dozens of strikes × every 60 s).
        Index(
            "ix_computed_metrics_symbol_type_exp_ts",
            "symbol",
            "metric_type",
            "expiration",
            text("ts DESC"),
        ),
        Index(
            "ix_computed_metrics_0dte",
            "symbol",
            text("ts DESC"),
            postgresql_where=text(
                "metric_type LIKE 'GEX_0DTE%' OR metric_type LIKE 'CHARM_0DTE%'"
            ),
            postgresql_using="btree",
        ),
    )


class EodOpenInterest(Base):
    """End-of-day Open Interest snapshots.

    OPRA Pillar's ``definition`` schema doesn't include open interest, so
    intraday OI is frequently missing on a fresh deployment. This table is
    populated by a daily ingestion job that pulls the most recent OI per
    contract (best-effort — falls back to "no rows" if the data source isn't
    available). The compute pipeline merges these snapshots back into the
    options chain whenever live OI is null/zero.

    REV11-LANE-M-followup: widen ``eod_open_interest`` PK to include
    ``oi_date`` so a back-dated historical row cannot lock out a fresher
    entry on UPSERT. The current PK ``(symbol, expiration, strike,
    option_type)`` collapses every observation date for the same contract
    onto a single row; combined with the freshness gate added in
    Lane D / DR-13/14, an older ``oi_date`` clobbering a newer one means
    the loader's freshness check sees stale data and refuses to use it
    (degrading to NaN OI rather than the actual current value). Migration
    work needed: add ``oi_date`` to PK and add an index on the lookup
    columns. Tracked for Lane M; Lane D handles the loader-side filtering
    until then.
    """

    __tablename__ = "eod_open_interest"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    expiration: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(20, 6), primary_key=True, nullable=False)
    option_type: Mapped[str] = mapped_column(CHAR(1), primary_key=True, nullable=False)

    oi_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_interest: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_eod_oi_symbol_expiry", "symbol", "expiration"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # Keyed BLAKE2b lookup digest (hex string). Lets the auth path probe
    # the row in O(1) instead of bcrypt-verifying every candidate that
    # shares the same 11-char ``key_prefix``. Nullable for backward
    # compatibility with rows issued before migration 0010 — the auth
    # path falls back to the prefix-scan when a row's ``key_lookup`` is
    # NULL and lazily backfills it on a successful verify.
    key_lookup: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_symbols: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # ── Rev 8 SEC-10 ─────────────────────────────────────────────────────
    # ``deleted_at`` records when an API key was soft-deleted via
    # ``DELETE /admin/api-keys/{id}``. The row stays around so
    # ``admin_audit_events`` rows that reference it remain readable. The
    # auth path treats any non-NULL ``deleted_at`` as inactive
    # (independent of ``is_active``).
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        UniqueConstraint("key_lookup", name="uq_api_keys_key_lookup"),
        Index("ix_api_keys_key_prefix", "key_prefix"),
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class JwtRevocation(Base):
    """Server-side JWT revocation list (Rev 8 SEC-2).

    Each successful ``POST /admin/logout`` inserts the current token's
    ``jti`` here; the admin auth dependency rejects any token whose
    ``jti`` is present. ``expires_at`` mirrors the token's ``exp`` claim
    so a periodic prune can drop rows that can no longer be presented.
    """

    __tablename__ = "jwt_revocations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    jti: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_jwt_revocations_expires_at", "expires_at"),
    )


class AdminAuditEvent(Base):
    """Admin-action audit trail (Rev 8 SEC-10).

    Capture-all log of admin-initiated operations so a deleted /
    rotated API key can be traced back to its actor. Today only
    ``api_key_deleted`` is emitted; any future destructive admin
    endpoint should append a row here.
    """

    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    actor_username: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    extra_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_admin_audit_events_ts", text("ts DESC")),
        Index("ix_admin_audit_events_action_ts", "action", text("ts DESC")),
    )


# ── Phase 2: futures + flow + alerts ────────────────────────────────────────


class FuturesTick(Base):
    """Globex MDP 3.0 futures trade tape.

    One row per trade event. Promoted to a TimescaleDB hypertable in
    migration 0003. Volume can balloon quickly (~5M ES trades / day); a
    short retention window is applied.
    """

    __tablename__ = "futures_ticks"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)

    price: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    aggressor: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    """+1 = buyer-aggressor, -1 = seller-aggressor, NULL = unknown."""
    bid: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    ask: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_futures_ticks_symbol_ts", "symbol", "ts"),
    )


class OptionsTrade(Base):
    """OPRA trade tape, classified via Lee-Ready downstream.

    One row per trade message. Promoted to a hypertable.
    """

    __tablename__ = "options_trades"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    expiration: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(20, 6), primary_key=True, nullable=False)
    option_type: Mapped[str] = mapped_column(CHAR(1), primary_key=True, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)

    price: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bid: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    ask: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    exchange: Mapped[str | None] = mapped_column(Text, nullable=True)
    side: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    """Lee-Ready customer side: +1 (buy), -1 (sell), 0 (unclassified)."""
    signed_premium: Mapped[float | None] = mapped_column(Numeric(30, 6), nullable=True)

    __table_args__ = (
        Index("ix_options_trades_symbol_ts", "symbol", "ts"),
        Index("ix_options_trades_contract", "symbol", "expiration", "strike", "option_type"),
    )


class FlowEvent(Base):
    """Detected sweeps / blocks / UOA. Persisted for the website + alerts."""

    __tablename__ = "flow_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    expiration: Mapped[date] = mapped_column(Date, nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    option_type: Mapped[str] = mapped_column(CHAR(1), nullable=False)

    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    """``SWEEP`` | ``BLOCK`` | ``UOA``."""
    side: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    price: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    legs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    venues: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_flow_events_symbol_ts_desc", "symbol", text("ts DESC")),
        Index("ix_flow_events_type_ts", "event_type", "ts"),
        # Created by migration 0007 for operator-driven retention deletes
        # (``DELETE FROM flow_events WHERE ts < NOW() - INTERVAL ...``).
        # Declared here so an autogenerate-diff doesn't propose dropping it.
        Index("ix_flow_events_ts_only", "ts"),
    )


class LiquiditySnapshot(Base):
    """Globex MDP 3.0 MBO order-book depth snapshot.

    Stored as compact JSONB rather than per-level rows: order books
    update tens of thousands of times per second on ES, and we only need
    point-in-time snapshots for analytics. Snapshot frequency is
    configured by the ingester (default 1 / second).
    """

    __tablename__ = "liquidity_snapshots"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)

    bids: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    """``[{price, size, orders}, ...]`` highest bid first."""
    asks: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    """``[{price, size, orders}, ...]`` lowest ask first."""
    depth_levels: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    __table_args__ = (
        Index("ix_liquidity_snapshots_symbol_ts", "symbol", "ts"),
    )


class AlertRule(Base):
    """User-defined alert rule expressed as a JSON predicate tree."""

    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[dict] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="info")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("name", "symbol", name="uq_alert_rules_name_symbol"),
    )


class AlertEvent(Base):
    """An alert firing produced by an AlertRule on a specific snapshot."""

    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    matched: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_alert_events_symbol_ts", "symbol", "ts"),
        Index("ix_alert_events_rule_ts", "rule_id", "ts"),
    )


# ── Rev 3: operational telemetry / ingestion safety net ─────────────────────


class PipelineRun(Base):
    """One row per scheduler tick per symbol — runtime audit log.

    Used by :func:`app.api.endpoints.admin.system_status` to answer
    "did the last cycle complete cleanly and produce all 25+ metric
    types?" without re-deriving from raw ``computed_metrics``.

    Rev 4 adds four extra columns describing the 0DTE / spot state at
    run time (``is_expiration_day``, ``spot_source``, ``spot_price``,
    ``tau_0dte_years``) so operators can correlate a partial run with
    "we lost the futures feed" vs. "the chain was empty".
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    """``running`` | ``ok`` | ``partial`` | ``failed`` | ``session_open`` | ``session_close``."""
    rows_read: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    metric_rows_written: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    missing_metric_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Rev 4 additions ──────────────────────────────────────────────
    is_expiration_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    spot_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    """``futures_basis`` | ``parity`` | ``stale_cache`` | None."""
    spot_price: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    tau_0dte_years: Mapped[float | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_pipeline_runs_symbol_started",
            "symbol",
            text("started_at DESC"),
        ),
    )


class SessionEvent(Base):
    """Lightweight audit log of session open / close / reset events.

    Tiny table (2–4 rows per day per symbol). Not a hypertable.
    """

    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    """``session_open`` | ``session_close`` | ``reset`` | ``partial_open``."""
    symbol: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    extra_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_session_events_ts", text("ts DESC")),
    )


class MetricTypeRegistry(Base):
    """Catalogue of every ``metric_type`` discriminator the platform writes.

    Reference table used by the admin UI / docs — not by hot paths.
    """

    __tablename__ = "metric_type_registry"

    metric_type: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_0dte: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    added_in_rev: Mapped[str | None] = mapped_column(Text, nullable=True)


class DatabentoApiKey(Base):
    """Pool of fallback Databento API keys, per dataset.

    Encrypted at rest with Fernet (key derived from ``DB_ENCRYPTION_KEY``;
    falls back to ``JWT_SECRET`` only for legacy pre-Rev-5 deployments
    that haven't set ``DB_ENCRYPTION_KEY`` explicitly). Rotating
    ``DB_ENCRYPTION_KEY`` invalidates every encrypted key in this pool —
    operators must re-register them through the admin API.

    The ingester resolves the key list ordered by priority ASC for the
    relevant dataset and fails over on auth / connect errors.
    """

    __tablename__ = "databento_api_keys"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    label: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    dataset: Mapped[str] = mapped_column(Text, nullable=False)
    """``OPRA.PILLAR`` | ``GLBX.MDP3`` | ``BOTH``."""
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    """First ~8 characters of the plaintext key, used purely for admin
    UI identification. The full plaintext lives only in
    ``api_key_encrypted``."""

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_test_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_databento_keys_dataset_priority", "dataset", "priority"),
    )


class DeadLetterEntry(Base):
    """Records ingestion payloads we could not parse / write.

    Surfaced via /admin/inspector so operators can diagnose feed issues
    without trawling logs.
    """

    __tablename__ = "dead_letter_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    """``opra_live`` | ``opra_historical`` | ``globex_live`` | ``eod_oi`` | ``pipeline``."""
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_dlq_source_ts", "source", text("ts DESC")),
        # Created by migration 0007 for operator-driven retention deletes
        # (``DELETE FROM dead_letter_queue WHERE ts < NOW() - INTERVAL ...``).
        # Declared here so an autogenerate-diff doesn't propose dropping it.
        Index("ix_dead_letter_queue_ts_only", "ts"),
    )


class BackfillCheckpoint(Base):
    """Per (dataset, symbol) bookmark for resumable historical backfills."""

    __tablename__ = "backfill_checkpoints"

    dataset: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    last_completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ContractAdv(Base):
    """Trailing-N-day average daily volume per contract.

    Consumed by :func:`app.processing.flow_events.detect_flow_events` for
    the UOA branch. Refreshed by a daily post-close job.
    """

    __tablename__ = "contract_adv"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    expiration: Mapped[date] = mapped_column(
        Date, primary_key=True, nullable=False
    )
    strike: Mapped[float] = mapped_column(
        Numeric(20, 6), primary_key=True, nullable=False
    )
    option_type: Mapped[str] = mapped_column(
        CHAR(1), primary_key=True, nullable=False
    )

    avg_daily_volume: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_contract_adv_symbol_expiry", "symbol", "expiration"),
    )
