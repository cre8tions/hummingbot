from decimal import Decimal
from typing import Any, Dict

from pydantic import Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap, ClientFieldData
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True
EXAMPLE_PAIR = "ZRX-ETH"

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.003"),
    taker_percent_fee_decimal=Decimal("0.003"),
    buy_percent_fee_deducted_from_returns=True
)


def is_exchange_information_valid(exchange_info: Dict[str, Any]) -> bool:
    """
    Verifies if a trading pair is enabled to operate with based on its exchange information
    :param exchange_info: the exchange information for a trading pair
    :return: True if the trading pair is enabled, False otherwise
    """
    return exchange_info.get("status", None) == "TRADING" and "SPOT" in exchange_info.get("permissions", list())


class SouthXchangeConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="SouthXchange", const=True, client_data=None)
    SouthXchange_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your SouthXchange API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    SouthXchange_api_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your SouthXchange API secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "SouthXchange"


KEYS = SouthXchangeConfigMap.construct()

# OTHER_DOMAINS = ["SouthXchange_us"]
# OTHER_DOMAINS_PARAMETER = {"SouthXchange_us": "us"}
# OTHER_DOMAINS_EXAMPLE_PAIR = {"SouthXchange_us": "BTC-USDT"}
# OTHER_DOMAINS_DEFAULT_FEES = {"SouthXchange_us": DEFAULT_FEES}


class SouthXchangeUSConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="SouthXchange_us", const=True, client_data=None)
    SouthXchange_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your SouthXchange US API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    SouthXchange_api_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your SouthXchange US API secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "SouthXchange_us"


OTHER_DOMAINS_KEYS = {"SouthXchange_us": SouthXchangeUSConfigMap.construct()}
