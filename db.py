import sqlite3
from datetime import datetime
import os
import shutil
import json

DB_FILE = "game_data.db"
JSON_LOG = "game_log.json"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS chain_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER,
            receiver_id INTEGER,
            clip_url TEXT,
            artist TEXT,
            song TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_clip(sender_id, receiver_id, clip_url, artist, song):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO chain_log (sender_id, receiver_id, clip_url, artist, song)
        VALUES (?, ?, ?, ?, ?)
    ''', (sender_id, receiver_id, clip_url, artist, song))
    conn.commit()
    conn.close()

    log_to_json(sender_id, receiver_id, clip_url, artist, song)

def log_to_json(sender_id, receiver_id, clip_url, artist, song):
    entry = {
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "clip_url": clip_url,
        "artist": artist,
        "song": song,
        "timestamp": datetime.utcnow().isoformat()
    }
    if os.path.exists(JSON_LOG):
        with open(JSON_LOG, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = []

    data.append(entry)
    with open(JSON_LOG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_chain_log(bot):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT sender_id, receiver_id, clip_url, artist, song FROM chain_log ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()

    log = []
    for sender_id, receiver_id, clip_url, artist, song in rows:
        sender = bot.get_user(sender_id)
        receiver = bot.get_user(receiver_id) if receiver_id else None
        log.append((sender, receiver, clip_url, artist, song))
    return log

def archive_database():
    if os.path.exists(DB_FILE):
        timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        archived_name = f"archive_{timestamp}.db"
        shutil.move(DB_FILE, archived_name)
        print(f"📁 Archived game data to {archived_name}")
        
        if os.path.exists(JSON_LOG):
            shutil.copy(JSON_LOG, f"archive_{timestamp}.json")

        init_db()
