# Day Trader Skill Setup Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get https://github.com/Jakeschincariol/day-trader-skill running in `/var/www/html/python/Day-trader-skill`: code checked out, offline tests green, personal config in place, the 11 skills visible to Claude Code, the read-only data tools verified live, and (optionally) a paper-only Alpaca connector.

**Architecture:** The repo is 11 skill folders (`trader-*/SKILL.md` + one stdlib Python tool each) plus a `unittest` suite with offline fixtures. Every tool reads and writes a `trader/` folder relative to the current directory, so the repo root is also the working directory for trading sessions. Skills are exposed to Claude Code project-locally by symlinking each folder into `.claude/skills/`, so a `git pull` updates them in place.

**Executed layout (overrides the paths below):** `tests/test_repo.py::test_root_matches_the_reel` requires the repo root to hold only upstream files, so the checkout lives in `day-trader-skill/` and this folder is the workspace (`trader/`, `.claude/skills/` symlinks to `../../day-trader-skill/trader-*`, `docs/`). Run tools from this folder as `python3 day-trader-skill/trader-<x>/<tool>.py ...`.

**Tech Stack:** Python 3.8+ standard library only (machine has 3.10.12), `unittest`, git, Claude Code skills. Optional: `uv`/`uvx` + `alpaca-mcp-server` for the broker.

**Spec:** The upstream `README.md` ("Install", "The tools", "Fine print") and `trader-broker/SKILL.md` (connector setup). No separate design doc.

## Global Constraints

- Python 3.8 or newer; nothing to `pip install`. Do not add a requirements file or venv.
- `paper` stays `true` in `trader/config.json`; `ALPACA_PAPER_TRADE` stays `"true"`. No live-money setup in this plan.
- No order is placed by any step of this plan. `ticket.py show` is the furthest the pipeline goes.
- API keys go in the MCP config / environment only, never in chat, never in a tracked file.
- `sec_user_agent` must be a real `"Name email"` string (SEC rejects anonymous requests).
- Every `.md/.py/.json/.csv/.txt/.xml` file anywhere under the repo root (except `tests/fixtures/`) must contain no em dash (U+2014): `tests/test_repo.py:85-91` scans the whole tree, including this plan and `trader/`.
- Upstream code is not modified. Local additions (`trader/`, `.claude/`, `docs/superpowers/`) are excluded via `.git/info/exclude`, not `.gitignore`.

## Review Focus

1. **Running a tool from the wrong directory:** tools resolve `trader/...` relative to cwd; run from anywhere else and they silently create a second `trader/`. Every command below is run from the repo root (Task 4 Step 1 checks this).
2. **Personal email leaking into git:** `trader/config.json` holds the SEC user agent. Task 2 excludes `trader/` and Task 2 Step 4 proves `git status` does not list it.
3. **Em dash in a local file breaking the suite:** a pasted config or note with U+2014 fails `test_repo`. Task 2 Step 5 re-runs `tests/test_repo.py` after local files exist.
4. **Yahoo rate limiting / off-hours data:** the chart endpoint is unofficial and may return 429 or empty intraday bars outside 9:30-16:00 ET. Task 4 treats an HTTP error as "retry later", not a setup failure, and uses `universe` (Nasdaq Trader) as the always-available check.
5. **SEC 403 without a user agent:** Task 4 Step 4 runs `facts.py` with the config UA and expects data, not the "put your name and email" error.

---

### Task 1: Check out the repo and prove the offline suite passes

**Files:**
- Create: repo contents at `/var/www/html/python/Day-trader-skill/` (the directory already holds this plan, so use init+fetch rather than `git clone .`)

**Interfaces:**
- Produces: repo root at `/var/www/html/python/Day-trader-skill` on branch `main`, tracking `origin`.

- [ ] **Step 1: Fetch the repo into the existing directory**

```bash
cd /var/www/html/python/Day-trader-skill
git init -b main
git remote add origin https://github.com/Jakeschincariol/day-trader-skill.git
git fetch --depth 1 origin main
git checkout -t origin/main
```

Expected: `README.md`, `LICENSE`, 11 `trader-*` folders, `tests/`, `.claude-plugin/` present; `docs/` untouched.

- [ ] **Step 2: Confirm the interpreter**

Run: `python3 --version`
Expected: `Python 3.8` or newer (currently 3.10.12).

- [ ] **Step 3: Run the offline suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: ends with `OK`, no failures. If `test_repo` fails on em dash, it is this plan file: fix the character, do not edit tests.

### Task 2: Personal workspace and git hygiene

**Files:**
- Create: `trader/config.json` (copy of `trader-budget/config.example.json`)
- Modify: `.git/info/exclude`

**Interfaces:**
- Consumes: repo root from Task 1.
- Produces: `trader/config.json` with keys identical to `config.example.json`; read by `size.py`, `check.py`, `ticket.py`, `facts.py`, `news.py`, `entry.py`.

- [ ] **Step 1: Create the config**

```bash
mkdir -p trader
cp trader-budget/config.example.json trader/config.json
```

- [ ] **Step 2: Set the user's own values**

Edit `trader/config.json`: `sec_user_agent` to the user's real `"Name email"`; `budget`, `max_loss_per_trade_usd`, `max_daily_loss_usd`, `max_trades_per_day` to the user's numbers (defaults 5000 / 50 / 150 / 3). Leave `"paper": true`, `allow_options/margin/short: false`. Set `pdt_guard: true` if the user's broker still applies the pattern-day-trader rule. Ask the user for these values; do not invent an identity.

- [ ] **Step 3: Exclude local-only paths from git**

Append to `.git/info/exclude`:

```
trader/
.claude/
docs/superpowers/
```

