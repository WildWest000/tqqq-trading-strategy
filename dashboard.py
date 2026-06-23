"""
Interactive Dash/Plotly dashboard for the TQQQ/SQQQ trading strategy.

Redesigned with:
- KPI summary cards at the top for instant performance snapshot
- Side-by-side equity + drawdown charts (better use of horizontal space)
- Rolling metrics panel (Sharpe, volatility over time)
- Coordinated hover/click between charts
- Loading states for perceived performance
- Cached backtest results to avoid recomputation
- Card-based layout with consistent spacing
"""
import pandas as pd
import numpy as np
import os
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, html, dcc, Output, Input, State, callback_context, no_update
import dash_bootstrap_components as dbc
from datetime import datetime, timedelta
from functools import lru_cache
import config
from backtest import run_backtest
from forward_test import run_forward_test, load_state
import paper_trade_reader as ptr


app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])
app.title = "TQQQ Strategy Dashboard"

# --- Reusable Components ---

def kpi_card(title, value, subtitle="", color="primary"):
    """Create a compact KPI card."""
    return dbc.Card([
        dbc.CardBody([
            html.P(title, className="card-title text-muted mb-0", style={"fontSize": "0.75rem"}),
            html.H4(value, className=f"text-{color} mb-0", style={"fontWeight": "bold"}),
            html.Small(subtitle, className="text-muted") if subtitle else None,
        ], className="py-2 px-3")
    ], className="h-100")


def section_card(title, children, id=None):
    """Wrap content in a consistent card with header."""
    props = {"className": "mb-3"}
    if id:
        props["id"] = id
    return dbc.Card([
        dbc.CardHeader(html.H6(title, className="mb-0")),
        dbc.CardBody(children, className="p-2"),
    ], **props)


# --- Tab Content Builders ---

def _backtest_tab():
    """Tab 1: backtest KPIs, charts, trade log, and forward test."""
    return html.Div([
        # KPI Summary Row
        dbc.Row(id="kpi-row", children=[
            dbc.Col(kpi_card("Total Return", "—"), width=2),
            dbc.Col(kpi_card("Sharpe Ratio", "—"), width=2),
            dbc.Col(kpi_card("Max Drawdown", "—"), width=2),
            dbc.Col(kpi_card("vs B&H", "—"), width=2),
            dbc.Col(kpi_card("Trades", "—"), width=2),
            dbc.Col(kpi_card("Win Rate", "—"), width=2),
        ], className="mb-3 g-2"),

        # Main charts: equity + drawdown side by side
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    dcc.Graph(id="equity-chart", config={"displayModeBar": False}),
                    type="circle", color="#00d4aa"
                ),
            ], lg=8),
            dbc.Col([
                dcc.Loading(
                    dcc.Graph(id="drawdown-chart", config={"displayModeBar": False}),
                    type="circle", color="#ff6b6b"
                ),
            ], lg=4),
        ], className="mb-2"),

        # Secondary row: Signals + Rolling Metrics
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    dcc.Graph(id="signals-chart", config={"displayModeBar": False}),
                    type="circle", color="#4ecdc4"
                ),
            ], lg=8),
            dbc.Col([
                dcc.Loading(
                    dcc.Graph(id="rolling-chart", config={"displayModeBar": False}),
                    type="circle", color="#ffe66d"
                ),
            ], lg=4),
        ], className="mb-2"),

        # Trade log + Forward test
        dbc.Row([
            dbc.Col([
                section_card("Trade Log", [
                    dbc.Row([
                        dbc.Col([
                            dbc.Select(
                                id="trade-filter",
                                options=[
                                    {"label": "All Trades", "value": "all"},
                                    {"label": "Bullish Only", "value": "bullish"},
                                    {"label": "Defensive Only", "value": "defensive"},
                                    {"label": "Trailing Stops", "value": "stops"},
                                ],
                                value="all",
                                size="sm",
                                className="bg-dark text-light mb-2",
                            ),
                        ], width=3),
                    ]),
                    html.Div(id="trade-log", style={"maxHeight": "400px", "overflowY": "auto"}),
                ]),
            ], lg=8),
            dbc.Col([
                section_card("Forward Test", [
                    dbc.Button("Run Forward Test", id="forward-btn", color="success", size="sm",
                              className="mb-2"),
                    html.Div(id="forward-panel"),
                ]),
            ], lg=4),
        ]),
    ], className="pt-2")


