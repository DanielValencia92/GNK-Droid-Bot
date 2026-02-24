import discord
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta, time, timezone
import logging
import json
import os
import uuid
import csv
import pytz # New: For timezone handling
from helper import generate_standings_image, parse_deck_json, generate_meta_standings, generate_user_performance_report, generate_user_mastery_report
from logging.handlers import TimedRotatingFileHandler
import sys
import keys
import subprocess

# -- Logging setup
LOG_FILENAME = 'league_bot.log'

#Define the logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Create rotating file handler
file_handler = TimedRotatingFileHandler(
    LOG_FILENAME, 
    when="d", 
    interval=3, 
    backupCount=5,
    encoding='utf-8' # Good practice for Discord bots handling emojis/usernames
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

logging.info("Logging initialized: Rotating every 3 days.")

# --- CONFIGURATION ---
TOKEN = keys.TOKEN  
QUEUE_TIMEOUT_MINUTES = 30
RUNS_FILE = "current_runs.json"
COMPLETED_FILE = "completed_runs.json"
COMPLETED_FILE_PREV = "completed_runs_prev.json"
HISTORY_FILE = "user_history.json"
ADMIN_CHANNEL_ID = keys.ADMIN_CHANNEL_ID
REACTIVATION_REQUEST_CHANNEL_ID = keys.REACTIVATION_REQUEST_CHANNEL_ID
SERVER_ID = keys.SERVER_ID

# ACTUAL TROPHY CHANNEL BELOW
TROPHY_CHANNEL_ID = keys.TROPHY_CHANNEL_ID
LEADERBOARD_CHANNEL_ID = keys.LEADERBOARD_CHANNEL_ID

MAX_RUNS_PER_DAY = 2
MATCH_LIMIT = 3
LOCAL_TZ = pytz.timezone('America/Los_Angeles')

target_time = time(hour=8, minute=30, tzinfo=LOCAL_TZ) # 3 AM


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
pending_reactivations = {}

# Global Var for player queue
player_queue = {}

# Heartbeat queue cleanup
@tasks.loop(minutes=25)
async def queue_cleanup():
    now = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    to_remove = []
    
    for uid, join_time in player_queue.items():
        wait_time = (now - join_time).total_seconds() / 60
        if wait_time > 60: # Auto-remove after 60 minutes
            to_remove.append(uid)
            
    for uid in to_remove:
        del player_queue[uid]
        user = bot.get_user(uid)
        if user:
            try: await user.send("⏰ You've been in queue for over an hour and have been removed. DM any command to stay active!")
            except: pass

async def request_deck_json(user_id):
    user = bot.get_user(user_id)
    if not user:
        return None, None

    try:
        # 1. Send the initial request via DM
        embed = discord.Embed(
            title="🏆 3-0 Trophy Earned!",
            description=(
                "Congratulations! To track your stats, please paste your **SWUDB JSON** decklist here.\n\n"
                "**How to get it:**\n"
                "1. Go to your deck on SWUDB.\n"
                "2. Click **Export** > **Copy JSON**.\n"
                "3. Paste the text here.\n\n"
                "Reply with **'skip'** to track as 'Private Leader/Base'."
            ),
            color=discord.Color.gold()
        )
        await user.send(embed=embed)

        # 2. Define a check to ensure we only listen to this user in DMs
        def check(m):
            return m.author.id == user_id and isinstance(m.channel, discord.DMChannel)

        # 3. Wait for the response (timeout after 5 minutes)
        try:
            msg = await bot.wait_for('message', check=check, timeout=300.0)
            
            if msg.content.lower() == 'skip':
                await user.send("👍 Recorded as Private stats.")
                return "Private Leader", "Private Base"
            
            # 4. Use your helper function to parse the pasted JSON
            leader, base = parse_deck_json(msg.content)
            
            if leader == "Private Leader":
                await user.send("⚠️ I couldn't read that JSON format. Recorded as Private.")
            else:
                await user.send(f"✅ Success! Recorded **{leader}** on **{base}**.")
            
            return leader, base

        except asyncio.TimeoutError:
            await user.send("⏰ Timed out. Your run was recorded with Private stats.")
            return "Private Leader", "Private Base"

    except discord.Forbidden:
        logging.warning(f"Could not DM user {user_id}. They might have DMs closed.")
        return "Private Leader", "Private Base"

# --- TIMEZONE HELPERS ---
def get_last_3am_pacific():
    """Calculates the most recent 3 AM Pacific Time anchor."""
    tz = pytz.timezone('US/Pacific')
    now_pacific = datetime.now(tz)
    
    # Create today's 3 AM
    today_3am = now_pacific.replace(hour=3, minute=0, second=0, microsecond=0)
    
    # If it's currently before 3 AM, the 'reset' happened yesterday at 3 AM
    if now_pacific < today_3am:
        return today_3am - timedelta(days=1)
    return today_3am

# --- DATA PERSISTENCE HELPERS ---
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f: return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w") as f: json.dump(data, f, indent=4)

def can_start_run(user_id):
    history = load_json(HISTORY_FILE)
    user_runs = history.get(str(user_id), [])
    cutoff = get_last_3am_pacific()
    
    # Count runs started AFTER the most recent 3 AM reset
    count = 0
    for ts in user_runs:
        run_time = datetime.fromisoformat(ts)
        # Convert stored time to offset-aware Pacific for comparison
        if run_time.tzinfo is None:
            run_time = pytz.utc.localize(run_time).astimezone(pytz.timezone('US/Pacific'))
            
        if run_time > cutoff:
            count += 1
            
    return count < MAX_RUNS_PER_DAY

def log_new_run_timestamp(user_id):
    history = load_json(HISTORY_FILE)
    if str(user_id) not in history: history[str(user_id)] = []
    # Store as ISO format (UTC)
    history[str(user_id)].append(datetime.now(pytz.utc).isoformat())
    save_json(HISTORY_FILE, history)

# --- CORE LOGIC (Matching & Archiving) ---
async def archive_run(user_id):
    runs = load_json(RUNS_FILE)
    if str(user_id) not in runs: 
        return None
        
    completed = load_json(COMPLETED_FILE)
    
    # 1. Pull the run data (which already contains 'leader' and 'base' from the start)
    run_data = runs.pop(str(user_id))
    run_data["ended_at"] = datetime.now().isoformat()
    run_data["user_id"] = user_id 
    
    # 2. Calculate Final Score
    results = run_data.get("match_results", [])
    wins = sum(1 for m in results if m["res"] == "W")
    losses = sum(1 for m in results if m["res"] == "L")
    
    # 3. Automatic Trophy Logic (No more request_deck_json needed!)
    if wins == 3 and losses == 0:
        # We just fire off the announcement using the data we already have
        bot.loop.create_task(announce_trophy(user_id, run_data))
    
    # 4. Save to Completed History
    completed[run_data["run_id"]] = run_data
    save_json(RUNS_FILE, runs)
    save_json(COMPLETED_FILE, completed)
    
    # 5. Cleanup Queue
    if user_id in player_queue: 
        player_queue.pop(user_id, None) 
        
    return run_data

class ReactivationApprovalView(discord.ui.View):
    def __init__(self, user_id, run_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.run_id = run_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        completed = load_json(COMPLETED_FILE)
        runs = load_json(RUNS_FILE)

        if self.run_id not in completed:
            await interaction.response.send_message("❌ This run is no longer in the archive.", ephemeral=True)
            return

        # Move data back to active
        run_data = completed.pop(self.run_id)
        runs[str(self.user_id)] = run_data
        save_json(RUNS_FILE, runs)
        save_json(COMPLETED_FILE, completed)

        # Notify User
        user = bot.get_user(self.user_id)
        if user:
            embed = discord.Embed(title="♻️ Reactivation Approved", description=f"Your run `{self.run_id}` is now active again!", color=discord.Color.green())
            try: await user.send(embed=embed)
            except: pass

        await interaction.response.send_message(f"✅ Approved reactivation for <@{self.user_id}>", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = bot.get_user(self.user_id)
        if user:
            embed = discord.Embed(title="❌ Reactivation Denied", description=f"Your request to reactivate run `{self.run_id}` was denied by an admin.", color=discord.Color.red())
            try: await user.send(embed=embed)
            except: pass

        await interaction.response.send_message(f"❌ Denied reactivation for <@{self.user_id}>", ephemeral=True)
        self.stop()

class ResultView(discord.ui.View):
    def __init__(self, winner_id, loser_id, match_type="queue"):
        # The 60-second window for the loser to confirm or dispute
        super().__init__(timeout=60)
        self.winner_id, self.loser_id = winner_id, loser_id
        self.match_type = match_type
        self.confirmed = False

    async def process_results(self, is_auto=False):
        """Standard logic for finalizing a match."""
        self.confirmed = True
        runs = load_json(RUNS_FILE)
        
        for uid, opp, res in [(self.winner_id, self.loser_id, "W"), (self.loser_id, self.winner_id, "L")]:
            runs[str(uid)]["opponents_played"].append(opp)
            runs[str(uid)]["match_results"].append({"opp": opp, "res": res, "type": self.match_type})
            #runs[str(uid)]["type"] = self.match_type # Log the match type for future analytics
        
        save_json(RUNS_FILE, runs)

        # Notify Winner
        winner_user = bot.get_user(self.winner_id)
        if winner_user:
            msg = "✅ Result confirmed!" if not is_auto else "⏰ Auto-confirmed (Opponent timed out)."
            try: await winner_user.send(msg)
            except: pass
        
        # Notify Loser
        loser_user = bot.get_user(self.loser_id)
        if loser_user:  
            msg = "✅ Result confirmed!" if not is_auto else "⏰ Auto-confirmed (You timed out)."
            try: await loser_user.send(msg)
            except: pass

        # Check for Run Completion (3 matches)
        for uid in [self.winner_id, self.loser_id]:
            if len(runs[str(uid)]["match_results"]) >= MATCH_LIMIT:
                await archive_run(uid)
                u = bot.get_user(uid)
                if u: await u.send(f"🏆 Run complete and archived!")
        self.stop()

    @discord.ui.button(label="Confirm Result", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.loser_id:
            await interaction.response.send_message("Only the person who was reported as the loser can confirm.", ephemeral=True)
            return
        
        await interaction.response.send_message("✅ Result logged!", ephemeral=True)
        await self.process_results()

    @discord.ui.button(label="Dispute Result", style=discord.ButtonStyle.danger)
    async def dispute(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.loser_id:
            await interaction.response.send_message("Only the person reported as the loser can dispute.", ephemeral=True)
            return
        
        self.confirmed = True 
        self.stop()

        # Fetch player objects for the admin buttons
        winner_user = bot.get_user(self.winner_id)
        loser_user = interaction.user

        await interaction.response.send_message("🚨 **DISPUTE LOGGED.** A moderator has been notified.", ephemeral=True)
        if winner_user:
            await winner_user.send(f"🚨 **MATCH DISPUTED:** {loser_user.name} has disputed your win claim.")

        # Send interactive resolution message to Admin Channel
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID) 
        if admin_channel:
            view = DisputeResolutionView(winner_user, loser_user)
            # Update button labels to player names
            view.children[0].label = f"{winner_user.name} Won"
            view.children[1].label = f"{loser_user.name} Won"

            embed = discord.Embed(title="🚩 Match Dispute", color=discord.Color.red())
            embed.description = f"**{winner_user.name}** claimed a win over **{loser_user.name}**.\nSelect the actual winner below:"
            await admin_channel.send(embed=embed, view=view)

    async def on_timeout(self):
        """Runs if 60 seconds pass without a button click."""
        if not self.confirmed:
            logging.info(f"AUTO-CONFIRM: {self.loser_id} timed out. Awarding win to {self.winner_id}.")
            await self.process_results(is_auto=True)

class MatchReportView(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=None)
        self.p1, self.p2 = p1, p2

    @discord.ui.button(label="I Won", style=discord.ButtonStyle.primary)
    async def win_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        winner = interaction.user
        loser = self.p2 if winner.id == self.p1.id else self.p1
        await loser.send(f"⚠️ **Confirm Result:** {winner.name} claims the win.", view=ResultView(winner.id, loser.id))
        await interaction.response.send_message("Sent to opponent for confirmation.", ephemeral=True)
    @discord.ui.button(label="Opponent No-Show", style=discord.ButtonStyle.secondary)
    async def report_no_show(self, interaction: discord.Interaction, button: discord.ui.Button):
        reporter = interaction.user
        # Identify the other player
        ghost_id = self.p2.id if reporter.id == self.p1.id else self.p1.id
        
        await interaction.response.send_message("🚨 **Report Sent.** Admins have been notified. Please stay at your computer.", ephemeral=True)

        admin_chan = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_chan:
            embed = discord.Embed(title="👻 No-Show Alert", color=discord.Color.orange())
            embed.description = f"**{reporter.name}** reports that **<@{ghost_id}>** is not responding."
            
            # Send the view with the CANCEL button to the admin channel
            view = AdminNoShowView(reporter.id, ghost_id, interaction.message)
            await admin_chan.send(embed=embed, view=view)

class DisputeResolutionView(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=None)
        self.p1 = p1 # Player object
        self.p2 = p2 # Player object

    async def resolve(self, interaction, winner, loser):
        runs = load_json(RUNS_FILE)
        
        # Ensure both players are still in an active run
        if str(winner.id) not in runs or str(loser.id) not in runs:
            await interaction.response.send_message("❌ Error: One of these players no longer has an active run.", ephemeral=True)
            return

        # Log results
        runs[str(winner.id)]["opponents_played"].append(loser.id)
        runs[str(winner.id)]["match_results"].append({"opp": loser.id, "res": "W", "type": "admin_dispute"})
        
        runs[str(loser.id)]["opponents_played"].append(winner.id)
        runs[str(loser.id)]["match_results"].append({"opp": winner.id, "res": "L", "type": "admin_dispute"})
        
        save_json(RUNS_FILE, runs)

        # Notify Players via DM
        for player, msg in [(winner, "✅ A moderator has ruled you the winner of your disputed match."), 
                            (loser, "❌ A moderator has ruled you the loser of your disputed match.")]:
            try: await player.send(msg)
            except: pass

        await interaction.response.send_message(f"✅ Resolved: **{winner.name}** awarded win over **{loser.name}**.", ephemeral=True)
        
        # Auto-archive if they hit the limit
        for uid in [winner.id, loser.id]:
            if len(runs[str(uid)]["match_results"]) >= MATCH_LIMIT:
                await archive_run(uid)
                u = bot.get_user(uid)
                if u: await u.send(f"🏆 Run complete and archived!")
        
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.green)
    async def p1_wins(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, self.p1, self.p2)

    @discord.ui.button(style=discord.ButtonStyle.green)
    async def p2_wins(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, self.p2, self.p1)

class QueueView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start New League Run", style=discord.ButtonStyle.green, custom_id="start_run_btn")
    async def start_run(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        runs = load_json(RUNS_FILE)
        
        # 1. Acknowledge the interaction immediately and ephemerally.
        # This tells Discord the bot received the click without adding a visible "thinking" state.
        await interaction.response.defer(ephemeral=True)

        # Logic for the DM content
        dm_embed = None
        
        if str(uid) in runs:
            dm_embed = discord.Embed(
                description="✅ **You already have an active run!**\nType `ENTER_QUEUE` here in our DMs when you are ready to play.",
                color=discord.Color.blue()
            )
        elif not can_start_run(uid):
            dm_embed = discord.Embed(
                title="🚫 Limit Reached",
                description="You've reached the daily run limit! Runs reset at 3 AM PT.",
                color=discord.Color.red()
            )
        else:
            pending_deck_links[uid] = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
            dm_embed = discord.Embed(
                title="🚀 Start Your Run",
                description=(
                    "Please send your SWUDB JSON export to register.\n\n"
                    "**Note:** This will start your run but will *not* put you in the queue yet. "
                ),
                color=discord.Color.green()
            )

        # 2. Attempt to send the DM
        try:
            await interaction.user.send(embed=dm_embed)
        except discord.Forbidden:
            # ONLY if DMs are closed do we show an ephemeral message in the channel.
            # This is necessary so the user knows why nothing happened.
            await interaction.followup.send("⚠️ I couldn't DM you! Please enable DMs and try again.", ephemeral=True)
            return

        # 3. SILENT FINISH
        # By deleting the original response of the DEFER, we remove the "Only you can see this"
        # message immediately. The main button message remains completely unchanged.
        #await interaction.delete_original_response()
        #await interaction.response.defer(ephemeral=True)

# --- REMAINING BOILERPLATE (Queue/DMs/Admin) ---
#player_queue, queue_timers, pending_deck_links = [], {}, {}
queue_timers = {}
pending_deck_links = {}
pending_manual_reports = {}

async def join_queue_logic(user):
    """Handles priority queue entry and immediate matching using an embed."""
    player_queue[user.id] = datetime.now(timezone.utc).astimezone(LOCAL_TZ) # Track join time
    
    # Create the embed for queue confirmation
    embed = discord.Embed(
        title="📥 Entered Queue",
        description="You have successfully joined the matchmaking pool.",
        color=discord.Color.green()
    )
    embed.add_field(
        name="Priority Status", 
        value="I will prioritize finding you a match based on your wait time (Longest Wait First).", 
        inline=False
    )
    embed.set_footer(text="Stay active! You will be notified here when a match is found.")
    embed.timestamp = datetime.now(timezone.utc).astimezone(LOCAL_TZ)

    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        # Handle cases where the user has DMs disabled
        logging.warning(f"Could not send queue confirmation to {user.name} (DMs disabled).")

    # Proceed to check if an opponent is available
    await check_for_match()

async def check_for_match():
    runs = load_json(RUNS_FILE)
    # Sort queue by join time (Longest wait first)
    sorted_queue = sorted(player_queue.items(), key=lambda item: item[1])
    uids = [item[0] for item in sorted_queue]

    for i in range(len(uids)):
        for j in range(i + 1, len(uids)):
            p1_id, p2_id = uids[i], uids[j]
            
            # Check if they've played this run
            if p2_id not in runs[str(p1_id)]["opponents_played"]:
                # Remove both from queue
                player_queue.pop(p1_id)
                player_queue.pop(p2_id)
                
                p1, p2 = bot.get_user(p1_id), bot.get_user(p2_id)
                view = MatchReportView(p1, p2)
                await p1.send(f"⚔️ **Match Found!** vs {p2.name} [{runs[str(p2_id)]['leader']}] [{runs[str(p2_id)]['base']}]", view=view)
                await p2.send(f"⚔️ **Match Found!** vs {p1.name} [{runs[str(p1_id)]['leader']}] [{runs[str(p1_id)]['base']}]", view=view)
                return

@bot.event
@bot.event
async def on_message(message):

    uid, content = message.author.id, message.content.strip()

    for state_dict, state_name in [(pending_deck_links, "Deck Registration"), 
                                  (pending_reactivations, "Reactivation Request")]:
        if uid in state_dict:
            start_time = state_dict[uid]
            
            # Type guard for boolean legacy issues
            if isinstance(start_time, bool):
                del state_dict[uid]
                continue
                
            if (datetime.now(timezone.utc).astimezone(LOCAL_TZ) - start_time).total_seconds() > 600:
                del state_dict[uid]
                embed = discord.Embed(
                    title="⏰ Session Expired",
                    description=f"Your {state_name} session has timed out due to inactivity. Please start again.",
                    color=discord.Color.red()
                )
                await message.channel.send(embed=embed)
                return # Stop processing this message since the state is gone

    if uid in pending_manual_reports:
        # 1. Check for Timeout
        start_time = pending_manual_reports[uid]
        if (datetime.now(timezone.utc).astimezone(LOCAL_TZ) - start_time).total_seconds() > 300:
            del pending_manual_reports[uid]
            return

        # 2. Validate Opponent ID
        try:
            opp_id = int(content)
        except ValueError:
            await message.channel.send("❌ Please provide a valid numerical User ID.")
            return

        if opp_id == uid:
            await message.channel.send("❌ You cannot play against yourself.")
            return

        runs = load_json(RUNS_FILE)
        
        # 3. Validation Checks
        if str(opp_id) not in runs:
            await message.channel.send(f"❌ <@{opp_id}> does not have an active run.")
            return
            
        if opp_id in runs[str(uid)]["opponents_played"]:
            await message.channel.send(f"❌ You have already played against <@{opp_id}> during this run.")
            return

        # 4. Success - Clear state and launch ResultView
        del pending_manual_reports[uid]
        opp_user = bot.get_user(opp_id)
        
        # We pass "manual" as a new match_type argument
        view = ResultView(uid, opp_id, match_type="local") 
        await message.channel.send(f"Sent match confirmation to **{opp_user.name if opp_user else opp_id}**.")
        if opp_user:
            await opp_user.send(f"⚠️ **Local Match Report:** <@{uid}> claims they won a match against you.", view=view)
        return

    # --- 1. NEW RUN START (JSON SUBMISSION) ---
    if uid in pending_deck_links:
        # 1. Check for Timeout
        start_time = pending_deck_links[uid]
        if (datetime.now(timezone.utc).astimezone(LOCAL_TZ) - start_time).total_seconds() > 600:
            del pending_deck_links[uid]
            return

        # 2. Try to parse the content as JSON
        leader_name, base_name = parse_deck_json(content)
        
        if leader_name != "Private Leader" and base_name != "Private Base":
            runs = load_json(RUNS_FILE)
            run_id = str(uuid.uuid4())[:8]
            
            runs[str(uid)] = {
                "name": message.author.name, 
                "run_id": run_id, 
                "leader": leader_name,
                "base": base_name,
                "opponents_played": [], 
                "match_results": []
            }
            save_json(RUNS_FILE, runs)
            log_new_run_timestamp(uid)
            del pending_deck_links[uid]

            embed = discord.Embed(
                title="✅ Run Registered!",
                description="Your deck has been validated and saved.",
                color=discord.Color.green()
            )
            embed.add_field(name="Leader", value=f"**{leader_name}**", inline=True)
            embed.add_field(name="Base", value=f"**{base_name}**", inline=True)
            embed.add_field(name="Run ID", value=f"`{run_id}`", inline=False)
            embed.set_footer(text="Type ENTER_QUEUE whenever you're ready to play!")
            
            await message.channel.send(embed=embed)
            return 
        
        else:
            # If they sent something that isn't valid JSON
            # We let it fall through or send a gentle reminder
            await message.channel.send("⚠️ That doesn't look like a valid SWUDB JSON export. Please copy/paste the full JSON text.")
            return

    # -- REPORT MATCH COMMAND
    elif content.upper() == "REPORT_MATCH":
        runs = load_json(RUNS_FILE)
        if str(uid) not in runs:
            await message.channel.send("❌ You need an active run to report a match.")
            return
            
        pending_manual_reports[uid] = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
        await message.channel.send("📍 **Local Match Reporting**\nPlease enter the **User ID** of your opponent.")
        return

    # --- 2. FINISH RUN EARLY ---
    elif content.upper() == "FINISH":
        data = await archive_run(uid)
        if data:
            wins = sum(1 for m in data["match_results"] if m["res"] == "W")
            losses = len(data["match_results"]) - wins
            
            embed = discord.Embed(
                title="🏁 Run Archived",
                description="You have successfully completed and archived your run early.",
                color=discord.Color.gold()
            )
            embed.add_field(name="Final Score", value=f"**{wins}W - {losses}L**", inline=True)
            embed.set_footer(text=f"Run ID: {data['run_id']}")
            await message.channel.send(embed=embed)
        else:
            await message.channel.send("❌ You do not have an active run to finish.")

    # --- 3. LEAVE QUEUE ---
    elif content.upper() == "STOP" and uid in player_queue:
        player_queue.pop(uid, None)
        embed = discord.Embed(
            title="🛑 Left Queue",
            description="You have been removed from the matchmaking queue.",
            color=discord.Color.red()
        )
        await message.channel.send(embed=embed)

    # --- 4. STATUS COMMAND ---
    elif content.upper() == "STATUS":
        runs = load_json(RUNS_FILE)
        if str(uid) not in runs:
            await message.channel.send("❌ You do not have an active run. Click the button in the league channel to start one!")
            return

        player_data = runs[str(uid)]
        results = player_data.get("match_results", [])
        
        embed = discord.Embed(
            title="📊 Current Run Status",
            description=f"**Run ID:** `{player_data['run_id']}`",
            color=discord.Color.blue()
        )
        embed.add_field(name="Progress", value=f"{len(results)} / {MATCH_LIMIT} Matches", inline=True)
        #embed.add_field(name="Leader", value=f"[View on SWUDB]({player_data['deck_link']})", inline=True)
        embed.add_field(name="Leader", value=f"**{player_data['leader']}**", inline=False)
        embed.add_field(name="Base", value=f"**{player_data['base']}**", inline=False)

        if not results:
            history_text = "No matches recorded yet. Get out there and play!"
        else:
            history_text = ""
            for i, match in enumerate(results, 1):
                opp_id = match["opp"]
                res = "✅ Win" if match["res"] == "W" else "❌ Loss"
                opponent = bot.get_user(opp_id)
                opp_name = opponent.name if opponent else f"Unknown ({opp_id})"
                history_text += f"**{i}.** vs {opp_name} — {res}\n"

        embed.add_field(name="Match History", value=history_text, inline=False)
        embed.set_footer(text=f"Requested by {message.author.name}")
        embed.timestamp = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
        await message.channel.send(embed=embed)

    # --- 5. DATA EXPORT ---
    elif content.upper() == "MY_DATA":
        current = load_json(RUNS_FILE)
        completed = load_json(COMPLETED_FILE)
        user_data = {
            "active_run": current.get(str(uid), "None"),
            "completed_runs": [data for rid, data in completed.items() if str(data.get("user_id")) == str(uid) or data.get("name") == message.author.name]
        }
        filename = f"data_{uid}.json"
        with open(filename, "w") as f:
            json.dump(user_data, f, indent=4)
        try:
            await message.channel.send("📁 Here is a copy of all your league data:", file=discord.File(filename))
        finally:
            if os.path.exists(filename):
                os.remove(filename)
    
    elif content.upper() == "REQUEST_REACTIVATION":
        pending_reactivations[uid] = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
        await message.channel.send("Please enter the **Run ID** you wish to reactivate.")
    elif content.upper() == "HELP":

        is_admin = False
        guild = bot.get_guild(SERVER_ID) # Use your actual Guild ID
        if guild:
            member = guild.get_member(uid)
            if member and member.guild_permissions.administrator:
                is_admin = True

        embed = discord.Embed(
            title="🤖 GNK Droid Help Menu",
            description="Use these commands in this DM to manage your league runs.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🏃 Player Commands", 
            value=(
                "`STATUS` - View your current run progress.\n"
                "`STOP` - Leave the matchmaking queue.\n"
                "`FINISH` - Archive your run early.\n"
                "`MY_DATA` - Download your full run history.\n"
                "`REQUEST_REACTIVATION` - Request a finished run be reopened.\n"
                "`QUEUED` - View the current matchmaking queue status.\n"
                "`ENTER_QUEUE` - Join the matchmaking queue (if you have an active run and aren't already queued)."
            ), 
            inline=False
        )
        
        if is_admin:
            embed.add_field(
                name="⚙️ Admin Commands (Server Only)", 
                value=(
                    "`!spawn_queue` - Create the entry button.\n"
                    "`!user_run_history [ID]` - View all RunIDs for a player.\n"
                    "`!get_run_data [RunID]` - Detailed view of a specific run.\n"
                    "`!force_result [W_ID] [L_ID]` - Manually log a match result.\n"
                    "`!cancel_run [ID]` - Delete a run and reset history.\n"
                    "`!reactivate_run [RunID]` - Manually restore an archived run."
                ), 
                inline=False
            )
            embed.color = discord.Color.dark_red() # Change color for Admins
            embed.set_author(name="Admin Access Verified")
        
        embed.set_footer(text="GNK Droid Bot")
        await message.channel.send(embed=embed)
    elif content.upper() == "QUEUED":
        # Get the count from the global player_queue list
        count = len(player_queue)
        
        embed = discord.Embed(
            title="👥 Matchmaking Queue Status",
            description=f"There are currently **{count}** player(s) waiting for a match.",
            color=discord.Color.gold() if count > 0 else discord.Color.light_grey()
        )
        
        if count == 0:
            embed.set_footer(text="It's quiet... why not be the first to join?")
        elif count == 1:
            embed.set_footer(text="One person is waiting for an opponent!")
        else:
            embed.set_footer(text="The queue is active! Matches are being made.")

        await message.channel.send(embed=embed)
    elif content.upper() == "ENTER_QUEUE":
        runs = load_json(RUNS_FILE)
        
        # 1. Check if they already have an active run
        if str(uid) in runs:
            # Check if they are already in the queue to prevent duplicates
            if uid in player_queue:
                embed = discord.Embed(
                    description="⚠️ **You are already in the queue!**\nUse `QUEUED` to see how many players are waiting.",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
            else:
                # join_queue_logic handles the timestamping and matchmaking trigger
                # It already sends a confirmation message, but you can update that function too.
                await join_queue_logic(message.author)
        
        # 2. If no active run, inform them that they need to start one first
        else:
            embed = discord.Embed(
                    title="🚀 Start a New Run",
                    description=(
                        "You don't have an active run yet.\n\n"
                        "Please start a new run via the league-queue channel."
                    ),
                    color=discord.Color.blue()
                )
            await message.channel.send(embed=embed)
    elif uid in pending_reactivations:
        # 1. Check for Timeout (600 seconds = 10 minutes)
        start_time = pending_reactivations[uid]
        if (datetime.now(timezone.utc).astimezone(LOCAL_TZ) - start_time).total_seconds() > 600:
            del pending_reactivations[uid]
            # We don't 'return' here; we let the code continue 
            # so if they typed a new command like 'STATUS', it still works.
        else:
            # 2. Process the Run ID
            run_id = content
            completed = load_json(COMPLETED_FILE)
            
            if run_id not in completed:
                await message.channel.send("❌ Invalid Run ID. Please check your history with `MY_DATA`.")
                return
            
            del pending_reactivations[uid]
            
            # Send to Admin Channel
            admin_chan = bot.get_channel(REACTIVATION_REQUEST_CHANNEL_ID)
            if admin_chan:
                embed = discord.Embed(title="📩 Reactivation Request", color=discord.Color.orange())
                embed.add_field(name="User", value=f"<@{uid}>", inline=True)
                embed.add_field(name="Run ID", value=f"`{run_id}`", inline=True)
                
                view = ReactivationApprovalView(uid, run_id)
                await admin_chan.send(embed=embed, view=view)
                await message.channel.send("✅ Request sent to admins for approval.")
            else:
                await message.channel.send("❌ Error: Admin channel not found. Please contact an admin.")
            return # Exit so it doesn't try to process 'run_id' as a command
    await bot.process_commands(message)

@bot.hybrid_command()
@commands.is_owner()
async def spawn_queue(ctx):
    await ctx.send(embed=discord.Embed(title="GNK Droid", description="Daily run limits reset at 3 AM PT.\nHave fun!🚀⭐"), view=QueueView())

@bot.command(name="version")
@commands.is_owner()
async def version(ctx):
    """Displays the current build version based on git commits."""
    try:
        # Get the short hash, the relative time, and the commit message
        git_info = subprocess.check_output(
            ["git", "log", "-1", "--format=%h (%cr): %s"],
            stderr=subprocess.STDOUT
        ).decode("utf-8").strip()

        embed = discord.Embed(
            title="🤖 Bot Version Info",
            description=f"**Current Build:**\n`{git_info}`",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
    except:
        await ctx.send(F"❌ Could not retrieve version info: {e}")

@bot.hybrid_command()
@commands.is_owner()
async def force_result(ctx, winner_id: int, loser_id: int):
    """Admin only: Manually record a match result (resolves disputes)."""
    runs = load_json(RUNS_FILE)

    # 1. Validation: Check if both players actually have active runs
    if str(winner_id) not in runs or str(loser_id) not in runs:
        await ctx.send("❌ Error: One or both of those User IDs do not have an active run.")
        return

    # 2. Prevent self-matching
    if winner_id == loser_id:
        await ctx.send("❌ Error: A player cannot play against themselves.")
        return

    # 3. Log results using the same logic as the ResultView
    try:
        # Update Winner
        runs[str(winner_id)]["opponents_played"].append(loser_id)
        runs[str(winner_id)]["match_results"].append({"opp": loser_id, "res": "W", "type": "admin_forced"})
        
        # Update Loser
        runs[str(loser_id)]["opponents_played"].append(winner_id)
        runs[str(loser_id)]["match_results"].append({"opp": winner_id, "res": "L", "type": "admin_forced"})
        
        save_json(RUNS_FILE, runs)
        
        await ctx.send(f"✅ **Force Logged:** Win for <@{winner_id}> vs <@{loser_id}>.")

        # 4. Check for Auto-Completion for both players
        for uid in [winner_id, loser_id]:
            if len(runs[str(uid)]["match_results"]) >= MATCH_LIMIT:
                await archive_run(uid)
                u = bot.get_user(uid)
                if u: 
                    await u.send(f"🏆 Your run has been completed ({MATCH_LIMIT} matches) and archived!")
                await ctx.send(f"📦 Run for <@{uid}> has reached the limit and was archived.")

    except Exception as e:
        logging.error(f"FORCE_RESULT ERROR: {e}")
        await ctx.send(f"❌ An error occurred: {e}")

@bot.hybrid_command()
@commands.is_owner()
async def cancel_run(ctx, user_id: int):
    """Admin only: Deletes a player's current run without archiving it."""
    runs = load_json(RUNS_FILE)
    
    if str(user_id) not in runs:
        await ctx.send(f"❌ No active run found for User ID `{user_id}`.")
        return

    # Remove from current runs
    player_data = runs.pop(str(user_id))
    save_json(RUNS_FILE, runs)

    # Remove from queue if they are currently waiting
    if user_id in player_queue:
        player_queue.pop(user_id, None)
        if user_id in queue_timers:
            queue_timers[user_id].cancel()
            del queue_timers[user_id]
        logging.info(f"CANCEL_RUN: {player_data['name']} removed from queue.")

    # Clear their history to allow immediate new runs if needed
    history = load_json(HISTORY_FILE)
    if str(user_id) in history:
        history.pop(str(user_id))
        save_json(HISTORY_FILE, history)
        await ctx.send(f"✨ Daily limit history for <@{user_id}> has also been reset.")
    await ctx.send(f"🗑️ **Run Cancelled:** The run for **{player_data['name']}** has been deleted. They can now start a new run (history cleared).")

@bot.hybrid_command()
@commands.is_owner()
async def check_queue(ctx):
    if not player_queue:
        await ctx.send("The queue is empty.")
        return
    names = [bot.get_user(uid).name if bot.get_user(uid) else str(uid) for uid in player_queue]
    await ctx.send(f"**Current Queue ({len(player_queue)}):**\n" + "\n".join(names))

@bot.hybrid_command()
@commands.is_owner()
async def user_run_history(ctx, user_id: int):
    """Admin only: View all RunIDs associated with a User ID."""
    current = load_json(RUNS_FILE)
    completed = load_json(COMPLETED_FILE)
    
    history_found = []
    
    # Check current run
    if str(user_id) in current:
        history_found.append(f"`{current[str(user_id)]['run_id']}` (ACTIVE)")
    
    # Check archived runs
    for run_id, data in completed.items():
        # Match based on the stored user id in the run data
        # Note: Depending on your archive_run logic, ensure the ID is stored in the data
        if str(data.get("user_id")) == str(user_id) or data.get("name") == bot.get_user(user_id).name:
            history_found.append(f"`{run_id}` (COMPLETED)")

    if not history_found:
        await ctx.send(f"No run history found for User ID `{user_id}`.")
    else:
        await ctx.send(f"📜 **Run History for <@{user_id}>:**\n" + "\n".join(history_found))

@bot.hybrid_command()
@commands.is_owner()
async def reactivate_run(ctx, run_id: str):
    """Admin only: Moves a completed run back to active status."""
    completed = load_json(COMPLETED_FILE)
    runs = load_json(RUNS_FILE)

    if run_id not in completed:
        await ctx.send(f"❌ Could not find a completed run with ID `{run_id}`.")
        return

    # 1. Retrieve the data and find the User ID
    run_data = completed.pop(run_id)
    
    # We need the user's Discord ID to restore them to the current_runs file.
    # In your archive_run function, ensure 'user_id' is being saved inside the run_data.
    user_id = str(run_data.get("user_id")) 
    
    # Fallback: if user_id isn't explicitly saved, we attempt to find it via name lookup 
    # (Note: Saving the user_id during archiving is much more reliable)
    if not user_id or user_id == "None":
        await ctx.send("⚠️ Warning: Could not find a stored User ID in the archive. Restoration might be incomplete.")
        return

    # 2. Check if they already have a NEW active run started
    if user_id in runs:
        await ctx.send(f"❌ User <@{user_id}> already has a different active run. You must cancel that one first.")
        # Put the data back since we couldn't reactivate
        completed[run_id] = run_data 
        return

    # 3. Restore to current runs and save files
    runs[user_id] = run_data
    save_json(RUNS_FILE, runs)
    save_json(COMPLETED_FILE, completed)

    # 4. Notify the user and admin
    embed = discord.Embed(
        title="♻️ Run Reactivated",
        description=f"Run `{run_id}` has been moved back to active status.",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

    user = bot.get_user(int(user_id))
    if user:
        try:
            await user.send(f"✅ Your run (ID: `{run_id}`) has been reactivated by an administrator. You can now queue for matches again!")
        except discord.Forbidden:
            pass

@bot.hybrid_command()
@commands.is_owner()
async def get_run_data(ctx, run_id: str):
    """Admin only: Fetch full data for a specific RunID (active or completed)."""
    current = load_json(RUNS_FILE)
    completed = load_json(COMPLETED_FILE)
    
    run_data = None
    status_type = ""

    # 1. Search in Active Runs
    for uid, data in current.items():
        if data.get("run_id") == run_id:
            run_data = data
            status_type = "ACTIVE"
            break

    # 2. Search in Completed Runs if not found in active
    if not run_data and run_id in completed:
        run_data = completed[run_id]
        status_type = "COMPLETED"

    if not run_data:
        await ctx.send(f"❌ Could not find any run with ID `{run_id}`.")
        return

    # 3. Format the data (Similar to STATUS)
    results = run_data.get("match_results", [])
    
    embed = discord.Embed(
        title=f"Run Data: {run_id} ({status_type})",
        color=discord.Color.blue() if status_type == "ACTIVE" else discord.Color.green()
    )
    embed.add_field(name="Player", value=run_data.get("name", "Unknown"), inline=True)
    embed.add_field(name="Progress", value=f"{len(results)}/{MATCH_LIMIT} matches", inline=True)
    #embed.add_field(name="Deck Link", value=f"[View Deck]({run_data.get('deck_link')})", inline=False)
    embed.add_field(name="Leader", value=f"**{run_data.get('leader', 'Unknown')}**", inline=False)
    embed.add_field(name="Base", value=f"**{run_data.get('base', 'Unknown')}**", inline=False)

    if results:
        history_text = ""
        for i, match in enumerate(results, 1):
            opp_id = match["opp"]
            res = "Win" if match["res"] == "W" else "Loss"
            opponent = bot.get_user(opp_id)
            opp_name = opponent.name if opponent else f"ID: {opp_id}"
            history_text += f"{i}. vs **{opp_name}** — {res}\n"
        embed.add_field(name="Match History", value=history_text, inline=False)
    else:
        embed.add_field(name="Match History", value="No matches recorded yet.", inline=False)

    if "ended_at" in run_data:
        embed.set_footer(text=f"Run ended at: {run_data['ended_at']}")

    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.is_owner()
async def delete_run(ctx, run_id: str):
    """Admin only: Remove a specific RunID from history."""
    current = load_json(RUNS_FILE)
    completed = load_json(COMPLETED_FILE)
    deleted = False

    # Check Completed Runs
    if run_id in completed:
        del completed[run_id]
        save_json(COMPLETED_FILE, completed)
        deleted = True
    
    # Check Current Runs
    else:
        for uid, data in list(current.items()):
            if data['run_id'] == run_id:
                del current[uid]
                save_json(RUNS_FILE, current)
                deleted = True
                break

    if deleted:
        await ctx.send(f"🗑️ Run `{run_id}` has been successfully deleted from all records.")
    else:
        await ctx.send(f"❌ Could not find a run with ID `{run_id}`.")

async def announce_trophy(user_id, run_data):
    """Sends a public announcement to the trophy channel."""
    channel = bot.get_channel(TROPHY_CHANNEL_ID)
    if not channel:
        logging.error("Trophy channel not found. Check your TROPHY_CHANNEL_ID.")
        return

    username = run_data.get("name", "A player")
    deck_link = run_data.get("deck_link", "Unknown Deck")
    leader = run_data.get("leader_name", "Unknown Leader")
    base = run_data.get("base_name", "Unknown Base")

    embed = discord.Embed(
        title="🏆 Undefeated Run!",
        description=f"**{username}** earned a trophy!",
        color=discord.Color.gold()
    )
    embed.add_field(name="Leader", value=leader, inline=False)
    embed.add_field(name="Base", value=base, inline=False)
    #embed.add_field(name="Winning Deck", value=f"[View Decklist]({deck_link})")
    
    # Mention the user to celebrate their win
    await channel.send(content=f"🏆 <@{user_id}> earned a trophy!", embed=embed)

@bot.event
async def on_ready():
    queue_cleanup.start()
    passive_timeout_cleanup.start()
    bot.add_view(QueueView())
    daily_standings_report.start()

    if not update_presence.is_running():
        update_presence.start()

    logging.info(f"GNK Droid Online: {bot.user}")

@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Syncs slash commands to the server."""
    await bot.tree.sync()
    await ctx.send("✅ Slash commands synced! (It may take a few minutes to appear for all users.)")


class AdminNoShowView(discord.ui.View):
    def __init__(self, p1_id, p2_id, original_msg_for_players):
        super().__init__(timeout=None)
        self.p1_id = p1_id
        self.p2_id = p2_id
        # We store these to identify which match to "kill"

    @discord.ui.button(label="Cancel Match & Free Players", style=discord.ButtonStyle.danger)
    async def cancel_match(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Update your Match Management logic here
        # If you track active matches in a list/dict, remove it.
        # If they are just 'stuck' because they haven't reported, 
        # this button simply notifies them and lets them know they can re-enter.
        
        for uid in [self.p1_id, self.p2_id]:
            user = bot.get_user(uid)
            if user:
                try:
                    await user.send("⚠️ **Match Cancelled:** An admin has voided your current match. You may now re-enter the queue.")
                except: pass

        await interaction.response.send_message(f"✅ Match between <@{self.p1_id}> and <@{self.p2_id}> has been cancelled.", ephemeral=True)
        self.stop()

@bot.command(name="post_standings")
@commands.is_owner()
async def post_standings(ctx):
    """[ADMIN] Manually post the standings image."""
    image_path = generate_standings_image(COMPLETED_FILE, COMPLETED_FILE_PREV)
    
    if image_path:
        file = discord.File(image_path, filename="standings.png")
        await ctx.send("Generated Standings:", file=file)
    else:
        # Give the admin feedback, even if the daily task stays silent
        await ctx.send("⚠️ No new standings generated. No completed runs or no changes since last report.")


@bot.command()
@commands.is_owner() # Only YOU can trigger a pull/restart
async def update_bot(ctx):
    await ctx.send("📡 Pulling latest code from GitHub and restarting...")
    # This shuts down the bot process
    await bot.close()
        
@bot.command(name="meta")
@commands.is_owner()
async def meta_standings(ctx):
    """Generates an image showing Leader + Aspect win rates."""
    await ctx.send("📊 Analyzing the meta... one moment.")
    
    # Note: Ensure these file names match your actual paths
    image_path = generate_meta_standings("completed_runs.json", "./card_data_files/all_bases.json")
    
    if image_path:
        embed = discord.Embed(
            title="⚔️ Meta Performance Report",
            description="Leader and Aspect color combinations sorted by Win Percentage.",
            color=discord.Color.blue()
        )
        file = discord.File(image_path, filename="meta.png")
        embed.set_image(url="attachment://meta.png")
        await ctx.send(embed=embed, file=file)
    else:
        await ctx.send("❌ No completed runs found to analyze.")

@tasks.loop(seconds=60)
async def update_presence():
    """Updates the bot's status to show how many players are in the queue."""
    await bot.wait_until_ready()
    
    count = len(player_queue)
    
    if count == 0:
        # Status when the queue is empty
        activity = discord.Activity(
            type=discord.ActivityType.watching, 
            name="the queue (Empty)"
        )
    elif count == 1:
        activity = discord.Activity(
            type=discord.ActivityType.watching, 
            name="1 player in queue"
        )
    else:
        activity = discord.Activity(
            type=discord.ActivityType.watching, 
            name=f"{count} players in queue"
        )
    
    await bot.change_presence(activity=activity)

@tasks.loop(minutes=2)
async def passive_timeout_cleanup():
    """Checks every minute for players who started a process but never finished."""
    now = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    
    # Check both pending dictionaries
    for state_dict, label in [(pending_deck_links, "Deck Registration"), 
                             (pending_reactivations, "Reactivation Request")]:
        to_remove = []
        for uid, start_time in state_dict.items():
            if isinstance(start_time, datetime) and (now - start_time).total_seconds() > 600:
                to_remove.append(uid)
        
        for uid in to_remove:
            del state_dict[uid]
            user = bot.get_user(uid)
            if user:
                try:
                    embed = discord.Embed(
                        title="⏰ Timeout",
                        description=f"Your {label} process has timed out. If you still need help, just type the command again!",
                        color=discord.Color.red()
                    )
                    await user.send(embed=embed)
                except:
                    pass # User has DMs closed

def get_loop_time(hour, minute):
    # This grabs the CORRECT -07:00 or -08:00 offset for TODAY
    current_offset = datetime.now(timezone.utc).astimezone(LOCAL_TZ).utcoffset()
    return time(hour=hour, minute=minute, tzinfo=timezone(current_offset))

@tasks.loop(time=get_loop_time(8,30))
async def daily_standings_report():
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel: 
        return

    image_path = generate_standings_image(COMPLETED_FILE, COMPLETED_FILE_PREV)
    
    # Only proceed if an image was successfully created
    if image_path and os.path.exists(image_path):
        embed = discord.Embed(
            title="📊 Daily League Standings",
            description="Here are the current rankings for the season!",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc).astimezone(LOCAL_TZ)
        )
        file = discord.File(image_path, filename="standings.png")
        embed.set_image(url="attachment://standings.png")
        await channel.send(embed=embed, file=file)
        
        # Optional: Clean up the image file after sending
        os.remove(image_path)
    else:
        logging.info("No new standings to post today.")

@bot.command(name="test_trophy")
@commands.is_owner()
async def test_trophy(ctx, member: discord.Member = None):
    """Admin command to test the 3-0 DM and Trophy Announcement flow."""
    target_user = member or ctx.author
    
    await ctx.send(f"🧪 Starting trophy test for {target_user.mention}. Check your DMs!")
    
    # 1. Trigger the DM and wait for JSON (The function we wrote earlier)
    # This will use your all_bases.json and all_leaders.json files to parse
    leader, base = await request_deck_json(target_user.id)
    
    # 2. Create a "mock" run data object to pass to the announcer
    mock_run_data = {
        "name": target_user.display_name,
        "deck_link": "https://swudb.com/deck/view/example", # Placeholder
        "leader_name": leader,
        "base_name": base
    }
    
    # 3. Trigger the public announcement
    await announce_trophy(target_user.id, mock_run_data)
    
    await ctx.send(f"✅ Test complete! Announcement sent to <#{TROPHY_CHANNEL_ID}>.")

@bot.command(name="user_report")
@commands.is_owner()
async def user_report(ctx):
    """Admin only: Generates a table of user performance and positive run counts."""
    await ctx.send("📊 Compiling user performance data... please wait.")
    
    # Generate the image using the helper
    image_path = generate_user_performance_report(COMPLETED_FILE)
    
    if image_path and os.path.exists(image_path):
        embed = discord.Embed(
            title="🏆 User Performance & Positive Records",
            description="Analysis of total wins, losses, and number of runs with a positive record.",
            color=discord.Color.gold()
        )
        file = discord.File(image_path, filename="user_report.png")
        embed.set_image(url="attachment://user_report.png")
        await ctx.send(embed=embed, file=file)
        
        # Cleanup
        os.remove(image_path)
    else:
        await ctx.send("❌ No completed run data found to generate a report.")

@bot.command(name="mastery_report")
@commands.is_owner()
async def mastery_report(ctx):
    """Generates a report of how many unique leaders players have won with."""
    await ctx.send("📋 Calculating player mastery stats...")
    
    image_path = generate_user_mastery_report(COMPLETED_FILE)
    
    if image_path:
        embed = discord.Embed(
            title="🎖️ Player Mastery & Win Rates",
            description="Leaders played to a positive record (Wins > Losses) per player.",
            color=discord.Color.purple()
        )
        file = discord.File(image_path, filename="mastery.png")
        embed.set_image(url="attachment://mastery.png")
        await ctx.send(embed=embed, file=file)
        
        # Cleanup file
        os.remove(image_path)
    else:
        await ctx.send("❌ No data found in completed runs.")

bot.run(TOKEN)
