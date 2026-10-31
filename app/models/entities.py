import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    Column, String, Boolean, DateTime, Numeric, Integer, BigInteger,
    ForeignKey, Text, JSON, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_uuid() -> str:
    return str(uuid.uuid4())


# 1. users
class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(120), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    two_factor_enabled = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    exchange_connections = relationship("ExchangeConnection", back_populates="user", cascade="all, delete-orphan")
    bots = relationship("TradingBot", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    risk_limits = relationship("RiskLimit", back_populates="user", uselist=False, cascade="all, delete-orphan")
    notification_preferences = relationship("NotificationPreference", back_populates="user", uselist=False, cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="user", cascade="all, delete-orphan")


# 2. user_sessions
class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_token_hash = Column(String(255), unique=True, nullable=False, index=True)
    device_info = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")


# 3. exchange_connections
class ExchangeConnection(Base):
    __tablename__ = "exchange_connections"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exchange = Column(String(32), default="binance", nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    api_secret_encrypted = Column(Text, nullable=False)
    is_testnet = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    permissions_verified = Column(Boolean, default=False, nullable=False)
    can_trade = Column(Boolean, default=False, nullable=False)
    can_withdraw = Column(Boolean, default=False, nullable=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", back_populates="exchange_connections")


# 4. trading_strategies
class TradingStrategy(Base):
    __tablename__ = "trading_strategies"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    parameters_schema = Column(JSON, nullable=False, default=dict)
    is_built_in = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    bots = relationship("TradingBot", back_populates="strategy")


# 5. trading_bots
class TradingBot(Base):
    __tablename__ = "trading_bots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy_id = Column(String(36), ForeignKey("trading_strategies.id"), nullable=True)
    name = Column(String(100), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), default="1h", nullable=False)
    operating_mode = Column(String(20), default="demo", nullable=False)   # demo, live, manual_approval, automatic
    status = Column(String(20), default="paused", nullable=False)         # active, paused, stopped
    allocated_budget = Column(Numeric(20, 8), nullable=False, default=Decimal("1000.00000000"))
    risk_percentage = Column(Numeric(5, 2), nullable=False, default=Decimal("0.50"))
    max_daily_trades = Column(Integer, default=5, nullable=False)
    trades_count_today = Column(Integer, default=0, nullable=False)
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User", back_populates="bots")
    strategy = relationship("TradingStrategy", back_populates="bots")
    configuration = relationship("BotConfiguration", back_populates="bot", uselist=False, cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="bot")
    signals = relationship("TradingSignal", back_populates="bot")


# 6. bot_configurations
class BotConfiguration(Base):
    __tablename__ = "bot_configurations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("trading_bots.id", ondelete="CASCADE"), unique=True, nullable=False)
    stop_loss_type = Column(String(20), default="atr", nullable=False)     # fixed, percentage, atr
    stop_loss_value = Column(Numeric(10, 4), default=Decimal("1.5000"), nullable=False)
    take_profit_type = Column(String(20), default="rr_target", nullable=False)  # fixed, percentage, rr_target
    take_profit_value = Column(Numeric(10, 4), default=Decimal("2.0000"), nullable=False)
    trailing_stop_enabled = Column(Boolean, default=False, nullable=False)
    trailing_stop_callback = Column(Numeric(5, 2), default=Decimal("0.50"), nullable=False)
    strategy_params = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    bot = relationship("TradingBot", back_populates="configuration")


# 7. market_candles
class MarketCandle(Base):
    __tablename__ = "market_candles"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False, index=True)
    open_time = Column(DateTime(timezone=True), nullable=False, index=True)
    close_time = Column(DateTime(timezone=True), nullable=False)
    open_price = Column(Numeric(20, 8), nullable=False)
    high_price = Column(Numeric(20, 8), nullable=False)
    low_price = Column(Numeric(20, 8), nullable=False)
    close_price = Column(Numeric(20, 8), nullable=False)
    volume = Column(Numeric(24, 8), nullable=False)
    quote_volume = Column(Numeric(24, 8), nullable=False)
    trades_count = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candle_symbol_tf_time"),
    )


