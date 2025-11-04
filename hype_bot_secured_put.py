import time
import os
from datetime import datetime
import ccxt
from dotenv import load_dotenv

# Load environment variables from .env file (if it exists)
load_dotenv()

# Configuration
COIN = "HYPE"
THRESHOLD = 37.65
POSITION_SIZE_USD = 10.0
LEVERAGE = 1
PRICE_OPTIMIZATION_DELAY = 0.5  # Wait up to 1 second to get better price near threshold
PRICE_OPTIMIZATION_TOLERANCE = 0.01  # 1 cent tolerance - execute if price gets within this of threshold

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

class HyperliquidSecuredPutBot:
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
                'defaultSlippage': 0.005,  # 0.5% default slippage for market orders
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
        """Check current position using CCXT - returns True for short positions"""
        try:
            positions = self.exchange.fetch_positions([self.symbol])
            
            if positions:
                for pos in positions:
                    # Check if position is for our coin
                    pos_symbol = pos.get('symbol', '')
                    if (pos_symbol == self.symbol or 
                        COIN.upper() in pos_symbol.upper()):
                        # Get position size
                        size = None
                        if 'contracts' in pos and pos['contracts'] is not None:
                            size = float(pos['contracts'])
                        elif 'contractSize' in pos and pos['contractSize'] is not None:
                            size = float(pos['contractSize'])
                        elif 'size' in pos and pos['size'] is not None:
                            size = float(pos['size'])
                        
                        if size is not None and abs(size) > 0.0001:
                            # CCXT normalizes contracts to positive, so check 'side' field instead
                            side = pos.get('side', '').lower()
                            # Also check raw data if available
                            raw_size = None
                            if 'info' in pos and isinstance(pos['info'], dict):
                                position_data = pos['info'].get('position', {})
                                if 'szi' in position_data:
                                    raw_size = float(position_data['szi'])
                            
                            # Determine if short: check 'side' field first, then raw size
                            is_short = False
                            if side == 'short':
                                is_short = True
                            elif raw_size is not None and raw_size < 0:
                                is_short = True
                            
                            # if is_short:
                            #     self.log(f"🔍 SHORT position detected: symbol={pos_symbol}, size={size:.6f}, side={side}")
                            # else:
                            #     self.log(f"🔍 LONG position detected: symbol={pos_symbol}, size={size:.6f}, side={side}")
                            
                            return is_short
            
            return False
        except Exception as e:
            self.log(f"Error checking position: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False
    
    def get_position_size(self):
        """Get current position size (absolute value for short positions)"""
        try:
            positions = self.exchange.fetch_positions([self.symbol])
            
            if positions:
                for pos in positions:
                    if (pos['symbol'] == self.symbol or 
                        COIN.upper() in pos['symbol'].upper()):
                        size = float(pos['contracts']) if pos['contracts'] else 0
                        return abs(size)  # Return absolute value for short positions
            return 0
        except Exception as e:
            self.log(f"Error getting position size: {e}")
            return 0
    
    def _optimize_price_near_threshold(self, initial_price, threshold, direction='below'):
        """Wait briefly to get better price execution near threshold, executes early if price slips away"""
        if initial_price is None:
            return initial_price
        
        best_price = initial_price
        best_distance = abs(initial_price - threshold)
        start_time = time.time()
        consecutive_worse = 0  # Track if price is getting worse
        
        # Only optimize if we're not already very close to threshold
        if best_distance <= PRICE_OPTIMIZATION_TOLERANCE:
            return initial_price  # Already close enough
        
        self.log(f"⏳ Optimizing price near threshold (waiting up to {PRICE_OPTIMIZATION_DELAY}s)...")
        
        while time.time() - start_time < PRICE_OPTIMIZATION_DELAY:
            time.sleep(0.2)  # Check every 200ms
            current_price = self.get_price()
            
            if current_price is None:
                continue
            
            current_distance = abs(current_price - threshold)
            
            if direction == 'below':
                # For opening SHORT: want price as close to threshold as possible (from below)
                # Better if price moves up towards threshold (distance decreases)
                if current_distance < best_distance:
                    best_price = current_price
                    best_distance = current_distance
                    consecutive_worse = 0  # Reset counter
                    if current_distance <= PRICE_OPTIMIZATION_TOLERANCE:
                        self.log(f"✅ Price reached within ${PRICE_OPTIMIZATION_TOLERANCE:.2f} of threshold")
                        break
                else:
                    consecutive_worse += 1
                    # If price moves away significantly (more than 2x tolerance), execute immediately
                    if current_distance > best_distance * 2 and current_distance > PRICE_OPTIMIZATION_TOLERANCE * 2:
                        self.log(f"⚠️  Price slipping away from threshold (${current_price:.2f}), executing with best price ${best_price:.2f}")
                        break
            else:  # direction == 'above'
                # For closing SHORT: want price as close to threshold as possible (from above)
                # Better if price moves down towards threshold (distance decreases)
                if current_distance < best_distance:
                    best_price = current_price
                    best_distance = current_distance
                    consecutive_worse = 0  # Reset counter
                    if current_distance <= PRICE_OPTIMIZATION_TOLERANCE:
                        self.log(f"✅ Price reached within ${PRICE_OPTIMIZATION_TOLERANCE:.2f} of threshold")
                        break
                else:
                    consecutive_worse += 1
                    # If price moves away significantly (more than 2x tolerance), execute immediately
                    if current_distance > best_distance * 2 and current_distance > PRICE_OPTIMIZATION_TOLERANCE * 2:
                        self.log(f"⚠️  Price slipping away from threshold (${current_price:.2f}), executing with best price ${best_price:.2f}")
                        break
            
            # If price gets worse for 3 consecutive checks (0.6s), execute to avoid further slippage
            if consecutive_worse >= 3:
                self.log(f"⚠️  Price moving away for {consecutive_worse * 0.2:.1f}s, executing to prevent slippage")
                break
        
        price_improvement = abs(initial_price - threshold) - abs(best_price - threshold)
        if price_improvement > 0:
            self.log(f"📊 Price optimized: ${initial_price:.2f} → ${best_price:.2f} (${price_improvement:.2f} closer to threshold)")
        elif price_improvement < 0:
            self.log(f"📊 Price slipped: ${initial_price:.2f} → ${best_price:.2f} (using best seen: ${best_price:.2f})")
        else:
            self.log(f"📊 Using initial price: ${best_price:.2f}")
        
        return best_price
    
    def open_short(self, price):
        """Open isolated SHORT position using CCXT - optimizes for price near threshold"""
        try:
            # Optimize entry price: wait briefly if price is moving towards threshold
            best_price = self._optimize_price_near_threshold(price, THRESHOLD, direction='below')
            
            # Calculate size in coins with small buffer to ensure minimum $10 order value
            # Use best_price for size calculation to ensure we meet minimum
            size = (POSITION_SIZE_USD * 1.005) / best_price
            
            # Verify order value meets minimum
            order_value = size * best_price
            if order_value < 10.0:
                self.log(f"⚠️  Calculated order value ${order_value:.2f} is below $10 minimum")
                # Increase size to meet minimum
                size = 10.0 / best_price
                self.log(f"⚠️  Adjusted size to ${size * best_price:.2f} to meet minimum")
            
            self.log(f"📉 Attempting to open SHORT position: {size:.4f} {COIN} at ${best_price:.2f} (value: ${size * best_price:.2f})")
            
            # Create market sell order to open short position
            # Hyperliquid requires price parameter for market orders to calculate max slippage
            # Leverage should already be set via set_leverage() in __init__
            order = self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side='sell',  # Sell to open short
                amount=size,
                price=best_price,  # Use optimized price for slippage calculation
                params={
                    'type': 'swap',  # Explicitly use swap/perp, not spot
                    'marginMode': 'isolated',  # Use isolated margin
                }
            )
            
            self.log(f"✅ OPENED SHORT at ${best_price:.2f} | Size: {size:.4f} {COIN} (${POSITION_SIZE_USD})")
            self.log(f"Order ID: {order.get('id', 'N/A')}")
            self.in_position = True
            return order
            
        except Exception as e:
            self.log(f"❌ Error opening short position: {e}")
            # Log more details for debugging
            if hasattr(e, 'args'):
                self.log(f"Error details: {e.args}")
            return None
    
    def close_position(self, price):
        """Close current SHORT position using CCXT - optimizes for price near threshold"""
        try:
            position_size = self.get_position_size()
            
            if position_size == 0:
                self.log("No position to close")
                self.in_position = False
                return
            
            # Optimize exit price: wait briefly if price is moving towards threshold
            best_price = self._optimize_price_near_threshold(price, THRESHOLD, direction='above')
            
            self.log(f"📈 Attempting to close SHORT position: {position_size:.4f} {COIN} at ${best_price:.2f}")
            
            # Create market buy order to close short position (buy to close short)
            # Hyperliquid requires price parameter for market orders to calculate max slippage
            # Explicitly specify type='swap' to ensure we're trading perpetuals, not spot
            order = self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side='buy',  # Buy to close short
                amount=position_size,
                price=best_price,  # Use optimized price for slippage calculation
                params={
                    'reduceOnly': True,  # Reduce-only order
                    'type': 'swap',  # Explicitly use swap/perp, not spot
                    'marginMode': 'isolated',  # Use isolated margin
                }
            )
            
            self.log(f"🔴 CLOSED SHORT POSITION at ${best_price:.2f}")
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
        self.log("🤖 Hyperliquid $HYPE Secured Put Bot Started (CCXT)")
        self.log(f"📊 Strategy: SHORT when price crosses below ${THRESHOLD}, Close when price crosses above ${THRESHOLD}")
        self.log(f"💰 Position Size: ${POSITION_SIZE_USD} at {LEVERAGE}x leverage (isolated)")
        self.log(f"🔑 Trading Address: {WALLET_ADDRESS}")
        self.log(f"📈 Trading Symbol: {self.symbol}")
        self.log("Press Ctrl+C to stop\n")
        
        # Initialize previous_price with current price to detect crossings
        initial_price = self.get_price()
        previous_price = initial_price if initial_price else None
        
        if initial_price:
            initial_position = self.get_position()
            if initial_price < THRESHOLD:
                if initial_position:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is below threshold ${THRESHOLD:.2f} with open SHORT position")
                    self.log(f"   Will close position when price crosses above ${THRESHOLD:.2f}")
                else:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is below threshold ${THRESHOLD:.2f} with no position")
                    self.log(f"   Waiting for price to rise above ${THRESHOLD:.2f}, then will SHORT when it crosses back below")
            else:
                if initial_position:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is above threshold ${THRESHOLD:.2f} with open SHORT position")
                    self.log(f"   Will close position immediately")
                    self.close_position(initial_price)
                else:
                    self.log(f"ℹ️  Bot started: Price ${initial_price:.2f} is above threshold ${THRESHOLD:.2f} with no position")
                    self.log(f"   Will SHORT when price crosses below ${THRESHOLD:.2f}")
        
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
              
                    status = "📉 SHORT POSITION" if actual_position else "📊 NO POSITION"
                    threshold_status = "ABOVE" if price > THRESHOLD else "BELOW"
                    self.log(f"{status} | Current Price: ${price:.2f} | Threshold: ${THRESHOLD:.2f} ({threshold_status})")
                    self.last_price = price
                
                # Trading logic - check closing FIRST if we have a position
                # Close SHORT position when price crosses ABOVE threshold (from below to above)
                if actual_position:
                    self.log(f"324")
                    # Check if price crossed above threshold
                    if previous_price is not None and previous_price < THRESHOLD and price >= THRESHOLD:
                        self.log(f"📈 Price crossed above ${THRESHOLD} (from ${previous_price:.2f} to ${price:.2f}) with open SHORT position!")
                        self.close_position(price)
                    # Safety net: if price is at or above threshold with position, close it
                    elif price > THRESHOLD:
                        self.log(f"⚠️  Price is at/above threshold ${THRESHOLD:.2f} (${price:.2f}) with open SHORT position - closing!")
                        self.close_position(price)
                
                # SHORT when price crosses BELOW threshold (from above to below) - only if no position
                elif previous_price is not None and previous_price >= THRESHOLD and price < THRESHOLD and not actual_position:
                    self.log(f"📉 Price crossed below ${THRESHOLD} (from ${previous_price:.2f} to ${price:.2f})!")
                    self.log(f"🛒 Opening SHORT position at ${price:.2f}")
                    self.open_short(price)
                
                # Update previous price for next iteration
                previous_price = price
                
                time.sleep(2)  # Check every 2 seconds
                
        except KeyboardInterrupt:
            self.log("\n🛑 Bot stopped by user")
            if self.in_position:
                self.log("⚠️  Warning: You still have an open SHORT position!")
        except Exception as e:
            self.log(f"❌ Fatal error: {e}")
            import traceback
            self.log(traceback.format_exc())

if __name__ == "__main__":
    bot = HyperliquidSecuredPutBot()
    bot.run()

