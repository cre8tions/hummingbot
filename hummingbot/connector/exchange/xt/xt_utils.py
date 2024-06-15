from decimal import Decimal
from typing import Any, Dict

from pydantic import Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap, ClientFieldData
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True
EXAMPLE_PAIR = "ZRX-ETH"

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.001"),
    taker_percent_fee_decimal=Decimal("0.001"),
    buy_percent_fee_deducted_from_returns=True
)


def is_exchange_information_valid(exchange_info: Dict[str, Any]) -> bool:
    """
    Verifies if a trading pair is enabled to operate with based on its exchange information
    :param exchange_info: the exchange information for a trading pair
    :return: True if the trading pair is enabled, False otherwise
    """
    is_spot = False
    is_trading = False

    if exchange_info.get("status", None) == "TRADING":
        is_trading = True

    permissions_sets = exchange_info.get("permissionSets", list())
    for permission_set in permissions_sets:
        # PermissionSet is a list, find if in this list we have "SPOT" value or not
        if "SPOT" in permission_set:
            is_spot = True
            break

    return is_trading and is_spot


class XtConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="xt", const=True, client_data=None)
    xt_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Xt API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    xt_api_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Xt API secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "xt"


KEYS = XtConfigMap.construct()

OTHER_DOMAINS = ["xt_us"]
OTHER_DOMAINS_PARAMETER = {"xt_us": "us"}
OTHER_DOMAINS_EXAMPLE_PAIR = {"xt_us": "BTC-USDT"}
OTHER_DOMAINS_DEFAULT_FEES = {"xt_us": DEFAULT_FEES}


class XtUSConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="xt_us", const=True, client_data=None)
    xt_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Xt US API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    xt_api_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Xt US API secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "xt_us"


OTHER_DOMAINS_KEYS = {"xt_us": XtUSConfigMap.construct()}