def _confirmations_tab():
    """Tab 2: live Alpaca paper-bot status + parsed order confirmations."""
    return html.Div([
        dbc.Row([
            dbc.Col(html.Small(
                "Live paper-trading bot — read from paper_trade/state.json and logs/trade_*.log",
                className="text-muted"), width=9),
            dbc.Col(
                dbc.Button("↻ Refresh", id="confirm-refresh-btn", color="secondary",
                           size="sm", className="float-end"),
                width=3),
        ], className="align-items-center mb-2 pt-2"),

        # Portfolio summary (live equity / cash / positions / day P&L)
        html.H6("Portfolio Summary", className="text-light mt-1 mb-2"),
        dbc.Row(id="portfolio-summary", className="mb-3 g-2"),

        # Bot status KPI cards
        html.H6("Bot Status", className="text-light mb-2"),
        dbc.Row(id="bot-status-cards", className="mb-3 g-2"),

        # Confirmations table
        section_card("Order Confirmations & Events", [
            html.Div(id="confirmations-table",
                     style={"maxHeight": "480px", "overflowY": "auto"}),
        ]),
    ])


def _logs_tab():
    """Tab 3: raw daily bot log viewer."""
    return html.Div([
        dbc.Row([
            dbc.Col(
                dbc.Select(id="log-file-select", size="sm",
                           className="bg-dark text-light"),
                width=6),
            dbc.Col(
                dbc.Button("↻ Refresh", id="log-refresh-btn", color="secondary",
                           size="sm", className="float-end"),
                width=3),
            dbc.Col(html.Div(id="log-file-meta", className="text-muted small pt-1 text-end"),
                    width=3),
        ], className="align-items-center mb-2 pt-2"),

        dbc.Card(dbc.CardBody(
            html.Pre(id="log-content",
                     style={"maxHeight": "560px", "overflowY": "auto",
                            "fontSize": "0.75rem", "whiteSpace": "pre-wrap",
                            "marginBottom": "0"}),
            className="p-2",
        )),
    ])


# --- Layout ---

# Build the header subtitle dynamically so it reflects whatever risk controls
# are active (overlays default OFF → reads "Method | Trailing Stop").
_subtitle_parts = [f"Method: {config.REGIME_METHOD}", "Trailing Stop: 25%"]
if getattr(config, "VOL_TARGET_ENABLED", False):
    _subtitle_parts.append(f"VolTgt: {config.VOL_TARGET_ANNUAL:.0%}")
if getattr(config, "MAX_TQQQ_ALLOC", 1.0) < 1.0:
    _subtitle_parts.append(f"Cap: {config.MAX_TQQQ_ALLOC:.0%}")
if getattr(config, "REENTRY_COOLDOWN_DAYS", 0):
    _subtitle_parts.append(f"Cooldown: {config.REENTRY_COOLDOWN_DAYS}d")
HEADER_SUBTITLE = " | ".join(_subtitle_parts)

app.layout = dbc.Container([
    # Header row: title + controls inline
    dbc.Row([
        dbc.Col([
            html.H4("TQQQ/SQQQ Strategy", className="mb-0 text-light"),
            html.Small(HEADER_SUBTITLE, className="text-muted"),
        ], width=4),
        dbc.Col([
            dbc.InputGroup([
                dbc.InputGroupText("From", className="bg-dark text-muted"),
                dbc.Input(id="start-date-input", type="date",
                         value=config.DEFAULT_BACKTEST_START,
                         className="bg-dark text-light"),
                dbc.InputGroupText("To", className="bg-dark text-muted"),
                dbc.Input(id="end-date-input", type="date",
                         value=config.DEFAULT_BACKTEST_END,
                         className="bg-dark text-light"),
                dbc.Button("Run", id="run-btn", color="primary", size="sm"),
            ], size="sm"),
        ], width=6),
        dbc.Col([
            html.Div(id="status-msg", className="text-info small text-end pt-1"),
            html.Div(id="data-freshness", className="text-muted small text-end"),
        ], width=2),
    ], className="py-2 mb-2 border-bottom border-secondary align-items-center"),
    
    # Daily data auto-refresh (fires on load and every 6 hours)
    dcc.Interval(id="data-refresh-interval", interval=6 * 60 * 60 * 1000, n_intervals=0),
    
    # Tabbed interface
    dbc.Tabs(id="main-tabs", active_tab="tab-backtest", className="mb-3", children=[
        dbc.Tab(label="Backtesting", tab_id="tab-backtest", children=_backtest_tab()),
        dbc.Tab(label="Trading Confirmations", tab_id="tab-confirm", children=_confirmations_tab()),
        dbc.Tab(label="Logs", tab_id="tab-logs", children=_logs_tab()),
    ]),
    
    # Hidden stores
    dcc.Store(id="backtest-store"),
    dcc.Store(id="trades-store"),
], fluid=True, className="px-3")


# --- Callbacks ---

