# Exness Signal Bot (MT5)

Scans forex, gold, silver, oil, indices and stock CFDs on your Exness MT5 account. It sends signals to Telegram and can optionally place orders, with safety checks.

## Setup
1. Install the **MetaTrader 5** terminal from Exness and log in. Start with a **demo** account.
2. In MT5, go to Tools → Options → Expert Advisors and tick **Allow algorithmic trading**. You only need this if execution is on.
3. Install the Python packages: `pip install MetaTrader5 pandas pyyaml`
4. Optional Telegram alerts: create a bot with @BotFather, then set these environment variables:
   `setx TELEGRAM_BOT_TOKEN "..."` and `setx TELEGRAM_CHAT_ID "..."`
5. Run `python bot.py symbols` and fix the symbol names in `config.yaml` to match your account (for example `XAUUSDm` or `XAUUSD`).

## Use
- `python bot.py scan`: one pass over the watchlist
- `python bot.py run`: runs continuously and checks after every bar closes
- `python bot.py --json scan`: JSON output for agents and CLI-Anything
- `python test_strategy.py`: offline logic tests

## Strategy (default)
EMA 20/50 crossover in the direction of EMA 200, with an RSI filter. Stop loss is 1.5×ATR and take profit is 3×ATR. Edit `strategy.py` to change it, and backtest any change before using it on a live account.

## Safety
- `execution.enabled: false` is the default, so the bot only sends signals.
- `demo_only: true` makes the bot refuse to trade unless the account is a demo account.
- Risk is 0.5% of balance per trade, with at most 3 open positions and a 2% daily loss stop. Only orders tagged with the bot's magic number count.
- The bot attaches to the MT5 terminal you are already logged in to and never stores your password.
