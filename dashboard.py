"""
Interactive Dash/Plotly dashboard for the TQQQ/SQQQ mean reversion strategy.
Displays backtest results, benchmark comparison, and forward test results.
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, html, dcc, Output, Input, State
import dash_bootstrap_components as dbc
from datetime import datetime, timedelta
import config
from backtest import run_backtest
from forward_test import run_forward_test, load_state


app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

# --- Layout ---
app.layout = dbc.Container([
    dbc.Row([
        dbc.Col(html.H1("TQQQ/SQQQ Regime-Based Strategy", className="text-center my-3"), width=12),
    ]),
    dbc.Row([
        dbc.Col(html.P(
            "Signals: previous day's close → Execution: next day's open | "
            "Trailing stop: 25% from peak → cash | "
            "Regimes: bull (100% TQQQ), neutral (70% TQQQ), bear (cash), crisis (20% SQQQ)",
            className="text-muted text-center small"
        ), width=12),
    ]),
    
    # Date range controls
    dbc.Row([
        dbc.Col([
            dbc.Label("Backtest Start Date"),
            dcc.DatePickerSingle(
                id="start-date",
                date=config.DEFAULT_BACKTEST_START,
                display_format="YYYY-MM-DD",
                month_format="MMMM YYYY",
            ),
        ], width=3),
        dbc.Col([
            dbc.Label("Backtest End Date"),
            dcc.DatePickerSingle(
                id="end-date",
                date=config.DEFAULT_BACKTEST_END,
                display_format="YYYY-MM-DD",
                month_format="MMMM YYYY",
            ),
        ], width=3),
        dbc.Col([
            dbc.Label("\u00a0"),
            html.Br(),
            dbc.Button("Run Backtest", id="run-btn", color="primary", className="me-2"),
            dbc.Button("Run Forward Test", id="forward-btn", color="success"),
        ], width=4),
        dbc.Col([
            dbc.Label("Status"),
            html.Div(id="status-msg", children="Ready", className="text-info"),
        ], width=2),
    ], className="mb-3"),
    
    # Performance metrics
    dbc.Row([
        dbc.Col([
            html.H4("Strategy vs Benchmark"),
            html.Div(id="metrics-table"),
        ], width=12),
    ], className="mb-3"),
    
    # Equity curve
    dbc.Row([
        dbc.Col([
            dcc.Graph(id="equity-chart"),
        ], width=12),
    ]),
    
    # Price + Signals chart
    dbc.Row([
        dbc.Col([
            dcc.Graph(id="signals-chart"),
        ], width=12),
    ]),
    
    # Drawdown chart
    dbc.Row([
        dbc.Col([
            dcc.Graph(id="drawdown-chart"),
        ], width=12),
    ]),
    
    # Trade log
    dbc.Row([
        dbc.Col([
            html.H4("Trade Log"),
            html.Div(id="trade-log"),
        ], width=12),
    ], className="mb-3"),
    
    # Forward test panel
    dbc.Row([
        dbc.Col([
            html.H4("Forward Test (Paper Trading)"),
            html.Div(id="forward-panel"),
        ], width=12),
    ], className="mb-3"),
    
    # Hidden store for results
    dcc.Store(id="backtest-results"),
], fluid=True)


@app.callback(
    [Output("status-msg", "children"),
     Output("equity-chart", "figure"),
     Output("signals-chart", "figure"),
     Output("drawdown-chart", "figure"),
     Output("metrics-table", "children"),
     Output("trade-log", "children")],
    [Input("run-btn", "n_clicks")],
    [State("start-date", "date"),
     State("end-date", "date")],
    prevent_initial_call=False,
)
def run_backtest_callback(n_clicks, start_date, end_date):
    """Run backtest and update all charts."""
    status_messages = []
    
    def status_cb(msg):
        status_messages.append(msg)
    
    try:
        results = run_backtest(start=start_date, end=end_date, status_callback=status_cb)
    except Exception as e:
        empty_fig = go.Figure()
        return (f"Error: {e}", empty_fig, empty_fig, empty_fig, "", "")
    
    portfolio_df = results["portfolio_df"]
    benchmark_df = results["benchmark_df"]
    signals_df = results["signals_df"]
    trades_df = results["trades_df"]
    strategy_metrics = results["strategy_metrics"]
    benchmark_metrics = results["benchmark_metrics"]
    
    # --- Equity Curve ---
    equity_fig = go.Figure()
    equity_fig.add_trace(go.Scatter(
        x=portfolio_df.index, y=portfolio_df["portfolio_value"],
        name="Strategy", line=dict(color="#00d4aa", width=2)
    ))
    if len(benchmark_df) > 0:
        equity_fig.add_trace(go.Scatter(
            x=benchmark_df.index, y=benchmark_df["portfolio_value"],
            name="Buy & Hold TQQQ", line=dict(color="#ff6b6b", width=2, dash="dash")
        ))
    
    # Shade regime regions on equity chart
    regime_colors = {"bull": "rgba(0,200,0,0.07)", "neutral": "rgba(255,255,0,0.07)",
                     "bear": "rgba(255,0,0,0.07)", "crisis": "rgba(255,0,0,0.15)"}
    if "regime" in portfolio_df.columns:
        regime_series = portfolio_df["regime"]
        prev_regime = None
        block_start = None
        for i, (date, regime_val) in enumerate(regime_series.items()):
            if regime_val != prev_regime:
                if prev_regime is not None and block_start is not None:
                    equity_fig.add_vrect(
                        x0=block_start, x1=date,
                        fillcolor=regime_colors.get(prev_regime, "rgba(0,0,0,0)"),
                        layer="below", line_width=0
                    )
                block_start = date
                prev_regime = regime_val
        if prev_regime and block_start:
            equity_fig.add_vrect(
                x0=block_start, x1=portfolio_df.index[-1],
                fillcolor=regime_colors.get(prev_regime, "rgba(0,0,0,0)"),
                layer="below", line_width=0
            )
    
    equity_fig.update_layout(
        title="Portfolio Equity Curve (shaded by regime: green=bull, yellow=neutral, red=bear/crisis)",
        xaxis_title="Date", yaxis_title="Portfolio Value ($)",
        template="plotly_dark", height=400,
    )
    
    # --- Signals Chart ---
    signals_fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                                row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.03,
                                subplot_titles=["TQQQ Price & Regime", "RSI", "Allocation"])
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["tqqq_close"],
        name="TQQQ Price", line=dict(color="#4ecdc4")
    ), row=1, col=1)
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["ema_short"],
        name=f"EMA({config.EMA_SHORT})", line=dict(color="#ffe66d", dash="dot")
    ), row=1, col=1)
    
    # Rebalance markers
    bullish_rebal = signals_df[signals_df["signal"] == "rebalance_bullish"]
    bearish_rebal = signals_df[signals_df["signal"] == "rebalance_bearish"]
    defensive_rebal = signals_df[signals_df["signal"] == "rebalance_defensive"]
    signals_fig.add_trace(go.Scatter(
        x=bullish_rebal.index, y=bullish_rebal["tqqq_close"],
        mode="markers", name="Bullish Rebalance",
        marker=dict(symbol="triangle-up", size=10, color="lime")
    ), row=1, col=1)
    signals_fig.add_trace(go.Scatter(
        x=bearish_rebal.index, y=bearish_rebal["tqqq_close"],
        mode="markers", name="Bearish Rebalance",
        marker=dict(symbol="triangle-down", size=10, color="red")
    ), row=1, col=1)
    signals_fig.add_trace(go.Scatter(
        x=defensive_rebal.index, y=defensive_rebal["tqqq_close"],
        mode="markers", name="Defensive Rebalance",
        marker=dict(symbol="diamond", size=8, color="orange")
    ), row=1, col=1)
    
    # Trailing stop markers (from trade log)
    if len(trades_df) > 0:
        trailing_stop_trades = trades_df[trades_df["signal"].str.contains("trailing_stop", na=False)]
        if len(trailing_stop_trades) > 0:
            ts_dates = pd.to_datetime(trailing_stop_trades["date"])
            ts_prices = signals_df["tqqq_close"].reindex(ts_dates, method="nearest")
            signals_fig.add_trace(go.Scatter(
                x=ts_dates, y=ts_prices,
                mode="markers", name="Trailing Stop",
                marker=dict(symbol="x", size=12, color="magenta", line=dict(width=2))
            ), row=1, col=1)
    
    # RSI subplot
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["rsi"],
        name="RSI", line=dict(color="#a8dadc")
    ), row=2, col=1)
    signals_fig.add_hline(y=config.RSI_OVERSOLD, line_dash="dash", line_color="lime",
                          annotation_text="Oversold", row=2, col=1)
    signals_fig.add_hline(y=config.RSI_OVERBOUGHT, line_dash="dash", line_color="red",
                          annotation_text="Overbought", row=2, col=1)
    
    # Allocation subplot (stacked area)
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["tqqq_alloc"] * 100,
        name="TQQQ %", fill="tozeroy", line=dict(color="#00d4aa"),
        fillcolor="rgba(0,212,170,0.3)"
    ), row=3, col=1)
    signals_fig.add_trace(go.Scatter(
        x=signals_df.index, y=(signals_df["tqqq_alloc"] + signals_df["sqqq_alloc"]) * 100,
        name="SQQQ %", fill="tonexty", line=dict(color="#ff6b6b"),
        fillcolor="rgba(255,107,107,0.3)"
    ), row=3, col=1)
    
    signals_fig.update_layout(
        title="TQQQ Price, RSI & Allocation Breakdown",
        template="plotly_dark", height=700,
    )
    
    # --- Drawdown Chart ---
    values = portfolio_df["portfolio_value"]
    cummax = values.cummax()
    drawdown = (values - cummax) / cummax * 100
    
    drawdown_fig = go.Figure()
    drawdown_fig.add_trace(go.Scatter(
        x=portfolio_df.index, y=drawdown,
        fill="tozeroy", name="Strategy Drawdown",
        line=dict(color="#ff6b6b")
    ))
    if len(benchmark_df) > 0:
        bench_values = benchmark_df["portfolio_value"]
        bench_cummax = bench_values.cummax()
        bench_dd = (bench_values - bench_cummax) / bench_cummax * 100
        drawdown_fig.add_trace(go.Scatter(
            x=benchmark_df.index, y=bench_dd,
            name="Benchmark Drawdown", line=dict(color="#ffa500", dash="dash")
        ))
    drawdown_fig.update_layout(
        title="Drawdown (%)",
        xaxis_title="Date", yaxis_title="Drawdown %",
        template="plotly_dark", height=300,
    )
    
    # --- Metrics Table ---
    def fmt_pct(v):
        return f"{v*100:.2f}%" if v is not None else "N/A"
    
    def fmt_val(v):
        return f"${v:,.2f}" if v is not None else "N/A"
    
    metrics_table = dbc.Table([
        html.Thead(html.Tr([
            html.Th("Metric"), html.Th("Strategy"), html.Th("Buy & Hold TQQQ")
        ])),
        html.Tbody([
            html.Tr([html.Td("Total Return"), html.Td(fmt_pct(strategy_metrics.get("total_return"))), html.Td(fmt_pct(benchmark_metrics.get("total_return")))]),
            html.Tr([html.Td("Annualized Return"), html.Td(fmt_pct(strategy_metrics.get("annualized_return"))), html.Td(fmt_pct(benchmark_metrics.get("annualized_return")))]),
            html.Tr([html.Td("Max Drawdown"), html.Td(fmt_pct(strategy_metrics.get("max_drawdown"))), html.Td(fmt_pct(benchmark_metrics.get("max_drawdown")))]),
            html.Tr([html.Td("Sharpe Ratio"), html.Td(f"{strategy_metrics.get('sharpe_ratio', 0):.2f}"), html.Td(f"{benchmark_metrics.get('sharpe_ratio', 0):.2f}")]),
            html.Tr([html.Td("Final Value"), html.Td(fmt_val(strategy_metrics.get("final_value"))), html.Td(fmt_val(benchmark_metrics.get("final_value")))]),
            html.Tr([html.Td("Rebalances"), html.Td(str(strategy_metrics.get("num_trades", 0))), html.Td("0")]),
            html.Tr([html.Td("Bullish Rebal"), html.Td(str(strategy_metrics.get("bull_rebal", 0))), html.Td("N/A")]),
            html.Tr([html.Td("Bearish Rebal"), html.Td(str(strategy_metrics.get("bear_rebal", 0))), html.Td("N/A")]),
            html.Tr([html.Td("Defensive Rebal"), html.Td(str(strategy_metrics.get("defensive_rebal", 0))), html.Td("N/A")]),
        ])
    ], bordered=True, color="dark", striped=True, hover=True, size="sm")
    
    # --- Trade Log (show buy/sell for both TQQQ and SQQQ) ---
    if len(trades_df) > 0:
        display_df = trades_df[["date", "signal", "regime",
                                "tqqq_action", "tqqq_alloc_from", "tqqq_alloc_to", "tqqq_price",
                                "sqqq_action", "sqqq_alloc_from", "sqqq_alloc_to", "sqqq_price",
                                "cash_after", "portfolio_value", "gain_loss"]].copy()
        display_df.columns = ["Date", "Signal", "Regime",
                              "TQQQ Action", "TQQQ% From", "TQQQ% To", "TQQQ Exec Price",
                              "SQQQ Action", "SQQQ% From", "SQQQ% To", "SQQQ Exec Price",
                              "Cash $", "Portfolio $", "Gain/Loss $"]
        display_df = display_df.round(2)
        trade_table = dbc.Table.from_dataframe(
            display_df.head(100), striped=True, bordered=True, hover=True, color="dark", size="sm"
        )
    else:
        trade_table = html.P("No trades executed in this period.")
    
    status = f"Backtest complete: {start_date} to {end_date} | {len(trades_df)} trades"
    return status, equity_fig, signals_fig, drawdown_fig, metrics_table, trade_table


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
        return html.P("No forward test data yet. Run the forward test to start paper trading.")
    
    # Show recent history
    history = state["portfolio_history"][-30:]  # Last 30 days
    
    content = [
        html.P(f"Current Regime: {history[-1].get('regime', 'N/A').upper()}", className="text-warning"),
        html.P(f"Current Value: ${history[-1]['portfolio_value']}"),
        html.P(f"TQQQ: {history[-1].get('tqqq_alloc', 'N/A')} | SQQQ: {history[-1].get('sqqq_alloc', 'N/A')}"),
        html.P(f"Total Rebalances: {len(state['trades'])}"),
        html.Hr(),
        html.H6("Recent Activity"),
    ]
    
    # Recent trades
    if state["trades"]:
        recent_trades = state["trades"][-10:]
        trade_rows = [html.Tr([
            html.Td(t["date"]), html.Td(t["action"]), html.Td(t.get("regime", "")),
            html.Td(t.get("tqqq_alloc", "")), html.Td(t.get("sqqq_alloc", "")),
            html.Td(f"${t.get('portfolio_value', 0)}")
        ]) for t in recent_trades]
        content.append(dbc.Table([
            html.Thead(html.Tr([html.Th("Date"), html.Th("Action"), html.Th("Regime"),
                               html.Th("TQQQ%"), html.Th("SQQQ%"), html.Th("Value")])),
            html.Tbody(trade_rows)
        ], bordered=True, color="dark", size="sm"))
    
    # Status messages
    if messages:
        content.append(html.P(" | ".join(messages[-3:]), className="text-muted small"))
    
    return content


def run_dashboard():
    """Start the dashboard server."""
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, debug=False)


if __name__ == "__main__":
    run_dashboard()
