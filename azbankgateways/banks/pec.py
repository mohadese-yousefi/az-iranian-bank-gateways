import logging
import uuid
from datetime import datetime
from urllib import parse

import requests
from zeep import Client, Transport

from azbankgateways.banks import BaseBank
from azbankgateways.exceptions import BankGatewayConnectionError, SettingDoesNotExist
from azbankgateways.exceptions.exceptions import BankGatewayRejectPayment
from azbankgateways.models import BankType, CurrencyEnum, PaymentStatus
from azbankgateways.utils import get_json


class PEC(BaseBank):
    _pin = None

    def __init__(self, **kwargs):
        super(PEC, self).__init__(**kwargs)
        self.set_gateway_currency(CurrencyEnum.IRR)
        self._token_api_url = "https://pec.shaparak.ir/NewIPGServices/Sale/SaleService.asmx?WSDL"
        self._payment_url = "https://pec.shaparak.ir/NewIPG/"
        self._verify_api_url = "https://pec.shaparak.ir/NewIPGServices/Confirm/ConfirmService.asmx?WSDL"

    def get_bank_type(self):
        return BankType.PEC

    def set_default_settings(self):
        for item in ["PIN"]:
            if item not in self.default_setting_kwargs:
                raise SettingDoesNotExist()
            setattr(self, f"_{item.lower()}", self.default_setting_kwargs[item])

    def get_pay_data(self):
        data = {
            "LoginAccount": self._pin,
            "Amount": self.get_gateway_amount(),
            "OrderId": self.get_tracking_code(),
            "CallBackUrl": self._get_gateway_callback_url(),
            "AdditionalData": "",
            "Originator": self.get_mobile_number() or "",
        }
        return data

    def prepare_pay(self):
        super(PEC, self).prepare_pay()
        # Generate a unique order ID if not already set
        if not self.get_tracking_code():
            order_id = str(int(str(uuid.uuid4().int)[-16:]))
            self._set_tracking_code(order_id)

    def pay(self):
        super(PEC, self).pay()
        data = self.get_pay_data()
        client = self._get_client(self._token_api_url)
        
        try:
            result = client.service.SalePaymentRequest(requestData=data)
            
            if result.Status != 0:
                logging.critical(f"PEC gateway reject payment: {result}")
                self._set_transaction_status_text(f"Error: {result.Message}")
                raise BankGatewayRejectPayment(self.get_transaction_status_text())
            
            # Payment was successful
            token = result.Token
            self._set_reference_number(token)
        except Exception as ex:
            logging.critical(f"Error in PEC payment: {ex}")
            raise BankGatewayConnectionError(str(ex))

    """
    : gateway
    """

    def _get_gateway_payment_url_parameter(self):
        return f"{self._payment_url}?token={self.get_reference_number()}"

    def _get_gateway_payment_method_parameter(self):
        return "GET"

    def _get_gateway_payment_parameter(self):
        return {}

    """
    verify from gateway
    """

    def prepare_verify_from_gateway(self):
        super(PEC, self).prepare_verify_from_gateway()
        request = self.get_request()
        
        # PEC sends data as form data, not query parameters
        if request.method == 'POST':
            # Get data from form
            token = request.POST.get("Token")
            order_id = request.POST.get("OrderId")
            rrn = request.POST.get("RRN")
            strace_number = request.POST.get("sTraceNo")
            card_number = request.POST.get("CardNumberMasked", "")
        else:
            # Fallback to GET parameters if needed
            token = request.GET.get("Token")
            order_id = request.GET.get("OrderId")
            rrn = request.GET.get("RRN", "")
            strace_number = request.GET.get("sTraceNo", "")
            card_number = request.GET.get("CardNumberMasked", "")
        
        self._set_tracking_code(order_id)
        self._set_bank_record()
        
        if token:
            self._set_reference_number(token)
            self._bank.reference_number = token
            
            # Store additional information
            extra_information = (
                f"CardNumberMasked={card_number}, "
                f"RRN={rrn}, "
                f"TraceNo={strace_number}"
            )
            self._bank.extra_information = extra_information
            self._bank.card_hash_number = card_number
            self._bank.save()

    def verify_from_gateway(self, request):
        super(PEC, self).verify_from_gateway(request)

    """
    verify
    """

    def get_verify_data(self):
        super(PEC, self).get_verify_data()
        data = {
            "LoginAccount": self._pin,
            "Token": self.get_reference_number(),
        }
        return data

    def prepare_verify(self, tracking_code):
        super(PEC, self).prepare_verify(tracking_code)

    def verify(self, transaction_code):
        super(PEC, self).verify(transaction_code)
        
        data = self.get_verify_data()
        client = self._get_client(self._verify_api_url)
        
        try:
            result = client.service.ConfirmPayment(requestData=data)
            
            if result.Status == 0:
                self._set_payment_status(PaymentStatus.COMPLETE)
                # Store masked card number from confirmation response
                if hasattr(result, 'CardNumberMasked'):
                    self._bank.card_hash_number = result.CardNumberMasked
                    self._bank.save()
            elif result.Status == -138:
                self._set_payment_status(PaymentStatus.CANCEL_BY_USER)
                self._set_transaction_status_text(f"Error: {result.Message}")
                logging.debug(f"PEC gateway unapprove payment: {result.Message}")
            else:
                self._set_payment_status(PaymentStatus.ERROR)
                self._set_transaction_status_text(f"Error: {result.Message}")
                logging.debug(f"PEC gateway unapprove payment: {result.Message}")
        except Exception as ex:
            logging.exception(f"Error in PEC verify: {ex}")
            self._set_payment_status(PaymentStatus.ERROR)
            raise BankGatewayConnectionError(str(ex))

    @staticmethod
    def _get_client(url):
        transport = Transport(timeout=5, operation_timeout=5)
        client = Client(url, transport=transport)
        return client

    def _get_gateway_callback_url(self):
        """Override to return callback URL without query parameters since PEC doesn't allow them"""
        from django.urls import reverse
        from .. import default_settings as settings
        
        url = reverse(settings.CALLBACK_NAMESPACE)
        if self.get_request():
            url_parts = list(parse.urlparse(url))
            if not (url_parts[0] and url_parts[1]):
                url = self.get_request().build_absolute_uri(url)
                
                # Force HTTPS if not already
                if url.startswith('http:'):
                    url = 'https:' + url[5:]
        
        # Don't add query parameters for PEC
        return url
