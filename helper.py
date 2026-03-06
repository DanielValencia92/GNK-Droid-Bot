import pandas as pd
import dataframe_image as dfi
import os
import json
import logging
import requests
from bs4 import BeautifulSoup
import re
from playwright.sync_api import sync_playwright
import shutil

BASE_DATA_PATH = 'card_data_files/all_bases.json'
LEADER_DATA_PATH = 'card_data_files/all_leaders.json'

ASPECT_MAP = {
    "Vigilance": "Blue",
    "Command": "Green",
    "Aggression": "Red",
    "Cunning": "Yellow",
}

def generate_user_performance_report(completed_file):
    if not os.path.exists(completed_file):
        return None

    with open(completed_file, 'r') as f:
        completed_runs = json.load(f)

    # Dictionary to store stats per user
    # { user_id: {"name": str, "wins": int, "losses": int, "pos_runs": int} }
    user_stats = {}

    for run in completed_runs.values():
        uid = str(run.get("user_id"))
        name = run.get("name", "Unknown")
        
        if uid not in user_stats:
            user_stats[uid] = {"Name": name, "Wins": 0, "Losses": 0, "Positive Runs": 0}

        # Calculate run score
        results = run.get("match_results", [])
        run_wins = sum(1 for m in results if m["res"] == "W")
        run_losses = sum(1 for m in results if m["res"] == "L")

        # Update totals
        user_stats[uid]["Wins"] += run_wins
        user_stats[uid]["Losses"] += run_losses
        
        # Track "Positive Records" (minimum 2-0; 1-0 early finishes do not count)
        if run_wins > run_losses and run_wins >= 2:
            user_stats[uid]["Positive Runs"] += 1

    # Convert to DataFrame
    df = pd.DataFrame.from_dict(user_stats, orient='index')
    
    if df.empty:
        return None

    # Calculate Overall Win %
    df['Total Games'] = df['Wins'] + df['Losses']
    df['Win %'] = (df['Wins'] / df['Total Games'] * 100).round(1).fillna(0)
    
    # Sort by Win % (Primary) and Positive Runs (Secondary)
    df = df.sort_values(by=['Win %', 'Positive Runs'], ascending=False).reset_index(drop=True)
    df.index += 1 # Ranking

    # Export
    output_file = "user_performance.png"
    # Adding a color gradient to the Win % column
    df_styled = df.style.background_gradient(subset=['Win %'], cmap='Blues')
    dfi.export(df_styled, output_file, table_conversion='matplotlib')
    
    return output_file

def generate_user_mastery_report(completed_file):
    if not os.path.exists(completed_file):
        return None

    with open(completed_file, 'r') as f:
        completed_runs = json.load(f)

    # Dictionary: { user_id: {"Name": str, "Wins": 0, "Losses": 0, "PosLeaders": set()} }
    user_stats = {}

    for run in completed_runs.values():
        uid = str(run.get("user_id"))
        name = run.get("name", "Unknown")
        leader = run.get("leader_name", "Unknown") # Or 'leader' depending on your JSON key
        
        if uid not in user_stats:
            user_stats[uid] = {"Name": name, "Wins": 0, "Losses": 0, "PosLeaders": set()}

        # Calculate this specific run's score
        results = run.get("match_results", [])
        run_wins = sum(1 for m in results if m["res"] == "W")
        run_losses = sum(1 for m in results if m["res"] == "L")

        # Update global totals
        user_stats[uid]["Wins"] += run_wins
        user_stats[uid]["Losses"] += run_losses
        
        # Mastery Check: If they had a positive record (minimum 2-0), add this leader to their set
        if run_wins > run_losses and run_wins >= 2:
            user_stats[uid]["PosLeaders"].add(leader)

    # Transform data for the table
    rows = []
    for uid, stats in user_stats.items():
        total_games = stats["Wins"] + stats["Losses"]
        win_rate = (stats["Wins"] / total_games * 100) if total_games > 0 else 0
        
        rows.append({
            "Player": stats["Name"],
            "Unique Positive Leaders": len(stats["PosLeaders"]),
            "Total Record": f"{stats['Wins']}W - {stats['Losses']}L",
            "Win %": round(win_rate, 1)
        })

    df = pd.DataFrame(rows)
    if df.empty: return None

    # Sort by Win % (highest first)
    df = df.sort_values(by="Win %", ascending=False).reset_index(drop=True)
    df.index += 1 # Ranking column

    # Export to image
    output_file = "user_mastery.png"
    # Highlight the Mastery column with a different color
    df_styled = df.style.background_gradient(subset=['Win %'], cmap='Greens') \
                        .background_gradient(subset=['Unique Positive Leaders'], cmap='Purples')
    
    dfi.export(df_styled, output_file, table_conversion='matplotlib')
    return output_file

