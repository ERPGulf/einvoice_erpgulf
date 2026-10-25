"""Decimal helpers shared by every standard and every adapter.

Amounts are rounded half-up to the number of decimals the currency uses
(2 for AED, EUR, SGD; 3 for OMR, BHD, KWD). A profile passes its currency
decimals; the plain helpers default to 2.
"""

from decimal import ROUND_HALF_UP, Decimal

SIX = Decimal("0.000001")


def dec(value):
	"""Decimal from anything ERPNext hands us (float, int, str, None)."""
	return Decimal(str(value or 0))


def quantum(decimals=2):
	return Decimal(1).scaleb(-int(decimals))


def money(value, decimals=2):
	"""Amount as a fixed-decimals string, half-up: 12.345 -> '12.35'."""
	return str(dec(value).quantize(quantum(decimals), ROUND_HALF_UP))


def qty(value):
	"""Quantity as a 6-dp string, half-up."""
	return str(dec(value).quantize(SIX, ROUND_HALF_UP))


def num(value):
	"""Plain number string without trailing zeros: 1000.000 -> '1000'."""
	d = dec(value).normalize()
	return format(d, "f")
