"""Commander inteligente: identifica papéis pela Scryfall e consulta ofertas via ReefAPI.

Não infere estoque físico: 'not_listed' significa apenas ausência de anúncio na
consulta, 'unknown' significa que o resultado não permitiu confirmação.
Sem API key, o módulo usa apenas o snapshot importado, com origem identificada.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

STORE_ID = '41369'
STORE_NAME = 'Taberna Geek'
REEF_URL = 'https://api.reefapi.com/ligamagic/v1/card'
SCRYFALL_URL = 'https://api.scryfall.com'
SCRY_HEADERS = {'User-Agent': 'TabernaVault/5.0 (personal Commander planning)', 'Accept': 'application/json'}


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').lower().strip())
                   if not unicodedata.combining(c))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def fetch_json(url, payload=None, headers=None, timeout=35):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = Request(url, data=data, headers={**(headers or {}), **({'Content-Type': 'application/json'} if data else {})},
                  method='POST' if data is not None else 'GET')
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except HTTPError as e:
        # Nunca incluir a chave no erro nem registrar o corpo com tokens.
        if e.code in (401, 403):
            raise ValueError('A fonte recusou a autenticação ou a consulta. Verifique a chave ReefAPI.') from e
        if e.code == 429:
            raise ValueError('Limite de consultas da fonte atingido. Tente novamente mais tarde.') from e
        if e.code == 404 and url.startswith(SCRYFALL_URL):
            raise ValueError('Carta não encontrada na Scryfall.') from e
        raise ValueError(f'Serviço externo indisponível (HTTP {e.code}).') from e
    except (URLError, TimeoutError, OSError) as e:
        raise ValueError('Não foi possível conectar ao serviço externo. Nada foi confirmado.') from e
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError('Serviço externo retornou dados inválidos. Nada foi confirmado.') from e


def get_scry_card(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 180:
        raise ValueError('Informe uma carta válida.')
    return fetch_json(SCRYFALL_URL + '/cards/named?exact=' + quote(name.strip()), headers=SCRY_HEADERS, timeout=20)


def card_text(card):
    return str(card.get('oracle_text') or '\n'.join(str(f.get('oracle_text', '')) for f in card.get('card_faces', [])))


def safe_positive_int(raw):
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
        return value if value >= 0 and str(raw).strip() not in ('', 'None') else None
    except (TypeError, ValueError, OverflowError):
        return None


def cents_from_any(value):
    if isinstance(value, dict):
        value = value.get('amount', value.get('value', value.get('brl')))
    if value is None or isinstance(value, bool):
        return None
    s = re.sub(r'[^\d.,-]', '', str(value))
    if not s:
        return None
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        amt = Decimal(s)
        return int((amt * 100).quantize(Decimal('1'))) if amt >= 0 else None
    except InvalidOperation:
        return None


def pick(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def store_match(offer):
    seller = pick(offer, 'store', 'seller', 'loja')
    ids = [pick(offer, 'store_id', 'seller_id', 'loja_id')]
    names = [pick(offer, 'store_name', 'seller_name', 'loja_nome')]
    if isinstance(seller, dict):
        ids.append(pick(seller, 'id', 'store_id', 'seller_id', 'loja_id'))
        names.append(pick(seller, 'name', 'store_name', 'nome', 'title'))
    elif isinstance(seller, str):
        names.append(seller)
    return any(str(x).strip() == STORE_ID for x in ids if x is not None) or any(
        norm(x) == norm(STORE_NAME) for x in names if x is not None)


def extract_offers(data):
    """Lida com ofertas no nível do card ou dentro das impressões.
    Só aceita coleções explicitamente rotuladas como ofertas; não confunde price guide.
    """
    found, seen, container_present = [], set(), False
    def walk(node, depth=0):
        nonlocal container_present
        if depth > 10:
            return
        if isinstance(node, dict):
            for key, val in node.items():
                if str(key).lower() in ('offers', 'store_offers', 'listings', 'ofertas'):
                    container_present = True
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict) and id(item) not in seen:
                                seen.add(id(item)); found.append(item)
                    elif isinstance(val, dict):
                        for nested in (val.get('items'), val.get('results'), val.get('data')):
                            if isinstance(nested, list):
                                for item in nested:
                                    if isinstance(item, dict) and id(item) not in seen:
                                        seen.add(id(item)); found.append(item)
                elif isinstance(val, (dict, list)):
                    walk(val, depth+1)
        elif isinstance(node, list):
            for val in node[:2500]:
                if isinstance(val, (dict, list)):
                    walk(val, depth+1)
    walk(data)
    return found, container_present


def parse_reef_result(payload, card_name, needed=1):
    """Traduz um retorno da API em verificação robusta por loja.

    'available' requer ID/nome da loja + quantidade e preço explícitos.
    'not_listed' somente se lista de ofertas completa aparente; não é estoque físico zero.
    """
    stamp = now()
    base = {'card': card_name, 'store_id': STORE_ID, 'store': STORE_NAME,
            'checked_at': stamp, 'source': 'ReefAPI / LigaMagic (consulta ao vivo)',
            'source_kind': 'live', 'offers': [], 'price_cents': None, 'quantity': None,
            'needed': needed, 'status': 'unknown', 'message': ''}
    if not isinstance(payload, dict) or payload.get('ok') is not True:
        base['message'] = 'A fonte não confirmou a consulta.'
        return base
    offers, valid = extract_offers(payload.get('data'))
    if not valid:
        base['message'] = 'Formato de ofertas desconhecido. A disponibilidade não pode ser confirmada.'
        return base
    # Se a fonte devolve exatamente o limite solicitado, não presumir que não há páginas adicionais.
    truncated = len(offers) >= 2000
    meta = payload.get('meta') or {}
    data = payload.get('data') or {}
    for parent in (meta, data if isinstance(data, dict) else {}):
        count = safe_positive_int(pick(parent, 'offers_total', 'total_offers', 'total_listings', 'offers_count'))
        if count is not None and count > len(offers):
            truncated = True
    matches = []
    unknown_quantity = False
    for offer in offers:
        if not store_match(offer):
            continue
        raw_price = pick(offer, 'price_brl', 'price', 'price_with_discount_brl', 'unit_price_brl', 'sale_price', 'valor')
        raw_qty = pick(offer, 'quantity', 'stock', 'stock_quantity', 'copies', 'available_quantity', 'qtd', 'qty')
        price = cents_from_any(raw_price)
        qty = safe_positive_int(raw_qty)
        if qty is None:
            unknown_quantity = True
        link = pick(offer, 'url', 'offer_url', 'link', 'href', 'product_url')
        if not isinstance(link, str) or not link.startswith('https://'):
            link = ''
        matches.append({'price_cents': price, 'quantity': qty,
                        'edition': str(pick(offer, 'edition_code', 'edition', 'set_code') or ''),
                        'condition': str(pick(offer, 'condition', 'state_condition', 'conservation') or ''),
                        'language': str(pick(offer, 'language', 'lang', 'idioma') or ''),
                        'url': link})
    base['offers'] = sorted(matches, key=lambda x: x['price_cents'] if x['price_cents'] is not None else 10**12)[:40]
    priced_stock = [o for o in matches if o['quantity'] is not None and o['quantity'] >= needed and o['price_cents'] is not None]
    if priced_stock:
        winner = min(priced_stock, key=lambda o: o['price_cents'])
        base.update(status='available', price_cents=winner['price_cents'], quantity=winner['quantity'],
                    offer=winner, message=f'Oferta identificada na Taberna Geek para {needed} unidade(s).')
    elif any(o['quantity'] is not None and o['quantity'] >= needed for o in matches):
        base['status'] = 'unknown'
        base['message'] = 'A loja informou unidades, mas não foi possível ler um preço confirmado.'
    elif unknown_quantity or truncated:
        base['status'] = 'unknown'
        base['message'] = 'Consulta não conclusiva: quantidade não informada ou ofertas potencialmente incompletas.'
    elif matches:
        base['status'] = 'not_listed'
        base['message'] = f'Nenhuma oferta da loja atende à quantidade de {needed} unidade(s) nesta consulta.'
    else:
        base['status'] = 'not_listed'
        base['message'] = 'Nenhuma oferta da Taberna Geek foi identificada nesta consulta; isso não prova estoque físico zero.'
    return base


def reef_check(card_name, needed=1, api_key=None):
    key = api_key if api_key is not None else os.getenv('REEF_API_KEY', '').strip()
    if not key:
        raise ValueError('Configure REEF_API_KEY nas variáveis de ambiente do Render.')
    payload = {'game': 'magic', 'card': card_name, 'offers_limit': 2000, 'offers_offset': 0}
    response = fetch_json(REEF_URL, payload=payload, headers={'x-api-key': key, 'Accept': 'application/json'}, timeout=75)
    if isinstance(response, dict) and response.get('ok') is False:
        err = response.get('error') or {}
        message = err.get('message') if isinstance(err, dict) else str(err)
        raise ValueError(f'A ReefAPI não concluiu a busca: {str(message)[:160]}')
    return parse_reef_result(response, card_name, needed)


# Papéis amplos; não dependem de uma lista fixa de cartas.
ROLES = [
    ('descarte', r'\bdiscard(?:s|ed|ing)?\b', 'o:discard', 'descarte'),
    ('sacrifício', r'\bsacrific(?:e|es|ed|ing)\b', 'o:sacrifice', 'sacrifício'),
    ('compra de cartas', r'\bdraw (?:a|two|three|x|that many|cards?)\b|\bdraws? cards?\b', 'o:draw', 'compra de cartas'),
    ('aceleração de mana', r'add \{|search your library for (?:a|up to).*?land|add one mana', 'o:mana', 'aceleração de mana'),
    ('remoção', r'destroy target|exile target|deals? \d+ damage to target', 'o:destroy', 'remoção'),
    ('anulação', r'counter target spell', 'o:counter', 'anulação'),
    ('criação de fichas', r'create (?:a|two|three|x|that many).*?token', 'o:token', 'criação de fichas'),
    ('cemitério', r'from your graveyard|from a graveyard|return target.*?graveyard|mill \d+', 'o:graveyard', 'recursão/cemitério'),
    ('tributal', r'\bsliver\b|\bgoblin\b|\bzombie\b|\bel[fv]\b|\bdragon\b', 't:creature', 'tribal'),
    ('piscar', r'exile .*?then return|exile target.*?return it', 'o:exile', 'blink'),
    ('terrenos', r'destroy target land|sacrifice (?:a|\d+) lands?', 'o:land', 'interação com terrenos'),
    ('proteção', r'hexproof|indestructible|protection from|ward \{', 'o:hexproof', 'proteção'),
    ('marcadores', r'\+1/\+1 counter|put a counter on', 'o:counter', 'marcadores'),
    ('artefatos', r'\bartifact\b', 'o:artifact', 'artefatos'),
    ('encantamentos', r'\benchantment\b', 'o:enchantment', 'encantamentos'),
]


def roles_for(card):
    txt = norm(card_text(card))
    result = [name for name, regex, _, _ in ROLES if re.search(regex, txt, re.I)]
    # descartar amplos termos genéricos ao mesmo tempo que tags mais precisas
    return result[:7]


def tribe_for(card):
    typ = str(card.get('type_line') or '').lower()
    if '—' not in typ and '-' not in typ:
        return ''
    tribal = typ.split('—')[-1].strip() if '—' in typ else typ.split(' - ')[-1].strip()
    terms = re.findall(r'\b(?:Sliver|Elf|Goblin|Zombie|Dragon|Merfolk|Vampire|Human|Rat|Cat|Soldier|Faerie|Angel|Demon|Artifact|Spirit)\b', tribal, re.I)
    for term in terms:
        if term.lower() not in ('human', 'artifact'):
            return term.lower()
    return ''


def score_card(target, cmd, candidate):
    wanted = set(roles_for(target))
    offered = set(roles_for(candidate))
    intersection = wanted & offered
    if wanted and not intersection:
        return -100, []
    cmd_roles = set(roles_for(cmd))
    cmd_tribe = tribe_for(cmd)
    target_tribe = tribe_for(target)
    cand_tribe = tribe_for(candidate)
    score = 12 * len(intersection) + 7 * len(cmd_roles & offered)
    if target_tribe and target_tribe == cand_tribe:
        score += 12
    if cmd_tribe and cmd_tribe == cand_tribe:
        score += 10
    score += max(0, 6 - abs(float(candidate.get('cmc') or 0) - float(target.get('cmc') or 0)) * 2)
    if 'Land' in str(target.get('type_line') or '') and 'Land' in str(candidate.get('type_line') or ''):
        score += 8
    if 'Creature' in str(target.get('type_line') or '') and 'Creature' in str(candidate.get('type_line') or ''):
        score += 4
    if not wanted and not (cmd_tribe and cmd_tribe == cand_tribe):
        return -100, []
    reasons = []
    if intersection:
        reasons.append('função em comum: '+', '.join(sorted(intersection)[:3]))
    if cmd_roles & offered:
        reasons.append('contribui para o plano do comandante')
    if target_tribe and cand_tribe == target_tribe:
        reasons.append('mesmo tipo de criatura')
    return score, reasons


def propose_replacements(card_name, commander, limit=6):
    """Sugere alternativas com base no texto Oracle, SEM alegar disponibilidade na loja."""
    target = get_scry_card(card_name)
    cmd = get_scry_card(commander)
    if target.get('legalities', {}).get('commander') != 'legal':
        raise ValueError('A carta original não é legal em Commander segundo a Scryfall.')
    roles = roles_for(target)
    cmd_colors = set(cmd.get('color_identity') or [])
    tribe = tribe_for(target)
    cmd_tribe = tribe_for(cmd)
    search_terms = []
    if tribe and tribe == cmd_tribe:
        search_terms.append('t:'+tribe)
    # Priorizar dois mecanismos de texto em vez de busca geral demais.
    for name, _, query, _ in ROLES:
        if name in roles:
            search_terms.append(query)
        if len(search_terms) >= 3:
            break
    if not search_terms:
        if cmd_tribe:
            search_terms.append('t:'+cmd_tribe)
        else:
            return {'target': target['name'], 'commander': cmd['name'], 'roles': roles,
                    'candidates': [], 'notice': 'Não foi possível identificar uma função comparável automaticamente.'}
    palette = ''.join(c.lower() for c in 'WUBRG' if c in cmd_colors) or 'c'
    seen = {norm(target['name']), norm(cmd['name'])}
    candidates = []
    for term in list(dict.fromkeys(search_terms))[:3]:
        query = f'f:commander id<={palette} {term}'
        endpoint = SCRYFALL_URL + '/cards/search?' + urlencode({'q': query, 'unique':'cards', 'order':'edhrec'})
        try:
            data = fetch_json(endpoint, headers=SCRY_HEADERS, timeout=25)
        except ValueError:
            continue
        for candidate in (data.get('data') or [])[:100]:
            name = candidate.get('name') or ''
            color_identity = set(candidate.get('color_identity') or [])
            if not name or norm(name) in seen or not color_identity <= cmd_colors:
                continue
            if candidate.get('legalities', {}).get('commander') != 'legal':
                continue
            rating, reasons = score_card(target, cmd, candidate)
            if rating < 0:
                continue
            seen.add(norm(name))
            candidates.append({'name': name, 'score': round(rating, 1), 'roles': roles_for(candidate),
                               'reason': '; '.join(reasons) or 'semelhança funcional aproximada',
                               'mana_value': candidate.get('cmc'), 'type_line': candidate.get('type_line') or '',
                               'colors': candidate.get('color_identity') or []})
        time.sleep(0.15)
    candidates.sort(key=lambda c:(-c['score'], abs(float(c.get('mana_value') or 0)-float(target.get('cmc') or 0)), c['name']))
    return {'target': target['name'], 'commander': cmd['name'], 'roles': roles,
            'candidates': candidates[:max(1, min(int(limit), 10))],
            'notice': 'Alternativas heurísticas: confirme função, restrições do deck e disponibilidade na loja.'}
