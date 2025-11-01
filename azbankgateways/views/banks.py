import logging
from urllib.parse import unquote

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from azbankgateways.bankfactories import BankFactory
from azbankgateways.exceptions import AZBankGatewaysException
from azbankgateways.models import Bank, BankType


@csrf_exempt
def callback_view(request):
    bank_type = request.GET.get("bank_type", None)
    identifier = request.GET.get("identifier", None)

    # Handle PEC callback which doesn't include query parameters
    # PEC sends OrderId in POST/GET data, which we can use to identify the bank
    if not bank_type:
        # Check if this is a PEC callback by looking for OrderId
        order_id = request.POST.get("OrderId") or request.GET.get("OrderId")
        if order_id:
            try:
                # Find the bank record by tracking_code (OrderId) and bank_type PEC
                bank_record = Bank.objects.get(
                    tracking_code=order_id,
                    bank_type=BankType.PEC
                )
                bank_type = BankType.PEC
                identifier = bank_record.bank_choose_identifier or "1"
                logging.debug(f"Identified PEC callback from OrderId: {order_id}")
            except Bank.DoesNotExist:
                logging.critical("Bank type is required and OrderId not found for PEC.")
                raise Http404
        else:
            logging.critical("Bank type is required. but it doesnt send.")
            raise Http404

    factory = BankFactory()
    bank = factory.create(bank_type, identifier=identifier)
    try:
        bank.verify_from_gateway(request)
    except AZBankGatewaysException:
        logging.exception("Verify from gateway failed.", stack_info=True)
    return bank.redirect_client_callback()


@csrf_exempt
def go_to_bank_gateway(request):
    context = {"params": {}}
    for key, value in request.GET.items():
        if key == "url" or key == "method":
            context[key] = unquote(value)
        else:
            context["params"][key] = unquote(value)

    return render(request, "azbankgateways/redirect_to_bank.html", context=context)
