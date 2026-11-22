#!/usr/bin/env python3
"""Read-only monitor of PUBLIC manufacturing RFQs; never logs in or sends messages."""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'report.json'
STATE = ROOT / 'state.json'
SOURCES = [
    ('metalorders', 'https://metalloobrabotchiki.ru/orders', r'^/orders/\d+$'),
    ('moscow_orders', 'https://msk.metalloobrabotchiki.ru/orders', r'^/orders/\d+$'),
    ('ceh', 'https://xn----7sbocllqn1abl5bl1g.xn--p1ai/zakazy', r'^/zakazy/[a-z0-9]{12,}$'),
    ('tomsk_subcontract', 'https://subcontract.tomsk.ru/uslugi/proizvodstvennye-zakazy/', r'^/uslugi/[^#?]+$'),
    ('energybase_parts', 'https://energybase.ru/tender/catalog/parts-manufacturing', r'^/tender/[0-9a-f-]{20,}$'),
    ('kontur_metalwork', 'https://zakupki.kontur.ru/category/raboty-i-uslugi-po-metalloobrabotke', r'^/(?:IS\d+|\d{5,}(?:_\d+)?)$'),
]
BLOCKS = ('подключите подходящий «тариф»', 'разовый доступ', 'контакты заказчика скрыты', 'войдите как исполнитель', 'купите доступ')
POSITIVE = ('алюмини', 'д16т', 'амг', 'ад31', 'фрезер', 'точен', 'токар', 'электроэроз', 'корпус', 'крышк', 'плит', 'чпу', 'механообработ')
NEGATIVE = ('сварка', 'чугун', 'сталь 20', 'сталь 45', 'пружин', 'гибка', 'отливк', 'литьё')
UA = 'AMAPS-PublicDemandMonitor/1.0 (public pages only; no automation of messages)'

class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text = []
        self.active = None
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.active = {'href': dict(attrs).get('href', ''), 'text': ''}
    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())
            if self.active is not None:
                self.active['text'] += data.strip() + ' '
    def handle_endtag(self, tag):
        if tag == 'a' and self.active is not None:
            self.links.append(self.active)
            self.active = None
    def text_value(self):
        return ' '.join(self.text)

def get(url, timeout=16):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml'})
    with urllib.request.urlopen(req, timeout=timeout) as rsp:
        if rsp.status != 200:
            raise ValueError('HTTP ' + str(rsp.status))
        typ = rsp.headers.get('Content-Type', '')
        if 'html' not in typ:
            raise ValueError('Not HTML: ' + typ[:60])
        return rsp.read(1_500_000).decode('utf-8', errors='replace')

def robots_allowed(url, cache):
    u = urllib.parse.urlparse(url)
    host = u.scheme + '://' + u.netloc
    if host not in cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(host + '/robots.txt')
        try:
            rp.read()
            cache[host] = rp
        except Exception:
            cache[host] = None
    rp = cache[host]
    return rp is not None and rp.can_fetch(UA, url)

def find_links(html, base, pattern):
    page = Page()
    page.feed(html)
    base_host = urllib.parse.urlparse(base).netloc
    found = {}
    for link in page.links:
        u = urllib.parse.urljoin(base, link['href']).split('#')[0]
        p = urllib.parse.urlparse(u)
        if p.netloc != base_host or not re.fullmatch(pattern, p.path, flags=re.I):
            continue
        if u not in found:
            found[u] = link['text'].strip()[:180]
    return list(found.items())

def canonical_url(url):
    p = urllib.parse.urlparse(url)
    if p.hostname == 'metalloobrabotchiki.ru' or (p.hostname or '').endswith('.metalloobrabotchiki.ru'):
        return 'https://metalloobrabotchiki.ru' + p.path
    return p.scheme + '://' + p.netloc + p.path

