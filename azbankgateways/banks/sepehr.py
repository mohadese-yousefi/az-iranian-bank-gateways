import logging
import uuid
import json

import requests

from azbankgateways.banks import BaseBank
from azbankgateways.exceptions import BankGatewayConnectionError, SettingDoesNotExist
from azbankgateways.exceptions.exceptions import BankGatewayRejectPayment
from azbankgateways.models import BankType, CurrencyEnum, PaymentStatus
from azbankgateways.utils import get_json


class Sepehr(BaseBank):
    _terminal_id = None
    _payment_url = None

    def __init__(self, **kwargs):
        super(Sepehr, self).__init__(**kwargs)
        self.set_gateway_currency(CurrencyEnum.IRR)
        self._token_api_url = "https://sepehr.shaparak.ir:8081/V1/PeymentApi/GetToken"
        self._payment_url = "https://sepehr.shaparak.ir:8080/pay"
        self._verify_api_url = "https://sepehr.shaparak.ir:8081/V1/PeymentApi/Advice"

    def get_bank_type(self):
        return BankType.SEPEHR

    def set_default_settings(self):
        for item in ["TERMINAL_ID"]:
            if item not in self.default_setting_kwargs:
                logging.error(f"Missing required setting: {item}")
                raise SettingDoesNotExist(f"Missing required setting: {item}")
            setattr(self, f"_{item.lower()}", self.default_setting_kwargs[item])

    def check_gateway(self, amount=None):
        # Simple check - just verify we have the required settings
        if not self._terminal_id:
            raise BankGatewayConnectionError("terminal_id is not set")
        return True

    def get_pay_data(self):
        data = {
            "Amount": str(int(self.get_gateway_amount()) * 10),  # Convert to Rial
            "callbackURL": self._get_gateway_callback_url(),
            "invoiceID": self.get_tracking_code(),
            "terminalID": self._terminal_id,
        }
        return data

    def prepare_pay(self):
        super(Sepehr, self).prepare_pay()

    def pay(self):
        super(Sepehr, self).pay()
        data = self.get_pay_data()
        response_json = self._send_data(self._token_api_url, data)
        
        if response_json.get("Status") == 0:
            token = response_json.get("Accesstoken")
            self._set_reference_number(token)
        else:
            logging.critical("Sepehr gateway reject payment")
            self._set_transaction_status_text(response_json.get("Message", "Unknown error"))
            raise BankGatewayRejectPayment(self.get_transaction_status_text())

    """
    : gateway
    """

    def _get_gateway_payment_url_parameter(self):
        return self._payment_url

    def _get_gateway_payment_method_parameter(self):
        return "GET"

    def _get_gateway_payment_parameter(self):
        params = {
            "token": self.get_reference_number(),
            "terminalID": self._terminal_id,
            "GetMethod": "true",
        }
        return params

    """
    verify from gateway
    """

    def prepare_verify_from_gateway(self):
        super(Sepehr, self).prepare_verify_from_gateway()
        for method in ["GET", "POST", "data"]:
            token = getattr(self.get_request(), method).get("token", None)
            if token:
                logging.info(f"Sepehr method: {method}")
                break

        request = self.get_request()
        tracking_code = request.POST.get("invoiceid")
        digital_receipt = request.POST.get("digitalreceipt")

        self._set_tracking_code(tracking_code)
        self._set_bank_record()
        
        if request.POST.get("respcode", "-1") == "0" and digital_receipt:
            self._set_reference_number(digital_receipt)
            self._bank.reference_number = digital_receipt
            extra_information = f"digitalreceipt={digital_receipt}, rrn={request.POST.get('rrn')}, tracenumber={request.POST.get('tracenumber')}"
            self._bank.extra_information = extra_information
            self._bank.save()

    def verify_from_gateway(self, request):
        super(Sepehr, self).verify_from_gateway(request)

    """
    verify
    """

    def get_verify_data(self):
        super(Sepehr, self).get_verify_data()
        data = {
            "digitalreceipt": self.get_reference_number(),
            "Tid": self._terminal_id,
        }
        return data

    def prepare_verify(self, tracking_code):
        super(Sepehr, self).prepare_verify(tracking_code)

    def verify(self, transaction_code):
        super(Sepehr, self).verify(transaction_code)
        data = self.get_verify_data()
        response_json = self._send_data(self._verify_api_url, data)
        
        if response_json.get("Status") == "OK":
            self._set_payment_status(PaymentStatus.COMPLETE)
        else:
            self._set_payment_status(PaymentStatus.CANCEL_BY_USER)
            logging.debug("Sepehr gateway unapprove payment")

    def _send_data(self, api, data, timeout=10):
        try:
            headers = {'Content-Type': 'application/json'}
            response = requests.post(api, json=data, headers=headers, timeout=timeout)
        except requests.Timeout:
            logging.exception("Sepehr timeout gateway {}".format(data))
            raise BankGatewayConnectionError()
        except requests.ConnectionError:
            logging.exception("Sepehr connection error gateway {}".format(data))
            raise BankGatewayConnectionError()

        try:
            response_json = response.json()
        except ValueError:
            response_json = {}
            logging.exception("Sepehr invalid response gateway {}".format(response.text))
        
        if "Message" in response_json:
            self._set_transaction_status_text(response_json.get("Message"))
        
        return response_json
