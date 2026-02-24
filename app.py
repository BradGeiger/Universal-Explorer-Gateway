import os
import sqlite3
import json
import collections
import re
import shutil
from datetime import datetime
from flask import Flask, render_template_string, request, session, redirect, jsonify, abort

app = Flask(__name__)
app.secret_key = "TEMPORARY_SECRET_KEY_12345" # Slot: {{SECRET_KEY_STRING}}

# --- CONFIGURATION SLOTS ---
# Change these to point to your specific directories
DEFAULT_ROOT = os.path.abspath("./library_root") 
DB_PATH = "intelligence_schema.db" # Slot: {{DB_PATH}}
ARCHIVE_PATH = "./archives"       # Slot: {{ARCHIVE_DESTINATION}}
INSIGHTS_JSON = "insights.json"

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS explorer_paths 
                      (path_id INTEGER PRIMARY KEY, absolute_path TEXT UNIQUE, last_scraped TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS term_dictionary 
                      (term_id INTEGER PRIMARY KEY, word TEXT UNIQUE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS term_relationships 
                      (rel_id INTEGER PRIMARY KEY, path_id INTEGER, term_id INTEGER, frequency INTEGER)''')
    conn.commit()
    conn.close()

# --- HTML TEMPLATE (JINJA2 + D3.js + SIDEBAR) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Universal Gateway & Analytics</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; display: flex; margin: 0; background: #f4f7f6; height: 100vh; }
        #main-content { flex: 1; padding: 20px; overflow-y: auto; }
        #sidebar { width: 320px; background: #2c3e50; color: white; padding: 20px; box-shadow: -2px 0 10px rgba(0,0,0,0.3); overflow-y: auto; }
        .address-bar { background: #fff; padding: 15px; border-radius: 8px; display: flex; gap: 10px; margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .address-bar input { flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 4px; }
        .item-list { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; }
        .item-list td { padding: 12px; border-bottom: 1px solid #eee; }
        .folder { color: #f39c12; font-weight: bold; text-decoration: none; }
        .file { color: #3498db; text-decoration: none; }
        .insight-card { background: #34495e; padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 5px solid #e74c3c; }
        .btn { padding: 5px 10px; font-size: 10px; cursor: pointer; border: none; border-radius: 3px; }
        .btn-merge { background: #27ae60; color: white; }
        .btn-archive { background: #e67e22; color: white; }
    </style>
</head>
<body>
    <div id="main-content">
        <h1>📊 Universal Gateway</h1>
        
        <div class="address-bar">
            <form action="/connect" method="POST" style="display:flex; width:100%; gap:10px;">
                <input type="text" name="url_path" placeholder="Enter System Path..." value="{{ current_root }}">
                <button type="submit" style="background:#2ecc71; color:white; border:none; padding:10px 20px; border-radius:4px;">Connect</button>
            </form>
        </div>

        <div style="margin-bottom:20px;">
            <strong>Recent:</strong>
            {% for path in history %}
                <a href="/explore/{{ path }}" style="font-size:12px; margin-right:10px; color:#7f8c8d;">{{ path.split('/')[-1] }}</a>
            {% endfor %}
        </div>

        <table class="item-list">
            {% if items %}
                {% for item in items %}
                <tr>
                    <td><a href="{{ item.link }}" class="{{ 'folder' if item.is_dir else 'file' }}">
                        {{ '📁' if item.is_dir else '📄' }} {{ item.name }}
                    </a></td>
                    <td style="text-align:right; color:#95a5a6;">{{ item.size }}</td>
                </tr>
                {% endfor %}
            {% else %}
                <tr><td>No files found or directory not entered.</td></tr>
            {% endif %}
        </table>
    </div>

    <div id="sidebar">
        <h3>🚨 Insights & Mitigation</h3>
        <div id="insight-container">
            {% for insight in insights %}
            <div class="insight-card">
                <small>OVERLAP: {{ insight.similarity }}</small><br>
                <strong>Paths:</strong><br>
                <span style="font-size:10px; color:#bdc3c7;">{{ insight.path_a }}<br>vs<br>{{ insight.path_b }}</span>
                <div style="margin-top:10px;">
                    <button class="btn btn-merge" onclick="triggerAction('merge', '{{ insight.path_a }}', '{{ insight.path_b }}')">MERGE</button>
                    <button class="btn btn-archive" onclick="triggerAction('archive', '', '{{ insight.path_b }}')">ARCHIVE</button>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>

    <script>
        async function triggerAction(action, pA, pB) {
            if(!confirm(`Confirm ${action} operation?`)) return;
            const res = await fetch('/mitigate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ action: action, path_a: pA, path_b: pB })
            });
            const data = await res.json();
            alert(data.message);
            location.reload();
        }
    </script>
</body>
</html>
"""

# --- ROUTES & LOGIC ---

@app.route('/')
def home():
    history = session.get('history', [])
    insights = load_local_insights()
    return render_template_string(HTML_TEMPLATE, current_root="", items=[], history=history, insights=insights)

@app.route('/connect', methods=['POST'])
def connect():
    path = request.form.get('url_path')
    if path and os.path.exists(path):
        history = session.get('history', [])
        if path in history: history.remove(path)
        history.insert(0, path)
        session['history'] = history[:5]
        return redirect(f"/explore/{path}")
    return redirect("/")

@app.route('/explore/<path:full_path>')
def explore(full_path):
    # Ensure full_path starts from root correctly for OS
    if not full_path.startswith('/') and not ':' in full_path:
        full_path = '/' + full_path

    items = []
    try:
        for entry in os.scandir(full_path):
            if entry.name.startswith('.'): continue
            items.append({
                "name": entry.name, "is_dir": entry.is_dir(),
                "link": f"/explore/{os.path.join(full_path, entry.name)}",
                "size": f"{round(entry.stat().st_size/1024,1)}KB" if not entry.is_dir() else "-"
            })
    except Exception as e:
        return f"Error: {str(e)}"

    insights = load_local_insights()
    return render_template_string(HTML_TEMPLATE, current_root=full_path, 
                                  items=items, history=session.get('history', []), insights=insights)

@app.route('/mitigate', methods=['POST'])
def mitigate():
    data = request.json
    action, pA, pB = data.get('action'), data.get('path_a'), data.get('path_b')
    try:
        if action == 'archive':
            if not os.path.exists(ARCHIVE_PATH): os.makedirs(ARCHIVE_PATH)
            shutil.make_archive(os.path.join(ARCHIVE_PATH, os.path.basename(pB)), 'zip', pB)
            shutil.rmtree(pB)
            return jsonify({"status": "success", "message": "Path Archived."})
        elif action == 'merge':
            for f in os.listdir(pB):
                shutil.move(os.path.join(pB, f), os.path.join(pA, f))
            shutil.rmtree(pB)
            return jsonify({"status": "success", "message": "Paths Merged."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 
