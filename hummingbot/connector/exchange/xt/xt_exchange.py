import asyncio
import decimal
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from bidict import bidict

from hummingbot.connector.constants import s_decimal_NaN
from hummingbot.connector.exchange.xt import xt_constants as CONSTANTS, xt_utils, xt_web_utils as web_utils
from hummingbot.connector.exchange.xt.xt_api_order_book_data_source import XtAPIOrderBookDataSource
from hummingbot.connector.exchange.xt.xt_api_user_stream_data_source import XtAPIUserStreamDataSource
from hummingbot.connector.exchange.xt.xt_auth import XtAuth
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import DeductedFromReturnsTradeFee, TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.utils.async_utils import safe_gather
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter


class XtExchange(ExchangePyBase):
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 10.0
    SHORT_POLL_INTERVAL = 1.0
    LONG_POLL_INTERVAL = 1.0

    web_utils = web_utils

    def __init__(
        self,
        client_config_map: "ClientConfigAdapter",
        xt_api_key: str,
        xt_api_secret: str,
        trading_pairs: Optional[List[str]] = None,
        trading_required: bool = True,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
    ):
        self.api_key = xt_api_key
        self.secret_key = xt_api_secret
        self._domain = domain
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs
        self._last_trades_poll_xt_timestamp = 1.0
        super().__init__(client_config_map)

    @staticmethod
    def xt_order_type(order_type: OrderType) -> str:
        return order_type.name.upper()

    @staticmethod
    def to_hb_order_type(xt_type: str) -> OrderType:
        return OrderType[xt_type]

    @property
    def authenticator(self):
        return XtAuth(api_key=self.api_key, secret_key=self.secret_key, time_provider=self._time_synchronizer)

    @property
    def name(self) -> str:
        if self._domain == "sapi.xt.com":
            return "sapi.xt.com"
        else:
            return "sapi.xt.com"

    @property
    def rate_limits_rules(self):
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self):
        return self._domain

    @property
    def client_order_id_max_length(self):
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self):
        return CONSTANTS.HBOT_ORDER_ID_PREFIX

    @property
    def trading_rules_request_path(self):
        return CONSTANTS.EXCHANGE_INFO_PATH_URL

    @property
    def trading_pairs_request_path(self):
        return CONSTANTS.EXCHANGE_INFO_PATH_URL

    @property
    def check_network_request_path(self):
        return CONSTANTS.PING_PATH_URL

    @property
    def trading_pairs(self):
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    def supported_order_types(self):
        return [OrderType.LIMIT, OrderType.LIMIT_MAKER, OrderType.MARKET]

    async def get_all_pairs_prices(self) -> List[Dict[str, str]]:
        pairs_prices = await self._api_get(path_url=CONSTANTS.TICKER_BOOK_PATH_URL)
        return pairs_prices

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception):
        error_description = str(request_exception)
        is_time_synchronizer_related = (
            "AUTH_002" in error_description
            or "AUTH_003" in error_description
            or "AUTH_004" in error_description
            or "AUTH_105" in error_description
        )
        return is_time_synchronizer_related

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        # return str(CONSTANTS.ORDER_NOT_EXIST_ERROR_CODE) in str(
        #     status_update_exception
        # ) and CONSTANTS.ORDER_NOT_EXIST_MESSAGE in str(status_update_exception)
        pass

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        # return str(CONSTANTS.UNKNOWN_ORDER_ERROR_CODE) in str(
        #     cancelation_exception
        # ) and CONSTANTS.UNKNOWN_ORDER_MESSAGE in str(cancelation_exception)
        pass

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(
            throttler=self._throttler, time_synchronizer=self._time_synchronizer, domain=self._domain, auth=self._auth
        )

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        return XtAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            domain=self.domain,
            api_factory=self._web_assistants_factory,
        )

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return XtAPIUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self.domain,
        )

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = s_decimal_NaN,
        is_maker: Optional[bool] = None,
    ) -> TradeFeeBase:
        is_maker = order_type is OrderType.LIMIT_MAKER
        return DeductedFromReturnsTradeFee(percent=self.estimate_fee_pct(is_maker))

    async def _place_order(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        **kwargs,
    ) -> Tuple[str, float]:
        order_result = None
        amount_str = f"{amount:f}"
        type_str = XtExchange.xt_order_type(order_type)
        side_str = CONSTANTS.SIDE_BUY if trade_type is TradeType.BUY else CONSTANTS.SIDE_SELL
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        api_params = {
            "symbol": symbol,
            "side": side_str,
            "quantity": amount_str,
            "type": type_str,
            "clientOrderId": order_id,
            "bizType": "SPOT",
            "timeInForce": CONSTANTS.TIME_IN_FORCE_GTC,
        }

        if order_type is OrderType.LIMIT or order_type is OrderType.LIMIT_MAKER:
            price_str = f"{price:f}"
            api_params["price"] = price_str

        try:
            order_result = await self._api_post(
                path_url=CONSTANTS.ORDER_PATH_URL, data=api_params, is_auth_required=True
            )
            o_id = str(order_result["orderId"])
            transact_time = order_result["transactTime"] * 1e-3
        except IOError as e:
            error_description = str(e)
            is_server_overloaded = (
                "status is 503" in error_description
                and "Unknown error, please check your request or try again later." in error_description
            )
            if is_server_overloaded:
                o_id = "UNKNOWN"
                transact_time = self._time_synchronizer.time()
            else:
                raise
        return o_id, transact_time

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
        ex_order_id = await tracked_order.get_exchange_order_id()
        api_params = {
            "symbol": symbol,
            "orderId": ex_order_id,
        }
        cancel_result = await self._api_delete(
            path_url=f"{CONSTANTS.CANCEL_ORDER_PATH_URL}{ex_order_id}", params=api_params, is_auth_required=True
        )
        if cancel_result.get("status") == "CANCELED":
            return True
        return False

    async def _format_trading_rules(self, exchange_info_dict: Dict[str, Any]) -> List[TradingRule]:
        """
        Example:
        {
            "id"                    : 614,                   //ID
            "symbol"                : "btc_usdt",
            "state"                 : "ONLINE",           //symbol state [ONLINE;OFFLINE,DELISTED]
            "tradingEnabled"        : true,
            "openapiEnabled"        : true,      //Openapi transaction is available or not
            "nextStateTime"         : null,
            "nextState"             : null,
            "depthMergePrecision"   : 5,    //Depth Merge Accuracy
            "baseCurrency"          : "btc",
            "baseCurrencyPrecision" : 5,
            "baseCurrencyId"        : 2,
            "quoteCurrency"         : "usdt",
            "quoteCurrencyPrecision": 6,
            "quoteCurrencyId"       : 11,
            "pricePrecision"        : 4,         //Transaction price accuracy
            "quantityPrecision"     : 6,
            "orderTypes"            : [ LIMIT;MARKET ]
            "timeInForces"          : [ "GTC","FOK","IOC","GTX"],
        """
        trading_pair_rules = exchange_info_dict["result"].get("symbols", [])
        retval = []
        for rule in filter(xt_utils.is_exchange_information_valid, trading_pair_rules):
            try:
                trading_pair = await self.trading_pair_associated_to_exchange_symbol(symbol=rule.get("symbol"))
                filters = rule.get("filters")
                # price_filter = [f for f in filters if f.get("filterType") == "PRICE"][0]
                # lot_size_filter = [f for f in filters if f.get("filterType") == "LOT_SIZE"][0]
                # min_notional_filter = [f for f in filters if f.get("filterType") in ["MIN_NOTIONAL", "NOTIONAL"]][0]
                quote_qty_size_filter = next((f for f in filters if f.get("filter") == "QUOTE_QTY"), None)
                quantity_size_filter = next((f for f in filters if f.get("filter") == "QUANTITY"), None)

                min_order_size = Decimal(quantity_size_filter.get("min"))
                # tick_size = price_filter.get("tickSize")
                # step_size = Decimal(lot_size_filter.get("stepSize"))
                min_notional = Decimal(quote_qty_size_filter.get("min"))

                min_price_increment = Decimal("1") / (Decimal("10") ** Decimal(rule.get("pricePrecision")))
                min_base_amount_increment = Decimal("1") / (Decimal("10") ** Decimal(rule.get("quantityPrecision")))

                retval.append(
                    TradingRule(
                        trading_pair,
                        min_order_size=min_order_size,
                        min_price_increment=min_price_increment,
                        min_base_amount_increment=min_base_amount_increment,
                        min_notional_size=Decimal(min_notional),
                    )
                )

            except Exception:
                self.logger().exception(f"Error parsing the trading pair rule {rule}. Skipping.")
        return retval

    async def _status_polling_loop_fetch_updates(self):
        await self._update_order_fills_from_trades()
        await super()._status_polling_loop_fetch_updates()

    async def _update_trading_fees(self):
        """
        Update fees information from the exchange
        """
        pass

    async def _cancelled_order_handler(self, client_order_id: str, order_update: Optional[Dict[str, Any]]):
        """
        Custom function to handle XT's cancelled orders. Wait until all the trade fills of the order are recorded.
        """
        try:
            executed_amount_base = Decimal(str(order_update.get("eq"))) or Decimal(str(order_update.get("executedQty")))
        except decimal.InvalidOperation:
            executed_amount_base = Decimal("0")

        # if cancelled event comes before we have all the fills of that order,
        # wait 2 cycles to fetch trade fills before updating the status
        for _ in range(2):
            if (
                self.in_flight_orders.get(client_order_id, None) is not None
                and self.in_flight_orders.get(client_order_id).executed_amount_base < executed_amount_base
            ):
                await self._sleep(self.LONG_POLL_INTERVAL)
            else:
                break

        if (
            self.in_flight_orders.get(client_order_id, None) is not None
            and self.in_flight_orders.get(client_order_id).executed_amount_base < executed_amount_base
        ):
            self.logger().warning(
                f"The order fill updates did not arrive on time for {client_order_id}. "
                f"The cancel update will be processed with incomplete information."
            )

    async def _user_stream_event_listener(self):
        """
        This functions runs in background continuously processing the events received from the exchange by the user
        stream data source. It keeps reading events from the queue until the task is interrupted.
        The events received are balance updates, order updates and trade events.

        order:
            {
                "s": "btc_usdt",                // symbol
                "bc": "btc",                    // base currency
                "qc": "usdt",                   // quotation currency
                "t": 1656043204763,             // happened time
                "ct": 1656043204663,            // create time
                "i": "6216559590087220004",     // order id,
                "ci": "test123",                // client order id
                "st": "PARTIALLY_FILLED",       // state NEW/PARTIALLY_FILLED/FILLED/CANCELED/REJECTED/EXPIRED
                "sd": "BUY",                    // side BUY/SELL
                "tp": "LIMIT",                  // type LIMIT/MARKET
                "oq":  "4"                      // original quantity
                "oqq":  48000,                  // original quotation quantity
                "eq": "2",                      // executed quantity
                "lq": "2",                      // remaining quantity
                "p": "4000",                    // price
                "ap": "30000",                  // avg price
                "f":"0.002"                     // fee
            }

        balance:
            {
                "a": "123",           // accountId
                "t": 1656043204763,   // time happened time
                "c": "btc",           // currency
                "b": "123",           // all spot balance
                "f": "11",            // frozen
                "z": "SPOT",           // bizType [SPOT,LEVER]
                "s": "btc_usdt"       // symbol
            }

        trade:
            {
                "s": "btc_usdt",                // symbol
                "t": 1656043204763,             //time
                "i": "6316559590087251233",     // tradeId
                "oi": "6216559590087220004",    // orderId
                "p": "30000",                   // trade price
                "q": "3",                       // qty quantity
                "v": "90000"                    //volumn trade amount
            }

        """
        async for event_message in self._iter_user_event_queue():
            try:
                event_type = event_message.get("event")
                if event_type == "order":
                    order_update = event_message.get("data")
                    client_order_id = order_update.get("ci")

                    tracked_order = next(
                        (
                            order
                            for order in self._order_tracker.all_updatable_orders.values()
                            if order.client_order_id == client_order_id
                        ),
                        None,
                    )

                    if tracked_order is not None:
                        if CONSTANTS.ORDER_STATE[order_update.get("st")] == OrderState.CANCELED:
                            await self._cancelled_order_handler(tracked_order.client_order_id, order_update)

                        order_update = OrderUpdate(
                            trading_pair=tracked_order.trading_pair,
                            update_timestamp=order_update["ct"] * 1e-3,
                            new_state=CONSTANTS.ORDER_STATE[order_update["st"]],
                            client_order_id=tracked_order.client_order_id,
                            exchange_order_id=str(order_update["i"]),
                        )
                        self._order_tracker.process_order_update(order_update=order_update)

                        fee = TradeFeeBase.new_spot_fee(
                            fee_schema=self.trade_fee_schema(),
                            trade_type=tracked_order.trade_type,
                            flat_fees=order_update.get("f"),
                        )

                        trade_update = TradeUpdate(
                            trade_id=str(order_update["i"]),
                            client_order_id=client_order_id,
                            exchange_order_id=str(order_update["ci"]),
                            trading_pair=tracked_order.trading_pair,
                            fee=fee,
                        )

                        self._order_tracker.process_trade_update(trade_update)
                elif event_type == "balance":
                    balance_entry = event_message["data"]
                    asset_name = balance_entry["c"].upper()
                    total_balance = Decimal(balance_entry["b"])
                    frozen_balance = Decimal(balance_entry["f"])
                    free_balance = total_balance - frozen_balance
                    self._account_available_balances[asset_name] = free_balance
                    self._account_balances[asset_name] = total_balance
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().error("Unexpected error in user stream listener loop.", exc_info=True)
                await self._sleep(5.0)

    # async def _update_order_fills_from_trades(self):
    #     """
    #     This is intended to be a backup measure to get filled events with trade ID for orders,
    #     in case Xt's user stream events are not working.
    #     NOTE: It is not required to copy this functionality in other connectors.
    #     This is separated from _update_order_status which only updates the order status without producing filled
    #     events, since Xt's get order endpoint does not return trade IDs.
    #     The minimum poll interval for order status is 10 seconds.
    #     """
    #     small_interval_last_tick = self._last_poll_timestamp / self.UPDATE_ORDER_STATUS_MIN_INTERVAL
    #     small_interval_current_tick = self.current_timestamp / self.UPDATE_ORDER_STATUS_MIN_INTERVAL
    #     long_interval_last_tick = self._last_poll_timestamp / self.LONG_POLL_INTERVAL
    #     long_interval_current_tick = self.current_timestamp / self.LONG_POLL_INTERVAL

    #     if long_interval_current_tick > long_interval_last_tick or (
    #         self.in_flight_orders and small_interval_current_tick > small_interval_last_tick
    #     ):
    #         query_time = int(self._last_trades_poll_xt_timestamp * 1e3)
    #         self._last_trades_poll_xt_timestamp = self._time_synchronizer.time()
    #         order_by_exchange_id_map = {}
    #         for order in self._order_tracker.all_fillable_orders.values():
    #             order_by_exchange_id_map[order.exchange_order_id] = order

    #         tasks = []
    #         trading_pairs = self.trading_pairs
    #         for trading_pair in trading_pairs:
    #             params = {"symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)}
    #             if self._last_poll_timestamp > 0:
    #                 params["startTime"] = query_time
    #             tasks.append(self._api_get(path_url=CONSTANTS.MY_TRADES_PATH_URL, params=params, is_auth_required=True))

    #         self.logger().debug(f"Polling for order fills of {len(tasks)} trading pairs.")
    #         results = await safe_gather(*tasks, return_exceptions=True)

    #         for trades, trading_pair in zip(results, trading_pairs):

    #             if isinstance(trades, Exception):
    #                 self.logger().network(
    #                     f"Error fetching trades update for the order {trading_pair}: {trades}.",
    #                     app_warning_msg=f"Failed to fetch trade update for {trading_pair}.",
    #                 )
    #                 continue
    #             for trade in trades:
    #                 exchange_order_id = str(trade["orderId"])
    #                 if exchange_order_id in order_by_exchange_id_map:
    #                     # This is a fill for a tracked order
    #                     tracked_order = order_by_exchange_id_map[exchange_order_id]
    #                     fee = TradeFeeBase.new_spot_fee(
    #                         fee_schema=self.trade_fee_schema(),
    #                         trade_type=tracked_order.trade_type,
    #                         percent_token=trade["commissionAsset"],
    #                         flat_fees=[
    #                             TokenAmount(amount=Decimal(trade["commission"]), token=trade["commissionAsset"])
    #                         ],
    #                     )
    #                     trade_update = TradeUpdate(
    #                         trade_id=str(trade["id"]),
    #                         client_order_id=tracked_order.client_order_id,
    #                         exchange_order_id=exchange_order_id,
    #                         trading_pair=trading_pair,
    #                         fee=fee,
    #                         fill_base_amount=Decimal(trade["qty"]),
    #                         fill_quote_amount=Decimal(trade["quoteQty"]),
    #                         fill_price=Decimal(trade["price"]),
    #                         fill_timestamp=trade["time"] * 1e-3,
    #                     )
    #                     self._order_tracker.process_trade_update(trade_update)
    #                 elif self.is_confirmed_new_order_filled_event(str(trade["id"]), exchange_order_id, trading_pair):
    #                     # This is a fill of an order registered in the DB but not tracked any more
    #                     self._current_trade_fills.add(
    #                         TradeFillOrderDetails(
    #                             market=self.display_name, exchange_trade_id=str(trade["id"]), symbol=trading_pair
    #                         )
    #                     )
    #                     self.trigger_event(
    #                         MarketEvent.OrderFilled,
    #                         OrderFilledEvent(
    #                             timestamp=float(trade["time"]) * 1e-3,
    #                             order_id=self._exchange_order_ids.get(str(trade["orderId"]), None),
    #                             trading_pair=trading_pair,
    #                             trade_type=TradeType.BUY if trade["isBuyer"] else TradeType.SELL,
    #                             order_type=OrderType.LIMIT_MAKER if trade["isMaker"] else OrderType.LIMIT,
    #                             price=Decimal(trade["price"]),
    #                             amount=Decimal(trade["qty"]),
    #                             trade_fee=DeductedFromReturnsTradeFee(
    #                                 flat_fees=[TokenAmount(trade["commissionAsset"], Decimal(trade["commission"]))]
    #                             ),
    #                             exchange_trade_id=str(trade["id"]),
    #                         ),
    #                     )
    #                     self.logger().info(f"Recreating missing trade in TradeFill: {trade}")

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        trade_updates = []

        if order.exchange_order_id is not None:
            exchange_order_id = int(order.exchange_order_id)
            trading_pair = await self.exchange_symbol_associated_to_pair(trading_pair=order.trading_pair)
            all_fills_response = await self._api_get(
                path_url=CONSTANTS.MY_TRADES_PATH_URL,
                params={"symbol": trading_pair, "orderId": exchange_order_id},
                is_auth_required=True,
                limit_id=CONSTANTS.MY_TRADES_PATH_URL,
            )

            for trade in all_fills_response:
                exchange_order_id = str(trade["orderId"])
                fee = TradeFeeBase.new_spot_fee(
                    fee_schema=self.trade_fee_schema(),
                    trade_type=order.trade_type,
                    percent_token=trade["commissionAsset"],
                    flat_fees=[TokenAmount(amount=Decimal(trade["commission"]), token=trade["commissionAsset"])],
                )
                trade_update = TradeUpdate(
                    trade_id=str(trade["id"]),
                    client_order_id=order.client_order_id,
                    exchange_order_id=exchange_order_id,
                    trading_pair=trading_pair,
                    fee=fee,
                    fill_base_amount=Decimal(trade["qty"]),
                    fill_quote_amount=Decimal(trade["quoteQty"]),
                    fill_price=Decimal(trade["price"]),
                    fill_timestamp=trade["time"] * 1e-3,
                )
                trade_updates.append(trade_update)

        return trade_updates

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        client_order_id = tracked_order.client_order_id
        exchange_order_id = await tracked_order.get_exchange_order_id()
        response = await self._api_get(
            path_url=CONSTANTS.ORDER_PATH_URL,
            params={"orderId": int(exchange_order_id), "clientOrderId": client_order_id},
            is_auth_required=True,
            limit_id=CONSTANTS.MANAGE_ORDER,
        )

        # order update might've already come through user stream listner
        # and order might no longer be available on the exchange.
        if "result" not in response or response["result"] is None:
            return

        updated_order_data = response["result"]
        new_state = CONSTANTS.ORDER_STATE[updated_order_data["state"]]

        if new_state == OrderState.CANCELED:
            await self._cancelled_order_handler(client_order_id, updated_order_data)

        time = updated_order_data["time"] * 1e-3
        if updated_order_data["updatedTime"] is not None:
            time = updated_order_data["updatedTime"] * 1e-3

        order_update = OrderUpdate(
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=str(updated_order_data["orderId"]),
            trading_pair=tracked_order.trading_pair,
            update_timestamp=time,
            new_state=new_state,
        )

        return order_update

    async def _update_balances(self):
        """
        Updates the account balances by fetching the latest balance information from the exchange API.

        This method retrieves the account balances from the exchange API and updates the internal `_account_balances`
        and `_account_available_balances` dictionaries with the latest balance information.

        Raises:
            IOError: If there is an error fetching the account updates from the API.

        {
            "totalBtcAmount": 0,
            "assets": [
            {
                "currency": "string",
                "currencyId": 0,
                "frozenAmount": 0,
                "availableAmount": 0,
                "totalAmount": 0,
                "convertBtcAmount": 0
            }
            ]
        }

        """
        local_asset_names = set(self._account_balances.keys())
        remote_asset_names = set()

        account_info = await self._api_get(path_url=CONSTANTS.ACCOUNTS_PATH_URL, is_auth_required=True)

        if "result" not in account_info or account_info["result"] is None:
            raise IOError(f"Error fetching account updates. API response: {account_info}")

        balances = account_info["balances"]
        for balance_entry in balances:
            asset_name = balance_entry["asset"]
            free_balance = Decimal(balance_entry["free"])
            total_balance = Decimal(balance_entry["free"]) + Decimal(balance_entry["locked"])
            self._account_available_balances[asset_name] = free_balance
            self._account_balances[asset_name] = total_balance
            remote_asset_names.add(asset_name)

        asset_names_to_remove = local_asset_names.difference(remote_asset_names)
        for asset_name in asset_names_to_remove:
            del self._account_available_balances[asset_name]
            del self._account_balances[asset_name]

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        mapping = bidict()
        for symbol_data in filter(xt_utils.is_exchange_information_valid, exchange_info["result"]["symbols"]):
            mapping[symbol_data["symbol"]] = combine_to_hb_trading_pair(
                base=symbol_data["baseCurrency"], quote=symbol_data["quoteCurrency"]
            )
        self._set_trading_pair_symbol_map(mapping)

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        params = {"symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)}

        resp_json = await self._api_request(
            method=RESTMethod.GET, path_url=CONSTANTS.TICKER_PRICE_CHANGE_PATH_URL, params=params
        )

        return float(resp_json["lastPrice"])

    async def _get_open_orders(self):
        """
        Get all pending orders for the current spot trading pair.
        """
        tasks = []
        for trading_pair in self._trading_pairs:

            params = {
                "symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair),
                "bizType": "SPOT",
            }

            task = self._api_get(
                path_url=CONSTANTS.OPEN_ORDER_PATH_URL,
                params=params,
                is_auth_required=True,
                limit_id=CONSTANTS.MANAGE_ORDER,
            )

            tasks.append(task)

        open_orders = []
        responses = await safe_gather(*tasks, return_exceptions=True)
        for response in responses:
            if not isinstance(response, Exception) and "result" in response and isinstance(response["result"], list):
                for order in response["result"]:
                    open_orders.append(order["clientOrderId"])

        return open_orders
