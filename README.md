# Hype Bot - Hyperliquid Trading Bot

Automated trading bot for Hyperliquid exchange that trades HYPE coin based on price thresholds.

## Features

- **Long Bot** (`hype_bot.py`): Opens long positions when price crosses above threshold
- **Secured Put Bot** (`hype_bot_secured_put.py`): Opens short positions when price crosses below threshold
- Uses CCXT library for unified exchange interface
- Isolated margin mode with configurable leverage
- Price optimization to get better execution near threshold
- Automatic position management

## Requirements

- Python 3.8+
- CCXT library
- python-dotenv
- Hyperliquid API agent wallet

## License

Private use only. Keep your API keys secure.