@app.callback(
    [Output("status-msg", "children"),
     Output("kpi-row", "children"),
     Output("equity-chart", "figure"),
     Output("signals-chart", "figure"),
     Output("drawdown-chart", "figure"),
     Output("rolling-chart", "figure"),
     Output("trade-log", "children"),
     Output("trades-store", "data")],
    [Input("run-btn", "n_clicks")],
    [State("start-date-input", "value"),
     State("end-date-input", "value")],
    prevent_initial_call=False,
)
def run_backtest_callback(n_clicks, start_date, end_date):
    """Run backtest and update all charts + KPIs."""
    try:
        results = run_backtest(start=start_date, end=end_date)
    except Exception as e:
        empty_fig = go.Figure().update_layout(template="plotly_dark")
        empty_kpis = [dbc.Col(kpi_card("Error", str(e)[:20], color="danger"), width=12)]
        return (f"Error: {e}", empty_kpis, empty_fig, empty_fig, empty_fig, empty_fig, "", None)
    
    portfolio_df = results["portfolio_df"]
    benchmark_df = results["benchmark_df"]
    signals_df = results["signals_df"]
    trades_df = results["trades_df"]
    sm = results["strategy_metrics"]
    bm = results["benchmark_metrics"]
    
    # --- KPI Cards ---
    total_ret = sm.get("total_return", 0)
    sharpe = sm.get("sharpe_ratio", 0)
    max_dd = sm.get("max_drawdown", 0)
    bh_ret = bm.get("total_return", 0)
    alpha = total_ret - bh_ret
    num_trades = sm.get("num_trades", 0)
    
    # Win rate from trades
    if len(trades_df) > 0 and "gain_loss" in trades_df.columns:
        wins = (trades_df["gain_loss"] > 0).sum()
        win_rate = wins / len(trades_df) * 100
    else:
        win_rate = 0
    
    ret_color = "success" if total_ret > 0 else "danger"
    sharpe_color = "success" if sharpe > 1 else ("warning" if sharpe > 0.5 else "danger")
    dd_color = "success" if max_dd > -0.3 else ("warning" if max_dd > -0.5 else "danger")
    alpha_color = "success" if alpha > 0 else "danger"

    # --- Rolling 3-week (15 trading-day) short-horizon stats ---
    pv = portfolio_df["portfolio_value"]
    roll_3wk = (pv / pv.shift(15) - 1).dropna()
    if len(roll_3wk) > 0:
        wk_worst = roll_3wk.min() * 100
        wk_win = (roll_3wk > 0).mean() * 100
    else:
        wk_worst = wk_win = 0
    wk_worst_color = "success" if wk_worst > -15 else ("warning" if wk_worst > -25 else "danger")
    
    kpis = [
        dbc.Col(kpi_card("Total Return", f"{total_ret*100:+.1f}%",
                        f"${sm.get('final_value', 0):,.0f}", ret_color), width=2),
        dbc.Col(kpi_card("Sharpe Ratio", f"{sharpe:.2f}",
                        f"B&H: {bm.get('sharpe_ratio', 0):.2f}", sharpe_color), width=2),
        dbc.Col(kpi_card("Max Drawdown", f"{max_dd*100:.1f}%",
                        f"B&H: {bm.get('max_drawdown', 0)*100:.1f}%", dd_color), width=2),
        dbc.Col(kpi_card("vs Buy & Hold", f"{alpha*100:+.1f}%",
                        "outperform" if alpha > 0 else "underperform", alpha_color), width=2),
        dbc.Col(kpi_card("Trades", str(num_trades),
                        f"Bull: {sm.get('bull_rebal', 0)} Def: {sm.get('defensive_rebal', 0)}"), width=2),
        dbc.Col(kpi_card("Win Rate", f"{win_rate:.0f}%",
                        f"{int(win_rate*num_trades/100)}/{num_trades} profitable"), width=2),
        dbc.Col(kpi_card("3wk Worst", f"{wk_worst:.1f}%",
                        "worst rolling 15-day window", wk_worst_color), width=2),
        dbc.Col(kpi_card("3wk Win Rate", f"{wk_win:.0f}%",
                        "rolling 15-day windows > 0"), width=2),
    ]
    
    # --- Equity Curve ---
    equity_fig = go.Figure()
    equity_fig.add_trace(go.Scatter(
        x=portfolio_df.index, y=portfolio_df["portfolio_value"],
        name="Strategy", line=dict(color="#00d4aa", width=2),
        hovertemplate="Strategy: $%{y:,.0f}<extra></extra>"
    ))
    if len(benchmark_df) > 0:
        equity_fig.add_trace(go.Scatter(
            x=benchmark_df.index, y=benchmark_df["portfolio_value"],
            name="Buy & Hold", line=dict(color="#ff6b6b", width=1.5, dash="dash"),
            hovertemplate="B&H: $%{y:,.0f}<extra></extra>"
        ))
    
    # Regime shading
    regime_colors = {"bull": "rgba(0,200,0,0.06)", "neutral": "rgba(255,255,0,0.06)",
                     "bear": "rgba(255,60,60,0.08)", "crisis": "rgba(255,0,0,0.12)"}
    if "regime" in portfolio_df.columns:
        _add_regime_shading(equity_fig, portfolio_df["regime"], regime_colors)
    
    # Buy / Sell / Rebalance markers on the equity curve
    if len(trades_df) > 0:
        td = trades_df.copy()
        td["date"] = pd.to_datetime(td["date"])
        if "portfolio_value" not in td.columns:
            td["portfolio_value"] = portfolio_df["portfolio_value"].reindex(
                td["date"], method="nearest").values
        stops = td[td["signal"] == "trailing_stop_triggered"]
        buys = td[(td["tqqq_action"] == "buy") & (td["signal"] != "trailing_stop_triggered")]
        sells = td[(td["tqqq_action"] == "sell") & (td["signal"] != "trailing_stop_triggered")]
        if len(buys) > 0:
            equity_fig.add_trace(go.Scatter(
                x=buys["date"], y=buys["portfolio_value"],
                mode="markers", name="Buy",
                marker=dict(symbol="triangle-up", size=10, color="#2ecc71",
                            line=dict(width=1, color="#0b3d24")),
                customdata=buys["signal"],
                hovertemplate="BUY (%{customdata})<br>$%{y:,.0f}<extra></extra>"
            ))
        if len(sells) > 0:
            equity_fig.add_trace(go.Scatter(
                x=sells["date"], y=sells["portfolio_value"],
                mode="markers", name="Sell / Rebalance",
                marker=dict(symbol="triangle-down", size=10, color="#ff6b6b",
                            line=dict(width=1, color="#5a1a1a")),
                customdata=sells["signal"],
                hovertemplate="SELL (%{customdata})<br>$%{y:,.0f}<extra></extra>"
            ))
        if len(stops) > 0:
            equity_fig.add_trace(go.Scatter(
                x=stops["date"], y=stops["portfolio_value"],
                mode="markers", name="Trailing Stop",
                marker=dict(symbol="x", size=11, color="#f1c40f",
                            line=dict(width=1, color="#7a6200")),
                customdata=stops["signal"],
                hovertemplate="TRAILING STOP → cash<br>$%{y:,.0f}<extra></extra>"
            ))
    
    # Annotate max drawdown point
    values = portfolio_df["portfolio_value"]
    cummax = values.cummax()
    drawdown_series = (values - cummax) / cummax
    max_dd_idx = drawdown_series.idxmin()
    if max_dd_idx is not None:
        equity_fig.add_annotation(
            x=max_dd_idx, y=values[max_dd_idx],
            text=f"Max DD: {drawdown_series[max_dd_idx]*100:.1f}%",
            showarrow=True, arrowhead=2, arrowcolor="#ff6b6b",
            font=dict(size=10, color="#ff6b6b"),
            ax=0, ay=-30
        )
    
    equity_fig.update_layout(
        title=None,
        margin=dict(l=50, r=20, t=30, b=30),
        xaxis_title=None, yaxis_title="Portfolio Value ($)",
        template="plotly_dark", height=320,
        legend=dict(orientation="h", yanchor="top", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    
    # --- Drawdown Chart (compact, vertical) ---
    drawdown_pct = drawdown_series * 100
    drawdown_fig = go.Figure()
    drawdown_fig.add_trace(go.Scatter(
        x=portfolio_df.index, y=drawdown_pct,
        fill="tozeroy", name="Strategy",
        line=dict(color="#ff6b6b", width=1),
        fillcolor="rgba(255,107,107,0.2)",
        hovertemplate="%{y:.1f}%<extra></extra>"
    ))
    if len(benchmark_df) > 0:
        bench_values = benchmark_df["portfolio_value"]
        bench_dd = (bench_values - bench_values.cummax()) / bench_values.cummax() * 100
        drawdown_fig.add_trace(go.Scatter(
            x=benchmark_df.index, y=bench_dd,
            name="B&H", line=dict(color="#ffa500", width=1, dash="dot"),
            hovertemplate="%{y:.1f}%<extra></extra>"
        ))
    drawdown_fig.update_layout(
        title=dict(text="Drawdown", font=dict(size=12)),
        margin=dict(l=40, r=10, t=30, b=30),
        xaxis_title=None, yaxis_title="%",
        template="plotly_dark", height=320,
        legend=dict(orientation="h", yanchor="top", y=1.02, x=0),
        hovermode="x unified",
    )
    
    # --- Signals Chart ---
    signals_fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.5, 0.2, 0.3], vertical_spacing=0.02,
        subplot_titles=["TQQQ Price", "RSI", "Allocation %"]
    )
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["tqqq_close"],
        name="TQQQ", line=dict(color="#4ecdc4", width=1.5),
        hovertemplate="$%{y:.2f}<extra></extra>"
    ), row=1, col=1)
    
    # Trade markers
    if len(trades_df) > 0:
        buys = trades_df[trades_df["tqqq_action"] == "buy"]
        sells = trades_df[trades_df["tqqq_action"] == "sell"]
        if len(buys) > 0:
            buy_dates = pd.to_datetime(buys["date"])
            buy_prices = signals_df["tqqq_close"].reindex(buy_dates, method="nearest")
            signals_fig.add_trace(go.Scatter(
                x=buy_dates, y=buy_prices,
                mode="markers", name="Buy",
                marker=dict(symbol="triangle-up", size=8, color="lime"),
                hovertemplate="BUY @ $%{y:.2f}<extra></extra>"
            ), row=1, col=1)
        if len(sells) > 0:
            sell_dates = pd.to_datetime(sells["date"])
            sell_prices = signals_df["tqqq_close"].reindex(sell_dates, method="nearest")
            signals_fig.add_trace(go.Scatter(
                x=sell_dates, y=sell_prices,
                mode="markers", name="Sell",
                marker=dict(symbol="triangle-down", size=8, color="#ff6b6b"),
                hovertemplate="SELL @ $%{y:.2f}<extra></extra>"
            ), row=1, col=1)
    
    # RSI
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["rsi"],
        name="RSI", line=dict(color="#a8dadc", width=1),
        hovertemplate="RSI: %{y:.0f}<extra></extra>"
    ), row=2, col=1)
    signals_fig.add_hline(y=config.RSI_OVERSOLD, line_dash="dash", line_color="lime",
                          line_width=0.5, row=2, col=1)
    signals_fig.add_hline(y=config.RSI_OVERBOUGHT, line_dash="dash", line_color="red",
                          line_width=0.5, row=2, col=1)
    signals_fig.add_hrect(y0=0, y1=config.RSI_OVERSOLD, fillcolor="rgba(0,255,0,0.03)",
                          line_width=0, row=2, col=1)
    signals_fig.add_hrect(y0=config.RSI_OVERBOUGHT, y1=100, fillcolor="rgba(255,0,0,0.03)",
                          line_width=0, row=2, col=1)
    
    # Allocation
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["tqqq_alloc"] * 100,
        name="TQQQ%", fill="tozeroy",
        line=dict(color="#00d4aa", width=1), fillcolor="rgba(0,212,170,0.3)",
        hovertemplate="TQQQ: %{y:.0f}%<extra></extra>"
    ), row=3, col=1)
    if "sqqq_alloc" in signals_df.columns:
        sqqq_total = (signals_df["tqqq_alloc"] + signals_df["sqqq_alloc"]) * 100
        signals_fig.add_trace(go.Scatter(
            x=signals_df.index, y=sqqq_total,
            name="SQQQ%", fill="tonexty",
            line=dict(color="#ff6b6b", width=1), fillcolor="rgba(255,107,107,0.3)",
            hovertemplate="Total: %{y:.0f}%<extra></extra>"
        ), row=3, col=1)
    
    signals_fig.update_layout(
        title=None,
        margin=dict(l=50, r=20, t=30, b=30),
        template="plotly_dark", height=420,
        showlegend=False,
        hovermode="x unified",
    )
    signals_fig.update_yaxes(title_text="$", row=1, col=1)
    signals_fig.update_yaxes(title_text="RSI", row=2, col=1, range=[0, 100])
    signals_fig.update_yaxes(title_text="%", row=3, col=1, range=[0, 105])
    
    # --- Rolling Metrics Chart ---
    rolling_fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.5, 0.5], vertical_spacing=0.05,
        subplot_titles=["Rolling 60d Sharpe", "Rolling 20d Volatility"]
    )
    daily_ret = values.pct_change()
    rolling_sharpe = (
        (daily_ret.rolling(60).mean() - config.RISK_FREE_RATE / 252)
        / daily_ret.rolling(60).std()
    ) * np.sqrt(252)
    rolling_vol = daily_ret.rolling(20).std() * np.sqrt(252) * 100
    
    rolling_fig.add_trace(go.Scatter(
        x=portfolio_df.index, y=rolling_sharpe,
        name="Sharpe", line=dict(color="#ffe66d", width=1.5),
        hovertemplate="Sharpe: %{y:.2f}<extra></extra>"
    ), row=1, col=1)
    rolling_fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=0.5, row=1, col=1)
    rolling_fig.add_hline(y=1, line_dash="dot", line_color="lime", line_width=0.5, row=1, col=1)
    
    rolling_fig.add_trace(go.Scatter(
        x=portfolio_df.index, y=rolling_vol,
        name="Vol %", line=dict(color="#ff9f43", width=1.5),
        fill="tozeroy", fillcolor="rgba(255,159,67,0.1)",
        hovertemplate="Ann. Vol: %{y:.1f}%<extra></extra>"
    ), row=2, col=1)
    
    rolling_fig.update_layout(
        title=None,
        margin=dict(l=40, r=10, t=30, b=30),
        template="plotly_dark", height=420,
        showlegend=False,
        hovermode="x unified",
    )
    
    # --- Trade Log ---
    trade_table = _build_trade_table(trades_df, "all")
    
    # Serialize trades for filtering callback
    trades_data = trades_df.to_dict("records") if len(trades_df) > 0 else []
    
    status = f"✓ {start_date} → {end_date} | {len(trades_df)} trades | {(datetime.now()).strftime('%H:%M:%S')}"
    return status, kpis, equity_fig, signals_fig, drawdown_fig, rolling_fig, trade_table, trades_data