# 8. trading_signals
class TradingSignal(Base):
    __tablename__ = "trading_signals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    bot_id = Column(String(36), ForeignKey("trading_bots.id", ondelete="SET NULL"), nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    signal_type = Column(String(10), nullable=False)  # BUY, SELL
    strategy_name = Column(String(100), nullable=False)
    proposed_entry_price = Column(Numeric(20, 8), nullable=False)
    stop_loss = Column(Numeric(20, 8), nullable=False)
    take_profit = Column(Numeric(20, 8), nullable=False)
    risk_reward_ratio = Column(Numeric(6, 2), nullable=False)
    indicator_values = Column(JSON, nullable=False, default=dict)
    explanation = Column(Text, nullable=False)
    score = Column(Numeric(5, 2), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), default="PENDING", nullable=False)  # PENDING, APPROVED, REJECTED, EXPIRED, EXECUTED
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    bot = relationship("TradingBot", back_populates="signals")
    proposal = relationship("TradeProposal", back_populates="signal", uselist=False)


# 9. trade_proposals
class TradeProposal(Base):
    __tablename__ = "trade_proposals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    signal_id = Column(String(36), ForeignKey("trading_signals.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_id = Column(String(36), ForeignKey("trading_bots.id", ondelete="SET NULL"), nullable=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    proposed_quantity = Column(Numeric(20, 8), nullable=False)
    proposed_notional = Column(Numeric(20, 8), nullable=False)
    risk_amount = Column(Numeric(20, 8), nullable=False)
    status = Column(String(20), default="AWAITING_APPROVAL", nullable=False)  # AWAITING_APPROVAL, APPROVED, DECLINED, EXPIRED
    expires_at = Column(DateTime(timezone=True), nullable=False)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    signal = relationship("TradingSignal", back_populates="proposal")


# 10. orders
class Order(Base):
    __tablename__ = "orders"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_id = Column(String(36), ForeignKey("trading_bots.id", ondelete="SET NULL"), nullable=True, index=True)
    client_order_id = Column(String(64), unique=True, nullable=False, index=True)
    exchange_order_id = Column(String(64), nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)           # BUY, SELL
    order_type = Column(String(20), nullable=False)     # LIMIT, MARKET, STOP_LOSS_LIMIT, TAKE_PROFIT_LIMIT
    mode = Column(String(20), nullable=False, default="demo")   # demo, live
    status = Column(String(20), nullable=False, default="NEW")  # NEW, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED
    price = Column(Numeric(20, 8), nullable=True)
    quantity = Column(Numeric(20, 8), nullable=False)
    executed_quantity = Column(Numeric(20, 8), default=Decimal("0.0"), nullable=False)
    cumulative_quote_qty = Column(Numeric(20, 8), default=Decimal("0.0"), nullable=False)
    average_fill_price = Column(Numeric(20, 8), nullable=True)
    stop_price = Column(Numeric(20, 8), nullable=True)
    commission = Column(Numeric(20, 8), default=Decimal("0.0"), nullable=False)
    commission_asset = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User", back_populates="orders")
    bot = relationship("TradingBot", back_populates="orders")
    events = relationship("OrderEvent", back_populates="order", cascade="all, delete-orphan")
    executions = relationship("Execution", back_populates="order", cascade="all, delete-orphan")


# 11. order_events
class OrderEvent(Base):
    __tablename__ = "order_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    order_id = Column(String(36), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # SUBMITTED, RECEIVED, FILLED_PARTIAL, FILLED, FAILED, CANCELED
    raw_payload = Column(JSON, nullable=False, default=dict)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    order = relationship("Order", back_populates="events")


# 12. executions
class Execution(Base):
    __tablename__ = "executions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    order_id = Column(String(36), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    exchange_trade_id = Column(String(64), nullable=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    fee = Column(Numeric(20, 8), default=Decimal("0.0"), nullable=False)
    fee_asset = Column(String(10), nullable=False, default="USDT")
    realized_pnl = Column(Numeric(20, 8), default=Decimal("0.0"), nullable=False)
    executed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    order = relationship("Order", back_populates="executions")


# 13. portfolio_snapshots
class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mode = Column(String(20), default="demo", nullable=False)   # demo, live
    total_equity_usdt = Column(Numeric(24, 8), nullable=False)
    available_balance_usdt = Column(Numeric(24, 8), nullable=False)
    allocated_balance_usdt = Column(Numeric(24, 8), nullable=False)
    unrealized_pnl_usdt = Column(Numeric(24, 8), nullable=False, default=Decimal("0.0"))
    realized_pnl_24h_usdt = Column(Numeric(24, 8), nullable=False, default=Decimal("0.0"))
    positions_json = Column(JSON, nullable=False, default=list)
    snapshot_time = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# 14. risk_limits
class RiskLimit(Base):
    __tablename__ = "risk_limits"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    max_risk_per_trade_pct = Column(Numeric(5, 2), default=Decimal("0.50"), nullable=False)
    max_daily_loss_pct = Column(Numeric(5, 2), default=Decimal("2.00"), nullable=False)
    max_simultaneous_positions = Column(Integer, default=3, nullable=False)
    max_portfolio_exposure_pct = Column(Numeric(5, 2), default=Decimal("30.00"), nullable=False)
    max_consecutive_losses = Column(Integer, default=3, nullable=False)
    max_slippage_pct = Column(Numeric(5, 2), default=Decimal("0.20"), nullable=False)
    trade_allocation_pct = Column(Numeric(5, 2), default=Decimal("30.00"), nullable=False)
    take_profit_pct = Column(Numeric(5, 2), default=Decimal("10.00"), nullable=False)
    stop_loss_pct = Column(Numeric(5, 2), default=Decimal("5.00"), nullable=False)
    trailing_stop_enabled = Column(Boolean, default=True, nullable=False)
    trailing_stop_callback_pct = Column(Numeric(5, 2), default=Decimal("0.50"), nullable=False)
    kill_switch_active = Column(Boolean, default=False, nullable=False)
    daily_trading_locked_until = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User", back_populates="risk_limits")


# 15. risk_events
class RiskEvent(Base):
    __tablename__ = "risk_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_id = Column(String(36), ForeignKey("trading_bots.id", ondelete="SET NULL"), nullable=True)
    rule_violated = Column(String(100), nullable=False)
    context_data = Column(JSON, default=dict, nullable=False)
    action_taken = Column(String(100), nullable=False)  # REJECT_TRADE, PAUSE_BOT, LOCKOUT_24H, KILL_SWITCH
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# 16. backtest_runs
class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy_name = Column(String(100), nullable=False)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    initial_capital = Column(Numeric(20, 8), nullable=False)
    final_equity = Column(Numeric(20, 8), nullable=False)
    total_trades = Column(Integer, nullable=False)
    win_rate = Column(Numeric(5, 2), nullable=False)
    profit_factor = Column(Numeric(8, 2), nullable=False)
    max_drawdown_pct = Column(Numeric(5, 2), nullable=False)
    sharpe_ratio = Column(Numeric(8, 3), nullable=False)
    monte_carlo_var_95 = Column(Numeric(8, 2), nullable=True)
    equity_curve = Column(JSON, nullable=False, default=list)
    parameters = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    trades = relationship("BacktestTrade", back_populates="run", cascade="all, delete-orphan")


# 17. backtest_trades
class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=False)
    exit_price = Column(Numeric(20, 8), nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    pnl = Column(Numeric(20, 8), nullable=False)
    pnl_percent = Column(Numeric(8, 4), nullable=False)
    exit_reason = Column(String(50), nullable=False)  # TAKE_PROFIT, STOP_LOSS, TRAILING_STOP, TIME_LIMIT

    run = relationship("BacktestRun", back_populates="trades")


# 18. alerts
class Alert(Base):
    __tablename__ = "alerts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(50), nullable=False)  # PRICE_ALERT, SIGNAL, ORDER_EXECUTION, RISK_LIMIT, BOT_PAUSED, CONNECTION_ERROR
    title = Column(String(150), nullable=False)
    message = Column(Text, nullable=False)
    level = Column(String(20), default="INFO", nullable=False)  # INFO, WARNING, CRITICAL, SUCCESS
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", back_populates="alerts")


# 19. notification_preferences
class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    fcm_token = Column(Text, nullable=True)
    notify_signals = Column(Boolean, default=True, nullable=False)
    notify_orders = Column(Boolean, default=True, nullable=False)
    notify_risk_events = Column(Boolean, default=True, nullable=False)
    notify_price_thresholds = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User", back_populates="notification_preferences")


# 20. audit_logs
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(100), nullable=True)
    ip_address = Column(String(45), nullable=True)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False)
