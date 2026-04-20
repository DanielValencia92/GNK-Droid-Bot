# GNK Droid Bot

A Discord bot for managing an online Star Wars: Unlimited GNK league. Handles run registration, matchmaking, result reporting, standings, and player/meta analytics.

---

## Overview

Players start a **run** — a set of up to 3 league matches played with a single leader/base combination. The bot manages the full lifecycle:

1. Player clicks the queue button in the league channel → bot DMs them
2. Player pastes their SWUDB JSON export to register their deck
3. Player types `ENTER_QUEUE` to enter the matchmaking pool
4. Bot pairs players, creates a **private match thread**, adds both players, and DMs each a link to it
5. Winner clicks "I Won" in the thread → loser confirms or disputes (or auto-confirms after 3 minutes)
6. On confirmation the match thread is automatically deleted
7. After 3 matches the run is automatically archived; a 3-0 run triggers a public trophy announcement

Runs reset at **3 AM Pacific** with a limit of **2 runs per player per day**.

---

## Setup

### Requirements

```
discord.py
pandas
dataframe_image
matplotlib
pytz
beautifulsoup4
requests
psycopg2-binary
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### Configuration

Create a `keys.py` file in the project root with the following values:

```python
TOKEN                           = "your-bot-token"
ADMIN_CHANNEL_ID                = 000000000000000000
REACTIVATION_REQUEST_CHANNEL_ID = 000000000000000000
SERVER_ID                       = 000000000000000000
TROPHY_CHANNEL_ID               = 000000000000000000
LEADERBOARD_CHANNEL_ID          = 000000000000000000
MATCH_THREAD_CHANNEL_ID         = 000000000000000000  # Channel where private match threads are created
QUEUE_CHANNEL_ID                = 000000000000000000  # Channel where queue join notifications are posted
QUEUE_ROLE_ID                   = 000000000000000000  # Role to ping when a player joins the queue
```

> **`MATCH_THREAD_CHANNEL_ID`** must be a standard text channel in your server. The bot requires **Create Private Threads**, **Manage Threads**, and **Send Messages in Threads** permissions in that channel. Private threads also require the server to be at **Boost Level 2** or above. If the key is omitted or the thread cannot be created, the bot falls back to DM-only match notifications.

> **`QUEUE_CHANNEL_ID`** and **`QUEUE_ROLE_ID`** are optional. If set, the bot posts a notification embed to that channel mentioning the specified role whenever a player joins the matchmaking queue. If omitted, the feature is silently disabled.

### Card Data Files

The card database files in `card_data_files/` are used to resolve deck IDs from SWUDB JSON exports into human-readable leader/base names. On first setup, run the following admin command in Discord after the bot is online:

```
!update_card_data
```

This fetches the latest leaders and bases from the SWUDB API and writes `card_data_files/all_leaders.json` and `card_data_files/all_bases.json` automatically. Re-run the command whenever new cards are released.

### Running the Bot

For production use, the included `deploy.sh` script automatically restarts the bot after crashes or updates:

```bash
bash deploy.sh
```

The script reads the target Git branch from a local `target_branch` file (not committed). Create this file on the server before first launch:

```bash
echo "main" > target_branch
```

The branch can be changed at runtime using the `!update_bot` command — see Admin Commands below.

---

## Data Files

| File | Purpose |
|---|---|
| `current_runs.json` | Active (in-progress) runs, keyed by user ID |
| `completed_runs.json` | Archived completed runs, keyed by run ID |
| `completed_runs_prev.json` | Previous snapshot used for standings delta detection |
| `user_history.json` | Per-user run start timestamps for daily limit enforcement |
| `weekly_report_hash.txt` | SHA-256 hash of `completed_runs.json` at the time of the last weekly report post; used to skip the report if nothing has changed |

---

## Player Commands (DM the bot)

| Command | Description |
|---|---|
| *(SWUDB JSON paste)* | Registers your leader/base after clicking the Start Run button |
| `ENTER_QUEUE` | Join the matchmaking queue (requires an active run) |
| `STATUS` | View your current run: leader, base, match history, progress. If you registered a deck JSON, a button to copy/download it is included. |
| `RUN_STATS` | Get a personal stats image — total runs, overall win rate, and a breakdown by leader/base combo |
| `MY_DATA` | Download your full run history as a JSON file |
| `STOP` | Leave the matchmaking queue |
| `FINISH` | Archive your current run early (at any score) |
| `REPORT_MATCH` | Report a match played outside the bot queue (local/scheduled); requires opponent's Discord user ID |
| `REQUEST_REACTIVATION` | Request an admin to reopen a completed run |
| `QUEUED` | See how many players are currently in the matchmaking queue |
| `HELP` | Show the help menu (shows admin commands too if you are a server admin) |

### Queue Behaviour

- Players are matched in **longest-wait-first** order
- Players cannot be matched against the same opponent twice in the same run
- Queue entries are automatically removed after **60 minutes** of inactivity
- A pending deck registration or reactivation request expires after **10 minutes**

---

## Admin Commands (server prefix commands, bot owner only)

| Command | Description |
|---|---|
| `!spawn_queue` | Post the "Start New League Run" button in the current channel |
| `!force_result <winner_id> <loser_id>` | Manually record a match result for two active players |
| `!cancel_run <user_id>` | Delete a player's active run and reset their daily history |
| `!check_queue` | List the names of all players currently in the queue |
| `!user_run_history <user_id>` | Show all run IDs (active and completed) for a player |
| `!reactivate_run <run_id>` | Move a completed run back to active status |
| `!get_run_data <run_id>` | View full details of any run (active or completed) |
| `!delete_run <run_id>` | Permanently remove a run from all records |
| `!post_standings` | Manually post the current standings image |
| `!post_weekly_report` | Manually trigger the weekly season report to the leaderboard channel (respects the unchanged-data check) |
| `!post_weekly_report_here` | Trigger the weekly season report in the current channel; always forces output regardless of data changes — useful for testing |
| `!meta` | Generate a leader + aspect win rate breakdown image |
| `!user_report` | Generate a per-player wins/losses/positive runs performance image |
| `!mastery_report` | Generate a per-player unique positive leaders/win rate image |
| `!test_trophy <member>` | Test the 3-0 DM and trophy announcement flow |
| `!update_bot [branch]` | Pull the latest code and restart. Writes `branch` (default: `main`) to the `target_branch` file used by `deploy.sh`. Example: `!update_bot experimental` |
| `!update_card_data` | Fetch the latest leaders and bases from the SWUDB API and overwrite `card_data_files/all_leaders.json` and `card_data_files/all_bases.json` |
| `!version` | Show the current git build info |
| `!sync` | Sync slash commands to the server |

---

## Automated Tasks

| Task | Schedule | Description |
|---|---|---|
| Weekly season report | 8:30 AM PT every Monday | Posts four standings images (Champion, Tinkerer, Final Showdown, Meta) to the leaderboard channel. Skipped automatically if `completed_runs.json` has not changed since the last post. |
| Queue cleanup | Every 25 minutes | Removes players who have been in queue for over 60 minutes and notifies them |
| Passive timeout cleanup | Every 2 minutes | Clears expired deck registration and reactivation request sessions |
| Presence update | Every 60 seconds | Updates the bot's status to reflect the number of players currently in queue |

---

## Reports

### Weekly Season Report (automated + `!post_weekly_report`)
Posted every Monday at 8:30 AM PT to the leaderboard channel. Posts a header embed followed by four images covering all award categories:

| Image | Description |
|---|---|
| 👑 Champion Standings | Players ranked by number of 3-0 trophy runs, then total runs completed |
| 🛠️ Tinkerer Standings | Players ranked by unique leader/base combos taken to a positive result (≥2 wins, wins > losses), then win % |
| 📈 Final Showdown Standings | Players ranked by win % across runs completed in the last 14 days, then total games in that window |
| ⚔️ Meta Report | Leader + Aspect win rates across all completed runs |

The report is skipped if `completed_runs.json` has not changed since the last post. Use `!post_weekly_report_here` to force output in the current channel regardless.

### `!user_report` — Player Performance
Tabulated PNG showing each player's total **Wins**, **Losses**, **Positive Runs**, **Total Games**, and **Win %**, sorted by Win %.

> A run counts as a **positive run** only if the player won at least **2 matches** with a winning record (2-0, 2-1, or 3-0). A 1-0 early finish counts toward win/loss totals but does not qualify as a positive run.

### `!mastery_report` — Player Mastery
PNG table showing each player's **Unique Positive Leaders** (distinct leader/base combos taken to a positive record), overall record, and Win %, sorted by Win %.

### `!meta` — Meta Standings
PNG table of every **Leader + Aspect** combination that has been played, showing Wins, Losses, Total Games, and Win %, sorted by Win %.

### `RUN_STATS` (Player DM command)
Personal stats image sent directly to the requesting player. Shows:
- **Summary row:** Total Runs · Total Wins · Total Losses · Overall Win %
- **Combo breakdown table:** Each leader/base combination played, with Runs, Wins, Losses, and Win % (sorted by Win %)

---

## Deck JSON Reference

When a player registers their deck by pasting a SWUDB JSON export, the raw JSON is stored with their run. This allows them to retrieve the exact deck they registered at any time:

- **During a match:** A "📋 Deck Reference" button appears in the private match thread. Each player clicks it to privately receive their own deck JSON as an inline code block (one-click copy). If the JSON is unusually large, a downloadable file is sent instead.
- **Via `STATUS`:** The same button is included at the bottom of the status response whenever a deck JSON is on file for the current run.

Players who started a run before this feature was added will not have a stored deck JSON and will see an error if they click the button.

---



A **3-0 run** (3 wins, 0 losses) automatically triggers:
1. A public embed announcement in the configured trophy channel mentioning the player
2. The embed displays the winning leader and base

---

## Project Structure

```
gnk_bot.py          # Main bot logic — commands, events, tasks, views
helper.py           # Image generation (standings, reports, meta) and deck parsing utilities
db.py               # PostgreSQL key-value persistence layer (no-op when DATABASE_URL is not set)
deploy.sh           # Restart loop script; reads target branch from target_branch file
requirements.txt    # Python dependencies
keys.py             # Secret configuration (not committed)
target_branch       # Plain text file containing the Git branch to pull on restart (not committed)
card_data_files/
  all_leaders.json  # Leader card database (populated by !update_card_data)
  all_bases.json    # Base card database (populated by !update_card_data)
```