@app.callback(
    Output("trade-log", "children", allow_duplicate=True),
    Input("trade-filter", "value"),
    State("trades-store", "data"),
    prevent_initial_call=True,
)
def filter_trades(filter_val, trades_data):
    """Filter trade log without re-running backtest."""
    if not trades_data:
        return html.P("No trades.", className="text-muted")
    trades_df = pd.DataFrame(trades_data)
    return _build_trade_table(trades_df, filter_val)


@app.callback(
    Output("forward-panel", "children"),
    Input("forward-btn", "n_clicks"),
    prevent_initial_call=True,
)
def run_forward_callback(n_clicks):
    """Run forward test and display results."""
    messages = []
    def status_cb(msg):
        messages.append(msg)
    
    state = run_forward_test(status_callback=status_cb)
    
    if not state["portfolio_history"]:
        return html.P("No data yet. Run forward test to start.", className="text-muted small")
    
    history = state["portfolio_history"][-30:]
    latest = history[-1]
    
    content = [
        dbc.Row([
            dbc.Col([
                html.Span("Regime: ", className="text-muted small"),
                dbc.Badge(latest.get("regime", "N/A").upper(),
                         color="success" if latest.get("regime") == "bull" else "danger"),
            ], width=6),
            dbc.Col([
                html.Span(f"${latest['portfolio_value']:,.0f}",
                         className="text-light fw-bold"),
            ], width=6, className="text-end"),
        ], className="mb-2"),
        html.Small(f"Trades: {len(state['trades'])} | "
                  f"TQQQ: {latest.get('tqqq_alloc', 'N/A')} | "
                  f"SQQQ: {latest.get('sqqq_alloc', 'N/A')}",
                  className="text-muted"),
    ]
    
    if state["trades"]:
        recent = state["trades"][-5:]
        rows = [html.Tr([
            html.Td(t["date"], className="small"),
            html.Td(t["action"], className="small"),
            html.Td(f"${t.get('portfolio_value', 0):,.0f}", className="small"),
        ]) for t in recent]
        content.append(dbc.Table([
            html.Thead(html.Tr([html.Th("Date"), html.Th("Action"), html.Th("Value")])),
            html.Tbody(rows)
        ], bordered=True, color="dark", size="sm", className="mt-2"))
    
    return content


