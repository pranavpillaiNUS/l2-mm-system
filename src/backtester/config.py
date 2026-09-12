from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
import yaml

@dataclass
class FeeConfig:
    maker_bps: float = 10.0 # Binance spot fees (adding liquidity, usually unfufilled limit orders)
    taker_bps: float = 10.0 # Binance spot fees (remove liquidity, typically market/quick limit orders)

    @property
    def maker_rate(self) -> float: # convert bps to decimal, 1bps = 0.01% = 0.0001
        return self.maker_bps/10000
    
    @property
    def taker_rate(self) -> float: # convert bps to decimal, 1bps = 0.01% = 0.0001
        return self.taker_bps/10000
    
@dataclass
class SlippageConfig:
    spread_bps: float = 5.0 #bps of half-spread (half of bid-ask spread is paid in fees)
    impact_bps_per_unit: float = 1.0 # price impact per unit traded (assuming large orders move price)

@dataclass
class BacktestConfig:
    #Data source
    data_path: Path = field(default_factory=lambda: Path("data/bars"))
    symbol: str = 'BTCUSDT'
    start_date: Optional[str] = None #Data collected using YYYY-MM-DD (ISO) format
    end_date: Optional[str] = None

    #Captial
    initial_cash: float = 100_000.0 # 100_000.0 means 100000.00

    #Cost models
    fees: FeeConfig = field(default_factory=FeeConfig)
    slippage: SlippageConfig = field(default_factory=SlippageConfig)

    #Risk limits
    max_position: float = 10.0 # Only hold 10 BTC max
    max_leverage: float = 1.0 # No leverage (looks at cash + PnL)

    #Output
    output_dir: Path = field(default_factory=lambda: Path("results"))
    run_name: str = 'backtest'

    @classmethod
    def from_yaml(cls, path: Path) -> "BacktestConfig": # to load configuaration from YAML file
        with open(path) as f:
            data = yaml.safe_load(f) or {} # {} prevents "fees" in data from crashing if yaml.safe_load(f) returns None
        
        # to handle nested dataclasses, like FeeConfig and SlippageConfig
        if "fees" in data:
            data["fees"] = FeeConfig(**data["fees"])
        if "slippage" in data:
            data["slippage"] = SlippageConfig(**data["slippage"])
        
        # Handle Path objects
        for key in ["data_path", "output_dir"]:
            if key in data and data[key]:
                data[key] = Path(data[key])
        
        return cls(**data) # By using cls instead of hardcoding BacktestConfig(), I can subclass it
    
    def to_yaml(self, path: Path):
        data = {
            "data_path": str(self.data_path),
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_cash": self.initial_cash,
            "fees":{
                "maker_bps": self.fees.maker_bps,
                "taker_bps": self.fees.taker_bps,
            },
            "slippage": {
            "spread_bps": self.slippage.spread_bps,
            "impact_bps_per_unit": self.slippage.impact_bps_per_unit,
            },
            "max_position": self.max_position,
            "max_leverage": self.max_leverage,
            "output_dir": str(self.output_dir),
            "run_name": self.run_name,        
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    def __str__(self) -> str: # make it readable
        return (
            f"BacktestConfig({self.symbol}, "
            f"cash=${self.initial_cash:,.0f}, "
            f"fees={self.fees.maker_bps}/{self.fees.taker_bps}bps)"
        )


    
    


