from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase

from core.currency import format_currency


class CurrencyFormattingTests(SimpleTestCase):
    def test_formats_naira_with_grouping_and_two_decimals(self):
        self.assertEqual(format_currency(Decimal("1035874.98")), "₦1,035,874.98")

    def test_template_filter_uses_shared_naira_format(self):
        rendered = Template(
            "{% load currency %}{{ amount|naira }}"
        ).render(Context({"amount": Decimal("8762.5")}))

        self.assertEqual(rendered, "₦8,762.50")