# --- Daily Data Refresh Callback ---

@app.callback(
    Output("data-freshness", "children"),
    Input("data-refresh-interval", "n_intervals"),
    prevent_initial_call=False,
)
def auto_refresh_data(n_intervals):
    """Refresh cached price data to the latest bar (guarded once/day)."""
    from download_data import refresh_latest
    try:
        summary = refresh_latest()
    except Exception as e:
        return f"⚠ data refresh failed: {str(e)[:40]}"
    latest = summary.get("latest_date") or "—"
    tag = "updated" if summary.get("updated") else "current"
    return f"Data {tag} → {latest}"


# --- Trading Confirmations Tab Callbacks ---

@app.callback(
    [Output("portfolio-summary", "children"),
     Output("bot-status-cards", "children"),
     Output("confirmations-table", "children")],
    [Input("confirm-refresh-btn", "n_clicks"),
     Input("main-tabs", "active_tab")],
    prevent_initial_call=False,
)
def refresh_confirmations(n_clicks, active_tab):
    """Load Alpaca bot state + parsed order confirmations from disk."""
    state = ptr.load_bot_state()
    events = ptr.parse_confirmations()
    return (_build_portfolio_summary(state),
            _build_bot_status_cards(state),
            _build_confirmations_table(events))


# --- Logs Tab Callbacks ---

