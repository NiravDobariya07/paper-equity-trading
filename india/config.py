"""Paths and settings. Every path comes from this file's location, never the cwd."""

import copy
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "trader", "india")
EXAMPLE = os.path.join(ROOT, "india", "config.example.json")


def defaults():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def load(path=None):
    """Read config.json (created from the example on first use), filling missing keys."""
    path = path or os.path.join(DATA, "config.json")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(EXAMPLE, path)
    with open(path, encoding="utf-8") as fh:
        mine = json.load(fh)
    cfg = defaults()
    charges = copy.deepcopy(cfg["charges"])
    charges.update(mine.get("charges") or {})
    cfg.update(mine)
    cfg["charges"] = charges
    if not os.path.isabs(cfg["equity_list"]):
        cfg["equity_list"] = os.path.join(os.path.dirname(os.path.abspath(path)), cfg["equity_list"])
    return cfg