def generate_meta_standings(completed_file, bases_file):
    # 1. Load Base Data for Aspect Mapping
    with open(bases_file, 'r') as f:
        bases_data = json.load(f)
    
    # Map Base Name -> Aspect Color
    base_to_color = {}
    for card in bases_data.get('data', []):
        name = card.get('Name')
        aspects = card.get('Aspects', [])
        # Default to Gray for bases with no aspects (like the 30HP common bases)
        color = ASPECT_MAP.get(aspects[0], "Gray") if aspects else "Gray"
        base_to_color[name] = color

    # 2. Load Completed Runs
    with open(completed_file, 'r') as f:
        completed_runs = json.load(f)

    meta_stats = {}

    # 3. Aggregate results by Leader + Aspect
    for run in completed_runs.values():
        leader = run.get("leader", "Unknown Leader")
        base_name = run.get("base", "Unknown Base")
        color = base_to_color.get(base_name, "Gray")
        
        pair_key = f"{leader} ({color})"
        
        if pair_key not in meta_stats:
            meta_stats[pair_key] = {'Wins': 0, 'Losses': 0}

        for match in run.get("match_results", []):
            if match["res"] == "W":
                meta_stats[pair_key]['Wins'] += 1
            elif match["res"] == "L":
                meta_stats[pair_key]['Losses'] += 1

    # 4. Create DataFrame
    data = []
    for pair, stats in meta_stats.items():
        data.append({
            'Deck (Leader + Aspect)': pair,
            'Wins': stats['Wins'],
            'Losses': stats['Losses']
        })

    df = pd.DataFrame(data)
    
    # If no data, return None
    if df.empty:
        return None

    # 5. Calculations and Sorting
    df['Total Games'] = df['Wins'] + df['Losses']
    df['Win %'] = (df['Wins'] / df['Total Games'] * 100).round(1).fillna(0)
    
    # Sort by Win Rate (primary) and Total Games (secondary)
    df = df.sort_values(by=['Win %', 'Total Games'], ascending=False).reset_index(drop=True)
    df.index += 1 # Rank

    # 6. Export Image
    output_file = "meta_standings.png"
    # styling to make it look nicer
    df_styled = df.style.background_gradient(subset=['Win %'], cmap='Greens')
    dfi.export(df_styled, output_file, table_conversion='matplotlib')
    
    return output_file