@app.callback(
    [Output("log-file-select", "options"),
     Output("log-file-select", "value")],
    [Input("log-refresh-btn", "n_clicks"),
     Input("main-tabs", "active_tab")],
    [State("log-file-select", "value")],
    prevent_initial_call=False,
)
def refresh_log_files(n_clicks, active_tab, current_value):
    """Populate the log-file dropdown, newest first; keep selection if still valid."""
    files = ptr.list_log_files()
    options = [{"label": f["name"], "value": f["path"]} for f in files]
    valid_paths = {f["path"] for f in files}
    if current_value in valid_paths:
        value = current_value
    else:
        value = files[0]["path"] if files else None
    return options, value


@app.callback(
    [Output("log-content", "children"),
     Output("log-file-meta", "children")],
    [Input("log-file-select", "value"),
     Input("log-refresh-btn", "n_clicks")],
    prevent_initial_call=False,
)
def display_log(path, n_clicks):
    """Show the tail of the selected log file plus its size."""
    if not path:
        return ("No bot logs found. The Alpaca bot writes to "
                "paper_trade/logs/trade_YYYYMMDD.log once it runs."), ""
    content = ptr.read_log_file(path)
    try:
        size = os.path.getsize(path)
        meta = f"{size:,} bytes"
    except OSError:
        meta = ""
    return content, meta


