from django import template

from core.currency import format_currency


register = template.Library()


@register.filter
def naira(value):
    return format_currency(value)
