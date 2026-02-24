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
        
        # Track "Positive Records" (e.g., 2-1 or 3-0)
        if run_wins > run_losses:
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
        
        # Mastery Check: If they had a positive record, add this leader to their set
        if run_wins > run_losses:
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