import time
import os
from datetime import datetime
import ccxt
from dotenv import load_dotenv

# Load environment variables from .env file (if it exists)
load_dotenv()

# Configuration
COIN = "HYPE"
THRESHOLD = 37.55
POSITION_SIZE_USD = 10.0
LEVERAGE = 1

# Security: Load credentials from environment variables
# Set these in your shell:
# export HYPERLIQUID_WALLET_ADDRESS="0x..."
# export HYPERLIQUID_PRIVATE_KEY="0x..."
WALLET_ADDRESS = os.getenv("HYPERLIQUID_WALLET_ADDRESS")
PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY")

if not WALLET_ADDRESS or not PRIVATE_KEY:
    raise ValueError(
        "⚠️  SECURITY ERROR: Missing Hyperliquid credentials!\n"
        "Set these environment variables:\n"
        "  export HYPERLIQUID_WALLET_ADDRESS='0x...'\n"
        "  export HYPERLIQUID_PRIVATE_KEY='0x...'\n"
        "\n"
        "Note: Use your Hyperliquid API agent wallet credentials.\n"
        "Create one at: https://app.hyperliquid.xyz/API"
    )

class HyperliquidBot:
    def __init__(self):
        """Initialize the bot with CCXT Hyperliquid exchange"""
        self.in_position = False
        self.last_price = 0
        
        # Initialize CCXT Hyperliquid exchange
        self.exchange = ccxt.hyperliquid({
            'walletAddress': WALLET_ADDRESS,
            'privateKey': PRIVATE_KEY,
            'enableRateLimit': True,  # Built-in rate limiting
            'options': {
                'defaultType': 'swap',  # For perpetual futures
                'defaultSlippage': 0.01,  # 5% default slippage for market orders
            }
        })
        
        # Determine the symbol format - only use HYPE-USDC perpetual
        self.symbol = None
        self._find_symbol()  # Will raise error if symbol not found or is spot
        
        # Set leverage for the symbol before placing orders
        self._set_leverage()
        
    def _set_leverage(self):
        """Set leverage for the trading symbol with isolated margin"""
        try:
            if self.symbol:
                # Set leverage with isolated margin mode
                self.exchange.set_leverage(LEVERAGE, self.symbol, params={'marginMode': 'isolated'})
                self.log(f"✅ Set leverage to {LEVERAGE}x (isolated) for {self.symbol}")
        except Exception as e:
            self.log(f"⚠️  Warning: Could not set leverage: {e}")
            self.log("⚠️  Position may open with default/previous leverage setting")
    
    def _find_symbol(self):
        """Find HYPE/USDC:USDC perpetual market only - never use spot"""
        try:
            markets = self.exchange.load_markets()
            
            if not markets:
                self.log("❌ ERROR: No markets loaded")
                raise ValueError("Cannot load markets from exchange")
            
            # Look for HYPE/USDC:USDC perpetual (CCXT format)
            known_symbol = f"{COIN}/USDC:USDC"
            if known_symbol in markets:
                market = markets[known_symbol]
                market_type = market.get('type', '').lower()
                
                # Only accept swap/perpetual markets, reject spot
                if market_type in ['swap', 'perpetual', 'future', 'futures']:
                    self.symbol = known_symbol
                    self.log(f"✅ Found perpetual symbol: {known_symbol} (type: {market_type})")
                    return
                else:
                    self.log(f"❌ ERROR: {known_symbol} exists but is not a perpetual market (type: {market_type})")
                    self.log(f"❌ Only perpetual markets are allowed, rejecting spot market")
                    raise ValueError(f"{known_symbol} is a {market_type} market, not perpetual")
            else:
                self.log(f"❌ ERROR: {known_symbol} perpetual market not found")
                self.log(f"❌ Available HYPE markets:")
                for sym in markets.keys():
                    if COIN.upper() in sym.upper():
                        market = markets[sym]
                        self.log(f"   - {sym} (type: {market.get('type', 'unknown')})")
                raise ValueError(f"{known_symbol} perpetual market not found")
         
        except Exception as e:
            self.log(f"❌ Fatal error finding symbol: {e}")
            raise  # Re-raise to prevent bot from starting with wrong symbol
        
    def log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")
    
    def get_price(self):
        """Fetch current HYPE price using CCXT"""
        if not self.symbol:
            self.log("⚠️  Symbol not set, cannot fetch price")
            return None
        
        # Ensure symbol is a string
        if not isinstance(self.symbol, str):
            self.log(f"⚠️  Invalid symbol type: {type(self.symbol)}")
            return None
            
        try:
            ticker = self.exchange.fetch_ticker(self.symbol)
            if ticker and 'last' in ticker and ticker['last'] is not None:
                price = float(ticker['last'])
                return price
            else:
                self.log(f"⚠️  Ticker data incomplete for {self.symbol}")
                return None
        except Exception as e:
            self.log(f"Error fetching price for {self.symbol}: {e}")
            # Log more details for debugging
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
            # Fallback: try alternative method
            try:
                tickers = self.exchange.fetch_tickers()
                if tickers and self.symbol in tickers:
                    ticker_data = tickers[self.symbol]
                    if ticker_data and 'last' in ticker_data and ticker_data['last'] is not None:
                        return float(ticker_data['last'])
            except Exception as e2:
                self.log(f"Error in fallback price fetch: {e2}")
            return None
    
    def get_position(self):
        """Check current position using CCXT"""
        try:
            positions = self.exchange.fetch_positions([self.symbol])
            
            if positions:
                for pos in positions:
                    # Check if position is for our coin and has size > 0
                    if (pos['symbol'] == self.symbol or 
                        COIN.upper() in pos['symbol'].upper()):
                        size = float(pos['contracts']) if pos['contracts'] else 0
                        return size > 0
            
            return False
        except Exception as e:
            self.log(f"Error checking position: {e}")
            return False
    
    def get_position_size(self):
        """Get current position size"""
        try:
            positions = self.exchange.fetch_positions([self.symbol])
            
            if positions:
                for pos in positions:
                    if (pos['symbol'] == self.symbol or 
                        COIN.upper() in pos['symbol'].upper()):
                        return abs(float(pos['contracts'])) if pos['contracts'] else 0
            return 0
        except Exception as e:
            self.log(f"Error getting position size: {e}")
            return 0
    
    def open_long(self, price):
        """Open isolated long position using CCXT"""
        try:
            # Calculate size in coins with small buffer to ensure minimum $10 order value
            # Add 2% buffer to account for fees and ensure we meet minimum order size
            size = (POSITION_SIZE_USD * 1.02) / price
            
            # Verify order value meets minimum
            order_value = size * price
            if order_value < 10.0:
                self.log(f"⚠️  Calculated order value ${order_value:.2f} is below $10 minimum")
                # Increase size to meet minimum
                size = 10.0 / price
                self.log(f"⚠️  Adjusted size to ${size * price:.2f} to meet minimum")
            
            self.log(f"📈 Attempting to open long position: {size:.4f} {COIN} at ${price:.2f} (value: ${size * price:.2f})")
            
            # Create market buy order with price for slippage calculation
            # Hyperliquid requires price parameter for market orders to calculate max slippage
            # Leverage should already be set via set_leverage() in __init__
            order = self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side='buy',
                amount=size,
                price=price,  # Required for Hyperliquid slippage calculation (even for market orders)
                params={
                    'type': 'swap',  # Explicitly use swap/perp, not spot
                    'marginMode': 'isolated',  # Use isolated margin
                }
            )
            
            self.log(f"✅ OPENED LONG at ${price:.2f} | Size: {size:.4f} {COIN} (${POSITION_SIZE_USD})")
            self.log(f"Order ID: {order.get('id', 'N/A')}")
            self.in_position = True
            return order
            
        except Exception as e:
            self.log(f"❌ Error opening position: {e}")
            # Log more details for debugging
            if hasattr(e, 'args'):
                self.log(f"Error details: {e.args}")
            return None
    
    def close_position(self, price):
        """Close current position using CCXT"""
        try:
            position_size = self.get_position_size()
            
            if position_size == 0:
                self.log("No position to close")
                self.in_position = False
                return
            
            self.log(f"📉 Attempting to close position: {position_size:.4f} {COIN} at ${price:.2f}")
            
            # Create market sell order to close position with price for slippage calculation
            # Hyperliquid requires price parameter for market orders to calculate max slippage
            # Explicitly specify type='swap' to ensure we're trading perpetuals, not spot
            order = self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side='sell',
                amount=position_size,
                price=price,  # Required for Hyperliquid slippage calculation (even for market orders)
                params={
                    'reduceOnly': True,  # Reduce-only order
                    'type': 'swap',  # Explicitly use swap/perp, not spot
                    'marginMode': 'isolated',  # Use isolated margin
                }
            )
            
            self.log(f"🔴 CLOSED POSITION at ${price:.2f}")
            self.log(f"Order ID: {order.get('id', 'N/A')}")
            self.in_position = False
            return order
            
        except Exception as e:
            self.log(f"❌ Error closing position: {e}")
            # Log more details for debugging
            if hasattr(e, 'args'):
                self.log(f"Error details: {e.args}")
            return None
    
    def run(self):
        """Main bot loop"""
        self.log("🤖 Hyperliquid $HYPE Trading Bot Started (CCXT)")
        self.log(f"📊 Strategy: Buy when price crosses above ${THRESHOLD}, Close when price crosses below ${THRESHOLD}")
        self.log(f"💰 Position Size: ${POSITION_SIZE_USD} at {LEVERAGE}x leverage (isolated)")
        self.log(f"🔑 Trading Address: {WALLET_ADDRESS}")
        self.log(f"📈 Trading Symbol: {self.symbol}")
        self.log("Press Ctrl+C to stop\n")
        
        # Initialize previous_price with current price to detect crossings
        initial_price = self.get_price()
        previous_price = initial_price if initial_price else None
        
        if initial_price:
            initial_position = self.get_position()
            if initial_price > THRESHOLD:
                if initial_position:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is above threshold ${THRESHOLD:.2f} with open position")
                    self.log(f"   Will close position when price crosses below ${THRESHOLD:.2f}")
                else:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is above threshold ${THRESHOLD:.2f} with no position")
                    self.log(f"   Waiting for price to drop below ${THRESHOLD:.2f}, then will buy when it crosses back above")
            else:
                if initial_position:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is below threshold ${THRESHOLD:.2f} with open position")
                    self.log(f"   Will close position immediately")
                    self.close_position(initial_price)
                else:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is below threshold ${THRESHOLD:.2f} with no position")
                    self.log(f"   Will buy when price crosses above ${THRESHOLD:.2f}")
        
        try:
            while True:
                price = self.get_price()
                
                if price is None:
                    time.sleep(5)
                    continue
                
                # Check actual position status
                actual_position = self.get_position()
                
                # Log price update
                if price != self.last_price:
                    status = "📈 IN POSITION" if actual_position else "📊 NO POSITION"
                    self.log(f"{status} | Current Price: ${price:.2f}")
                    self.last_price = price
                
                # Trading logic
                # Buy when price crosses ABOVE threshold (from below to above)
                if previous_price is not None and previous_price <= THRESHOLD and price > THRESHOLD and not actual_position:
                    self.log(f"📈 Price crossed above ${THRESHOLD} (from ${previous_price:.2f} to ${price:.2f})!")
                    self.log(f"🛒 Opening long position at ${price:.2f}")
                    self.open_long(price)
                    
                # Close position when price crosses BELOW threshold (from above to below)
                elif previous_price is not None and previous_price > THRESHOLD and price <= THRESHOLD and actual_position:
                    self.log(f"📉 Price crossed below ${THRESHOLD} (from ${previous_price:.2f} to ${price:.2f}) with open position!")
                    self.close_position(price)
                
                # Update previous price for next iteration
                previous_price = price
                
                time.sleep(2)  # Check every 2 seconds
                
        except KeyboardInterrupt:
            self.log("\n🛑 Bot stopped by user")
            if self.in_position:
                self.log("⚠️  Warning: You still have an open position!")
        except Exception as e:
            self.log(f"❌ Fatal error: {e}")
            import traceback
            self.log(traceback.format_exc())

if __name__ == "__main__":
    bot = HyperliquidBot()
    bot.run()
