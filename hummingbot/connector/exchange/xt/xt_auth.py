# Public Key（AccessKey） 529993c5-444c-43a5-85aa-89e4a69fe40a
# Private Key（SecretKey）6d301c6b7cfe8c654e6c89f3e3f4d992731ef41e
import hashlib
import hmac
import json
import time
from typing import Dict

from hummingbot.connector.exchange.xt import xt_constants as CONSTANTS
from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest, WSRequest


class XtAuth(AuthBase):
    def __init__(self, api_key: str, secret_key: str, time_provider: TimeSynchronizer):
        self.api_key = api_key
        self.secret_key = secret_key
        self.time_provider = time_provider
        self.headers = {
            "Content-type": "application/x-www-form-urlencoded",
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:53.0) Gecko/20100101 Firefox/53.0'
        }

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        """
        Adds the server time and the signature to the request, required for authenticated interactions. It also adds
        the required parameter in the request header.
        :param request: the request to be configured for authenticated interaction
        """
        foo = None
        if request.method == RESTMethod.POST:
            foo = json.loads(request.data)
        else:
            foo = request.params

        headers = {}
        # if request.headers is not None:
        #     headers.update(request.headers)

        APIPath = f"/{request.url.replace(CONSTANTS.PROD_REST_URL, '')}"

        headers.update(self.header_for_authentication())
        headers.update(
            self.create_signature(
                APIPath, request.method.value, headers=headers, secret_key=self.secret_key, params=foo
            )
        )

        headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:53.0) Gecko/20100101 Firefox/53.0", "Content-Type:": "application/x-www-form-urlencoded"})

        request.headers = headers

        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        return request  # pass-through

    def header_for_authentication(self, path_url, params, method: RESTMethod = RESTMethod.POST) -> Dict[str, str]:
        headers = {}
        headers.update(
            {
                "xt-validate-algorithms": "HmacSHA256",
                "xt-validate-appkey": self.api_key,
                "xt-validate-recvwindow": "60000",
                "xt-validate-timestamp": str(int((time.time() - 30) * 1000)),
            }
        )

        headers.update(
            self.create_signature(
                path_url, method, headers=headers, secret_key=self.secret_key, params=params
            )
        )

    def create_signature(cls, url, method, headers=None, secret_key=None, **kwargs):
        path_str = url
        query = kwargs.pop("params", None)
        data = kwargs.pop("data", None) or kwargs.pop("json", None)
        query_str = (
            ""
            if query is None
            else "&".join(
                [
                    f"{key}={json.dumps(query[key]) if type(query[key]) in [dict, list] else query[key]}"
                    for key in sorted(query)
                ]
            )
        )
        body_str = json.dumps(data) if data is not None else ""
        y = "#" + "#".join([i for i in [method, path_str, query_str, body_str] if i])
        x = "&".join([f"{key}={headers[key]}" for key in sorted(headers)])
        sign = f"{x}{y}"
        # print(sign)
        return {
            "xt-validate-signature": hmac.new(secret_key.encode("utf-8"), sign.encode("utf-8"), hashlib.sha256)
            .hexdigest()
            .upper()
        }
