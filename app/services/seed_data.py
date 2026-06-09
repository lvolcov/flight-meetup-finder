"""Seed airport data for the destinations table (REQUIREMENTS §6).

Purpose: IATA -> human name map used to populate ``destinations`` on first
boot. Lucas can enable/disable any of these or add others in the UI.
Created 2026-06-09.
"""

from __future__ import annotations

# Candidate European destinations (REQUIREMENTS §6) plus the fixed origins so
# the UI can label them by name everywhere.
SEED_DESTINATIONS: dict[str, str] = {
    "BCN": "Barcelona",
    "MAD": "Madrid",
    "FCO": "Rome Fiumicino",
    "CIA": "Rome Ciampino",
    "MXP": "Milan Malpensa",
    "BGY": "Milan Bergamo",
    "LIN": "Milan Linate",
    "VCE": "Venice",
    "NAP": "Naples",
    "BLQ": "Bologna",
    "ATH": "Athens",
    "PRG": "Prague",
    "BUD": "Budapest",
    "VIE": "Vienna",
    "BER": "Berlin",
    "MUC": "Munich",
    "FRA": "Frankfurt",
    "HAM": "Hamburg",
    "AMS": "Amsterdam",
    "BRU": "Brussels",
    "CPH": "Copenhagen",
    "ARN": "Stockholm Arlanda",
    "OSL": "Oslo",
    "HEL": "Helsinki",
    "DUB": "Dublin",
    "EDI": "Edinburgh",
    "GLA": "Glasgow",
    "NCE": "Nice",
    "MRS": "Marseille",
    "LYS": "Lyon",
    "TLS": "Toulouse",
    "PMI": "Palma de Mallorca",
    "AGP": "Malaga",
    "SVQ": "Seville",
    "VLC": "Valencia",
    "BIO": "Bilbao",
    "OTP": "Bucharest",
    "SOF": "Sofia",
    "ZAG": "Zagreb",
    "SPU": "Split",
    "DBV": "Dubrovnik",
    "TIA": "Tirana",
    "KRK": "Krakow",
    "WAW": "Warsaw",
    "GVA": "Geneva",
    "ZRH": "Zurich",
    "LJU": "Ljubljana",
}

# Fixed origin / Portugal airports, named for display in both modes.
KNOWN_AIRPORTS: dict[str, str] = {
    "MAN": "Manchester",
    "LIS": "Lisbon",
    "OPO": "Porto",
    "FAO": "Faro",
    **SEED_DESTINATIONS,
}
