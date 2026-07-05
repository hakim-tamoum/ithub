import os
import time
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

last_save_time = 0

DEFAULT_COLS = {
    'users': ['ID', 'Nom complet', 'UserID', 'Service', 'Hostname', 'Switch', 'Port'],
    'hosts': ['ID', 'Hostname', 'Marque', 'CPU', 'RAM', 'Disque dure', 'Système d\'exploitation'],
    'switchs': ['ID', 'Nom', 'IP', 'Modèle', 'Ports Total'],
    'imprimantes': ['ID', 'Nom', 'IP', 'Modèle', 'Emplacement']
}

def get_file_path(entity):
    return f"{entity}.xlsx"

def ensure_excel_exists(entity):
    filepath = get_file_path(entity)
    if not os.path.exists(filepath):
        cols = DEFAULT_COLS.get(entity, ['ID', 'Nom', 'Description', 'Statut'])
        df = pd.DataFrame(columns=cols)
        df.to_excel(filepath, index=False)
        return cols
    else:
        return pd.read_excel(filepath, nrows=0).columns.tolist()

# --- LOGIQUE D'AUTOMATISATION ---
def auto_sync_host(hostname):
    """Vérifie si le hostname existe dans hosts.xlsx, sinon l'ajoute."""
    if not hostname or str(hostname).strip() == "":
        return
    
    db_hosts = read_db('hosts')
    # Vérifier si le hostname existe déjà (insensible à la casse)
    exists = any(str(h.get('Hostname', '')).lower() == str(hostname).lower() for h in db_hosts['data'])
    
    if not exists:
        cols = db_hosts['columns']
        new_host = {col: '' for col in cols}
        new_host['Hostname'] = hostname
        # Création d'un ID auto-incrémenté
        new_id = max([int(h.get('ID', 0)) for h in db_hosts['data']] + [0]) + 1
        new_host['ID'] = new_id
        
        db_hosts['data'].append(new_host)
        write_db('hosts', db_hosts['data'], cols)
        print(f"Auto-sync: Host '{hostname}' ajouté à hosts.xlsx")
        socketio.emit('db_updated', {'entity': 'hosts'})

# --- FONCTIONS DE BASE ---
def read_db(entity):
    filepath = get_file_path(entity)
    cols = ensure_excel_exists(entity)
    try:
        df = pd.read_excel(filepath, dtype=str).fillna('')
        items = []
        for _, row in df.iterrows():
            if not str(row.get('ID', '')).strip(): continue
            item = {col: row.get(col, '') for col in cols}
            # Conversion ID en int pour le calcul
            try: item['ID'] = int(float(row['ID'])) 
            except: pass
            items.append(item)
        return {'columns': cols, 'data': items}
    except Exception as e:
        print(f"Erreur lecture {filepath}: {e}")
        return {'columns': cols, 'data': []}

def write_db(entity, data, cols):
    global last_save_time
    filepath = get_file_path(entity)
    df = pd.DataFrame(data, columns=cols)
    last_save_time = time.time()
    try:
        df.to_excel(filepath, index=False)
    except PermissionError:
        print(f"Erreur: {filepath} est ouvert.")

# --- ROUTES API ---
@app.route('/')
def index():
    return render_template('ithub.html')

@app.route('/api/<entity>', methods=['GET'])
def get_items(entity):
    return jsonify(read_db(entity))

@app.route('/api/<entity>', methods=['POST'])
def add_item(entity):
    db = read_db(entity)
    cols, items = db['columns'], db['data']
    data = request.json
    
    new_id = max([int(i.get('ID', 0)) for i in items] + [0]) + 1
    new_item = {col: data.get(col, '') for col in cols}
    new_item['ID'] = new_id
    items.append(new_item)
    write_db(entity, items, cols)
    
    # --- DÉCLENCHEMENT AUTO-SYNC ---
    if entity == 'users' and 'Hostname' in data:
        auto_sync_host(data['Hostname'])
    
    socketio.emit('db_updated', {'entity': entity})
    return jsonify({'success': True})

@app.route('/api/<entity>/<int:item_id>', methods=['PUT'])
def update_item(entity, item_id):
    db = read_db(entity)
    cols, items = db['columns'], db['data']
    data = request.json
    
    for i, item in enumerate(items):
        if int(item.get('ID', -1)) == item_id:
            updated_item = {col: data.get(col, item.get(col, '')) for col in cols}
            updated_item['ID'] = item_id
            items[i] = updated_item
            
            # --- DÉCLENCHEMENT AUTO-SYNC ---
            if entity == 'users' and 'Hostname' in data:
                auto_sync_host(data['Hostname'])
            break
            
    write_db(entity, items, cols)
    socketio.emit('db_updated', {'entity': entity})
    return jsonify({'success': True})

@app.route('/api/<entity>/<int:item_id>', methods=['DELETE'])
def delete_item(entity, item_id):
    db = read_db(entity)
    cols, items = db['columns'], db['data']
    items = [i for i in items if int(i.get('ID', -1)) != item_id]
    write_db(entity, items, cols)
    socketio.emit('db_updated', {'entity': entity})
    return jsonify({'success': True})

@app.route('/api/<entity>/upload', methods=['POST'])
def upload_excel(entity):
    global last_save_time
    if 'file' not in request.files: return jsonify({'error': 'Fichier manquant'}), 400
    file = request.files['file']
    last_save_time = time.time()
    file.save(get_file_path(entity))
    socketio.emit('db_updated', {'entity': entity})
    return jsonify({'success': True})

@app.route('/api/<entity>/download', methods=['GET'])
def download_excel(entity):
    return send_file(get_file_path(entity), as_attachment=True)

class ExcelHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.xlsx') and not os.path.basename(event.src_path).startswith('~$'):
            global last_save_time
            if time.time() - last_save_time > 1.5:
                entity = os.path.basename(event.src_path).replace('.xlsx', '')
                socketio.emit('db_updated', {'entity': entity})

if __name__ == '__main__':
    observer = Observer()
    observer.schedule(ExcelHandler(), path='.', recursive=False)
    observer.start()
    socketio.run(app, host='127.0.0.1', port=5000, debug=False)
