#!/usr/bin/env python3
"""Monitor local, sob demanda, de singles MTG da Taberna Geek.

Funciona sem dependências externas. Consulta estoque APENAS por CSV completo
fornecido pela loja ou por URL HTTPS de feed que a loja autorize.
Não contorna bloqueios nem confunde anúncios históricos com estoque ao vivo.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
import zipfile
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from builder_engine import reef_check, propose_replacements, STORE_ID, STORE_NAME

BASE = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.getenv('TABERNA_DB', str(BASE / 'taberna_inventory.sqlite3')))
ADMIN_TOKEN = os.getenv('TABERNA_ADMIN_TOKEN', '')
MAX_UPLOAD = 12 * 1024 * 1024
MAX_BACKUP_UPLOAD = 24 * 1024 * 1024
MAX_BACKUP_JSON = 96 * 1024 * 1024
SCRYFALL_UA = 'TabernaMTGMonitor/1.0 (personal-use; public-card-data)'
SCRYFALL_HEADERS = {'User-Agent': SCRYFALL_UA, 'Accept': 'application/json'}
LOCK = threading.RLock()


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').strip().lower())
                   if not unicodedata.combining(c))


ALIASES = {
    'name': ['nome', 'nome da carta', 'carta', 'card', 'card name', 'card_name', 'name', 'produto', 'titulo'],
    'set_code': ['set_code', 'set', 'edicao', 'edição', 'colecao', 'coleção', 'expansao', 'expansão'],
    'collector_number': ['collector_number', 'numero', 'número', 'numero da carta', 'number', 'nº'],
    'price': ['preco', 'preço', 'valor', 'price', 'preco r$', 'preco unitario', 'preço unitário'],
    'quantity': ['quantidade', 'qtd', 'qtde', 'quantity', 'estoque', 'stock', 'disponivel', 'disponível'],
    'language': ['idioma', 'lingua', 'língua', 'language', 'lang'],
    'condition': ['condicao', 'condição', 'conservacao', 'conservação', 'condition'],
    'finish': ['acabamento', 'finish', 'foil', 'versao', 'versão'],
    'url': ['link', 'url', 'link da carta', 'product_url'],
    'observed_at': ['observado_em', 'observed_at', 'data_observacao', 'data observacao'],
}
ALIAS_MAP = {normalize(alias): key for key, aliases in ALIASES.items() for alias in aliases}


def price_to_cents(raw):
    text = re.sub(r'[^0-9,.\-]', '', str(raw or '').strip())
    if not text:
        return None
    if ',' in text and '.' in text:
        sep = ',' if text.rfind(',') > text.rfind('.') else '.'
        text = text.replace('.' if sep == ',' else ',', '')
        if sep == ',':
            text = text.replace(',', '.')
    elif ',' in text:
        text = text.replace(',', '.')
    elif text.count('.') > 1:
        text = text.replace('.', '')
    try:
        result = int((Decimal(text) * 100).quantize(Decimal('1')))
    except InvalidOperation:
        raise ValueError(f'Preço inválido: {raw!r}')
    if result < 0:
        raise ValueError('Preço negativo não permitido')
    return result


def quantity_to_int(raw):
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip().replace(' ', '')
    if not re.fullmatch(r'\d+', text):
        raise ValueError(f'Quantidade inválida: {raw!r}')
    return int(text)


def read_inventory_csv(content: bytes | str):
    if isinstance(content, bytes):
        if len(content) > MAX_UPLOAD:
            raise ValueError('Arquivo maior que 12 MB.')
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('cp1252')
    else:
        text = content.lstrip('\ufeff')
        if len(text.encode('utf-8')) > MAX_UPLOAD:
            raise ValueError('Arquivo maior que 12 MB.')
    if not text.strip():
        raise ValueError('CSV vazio.')
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    fields = {h: ALIAS_MAP.get(normalize(h)) for h in headers}
    if 'name' not in fields.values():
        raise ValueError('Coluna obrigatória ausente: nome/carta/name.')
    if not {'price', 'quantity'} & set(fields.values()):
        raise ValueError('Informe ao menos uma coluna: preço ou quantidade.')
    output = []
    for row_number, raw in enumerate(reader, 2):
        if not any(str(v or '').strip() for v in raw.values()):
            continue
        values = {field: str(raw.get(src) or '').strip() for src, field in fields.items() if field}
        name = values.get('name', '').strip()
        if not name:
            raise ValueError(f'Linha {row_number}: nome da carta vazio.')
        try:
            cents = price_to_cents(values.get('price'))
            quantity = quantity_to_int(values.get('quantity'))
        except ValueError as exc:
            raise ValueError(f'Linha {row_number}: {exc}') from exc
        if cents is None and quantity is None:
            raise ValueError(f'Linha {row_number}: informe preço ou quantidade.')
        output.append({
            'name': name, 'set_code': values.get('set_code', ''),
            'collector_number': values.get('collector_number', ''),
            'price_cents': cents, 'quantity': quantity,
            'language': values.get('language', ''), 'condition': values.get('condition', ''),
            'finish': values.get('finish', ''), 'url': values.get('url', ''),
            'observed_at': values.get('observed_at', ''),
        })
    if not output:
        raise ValueError('Não há linhas válidas para importar.')
    if len(output) > 300000:
        raise ValueError('Limite de 300 mil anúncios por CSV.')
    return output


def init_db(connection):
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imported_at TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_label TEXT NOT NULL,
            rows_count INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            name TEXT NOT NULL, normalized_name TEXT NOT NULL,
            set_code TEXT, collector_number TEXT,
            price_cents INTEGER, quantity INTEGER,
            language TEXT, condition TEXT, finish TEXT,
            url TEXT, observed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS offers_snapshot ON offers(snapshot_id);
        CREATE INDEX IF NOT EXISTS offers_name ON offers(snapshot_id, normalized_name);
        CREATE TABLE IF NOT EXISTS card_meta (
            normalized_name TEXT PRIMARY KEY, name TEXT NOT NULL,
            oracle_id TEXT, color_identity TEXT, type_line TEXT,
            oracle_text TEXT, mana_value REAL, legal_commander TEXT,
            keywords TEXT, image_url TEXT, updated_at TEXT NOT NULL
        );
    ''')
    connection.commit()


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def set_feed(self, url):
        if url:
            validate_public_https(url)
        with LOCK, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', ('feed_url', url))

    def feed(self):
        with LOCK:
            row = self.conn.execute('SELECT value FROM settings WHERE key=?', ('feed_url',)).fetchone()
        return row['value'] if row else ''

    def import_rows(self, rows, kind, label, checksum='', note=''):
        if kind not in ('csv_upload', 'csv_feed', 'historical_reference'):
            raise ValueError('Origem inválida.')
        if not rows:
            raise ValueError('Nenhuma carta encontrada.')
        with LOCK, self.conn:
            cursor = self.conn.execute(
                'INSERT INTO snapshots(imported_at,source_kind,source_label,rows_count,checksum,note) VALUES(?,?,?,?,?,?)',
                (iso_now(), kind, label[:300], len(rows), checksum, note[:500]))
            sid = cursor.lastrowid
            self.conn.executemany('''INSERT INTO offers
                (snapshot_id,name,normalized_name,set_code,collector_number,price_cents,quantity,
                 language,condition,finish,url,observed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', [
                (sid, r['name'], normalize(r['name']), r['set_code'], r['collector_number'],
                 r['price_cents'], r['quantity'], r['language'], r['condition'], r['finish'],
                 r['url'], r['observed_at']) for r in rows])
        return sid

    def latest(self):
        with LOCK:
            row = self.conn.execute('SELECT * FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
            return dict(row) if row else None

    def status(self):
        latest = self.latest()
        result = {'latest': latest, 'feed_url': self.feed(), 'feed_configured': bool(self.feed()), 'auth_required': bool(ADMIN_TOKEN), 'stats': None}
        if latest:
            with LOCK:
                r = self.conn.execute('''SELECT COUNT(*) AS offers,
                    COUNT(DISTINCT normalized_name) AS names,
                    SUM(CASE WHEN quantity>0 THEN 1 ELSE 0 END) AS positive,
                    SUM(CASE WHEN quantity IS NULL THEN 1 ELSE 0 END) AS uncertain
                    FROM offers WHERE snapshot_id=?''', (latest['id'],)).fetchone()
            result['stats'] = dict(r)
        return result

    def snapshots(self):
        with LOCK:
            return [dict(r) for r in self.conn.execute('SELECT * FROM snapshots ORDER BY id DESC LIMIT 30')]

    def cards(self, query='', only_stock=False, limit=3000, offset=0, sort='name', set_code=''):
        latest = self.latest()
        if not latest:
            return []
        clause = ' WHERE o.snapshot_id=?'
        params = [latest['id']]
        if query:
            clause += " AND o.normalized_name LIKE ? ESCAPE '\\'"
            params.append('%' + normalize(query).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        if only_stock:
            clause += ' AND o.quantity>0'
        if set_code:
            clause += ' AND LOWER(o.set_code)=?'
            params.append(set_code.strip().lower())
        orders = {
            'name': 'o.normalized_name ASC,o.price_cents ASC',
            'price_asc': 'o.price_cents IS NULL ASC,o.price_cents ASC,o.normalized_name ASC',
            'price_desc': 'o.price_cents IS NULL ASC,o.price_cents DESC,o.normalized_name ASC',
            'quantity': 'o.quantity IS NULL ASC,o.quantity DESC,o.normalized_name ASC',
        }
        sql = '''SELECT o.*, m.name AS canonical_name, m.color_identity, m.type_line,
            m.oracle_text, m.legal_commander, m.keywords, m.image_url, m.mana_value
            FROM offers o LEFT JOIN card_meta m ON m.normalized_name=o.normalized_name''' + clause
        sql += ' ORDER BY ' + orders.get(sort, orders['name']) + ' LIMIT ? OFFSET ?'
        params.extend([max(1, min(int(limit), 300000)), max(0, int(offset))])
        with LOCK:
            return [dict(r) for r in self.conn.execute(sql, params)]

    def count_cards(self, query='', only_stock=False, set_code=''):
        latest = self.latest()
        if not latest:
            return 0
        clause = ' WHERE o.snapshot_id=?'
        params = [latest['id']]
        if query:
            clause += " AND o.normalized_name LIKE ? ESCAPE '\\'"
            params.append('%' + normalize(query).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        if only_stock:
            clause += ' AND o.quantity>0'
        if set_code:
            clause += ' AND LOWER(o.set_code)=?'
            params.append(set_code.strip().lower())
        with LOCK:
            return self.conn.execute('SELECT COUNT(*) FROM offers o' + clause, params).fetchone()[0]

    def changes(self):
        with LOCK:
            ids = [r['id'] for r in self.conn.execute('SELECT id FROM snapshots ORDER BY id DESC LIMIT 2')]
        if len(ids) < 2:
            return []
        def variants(sid):
            with LOCK:
                rows = [dict(r) for r in self.conn.execute('SELECT * FROM offers WHERE snapshot_id=?', (sid,))]
            buckets = {}
            for r in rows:
                key = tuple(normalize(r[f]) for f in ['name', 'set_code', 'collector_number', 'language', 'condition', 'finish'])
                obj = buckets.setdefault(key, {'name': r['name'], 'set_code': r['set_code'], 'price_cents': None,
                                                'quantity': 0, 'quantity_unknown': False})
                if r['price_cents'] is not None:
                    obj['price_cents'] = min(obj['price_cents'], r['price_cents']) if obj['price_cents'] is not None else r['price_cents']
                if r['quantity'] is None:
                    obj['quantity_unknown'] = True
                else:
                    obj['quantity'] += r['quantity']
            for item in buckets.values():
                if item.pop('quantity_unknown'):
                    item['quantity'] = None
            return buckets
        current, old = variants(ids[0]), variants(ids[1])
        changes = []
        for key in set(current) | set(old):
            a, b = old.get(key), current.get(key)
            if a == b:
                continue
            category = 'entrada' if b and not a else 'saida' if a and not b else 'alteracao'
            changes.append({'category': category, 'name': (b or a)['name'], 'set_code': (b or a)['set_code'],
                            'before': a, 'after': b})
        return sorted(changes, key=lambda x: (x['category'], normalize(x['name'])))[:10000]

    def uncached_names(self, limit=225):
        latest = self.latest()
        if not latest:
            return []
        with LOCK:
            rows = self.conn.execute('''SELECT MIN(o.name) name, o.normalized_name FROM offers o
                LEFT JOIN card_meta m ON m.normalized_name=o.normalized_name
                WHERE o.snapshot_id=? AND (m.normalized_name IS NULL OR m.updated_at<?)
                GROUP BY o.normalized_name ORDER BY o.normalized_name LIMIT ?''',
                (latest['id'], (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec='seconds'), limit)).fetchall()
        return [dict(r) for r in rows]

    def save_meta(self, data):
        with LOCK, self.conn:
            self.conn.executemany('''INSERT INTO card_meta
                (normalized_name,name,oracle_id,color_identity,type_line,oracle_text,mana_value,
                 legal_commander,keywords,image_url,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(normalized_name) DO UPDATE SET
                name=excluded.name,oracle_id=excluded.oracle_id,color_identity=excluded.color_identity,
                type_line=excluded.type_line,oracle_text=excluded.oracle_text,
                mana_value=excluded.mana_value,legal_commander=excluded.legal_commander,
                keywords=excluded.keywords,image_url=excluded.image_url,updated_at=excluded.updated_at''', data)


    def export_backup(self):
        """Salva snapshots, ofertas, metadados e configuração do feed em ZIP.

        Não inclui segredo de administrador ou arquivos do servidor.
        """
        columns = {
            'settings': ('key', 'value'),
            'snapshots': ('id', 'imported_at', 'source_kind', 'source_label', 'rows_count', 'checksum', 'note'),
            'offers': ('id', 'snapshot_id', 'name', 'normalized_name', 'set_code', 'collector_number',
                       'price_cents', 'quantity', 'language', 'condition', 'finish', 'url', 'observed_at'),
            'card_meta': ('normalized_name', 'name', 'oracle_id', 'color_identity', 'type_line',
                          'oracle_text', 'mana_value', 'legal_commander', 'keywords', 'image_url', 'updated_at'),
        }
        with LOCK:
            tables = {table: [dict(r) for r in self.conn.execute(
                'SELECT ' + ','.join(fields) + ' FROM ' + table)] for table, fields in columns.items()}
        # Não incluir outros ajustes/segredos adicionados futuramente.
        tables['settings'] = [r for r in tables['settings'] if r['key'] == 'feed_url']
        data = json.dumps({'format': 'taberna-vault-backup-v1', 'created_at': iso_now(),
                           'tables': tables}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(data) > MAX_BACKUP_JSON:
            raise ValueError('Histórico muito grande para backup único (limite de 96 MB).')
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr('backup.json', data)
        return memory.getvalue()

    def restore_backup(self, content):
        """Restauração transacional a partir de backup que o próprio app gerou."""
        if len(content) > MAX_BACKUP_UPLOAD:
            raise ValueError('Backup excede 24 MB.')
        try:
            with zipfile.ZipFile(io.BytesIO(content), 'r') as z:
                if z.namelist() != ['backup.json']:
                    raise ValueError('Arquivo não é um backup Taberna Vault válido.')
                entry = z.getinfo('backup.json')
                if entry.file_size > MAX_BACKUP_JSON:
                    raise ValueError('Conteúdo descompactado muito grande.')
                with z.open(entry) as f:
                    raw = f.read(MAX_BACKUP_JSON + 1)
                if len(raw) > MAX_BACKUP_JSON:
                    raise ValueError('Backup muito grande.')
        except (zipfile.BadZipFile, OSError) as ex:
            raise ValueError('Backup ZIP inválido.') from ex
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as ex:
            raise ValueError('JSON de backup inválido.') from ex
        if not isinstance(payload, dict) or payload.get('format') != 'taberna-vault-backup-v1':
            raise ValueError('Formato de backup desconhecido.')
        tables = payload.get('tables')
        expected = {
            'settings': ('key', 'value'),
            'snapshots': ('id', 'imported_at', 'source_kind', 'source_label', 'rows_count', 'checksum', 'note'),
            'offers': ('id', 'snapshot_id', 'name', 'normalized_name', 'set_code', 'collector_number',
                       'price_cents', 'quantity', 'language', 'condition', 'finish', 'url', 'observed_at'),
            'card_meta': ('normalized_name', 'name', 'oracle_id', 'color_identity', 'type_line',
                          'oracle_text', 'mana_value', 'legal_commander', 'keywords', 'image_url', 'updated_at'),
        }
        caps = {'settings': 1, 'snapshots': 1000, 'offers': 500000, 'card_meta': 250000}
        if not isinstance(tables, dict) or set(tables) != set(expected):
            raise ValueError('Tabelas de backup incompatíveis.')
        for table, fields in expected.items():
            rows = tables[table]
            if not isinstance(rows, list) or len(rows) > caps[table]:
                raise ValueError('Quantidade de registros inválida no backup.')
            if any(not isinstance(row, dict) or set(row) != set(fields) for row in rows):
                raise ValueError('Backup possui campos inesperados.')
        if any(r['key'] != 'feed_url' or not isinstance(r['value'], str) for r in tables['settings']):
            raise ValueError('Configuração de fonte inválida.')
        snapshot_ids = set()
        for r in tables['snapshots']:
            if type(r['id']) is not int or r['id'] <= 0 or r['id'] in snapshot_ids:
                raise ValueError('Identificador de versão inválido.')
            if r['source_kind'] not in ('csv_upload', 'csv_feed', 'historical_reference'):
                raise ValueError('Origem de versão inválida.')
            snapshot_ids.add(r['id'])
        for r in tables['offers']:
            if (type(r['id']) is not int or r['id'] <= 0 or r['snapshot_id'] not in snapshot_ids
                    or not isinstance(r['name'], str) or not r['name']):
                raise ValueError('Oferta inválida no backup.')
            if r['price_cents'] is not None and (type(r['price_cents']) is not int or r['price_cents'] < 0):
                raise ValueError('Preço inválido no backup.')
            if r['quantity'] is not None and (type(r['quantity']) is not int or r['quantity'] < 0):
                raise ValueError('Quantidade inválida no backup.')
        # Apenas após validar restauramos; falha no SQL executa rollback.
        with LOCK, self.conn:
            for table in ('offers', 'snapshots', 'card_meta', 'settings'):
                self.conn.execute('DELETE FROM ' + table)
            for table, fields in expected.items():
                if not tables[table]:
                    continue
                placeholders = ','.join('?' for _ in fields)
                query = 'INSERT INTO ' + table + ' (' + ','.join(fields) + ') VALUES (' + placeholders + ')'
                self.conn.executemany(query, ([r[f] for f in fields] for r in tables[table]))
        return {'snapshots': len(tables['snapshots']), 'offers': len(tables['offers']),
                'metadata': len(tables['card_meta'])}


def validate_public_https(raw_url):
    url = urlsplit(raw_url.strip())
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.port not in (None, 443):
        raise ValueError('Use uma URL HTTPS pública de um arquivo CSV, sem credenciais.')
    hostname = url.hostname or ''
    if hostname.lower() in ('localhost', 'localhost.localdomain') or hostname.lower().endswith(('.local', '.internal')):
        raise ValueError('Endereços locais não são permitidos.')
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        for record in addresses:
            addr = ipaddress.ip_address(record[4][0])
            if not addr.is_global:
                raise ValueError('O feed precisa estar em um endereço público.')
    except socket.gaierror as exc:
        raise ValueError('Não foi possível resolver o domínio da URL.') from exc
    return raw_url.strip()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirecionamentos do feed não são seguidos por segurança.')


def download_feed(url):
    validate_public_https(url)
    opener = build_opener(NoRedirect())
    request = Request(url, headers={'User-Agent': SCRYFALL_UA, 'Accept': 'text/csv,text/plain'})
    with opener.open(request, timeout=20) as response:
        raw = response.read(MAX_UPLOAD + 1)
        if len(raw) > MAX_UPLOAD:
            raise ValueError('O feed excede 12 MB.')
        return raw


def scryfall_enrich(store, limit=225):
    import urllib.request
    names = store.uncached_names(min(max(int(limit), 1), 450))
    if not names:
        return {'received': 0, 'remaining': 0, 'message': 'Todas as cartas desta importação já foram consultadas.'}
    collected, unmatched = [], []
    for offset in range(0, len(names), 75):
        group = names[offset:offset + 75]
        raw = json.dumps({'identifiers': [{'name': r['name']} for r in group]}).encode('utf-8')
        req = Request('https://api.scryfall.com/cards/collection', data=raw, method='POST',
                      headers={**SCRYFALL_HEADERS, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                response = json.load(resp)
        except Exception as exc:
            raise ValueError(f'Scryfall indisponível: {exc}. Dados do estoque preservados.') from exc
        for card in response.get('data', []):
            faces = card.get('card_faces') or []
            oracle = card.get('oracle_text') or '\n'.join(f.get('oracle_text', '') for f in faces)
            image = (card.get('image_uris') or {}).get('normal') or (faces[0].get('image_uris') or {}).get('normal') if faces else (card.get('image_uris') or {}).get('normal')
            collected.append((normalize(card['name']), card['name'], card.get('oracle_id', ''),
                              json.dumps(card.get('color_identity', [])), card.get('type_line', ''), oracle,
                              card.get('cmc'), card.get('legalities', {}).get('commander', 'unknown'),
                              json.dumps(card.get('keywords', [])), image or '', iso_now()))
        unmatched.extend(response.get('not_found', []))
        time.sleep(0.65)  # Respeita limite conservador de requests na API.
    store.save_meta(collected)
    return {'received': len(collected), 'not_found': len(unmatched),
            'remaining': len(store.uncached_names(1)),
            'message': 'Metadados Scryfall atualizados. Não são dados de estoque.'}


def compatible(card, identity):
    """True/False/None: não confundir carta não analisada com incompatível."""
    if card.get('color_identity') is None or card.get('legal_commander') in (None, 'unknown'):
        return None
    try:
        colors = set(json.loads(card['color_identity']))
    except (TypeError, ValueError):
        return None
    return colors <= set(identity) and card['legal_commander'] == 'legal'


HTML = (BASE / 'index.html')
STATIC_FILES = {'/': (HTML, 'text/html; charset=utf-8'), '/index.html': (HTML, 'text/html; charset=utf-8'), '/styles.css': (BASE / 'styles.css', 'text/css; charset=utf-8'), '/app.js': (BASE / 'app.js', 'text/javascript; charset=utf-8'), '/favicon.svg': (BASE / 'favicon.svg', 'image/svg+xml')} 


class Handler(BaseHTTPRequestHandler):
    server_version = 'TabernaMTG/5.0'

    @property
    def store(self):
        return self.server.store

    def send_json(self, status, result):
        raw = json.dumps(result, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, name, mime):
        data = name.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def require_auth(self):
        if ADMIN_TOKEN and not secrets.compare_digest(self.headers.get('X-Admin-Token', ''), ADMIN_TOKEN):
            self.send_json(401, {'error': 'Acesso de edição protegido. Configure a chave de administrador em Conexão.'})
            return False
        return True

    def get_body(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('Tamanho da requisição inválido.')
        if size <= 0 or size > MAX_UPLOAD * 3:
            raise ValueError('Requisição vazia ou grande demais.')
        return json.loads(self.rfile.read(size))

    def do_GET(self):
        route = urlsplit(self.path)
        q = parse_qs(route.query)
        try:
            if route.path in STATIC_FILES:
                path, mime = STATIC_FILES[route.path]
                return self.send_file(path, mime)
            if route.path == '/health':
                return self.send_json(200, {'ok': True})
            if route.path == '/api/builder/status':
                snapshot = self.store.latest()
                return self.send_json(200, {
                    'live_enabled': bool(os.getenv('REEF_API_KEY','').strip()),
                    'snapshot_available': bool(snapshot),
                    'snapshot_kind': snapshot.get('source_kind') if snapshot else None,
                    'snapshot_date': snapshot.get('imported_at') if snapshot else None,
                    'store_id': STORE_ID, 'store_name': STORE_NAME,
                    'cost_per_live_check_credits': 4,
                    'message': 'A ReefAPI exige chave privada no servidor. Sem ela, somente o CSV importado pode ser consultado.'
                })
            if route.path == '/api/status':
                return self.send_json(200, self.store.status())
            if route.path == '/api/snapshots':
                return self.send_json(200, {'items': self.store.snapshots()})
            if route.path == '/api/cards':
                query = q.get('q', [''])[0]
                stocked = q.get('stock', ['0'])[0] == '1'
                edition = q.get('set', [''])[0]
                return self.send_json(200, {'items': self.store.cards(
                    query, stocked, int(q.get('limit', ['60'])[0]),
                    int(q.get('offset', ['0'])[0]), q.get('sort', ['name'])[0], edition),
                    'total': self.store.count_cards(query, stocked, edition)})
            if route.path == '/api/changes':
                return self.send_json(200, {'items': self.store.changes()})
            if route.path == '/api/backup.zip':
                if not self.require_auth():
                    return
                blob = self.store.export_backup()
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Disposition', 'attachment; filename="taberna-vault-backup.zip"')
                self.send_header('Content-Length', str(len(blob)))
                self.end_headers()
                return self.wfile.write(blob)
            if route.path == '/api/export.json':
                return self.send_json(200, {'notice': 'Fotografia do último CSV; não garante disponibilidade em tempo real.',
                                            'snapshot': self.store.latest(), 'offers': self.store.cards(limit=300000)})
            if route.path == '/api/export.csv':
                rows = self.store.cards(limit=300000)
                output = io.StringIO()
                writer = csv.writer(output, delimiter=';')
                keys = ['nome', 'edicao', 'numero', 'preco', 'quantidade', 'idioma', 'condicao', 'acabamento', 'url', 'observado_em']
                writer.writerow(keys)
                for r in rows:
                    cents = r.get('price_cents')
                    price = f'{cents / 100:.2f}'.replace('.', ',') if cents is not None else ''
                    writer.writerow([r['name'], r['set_code'], r['collector_number'], price,
                                     r['quantity'] if r['quantity'] is not None else '',
                                     r['language'], r['condition'], r['finish'], r['url'], r['observed_at']])
                data = ('\ufeff' + output.getvalue()).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="taberna_ultimo_snapshot.csv"')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            return self.send_json(404, {'error': 'Rota não encontrada.'})
        except Exception as exc:
            return self.send_json(400, {'error': str(exc)})

    def do_POST(self):
        route = urlsplit(self.path).path
        if not self.require_auth():
            return
        try:
            if route == '/api/restore':
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise ValueError('Tamanho inválido.')
                if length <= 0 or length > MAX_BACKUP_UPLOAD:
                    raise ValueError('Arquivo de backup inválido ou grande demais (24 MB).')
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/zip':
                    raise ValueError('Envie um arquivo ZIP de backup.')
                restored = self.store.restore_backup(self.rfile.read(length))
                return self.send_json(200, {'ok': True, **restored})
            payload = self.get_body()
            if route == '/api/builder/candidates':
                desired = str(payload.get('card', '')).strip()
                commander = str(payload.get('commander', '')).strip()
                if not desired or not commander or len(desired)>180 or len(commander)>180:
                    raise ValueError('Informe comandante e carta (até 180 caracteres).')
                return self.send_json(200, propose_replacements(desired, commander, limit=7))
            if route == '/api/builder/availability':
                name = str(payload.get('card','')).strip()
                if not name or len(name)>180:
                    raise ValueError('Informe o nome de uma carta (até 180 caracteres).')
                try:
                    needed = int(payload.get('quantity',1))
                except (TypeError,ValueError):
                    raise ValueError('Quantidade inválida.')
                if not 1 <= needed <= 100:
                    raise ValueError('Quantidade deve ser de 1 a 100.')
                if os.getenv('REEF_API_KEY','').strip():
                    return self.send_json(200, reef_check(name, needed))
                snap = self.store.latest()
                base = {'card':name, 'store':STORE_NAME,'store_id':STORE_ID,
                        'needed':needed,'status':'unknown','source_kind':'snapshot',
                        'checked_at':snap.get('imported_at') if snap else None,
                        'price_cents':None, 'quantity':None, 'offers':[]}
                if not snap:
                    base.update(source='Não configurada',source_kind='unconfigured',
                                message='Sem integração ao vivo e sem CSV importado. Configure REEF_API_KEY no Render.')
                    return self.send_json(200,base)
                base['source'] = 'Último CSV importado: '+snap.get('source_label','')
                found = [r for r in self.store.cards(query=name,limit=1000) if normalize(r['name'])==normalize(name)]
                matches = [r for r in found if r.get('quantity') is not None and r['quantity'] >= needed and r.get('price_cents') is not None]
                if snap['source_kind']=='historical_reference':
                    base['message']='A fonte mais recente é histórica. Não permite confirmar disponibilidade.'
                elif matches:
                    winner = min(matches, key=lambda x:x['price_cents'])
                    base.update(status='snapshot_available',price_cents=winner['price_cents'],
                                quantity=winner['quantity'], offer={'url':winner.get('url',''),'price_cents':winner['price_cents'],
                                'quantity':winner['quantity'], 'edition':winner.get('set_code','')},
                                message='Carta listada no CSV importado; não foi consultada ao vivo.')
                else:
                    base['status']='snapshot_not_found'
                    base['message']='Sem oferta suficiente no último CSV. Isso não comprova falta de estoque atual.'
                return self.send_json(200,base)
            if route == '/api/feed':
                url = str(payload.get('url', '')).strip()
                self.store.set_feed(url)
                return self.send_json(200, {'ok': True, 'feed_url': url})
            if route == '/api/upload':
                text = payload.get('csv', '')
                if not isinstance(text, str):
                    raise ValueError('CSV deve ser texto.')
                content = text.encode('utf-8')
                rows = read_inventory_csv(content)
                kind = 'historical_reference' if payload.get('historical') else 'csv_upload'
                sid = self.store.import_rows(rows, kind, str(payload.get('filename', 'arquivo.csv')),
                                             hashlib.sha256(content).hexdigest())
                return self.send_json(200, {'ok': True, 'snapshot_id': sid, 'rows': len(rows)})
            if route == '/api/sync':
                feed = self.store.feed()
                if not feed:
                    raise ValueError('Nenhum feed CSV autorizado configurado. Importe um CSV ou configure uma URL pública da loja.')
                content = download_feed(feed)
                rows = read_inventory_csv(content)
                sid = self.store.import_rows(rows, 'csv_feed', feed,
                                             hashlib.sha256(content).hexdigest())
                return self.send_json(200, {'ok': True, 'snapshot_id': sid, 'rows': len(rows)})
            if route == '/api/enrich':
                return self.send_json(200, scryfall_enrich(self.store, payload.get('limit', 225)))
            if route == '/api/commander':
                name = str(payload.get('name', '')).strip()
                if not name or len(name) > 200:
                    raise ValueError('Informe o nome do comandante.')
                req = Request('https://api.scryfall.com/cards/named?exact=' +
                              __import__('urllib.parse', fromlist=['quote']).quote(name), headers=SCRYFALL_HEADERS)
                import urllib.request
                with urllib.request.urlopen(req, timeout=20) as response:
                    card = json.load(response)
                colors = card.get('color_identity', [])
                rows = self.store.cards(limit=20000)
                good, pending = [], 0
                for row in rows:
                    if not row.get('quantity') or row['quantity'] < 1:
                        continue
                    allowed = compatible(row, colors)
                    if allowed is True:
                        good.append(row)
                    elif allowed is None:
                        pending += 1
                return self.send_json(200, {
                    'commander': {'name': card.get('name'), 'colors': colors,
                                  'legal_commander': card.get('legalities', {}).get('commander'),
                                  'type_line': card.get('type_line'), 'oracle_text': card.get('oracle_text'),
                                  'image_url': (card.get('image_uris') or {}).get('normal', '')},
                    'compatible': good[:1000], 'unverified': pending,
                    'note': 'Triagem de identidade de cor e legalidade; não calcula sinergia nem confirma o estoque ao vivo.'})
            return self.send_json(404, {'error': 'Rota não encontrada.'})
        except Exception as exc:
            self.send_json(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description='Painel local MTG Taberna Geek')
    parser.add_argument('--host', default=os.getenv('HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '8765')))
    parser.add_argument('--db', default=str(DEFAULT_DB))
    parser.add_argument('--import-csv', help='Importa CSV completo via terminal e encerra.')
    parser.add_argument('--historical', action='store_true', help='Marca importação do terminal como referência histórica.')
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    if args.import_csv:
        content = Path(args.import_csv).read_bytes()
        rows = read_inventory_csv(content)
        kind = 'historical_reference' if args.historical else 'csv_upload'
        sid = store.import_rows(rows, kind, str(Path(args.import_csv).name), hashlib.sha256(content).hexdigest())
        print(f'Importado snapshot {sid}: {len(rows)} ofertas. Origem: {kind}.')
        return
    if not 1024 <= args.port <= 65535:
        parser.error('Escolha uma porta entre 1024 e 65535.')
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not ADMIN_TOKEN:
        parser.error('Ao publicar o app, defina TABERNA_ADMIN_TOKEN no ambiente para proteger as operações.')
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.store = store
    print(f'Taberna MTG Webapp em http://{args.host}:{args.port} (Ctrl+C para encerrar)')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nEncerrando.')
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
