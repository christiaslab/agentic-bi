"""The slice and the column allow-list of ADR-0001, written once.

The filter below is the only definition of the slice. ``fetch`` sends it to the
transaction download and copies it into the manifest; ``golden`` sends the same
object to every aggregate search endpoint, so the snapshot and the golden values
describe one set of transactions.
"""

from __future__ import annotations

import copy
import re

API_BASE = "https://api.usaspending.gov"
FILES_HOST = "files.usaspending.gov"
ALLOWED_HOSTS = frozenset({"api.usaspending.gov", FILES_HOST})

AGENCY_TOPTIER_CODE = "068"
AGENCY_NAME = "Environmental Protection Agency"
FISCAL_YEAR = 2025
AWARD_TYPE_CODES = ("A", "B", "C", "D")
START_DATE = "2024-10-01"
END_DATE = "2025-09-30"

# ADR-0001 point 2. The download endpoint and the search endpoints accept this object
# as is. The agency is named, because the search filter does not accept a toptier code;
# the fetch checks that every row carries awarding agency code 068.
_FILTERS = {
    "award_type_codes": list(AWARD_TYPE_CODES),
    "time_period": [{"start_date": START_DATE, "end_date": END_DATE, "date_type": "action_date"}],
    "agencies": [{"type": "awarding", "tier": "toptier", "name": AGENCY_NAME}],
}


def filters() -> dict:
    """A fresh copy of the slice filter, safe to mutate."""
    return copy.deepcopy(_FILTERS)


# ADR-0001 point 6: the explicit allow-list, in the order the download returns it.
# docs/sources.md maps each field to its star-schema column or marks it unused.
FIELDS: tuple[str, ...] = (
    "contract_transaction_unique_key",
    "contract_award_unique_key",
    "award_id_piid",
    "modification_number",
    "federal_action_obligation",
    "action_date",
    "action_date_fiscal_year",
    "awarding_agency_code",
    "awarding_sub_agency_code",
    "awarding_sub_agency_name",
    "recipient_uei",
    "recipient_name",
    "recipient_parent_uei",
    "recipient_parent_name",
    "primary_place_of_performance_country_code",
    "primary_place_of_performance_state_code",
    "award_type_code",
    "award_type",
    "product_or_service_code",
    "product_or_service_code_description",
    "naics_code",
    "naics_description",
    "extent_competed_code",
    "extent_competed",
)

# Typed columns of the snapshot; every other field stays VARCHAR.
DECIMAL_FIELDS = frozenset({"federal_action_obligation"})
DATE_FIELDS = frozenset({"action_date"})
INTEGER_FIELDS = frozenset({"action_date_fiscal_year"})

# ADR-0001 point 6: never in the snapshot, whatever the allow-list says.
EXCLUDED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"phone", re.IGNORECASE),
    re.compile(r"fax", re.IGNORECASE),
    re.compile(r"address_line", re.IGNORECASE),
    re.compile(r"^highly_compensated_officer_", re.IGNORECASE),
)


def is_excluded(name: str) -> bool:
    """True when a column name is one ADR-0001 point 6 keeps out of the snapshot."""
    return any(pattern.search(name) for pattern in EXCLUDED_PATTERNS)


def excluded_in(names: list[str] | tuple[str, ...]) -> list[str]:
    """The excluded names among ``names``, in their order."""
    return [name for name in names if is_excluded(name)]
