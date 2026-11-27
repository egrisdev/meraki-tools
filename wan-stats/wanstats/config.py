"""Settings, read from the environment or from a `.env` file beside the scripts."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent      # the wan-stats folder
DEFAULT_DB = 'sqlite:///wan-stats.sqlite'


@dataclass
class Config:
    api_key: str
    org_ids: list        # empty means every organization the key can see
    db: str
    keep_days: int       # 0 means keep every reading


def load():
    """Read the settings. A variable that is already set in the environment wins over the `.env` file."""
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / '.env')
    except ImportError:          # python-dotenv is optional when the variables are set another way
        pass
    return Config(
        api_key=os.getenv('MERAKI_API_KEY', '').strip(),
        org_ids=[x.strip() for x in os.getenv('MERAKI_ORG_ID', '').split(',') if x.strip()],
        db=os.getenv('WAN_STATS_DB', '').strip() or DEFAULT_DB,
        keep_days=int(os.getenv('WAN_STATS_KEEP_DAYS', '').strip() or 0))


def open_db(url=None, create=True):
    """Open the store the settings name. A relative SQLite path is relative to the wan-stats folder.

    The reports pass `create=False`: a mistyped path should be an error, not a new empty database.
    """
    from .storage import open_store
    try:
        return open_store(url or load().db, base=str(HERE), create=create)
    except (FileNotFoundError, ValueError, ConnectionError) as e:
        raise SystemExit('WAN stats: %s' % e)
