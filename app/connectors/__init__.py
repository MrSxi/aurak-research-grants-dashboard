"""Grant source connectors. Each connector yields normalized grant dicts (see base.GrantRecord keys).

Add a new source by writing a class with `key`, `name`, `url`, `method`, `scope` and a
`fetch()` method, then listing it in CONNECTORS.
"""
from app.connectors.eu_ft import EUFundingTenders
from app.connectors.grants_gov import GrantsGov
from app.connectors.uae import MANUAL_SOURCES, MONITORED_SOURCES, UAE_SCRAPERS

CONNECTORS = [GrantsGov(), EUFundingTenders(), *UAE_SCRAPERS, *MONITORED_SOURCES]


def all_sources():
    """Every source shown in Source health, including manual-entry-only funders."""
    return [*CONNECTORS, *MANUAL_SOURCES]