def rfq_details(url, html, now, title=''):
    page = Page()
    page.feed(html)
    plain = re.sub(r'\s+', ' ', page.text_value())
    if 'Описание заказа' in plain:
        scope = plain.split('Описание заказа', 1)[1].split('Контакты заказчика', 1)[0]
    else:
        scope = plain
    lowered = scope.lower()
    dates = []
    for raw in re.findall(r'\b([0-3]?\d\.[01]?\d\.20\d\d)\b', plain):
        try:
            dates.append(dt.datetime.strptime(raw, '%d.%m.%Y').date())
        except ValueError:
            pass
    months = {'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
              'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12}
    months_pattern = '|'.join(months)
    for m in re.finditer(r'\b([0-3]?\d)\s+(' + months_pattern + r')\s+(20\d{2})\b', plain.lower()):
        try:
            dates.append(dt.date(int(m.group(3)), months[m.group(2)], int(m.group(1))))
        except ValueError:
            pass
    recent = any(now - dt.timedelta(days=15) <= d <= now + dt.timedelta(days=2) for d in dates)
    material = any(t in lowered for t in ('алюмини', 'д16т', 'амг', 'ад31', 'ад0', '6061', '7075'))
    edm = 'электроэроз' in lowered or 'проволочн' in lowered
    relevance = (3 if material else 0) + (3 if edm else 0)
    if material or edm:
        relevance += sum(1 for x in ('фрезер', 'точен', 'токар', 'корпус', 'крышк', 'плит') if x in lowered)
    hidden = any(b in plain.lower() for b in BLOCKS)
    status = ('НЕ ПРИНЯТА — сведения заказчика скрыты; прямой контакт не установлен'
              if hidden else 'СИГНАЛ — нужны первоисточник покупателя, полный комплект ТЗ и подтверждение свободной потребности')
    return {'url': canonical_url(url), 'title': title or scope[:150], 'relevance': relevance,
            'aluminum_or_edm_in_order_description': material or edm,
            'recent_date_detected': recent, 'dates_on_page': sorted(set(str(d) for d in dates))[:10],
            'buyer_contact_hidden': hidden, 'buyer_and_live_need_confirmed': False,
            'status': status, 'extract': scope[:550]}

def run():
    now = dt.datetime.now(dt.timezone.utc).date()
    known = set()
    if STATE.exists():
        try: known = set(json.loads(STATE.read_text(encoding='utf-8')).get('seen_urls', []))
        except Exception: pass
    robots_cache = {}
    indexes_opened = 0
    seen_in_run = set()
    errors, results, checked = [], [], 0
    for key, base, path_pattern in SOURCES:
        try:
            if not robots_allowed(base, robots_cache):
                errors.append({'source': key, 'error': 'robots.txt: prohibited or unavailable'}); continue
            html = get(base)
            indexes_opened += 1
            urls = find_links(html, base, path_pattern)
            urls.sort(key=lambda item: sum(k in item[1].lower() for k in ('алюмини', 'д16т', 'амг', 'корпус', 'крышк', 'детал', 'фрезер', 'электроэроз')), reverse=True)
            for url, title in urls[:5]:
                identity = canonical_url(url)
                if identity in seen_in_run:
                    continue
                seen_in_run.add(identity)
                if not robots_allowed(url, robots_cache): continue
                checked += 1
                try:
                    result = rfq_details(url, get(url), now, title)
                    if result['recent_date_detected'] and result['aluminum_or_edm_in_order_description']:
                        result['new_since_previous_run'] = identity not in known
                        results.append(result)
                        known.add(identity)
                except Exception as exc:
                    errors.append({'source': key, 'url': url, 'error': type(exc).__name__ + ': ' + str(exc)[:130]})
                time.sleep(1.2)
        except Exception as exc:
            errors.append({'source': key, 'error': type(exc).__name__ + ': ' + str(exc)[:130]})
    results.sort(key=lambda x:(x.get('new_since_previous_run', False), x['relevance']), reverse=True)
    report = {'time_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'mode': 'public-read-only',
              'sources_configured': len(SOURCES), 'indexes_opened': indexes_opened, 'detail_pages_checked': checked,
              'new_preliminary_signals': sum(bool(r.get('new_since_previous_run')) for r in results),
              'confirmed_qualified_orders': 0, 'messages_sent': 0,
              'items': results[:20], 'errors': errors}
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    STATE.write_text(json.dumps({'seen_urls': sorted(known)[-2500:]}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'configured': len(SOURCES), 'indexes_opened': indexes_opened, 'details': checked, 'preliminary': report['new_preliminary_signals'],
                      'qualified': 0, 'errors': len(errors)}, ensure_ascii=False))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as s:
            s.write('## АМАПС — поиск открытого прямого спроса\n\n')
            s.write(f"Доступно источников: {indexes_opened} из {len(SOURCES)}. Проверено карточек: {checked}. Новых предварительных сигналов: {report['new_preliminary_signals']}. ")
            s.write('Квалифицированных заказов без ручной проверки: **0**. Сообщений не отправлялось.\n\n')
            s.write('Вложение amaps-public-monitor содержит проверенные URL и причины ошибок.\n')
            for row in results[:8]:
                s.write('- ' + row['title'].replace('\n',' ')[:100] + ': ' + row['url'] + '\n')
    return 0

if __name__ == '__main__':
    sys.exit(run())
