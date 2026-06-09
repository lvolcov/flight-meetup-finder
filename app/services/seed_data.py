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

# Airports OUTSIDE the Schengen area — flying there from Lisbon means
# passport control, which Talita's visa situation does not allow (F-1).
# Covers the seed list plus common European airports a user might add.
# Note this is Schengen, not EU: Dublin is EU but has immigration;
# Switzerland/Norway/Iceland are not EU but have none. Romania, Bulgaria
# and Croatia are Schengen since 2023/2024.
NON_SCHENGEN_IATA: frozenset[str] = frozenset(
    {
        # United Kingdom
        "LHR", "LGW", "STN", "LTN", "LCY", "SEN", "MAN", "BHX", "BRS",
        "NCL", "LPL", "LBA", "EMA", "EDI", "GLA", "ABZ", "BFS", "BHD",
        "GIB",
        # Ireland (EU but not Schengen)
        "DUB", "ORK", "SNN", "KIR",
        # Cyprus (EU but not Schengen)
        "LCA", "PFO",
        # Western Balkans
        "TIA", "BEG", "INI", "SJJ", "TZL", "BNX", "TGD", "TIV", "PRN",
        "SKP", "OHD",
        # Türkiye
        "IST", "SAW", "ESB", "ADB", "AYT",
        # Eastern Europe (non-Schengen)
        "KIV", "RMO", "KBP", "IEV", "LWO", "ODS", "MSQ",
    }
)


def is_schengen(iata: str) -> bool:
    """Return True when an airport is inside the Schengen area.

    Unknown codes default to True (the user can still untick them manually);
    the known non-Schengen set above keeps the common cases honest.
    """
    return iata.strip().upper() not in NON_SCHENGEN_IATA


# Fixed origin / Portugal airports, named for display in both modes.
KNOWN_AIRPORTS: dict[str, str] = {
    "MAN": "Manchester",
    "LIS": "Lisbon",
    "OPO": "Porto",
    "FAO": "Faro",
    **SEED_DESTINATIONS,
}
