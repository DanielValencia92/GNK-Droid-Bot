# GNK Droid Bot

A Discord bot for managing an online Star Wars: Unlimited GNK league. Handles run registration, matchmaking, result reporting, standings, and player/meta analytics.

---

## Overview

Players start a **run** — a set of up to 3 league matches played with a single leader/base combination. The bot manages the full lifecycle:

1. Player clicks the queue button in the league channel → bot DMs them
2. Player pastes their SWUDB JSON export to register their deck
3. Player types `ENTER_QUEUE` to enter the matchmaking pool
4. Bot pairs players and notifies both via DM
5. Winner clicks "I Won" → loser confirms (or auto-confirms after 3 minutes)
6. After 3 matches the run is automatically archived; a 3-0 run triggers a public trophy announcement

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
playwright
beautifulsoup4
requests
```

Install dependencies:

```bash
pip install -r requirements.txt
playwright install
```

### Configuration

Create a `keys.py` file in the project root with the following values:

```python
TOKEN                         = "your-bot-token"
ADMIN_CHANNEL_ID              = 000000000000000000
REACTIVATION_REQUEST_CHANNEL_ID = 000000000000000000
SERVER_ID                     = 000000000000000000
TROPHY_CHANNEL_ID             = 000000000000000000
LEADERBOARD_CHANNEL_ID        = 000000000000000000
```

### Card Data Files

Place the following JSON files (card database exports) in `card_data_files/`:

- `all_leaders.json`
- `all_bases.json`

These are used to resolve deck IDs from SWUDB JSON exports into human-readable leader/base names.

### Running the Bot

```bash
python gnk_bot.py
```

---

## Data Files

| File | Purpose |
|---|---|
| `current_runs.json` | Active (in-progress) runs, keyed by user ID |
| `completed_runs.json` | Archived completed runs, keyed by run ID |
| `completed_runs_prev.json` | Previous snapshot used for standings delta detection |
| `user_history.json` | Per-user run start timestamps for daily limit enforcement |

---

## Player Commands (DM the bot)

| Command | Description |
|---|---|
| *(SWUDB JSON paste)* | Registers your leader/base after clicking the Start Run button |
| `ENTER_QUEUE` | Join the matchmaking queue (requires an active run) |
| `STATUS` | View your current run: leader, base, match history, progress |
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
| `!meta` | Generate a leader + aspect win rate breakdown image |
| `!user_report` | Generate a per-player wins/losses/positive runs performance image |
| `!mastery_report` | Generate a per-player unique positive leaders/win rate image |
| `!test_trophy <member>` | Test the 3-0 DM and trophy announcement flow |
| `!update_bot` | Pull the latest code from GitHub and restart the bot |
| `!version` | Show the current git build info |
| `!sync` | Sync slash commands to the server |

---

## Automated Tasks

| Task | Schedule | Description |
|---|---|---|
| Daily standings report | 8:30 AM PT | Posts a standings image to the leaderboard channel if data has changed since the last post |
| Queue cleanup | Every 25 minutes | Removes players who have been in queue for over 60 minutes and notifies them |
| Passive timeout cleanup | Every 2 minutes | Clears expired deck registration and reactivation request sessions |
| Presence update | Every 60 seconds | Updates the bot's status to reflect the number of players currently in queue |

---

## Reports

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

## Trophy System

A **3-0 run** (3 wins, 0 losses) automatically triggers:
1. A public embed announcement in the configured trophy channel mentioning the player
2. The embed displays the winning leader and base

---

## Project Structure

```
gnk_bot.py          # Main bot logic — commands, events, tasks, views
helper.py           # Image generation (standings, reports, meta) and deck parsing utilities
keys.py             # Secret configuration (not committed)
card_data_files/
  all_leaders.json  # Leader card database
  all_bases.json    # Base card database
```