- [ ] **Step 4: Verify nothing personal is visible to git**

Run: `git status --porcelain`
Expected: empty output.

- [ ] **Step 5: Verify config parses and the repo checks still pass**

Run: `python3 -c "import json;c=json.load(open('trader/config.json'));assert c['paper'] is True and '@' in c['sec_user_agent'];print('ok')" && python3 -m unittest tests/test_repo.py`
Expected: `ok`, then `OK`.

### Task 3: Expose the 11 skills to Claude Code

**Files:**
- Create: `.claude/skills/trader-*` (11 symlinks to `../../trader-*`)

**Interfaces:**
- Consumes: skill folders from Task 1.
- Produces: slash commands `/trader-scanner` ... `/trader-pelosi` in Claude Code sessions opened at the repo root.

- [ ] **Step 1: Link the skill folders**

```bash
mkdir -p .claude/skills
for d in trader-*/; do ln -sfn "../../${d%/}" ".claude/skills/${d%/}"; done
```

(Symlinks rather than `cp -r` so `git pull` updates skills. Alternative the user may prefer instead: `/plugin marketplace add Jakeschincariol/day-trader-skill` then `/plugin install day-trader-skill@day-trader-skill`, which namespaces commands as `/day-trader-skill:trader-scanner`. Do not do both: duplicate skills.)

- [ ] **Step 2: Verify links resolve**

Run: `ls .claude/skills | wc -l && for l in .claude/skills/*; do test -f "$l/SKILL.md" || echo "BROKEN $l"; done`
Expected: `11`, no `BROKEN` lines.

- [ ] **Step 3: Verify Claude Code sees them**

Restart Claude Code in `/var/www/html/python/Day-trader-skill`, type `/trader-`.
Expected: all 11 commands autocomplete.

- [ ] **Step 4: Re-run the suite**

Run: `python3 -m unittest discover -s tests`
Expected: `OK` (os.walk does not follow the symlinks, so the 11-folder check is unaffected).

### Task 4: Live smoke test of the read-only data tools

Network required; no files written except under `trader/`. All commands from the repo root.

**Interfaces:**
- Consumes: `trader/config.json` from Task 2.

- [ ] **Step 1: Confirm cwd**

Run: `test -f trader/config.json && test -d trader-scanner && echo root`
Expected: `root`.

- [ ] **Step 2: Symbol universe (Nasdaq Trader, always available)**

Run: `python3 trader-scanner/scan.py universe`
Expected: a count near 5,900 US stocks, no traceback.

- [ ] **Step 3: News for one ticker (Yahoo + SEC)**

Run: `python3 trader-news/news.py --tickers TSLA --hours 12`
Expected: headlines in New York time and/or 8-K lines. HTTP 429/5xx: wait and retry once; persistent failure is an upstream data issue, report it, do not patch code.

- [ ] **Step 4: SEC facts with the configured user agent**

Run: `python3 trader-analyst/facts.py TSLA`
Expected: quarterly revenue/EPS table; not the "put your name and email in trader/config.json" message.

- [ ] **Step 5: House Clerk disclosures**

Run: `python3 trader-pelosi/ptr.py index --member pelosi`
Expected: list of this year's PTR filings (possibly short), no traceback.

- [ ] **Step 6: Intraday tools (only between 9:45 and 15:30 ET on a trading day)**

Run: `python3 trader-scanner/scan.py orb --at 10:15` then `python3 trader-entry/entry.py check TSLA --setup orb`
Expected: a scan list, then `waiting`, `triggered` or `expired`. Outside market hours, skip and note it.

### Task 5: Paper pipeline dry run (no order)

**Interfaces:**
- Consumes: Task 2 config, Task 4 live data.
- Produces: `trader/plan.json`, `trader/timemachine.json`, `trader/orders.json`.

- [ ] **Step 1: Plan, simulate, size, check, show**

```bash
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write
python3 trader-timemachine/timemachine.py run --write
python3 trader-budget/size.py --write
python3 trader-risk/check.py
python3 trader-broker/ticket.py show
```

Expected: each `trader/*.json` exists after its step; `check.py` prints PASS or BLOCK with a reason; `ticket.py show` prints the ticket(s) with READY or not. If `exit.py` reports no setup (no trigger today), the dry run stops there and that is a valid result.

- [ ] **Step 2: Confirm no broker call happened**

Expected: no MCP broker server configured yet (Task 6 is optional) and nothing in this task calls one; `ticket.py` only prints.

### Task 6 (optional, only on the user's explicit go-ahead): Alpaca paper connector

**Files:**
- Modify: Claude Code MCP config (via `claude mcp add`), not any repo file.

- [ ] **Step 1: Install uv**

Run: `curl -LsSf https://astral.sh/uv/install.sh | sh` (per docs.astral.sh/uv), then `uvx --version`.
Expected: a version string.

- [ ] **Step 2: Register the server with paper keys**

The user creates a **paper** account key at alpaca.markets and runs, in their own terminal (keys never pasted into chat):

```bash
claude mcp add alpaca -s project \
  -e ALPACA_API_KEY=... -e ALPACA_SECRET_KEY=... \
  -e ALPACA_PAPER_TRADE=true -e ALPACA_TOOLSETS=account,trading,stock-data \
  -- uvx alpaca-mcp-server
```

`-s project` writes `.mcp.json` at the repo root, which contains secrets: add `.mcp.json` to `.git/info/exclude`, or use `-s local` instead.

- [ ] **Step 3: Verify read-only connectivity**

In Claude Code: `/mcp` shows `alpaca` connected; ask for the paper account balance.
Expected: paper account equity returned. Order tools left on approval-required (never "Always allow").