# --- Helper Functions ---

def _build_portfolio_summary(state):
    """Live portfolio summary cards from the bot's persisted snapshot."""
    snap = (state or {}).get("portfolio")
    if not snap:
        return [dbc.Col(kpi_card(
            "Portfolio", "No data",
            "Snapshot appears after the bot's next run", "secondary"
        ), width=12)]

    equity = snap.get("equity", 0) or 0
    cash = snap.get("cash", 0) or 0
    tqqq_sh = snap.get("tqqq_shares", 0) or 0
    tqqq_val = snap.get("tqqq_value", 0) or 0
    sqqq_sh = snap.get("sqqq_shares", 0) or 0
    sqqq_val = snap.get("sqqq_value", 0) or 0
    day_pl = snap.get("day_pl", 0) or 0
    day_pl_pct = snap.get("day_pl_pct", 0) or 0
    as_of = snap.get("as_of", "")
    if isinstance(as_of, str) and "T" in as_of:
        as_of = as_of.replace("T", " ")[:19]

    tqqq_pct = (tqqq_val / equity * 100) if equity else 0
    sqqq_pct = (sqqq_val / equity * 100) if equity else 0
    cash_pct = (cash / equity * 100) if equity else 0
    pl_color = "success" if day_pl >= 0 else "danger"

    return [
        dbc.Col(kpi_card("Equity", f"${equity:,.0f}", f"as of {as_of}"), width=3),
        dbc.Col(kpi_card("Since Last Run", f"{day_pl:+,.0f}",
                         f"{day_pl_pct:+.2f}%", pl_color), width=3),
        dbc.Col(kpi_card("Cash", f"${cash:,.0f}", f"{cash_pct:.0f}% of equity"), width=2),
        dbc.Col(kpi_card("TQQQ", f"{tqqq_sh:,} sh",
                         f"${tqqq_val:,.0f} · {tqqq_pct:.0f}%"), width=2),
        dbc.Col(kpi_card("SQQQ", f"{sqqq_sh:,} sh",
                         f"${sqqq_val:,.0f} · {sqqq_pct:.0f}%"), width=2),
    ]


def _build_bot_status_cards(state):
    """KPI cards summarizing the live bot's persisted state.json."""
    if not state:
        return [dbc.Col(kpi_card(
            "Bot Status", "No state", "state.json not found — bot hasn't run", "warning"
        ), width=12)]

    last_run = state.get("last_run") or "—"
    if isinstance(last_run, str) and "T" in last_run:
        last_run = last_run.replace("T", " ")[:19]
    regime = str(state.get("last_regime", "unknown"))
    in_cash = state.get("in_cash_mode", False)
    cash_days = state.get("cash_mode_days", 0)
    peak = state.get("portfolio_peak", 0) or 0

    regime_color = "success" if regime == "bull" else (
        "danger" if regime in ("bear", "crisis") else "warning")
    cash_color = "danger" if in_cash else "success"

    return [
        dbc.Col(kpi_card("Last Run", last_run, "most recent bot cycle"), width=3),
        dbc.Col(kpi_card("Last Regime", regime.upper(), color=regime_color), width=3),
        dbc.Col(kpi_card(
            "Trailing Stop", "IN CASH" if in_cash else "ACTIVE",
            f"cooldown day {cash_days}/5" if in_cash else "tracking peak",
            cash_color), width=3),
        dbc.Col(kpi_card("Portfolio Peak", f"${peak:,.0f}", "trailing-stop reference"), width=3),
    ]


_CONFIRM_BADGE = {
    "order_submitted": ("SUBMITTED", "info"),
    "order_filled": ("FILLED", "success"),
    "order_failed": ("FAILED", "danger"),
    "trailing_stop": ("TRAILING STOP", "danger"),
    "protective_stop": ("PROTECTIVE STOP", "warning"),
    "rebalance": ("REBALANCED", "primary"),
    "no_action": ("NO ACTION", "secondary"),
    "cash_mode": ("CASH MODE", "warning"),
}