def generate_run_stats_report(user_id, completed_file):
    """
    Generates a personal stats image for a specific user.
    Shows overall totals and a per leader/base breakdown.
    Returns the filepath to the generated PNG, or None if no completed runs found.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    if not os.path.exists(completed_file):
        return None

    with open(completed_file, 'r') as f:
        completed_runs = json.load(f)

    # Filter runs belonging to this user
    user_runs = [run for run in completed_runs.values() if str(run.get("user_id")) == str(user_id)]

    if not user_runs:
        return None

    # --- Aggregate totals and per-combo stats ---
    combo_stats = {}
    total_wins = 0
    total_losses = 0

    for run in user_runs:
        leader = run.get("leader", "Unknown Leader")
        base = run.get("base", "Unknown Base")
        combo_key = f"{leader} / {base}"

        if combo_key not in combo_stats:
            combo_stats[combo_key] = {"Runs": 0, "Wins": 0, "Losses": 0}

        results = run.get("match_results", [])
        run_wins = sum(1 for m in results if m["res"] == "W")
        run_losses = sum(1 for m in results if m["res"] == "L")

        combo_stats[combo_key]["Runs"] += 1
        combo_stats[combo_key]["Wins"] += run_wins
        combo_stats[combo_key]["Losses"] += run_losses
        total_wins += run_wins
        total_losses += run_losses

    total_runs = len(user_runs)
    total_games = total_wins + total_losses
    overall_win_pct = round(total_wins / total_games * 100, 1) if total_games > 0 else 0.0

    # --- Build combo rows, sorted by Win % then Runs ---
    combo_rows = []
    for combo, stats in combo_stats.items():
        games = stats["Wins"] + stats["Losses"]
        win_pct = round(stats["Wins"] / games * 100, 1) if games > 0 else 0.0
        combo_rows.append({
            "Leader / Base": combo,
            "Runs": stats["Runs"],
            "Wins": stats["Wins"],
            "Losses": stats["Losses"],
            "Win %": win_pct
        })

    combo_rows.sort(key=lambda r: (r["Win %"], r["Runs"]), reverse=True)

    # --- Build Matplotlib figure with two stacked tables ---
    BG     = '#1e1e2e'
    HDR_S  = '#4b4b8f'   # Summary header
    ROW_S  = '#2a2a4a'   # Summary row
    HDR_C  = '#2e6b3e'   # Combo header
    ROW_C1 = '#1e3a28'
    ROW_C2 = '#162b1f'
    TXT    = 'white'
    EDGE   = '#444466'

    n_combo = len(combo_rows)
    ROW_H = 0.5   # inches per row
    fig_h = ROW_H * 3 + ROW_H * (n_combo + 1) + 0.6  # summary (2 rows) + gap + combo rows
    fig, axes = plt.subplots(2, 1, figsize=(11, max(fig_h, 3.0)),
                             gridspec_kw={'height_ratios': [1.8, max(n_combo + 1, 2)]})
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(BG)
        ax.axis('off')

    # -- Summary table --
    s_cols  = ["Total Runs", "Total Wins", "Total Losses", "Overall Win %"]
    s_vals  = [[total_runs, total_wins, total_losses, f"{overall_win_pct}%"]]
    st = axes[0].table(cellText=s_vals, colLabels=s_cols, cellLoc='center', loc='center')
    st.auto_set_font_size(False)
    st.set_fontsize(11)
    st.scale(1, 2.2)
    for (row, col), cell in st.get_celld().items():
        cell.set_edgecolor(EDGE)
        if row == 0:
            cell.set_facecolor(HDR_S)
            cell.set_text_props(color=TXT, fontweight='bold')
        else:
            cell.set_facecolor(ROW_S)
            cell.set_text_props(color=TXT, fontsize=12, fontweight='bold')

    # -- Combo breakdown table --
    c_cols  = ["#", "Leader / Base", "Runs", "Wins", "Losses", "Win %"]
    c_vals  = [[i + 1, r["Leader / Base"], r["Runs"], r["Wins"], r["Losses"], f"{r['Win %']}%"]
               for i, r in enumerate(combo_rows)]
    ct = axes[1].table(cellText=c_vals, colLabels=c_cols, cellLoc='center', loc='center')
    ct.auto_set_font_size(False)
    ct.set_fontsize(10)
    ct.scale(1, 1.8)
    # Widen the Leader/Base column (col index 1)
    for (row, col), cell in ct.get_celld().items():
        cell.set_edgecolor(EDGE)
        if row == 0:
            cell.set_facecolor(HDR_C)
            cell.set_text_props(color=TXT, fontweight='bold')
        else:
            cell.set_facecolor(ROW_C1 if row % 2 == 0 else ROW_C2)
            cell.set_text_props(color=TXT)
        if col == 1:  # widen Leader/Base column
            cell.set_width(0.45)

    output_file = f"run_stats_{user_id}.png"
    plt.suptitle("Your League Stats", color=TXT, fontsize=14, fontweight='bold', y=1.01)
    plt.tight_layout(pad=0.8)
    plt.savefig(output_file, bbox_inches='tight', facecolor=BG, dpi=130)
    plt.close(fig)

    return output_file


def get_card_map(file_path, card_type="Leader"):
    """
    Creates a mapping of 'SET_NUMBER' to card names.
    Ignores subtitles for Bases specifically.
    """
    card_map = {}
    if not os.path.exists(file_path):
        return card_map
        
    with open(file_path, 'r') as f:
        content = json.load(f)
        for card in content.get('data', []):
            card_id = f"{card.get('Set')}_{card.get('Number')}"
            name = card.get('Name', 'Unknown')
            
            # Subtitle Logic
            if card_type == "Leader":
                subtitle = card.get('Subtitle', '')
                full_display_name = f"{name} - {subtitle}" if subtitle else name
            else:
                # For Bases, we only want the Name
                full_display_name = name
                
            card_map[card_id] = full_display_name
            
    return card_map

def parse_deck_json(user_json_str):
    """
    Matches user IDs against the local card database files.
    """
    try:
        # Load the user's pasted JSON
        deck_data = json.loads(user_json_str)
        
        # Load our local translation maps
        leader_map = get_card_map(LEADER_DATA_PATH, card_type="Leader")
        base_map = get_card_map(BASE_DATA_PATH, card_type="Base")
        
        # Extract IDs from the user's message
        user_leader_id = deck_data.get('leader', {}).get('id', '')
        user_base_id = deck_data.get('base', {}).get('id', '')
        
        # Match names
        leader_name = leader_map.get(user_leader_id, f"Unknown Leader ({user_leader_id})")
        base_name = base_map.get(user_base_id, f"Unknown Base ({user_base_id})")
        
        return leader_name, base_name

    except Exception as e:
        print(f"Parsing error: {e}")
        return "Private Leader", "Private Base"

# --- DATA PERSISTENCE HELPERS ---
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f: return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w") as f: json.dump(data, f, indent=4)

def generate_standings_image(COMPLETED_FILE, COMPLETED_FILE_PREV):
    # 1. Basic Existence Check
    if not os.path.exists(COMPLETED_FILE):
        logging.info("Standings skip: completed_runs.json does not exist.")
        return None

    # 2. Load the current data
    completed = load_json(COMPLETED_FILE)
    if not completed:
        logging.info("Standings skip: completed_runs.json is empty.")
        return None

    # 3. Delta Check (Compare with previous version)
    if os.path.exists(COMPLETED_FILE_PREV):
        previous_data = load_json(COMPLETED_FILE_PREV)
        
        # If the data is exactly the same, stop here
        if completed == previous_data:
            logging.info("Standings skip: No changes detected since last post.")
            return None

    # 4. Data has changed! Update the 'PREV' file for next time
    # We use shutil.copy2 to preserve metadata while overwriting
    shutil.copy2(COMPLETED_FILE, COMPLETED_FILE_PREV)
    logging.info("Changes detected. Updating previous snapshot and generating image.")

    # --- Processing Logic ---
    stats = {}
    for run_id, data in completed.items():
        user = data.get("name", "Unknown")
        results = data.get("match_results", [])
        wins = sum(1 for m in results if m["res"] == "W")
        losses = sum(1 for m in results if m["res"] == "L")
        
        if user not in stats:
            stats[user] = {"Wins": 0, "Losses": 0, "Runs": 0}
        
        stats[user]["Wins"] += wins
        stats[user]["Losses"] += losses
        stats[user]["Runs"] += 1

    df = pd.DataFrame.from_dict(stats, orient='index').reset_index()
    df.columns = ['Player', 'Wins', 'Losses', 'Total Runs']
    
    df['Win %'] = (df['Wins'] / (df['Wins'] + df['Losses']) * 100).round(1).fillna(0)
    df = df.sort_values(by=['Wins', 'Win %'], ascending=False).reset_index(drop=True)
    df.index += 1 

    output_file = "standings.png"
    dfi.export(df, output_file, table_conversion='matplotlib')
    return output_file