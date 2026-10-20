"""Account ownership gate shared by cloud and Mac entry points."""
import json
from pathlib import Path
def is_local(account):
    path=Path(__file__).with_name('migrated_accounts.json')
    if not path.exists():
        return True
    # Invalid migration configuration must not resume an old publisher.
    return account not in json.loads(path.read_text())