def _build_confirmations_table(events):
    """Render parsed bot events as a table, newest first."""
    if not events:
        return html.P(
            "No order confirmations yet. They appear here once the Alpaca bot "
            "submits trades (paper_trade/logs/trade_*.log).",
            className="text-muted")

    rows = []
    for ev in events:
        label, color = _CONFIRM_BADGE.get(ev["type"], (ev["type"].upper(), "secondary"))
        if "symbol" in ev:
            detail = f"{ev['side'].upper()} {ev['qty']} {ev['symbol']}"
        else:
            detail = ev["message"][:80]
        submit_px = f"${ev['submit_price']:,.2f}" if ev.get("submit_price") else "—"
        fill_px = f"${ev['fill_price']:,.2f}" if ev.get("fill_price") else "—"
        # Slippage badge when both prices are known.
        slip_cell = "—"
        if ev.get("submit_price") and ev.get("fill_price"):
            slip = (ev["fill_price"] - ev["submit_price"]) / ev["submit_price"] * 100
            slip_color = "danger" if abs(slip) > 0.5 else "secondary"
            slip_cell = dbc.Badge(f"{slip:+.2f}%", color=slip_color, className="small")
        rows.append(html.Tr([
            html.Td(ev["timestamp"], className="small text-nowrap"),
            html.Td(dbc.Badge(label, color=color, className="small")),
            html.Td(detail, className="small"),
            html.Td(submit_px, className="small text-nowrap text-end"),
            html.Td(fill_px, className="small text-nowrap text-end"),
            html.Td(slip_cell, className="small text-nowrap text-end"),
        ]))

    header = html.Thead(html.Tr([
        html.Th("Time", className="small"),
        html.Th("Event", className="small"),
        html.Th("Detail", className="small"),
        html.Th("Submitted", className="small text-end"),
        html.Th("Filled", className="small text-end"),
        html.Th("Slippage", className="small text-end"),
    ]))
    return dbc.Table([header, html.Tbody(rows)],
                     bordered=True, hover=True, color="dark", size="sm", striped=True)


# --- Helper Functions ---

def _add_regime_shading(fig, regime_series, colors):
    """Add regime-colored vertical bands to a figure."""
    prev_regime = None
    block_start = None
    for date, regime_val in regime_series.items():
        if regime_val != prev_regime:
            if prev_regime is not None and block_start is not None:
                fig.add_vrect(
                    x0=block_start, x1=date,
                    fillcolor=colors.get(prev_regime, "rgba(0,0,0,0)"),
                    layer="below", line_width=0
                )
            block_start = date
            prev_regime = regime_val
    if prev_regime and block_start:
        fig.add_vrect(
            x0=block_start, x1=regime_series.index[-1],
            fillcolor=colors.get(prev_regime, "rgba(0,0,0,0)"),
            layer="below", line_width=0
        )


def _build_trade_table(trades_df, filter_val):
    """Build the filtered trade table."""
    if len(trades_df) == 0:
        return html.P("No trades executed.", className="text-muted")
    
    df = trades_df.copy()
    if filter_val == "bullish":
        df = df[df["signal"].str.contains("bullish", na=False)]
    elif filter_val == "defensive":
        df = df[df["signal"].str.contains("defensive", na=False)]
    elif filter_val == "stops":
        df = df[df["signal"].str.contains("trailing_stop", na=False)]
    
    if len(df) == 0:
        return html.P(f"No {filter_val} trades found.", className="text-muted")
    
    # Compact display columns
    display_cols = ["date", "signal", "regime", "tqqq_alloc_from", "tqqq_alloc_to",
                   "portfolio_value", "gain_loss"]
    available_cols = [c for c in display_cols if c in df.columns]
    display_df = df[available_cols].copy()
    display_df.columns = ["Date", "Signal", "Regime", "From%", "To%", "Value", "G/L"][:len(available_cols)]
    
    # Color gain/loss
    rows = []
    for _, row in display_df.tail(50).iterrows():
        cells = []
        for i, val in enumerate(row):
            if display_df.columns[i] == "G/L" and isinstance(val, (int, float)):
                color = "#00d4aa" if val > 0 else "#ff6b6b" if val < 0 else ""
                cells.append(html.Td(f"${val:+,.0f}", style={"color": color}, className="small"))
            elif display_df.columns[i] == "Value" and isinstance(val, (int, float)):
                cells.append(html.Td(f"${val:,.0f}", className="small"))
            else:
                cells.append(html.Td(str(val)[:16], className="small"))
        rows.append(html.Tr(cells))
    
    header = html.Thead(html.Tr([html.Th(c, className="small") for c in display_df.columns]))
    return dbc.Table([header, html.Tbody(rows)],
                    bordered=True, hover=True, color="dark", size="sm", striped=True)


def run_dashboard():
    """Start the dashboard server."""
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, debug=False)


if __name__ == "__main__":
    run_dashboard()
