#!/usr/bin/env python3
"""Read-only monitor of PUBLIC manufacturing RFQs; never logs in or sends messages."""
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo
from collections import Counter
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
BLOCKS = ('подключите подходящий «тариф»', 'разовый доступ', 'контакты заказчика скрыты', 'войдите как исполнитель', 'купите доступ', 'авторизируйтесь как исполнитель', 'чертежи скрыты')
POSITIVE = ('алюмини', 'д16т', 'амг', 'ад31', 'фрезер', 'точен', 'токар', 'электроэроз', 'корпус', 'крышк', 'плит', 'чпу', 'механообработ')
NEGATIVE = ('сварка', 'чугун', 'сталь 20', 'сталь 45', 'пружин', 'гибка', 'отливк', 'литьё')
UA = 'AMAPS-PublicDemandMonitor/1.0 (public pages only; no automation of messages)'

class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text = []
        self.active = None
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'nav', 'footer', 'header'):
            self.skip += 1
        if self.skip:
            return
        if tag == 'a':
            self.active = {'href': dict(attrs).get('href', ''), 'text': ''}
    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())
            if self.active is not None:
                self.active['text'] += data.strip() + ' '
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'nav', 'footer', 'header'):
            self.skip = max(0, self.skip - 1)
        if self.skip:
            return
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
        if canonical_url(rsp.geturl()) != canonical_url(url):
            raise ValueError('Redirect to a different resource')
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
    # Never qualify a listing or a page combining several RFQs as a single order.
    order_ids = set(re.findall(r'заказ\s*№\s*(\d+)', plain, flags=re.I))
    expected = re.fullmatch(r'/orders/(\d+)', urllib.parse.urlparse(url).path)
    is_metalorders = (urllib.parse.urlparse(canonical_url(url)).hostname == 'metalloobrabotchiki.ru')
    ambiguous_page = len(order_ids) > 1 or (is_metalorders and expected is not None and order_ids != {expected.group(1)})
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
    # Only explicitly labelled deadlines count; unrelated dates are not evidence.
    deadline_dates = []
    for m in re.finditer(r'(?:окончание(?:\s+при[её]ма)?|при[её]м\s+(?:заявок|предложений)\s+до|срок\s+подачи|подать\s+до|дата\s+окончания|сбор\s+кп\s+по|сбор\s+кп\s+до|сбор\s+предложений\s+до)[^0-9]{0,45}([0-3]?\d\.[01]?\d\.20\d\d)', plain.lower()):
        try:
            deadline_dates.append(dt.datetime.strptime(m.group(1), '%d.%m.%Y').date())
        except ValueError:
            pass
    ambiguous_page = ambiguous_page or len(set(deadline_dates)) > 1
    recent = bool(deadline_dates) and not ambiguous_page and min(deadline_dates) > now  # дата без времени: сегодня требует ручной проверки
    closed = bool(re.search(r'при[её]м (?:заявок|предложений) заверш[её]н|закупка (?:завершена|отменена)|заказ закрыт|\bзакрыт\b|договор заключ[её]н', plain.lower()))
    recent = recent and not closed
    material = any(t in lowered for t in ('алюмини', 'д16т', 'амг', 'ад31', 'ад0', '6061', '7075'))
    edm = 'электроэроз' in lowered or 'проволочн' in lowered
    relevance = (3 if material else 0) + (3 if edm else 0)
    if material or edm:
        relevance += sum(1 for x in ('фрезер', 'точен', 'токар', 'корпус', 'крышк', 'плит') if x in lowered)
    hidden = any(b in plain.lower() for b in BLOCKS)
    supplier_ad = bool(re.search(r'предлагаем услуги|оказываем услуги|принимаем заказы|ищем заказчиков|наше производство', lowered))
    sensitive_or_unknown = bool(re.search(r'военн|боеприпас|вооружен|оборонн|ракетн', lowered))
    attachments = []
    for link in page.links:
        target = urllib.parse.urljoin(url, link['href'])
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme == 'https' and parsed.netloc == urllib.parse.urlparse(url).netloc and not parsed.query and not parsed.fragment and re.search(r'\.(?:pdf|zip|rar|7z|step|stp|dwg|dxf|xlsx|docx)$', parsed.path, re.I):
            attachments.append(target)
    negative = any(b in lowered for b in NEGATIVE)
    demand = bool(re.search(r'изготовлен|требуется|закупк|запрос\s+кп|ищем\s+подрядчика|заказ\s+на', lowered))
    scoped = 'Описание заказа' in plain
    reasons = []
    if ambiguous_page: reasons.append('ambiguous_order_page')
    if not scoped: reasons.append('order_scope_unrecognized')
    if not deadline_dates: reasons.append('deadline_unrecognized')
    elif min(deadline_dates) < now: reasons.append('deadline_expired')
    if deadline_dates and min(deadline_dates) == now: reasons.append('deadline_today_time_unverified')
    if closed: reasons.append('closed')
    if not (material or edm): reasons.append('material_or_edm_missing')
    if not demand: reasons.append('demand_missing')
    if hidden: reasons.append('contact_hidden')
    if negative: reasons.append('negative_process_or_material')
    if supplier_ad: reasons.append('supplier_ad')
    if sensitive_or_unknown: reasons.append('restricted_end_use')
    # Conservative preliminary filter, never automatic qualification.
    eligible = recent and (material or edm) and demand and not hidden and not negative and scoped and not ambiguous_page and not supplier_ad and not sensitive_or_unknown
    status = ('НЕ ПРИНЯТА — сведения заказчика скрыты; прямой контакт не установлен'
              if hidden else 'СИГНАЛ — нужны первоисточник покупателя, полный комплект ТЗ и подтверждение свободной потребности')
    return {'url': canonical_url(url), 'title': title or scope[:150], 'relevance': relevance,
            'aluminum_or_edm_in_order_description': material or edm,
            'recent_date_detected': recent, 'dates_on_page': sorted(set(str(d) for d in dates))[:10],
            'buyer_contact_hidden': hidden, 'buyer_and_live_need_confirmed': False,
            'eligible_preliminary': eligible, 'rejection_reasons': reasons, 'deadline_dates': [str(d) for d in deadline_dates],
            'negative_material_or_process': negative, 'explicit_order_scope': scoped,
            'status': status,
            'document_links': sorted(set(attachments))[:20], 'documents_downloaded': 0,
            'evidence_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
            'observed_utc': dt.datetime.now(dt.timezone.utc).isoformat()}

def run():
    now = dt.datetime.now(ZoneInfo('Europe/Moscow')).date()
    known = set()
    history = {}
    if STATE.exists():
        try:
            saved = json.loads(STATE.read_text(encoding='utf-8'))
            known = set(saved.get('seen_urls', []))
            history = saved.get('checked_urls', {}) if saved.get('parser_revision') == 3 else {}
            if not isinstance(history, dict):
                history = {}
        except (OSError, ValueError, TypeError):
            pass
    scan_epoch = time.time()
    rejection_counts = Counter()
    source_stats = {}
    skipped_recent = 0
    robots_cache = {}
    indexes_opened = 0
    seen_in_run = set()
    run_deadline = time.monotonic() + 480
    errors, results, checked = [], [], 0
    review_queue = []
    successful_details = 0
    rejected = 0
    for key, base, path_pattern in SOURCES:
        stats = source_stats[key] = {'index_opened': False, 'links_found': 0, 'due_links': 0, 'checked': 0, 'rejected': 0, 'errors': 0, 'skipped_recent': 0, 'new_publications': None, 'coverage': 'Н/Д'}
        try:
            if not robots_allowed(base, robots_cache):
                stats['errors'] += 1
                errors.append({'source': key, 'error': 'robots.txt: prohibited or unavailable'}); continue
            html = get(base)
            indexes_opened += 1
            stats['index_opened'] = True
            urls = find_links(html, base, path_pattern)
            stats['links_found'] = len(urls)
            stats['index_evidence_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
            stats['index_observed_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
            if not urls:
                stats['errors'] += 1
                errors.append({'source': key, 'error': 'No recognized order links; coverage unverified'})
                continue
            due = []
            for url, title in urls:
                identity = canonical_url(url)
                previous = history.get(identity, {})
                if identity in seen_in_run:
                    continue
                if previous.get('title') == title and previous.get('evidence_sha256') and scan_epoch - previous.get('checked_at', 0) < (7200 if previous.get('eligible') else 86400):
                    stats['skipped_recent'] += 1
                    skipped_recent += 1
                    continue
                due.append((url, title))
            stats['due_links'] = len(due)
            # Unseen and changed listings before old rechecks; rotate through the entire index.
            due.sort(key=lambda item: (
                canonical_url(item[0]) in history,
                -sum(k in item[1].lower() for k in ('алюмини', 'д16т', 'амг', 'корпус', 'крышк', 'детал', 'фрезер', 'электроэроз')),
                history.get(canonical_url(item[0]), {}).get('checked_at', 0)))
            for url, title in due[:16]:
                if time.monotonic() >= run_deadline:
                    stats['errors'] += 1
                    errors.append({'source': key, 'error': 'Исчерпан лимит времени цикла'})
                    break
                identity = canonical_url(url)
                if identity in seen_in_run:
                    continue
                seen_in_run.add(identity)
                if not robots_allowed(url, robots_cache):
                    stats['errors'] += 1
                    errors.append({'source': key, 'url': url, 'error': 'robots.txt: detail prohibited or unavailable'})
                    continue
                checked += 1
                stats['checked'] += 1
                try:
                    result = rfq_details(url, get(url), now, title)
                    successful_details += 1
                    history[identity] = {'checked_at': scan_epoch, 'title': title, 'eligible': result['eligible_preliminary'], 'evidence_sha256': result['evidence_sha256'], 'deadline_dates': result['deadline_dates']}
                    if result['eligible_preliminary']:
                        result['new_since_previous_run'] = identity not in known
                        results.append(result)
                        known.add(identity)
                    else:
                        # Unrecognized structure is not evidence of irrelevance.
                        gaps = set(result['rejection_reasons'])
                        hard = {'closed', 'deadline_expired', 'restricted_end_use', 'supplier_ad'}
                        if gaps.intersection({'order_scope_unrecognized', 'deadline_unrecognized', 'deadline_today_time_unverified', 'ambiguous_order_page'}) and not gaps.intersection(hard):
                            if result['aluminum_or_edm_in_order_description'] or any(t in title.lower() for t in POSITIVE):
                                review_queue.append({'url': identity, 'reasons': sorted(gaps), 'evidence_sha256': result['evidence_sha256'], 'status': 'НЕ ПРИНЯТА — требуется проверка первоисточника'})
                        rejected += 1
                        stats['rejected'] += 1
                        rejection_counts.update(result['rejection_reasons'])
                except Exception as exc:
                    stats['errors'] += 1
                    errors.append({'source': key, 'url': url, 'error': type(exc).__name__ + ': ' + str(exc)[:130]})
                time.sleep(1.2)
        except Exception as exc:
            stats['errors'] += 1
            errors.append({'source': key, 'error': type(exc).__name__ + ': ' + str(exc)[:130]})
    for stats in source_stats.values():
        if stats['index_opened'] and stats['links_found'] and not stats['errors'] and stats['due_links'] <= stats['checked']:
            stats['coverage'] = 'Индекс и доступная очередь проверены'
        else:
            stats['coverage'] = 'Н/Д — неполная проверка'
    results.sort(key=lambda x:(x.get('new_since_previous_run', False), x['relevance']), reverse=True)
    report = {'time_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'mode': 'public-read-only',
              'sources_configured': len(SOURCES), 'indexes_opened': indexes_opened, 'detail_pages_checked': checked,
              'new_preliminary_signals': sum(bool(r.get('new_since_previous_run')) for r in results),
              'confirmed_qualified_orders': 0, 'messages_sent': 0,
              'successful_details': successful_details, 'rejected_details': rejected,
              'scan_healthy': all(s['coverage'] == 'Индекс и доступная очередь проверены' for s in source_stats.values()),
              'coverage_complete': all(s['coverage'] == 'Индекс и доступная очередь проверены' for s in source_stats.values()),
              'skipped_recent': skipped_recent, 'rejection_counts': dict(rejection_counts),
              'source_stats': source_stats,
              'items': results, 'errors': errors,
              'manual_review_queue': review_queue, 'manual_review_count': len(review_queue),
              'fallback_required': [{'source': key, 'index': base, 'domain': urllib.parse.urlparse(base).hostname, 'status': 'Н/Д — требуется адресная проверка разрешённым способом'} for key, base, _ in SOURCES if not source_stats[key]['index_opened']]}
    tmp = OUTPUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(OUTPUT)
    history = dict(sorted(history.items(), key=lambda item: item[1].get('checked_at', 0), reverse=True))
    state_tmp = STATE.with_suffix('.tmp')
    state_tmp.write_text(json.dumps({'seen_urls': sorted(known), 'checked_urls': history, 'parser_revision': 3}, ensure_ascii=False, indent=2), encoding='utf-8')
    state_tmp.replace(STATE)
    print(json.dumps({'configured': len(SOURCES), 'indexes_opened': indexes_opened, 'details': checked, 'preliminary': report['new_preliminary_signals'],
                      'qualified': 0, 'errors': len(errors)}, ensure_ascii=False))
    # Machine-readable fallback for read-only consumers without ZIP extraction.
    # Do not log page extracts, contact details, credentials, or nonpublic data.
    log_report = {k: report[k] for k in (
        'time_utc', 'sources_configured', 'indexes_opened',
        'detail_pages_checked', 'successful_details', 'rejected_details',
        'new_preliminary_signals', 'confirmed_qualified_orders',
        'messages_sent', 'scan_healthy', 'coverage_complete', 'skipped_recent', 'rejection_counts', 'source_stats')}
    log_report['items'] = [{k: row.get(k) for k in (
        'url', 'relevance', 'deadline_dates', 'new_since_previous_run',
        'eligible_preliminary', 'buyer_and_live_need_confirmed')} for row in report['items']]
    log_report['manual_review_count'] = report['manual_review_count']
    log_report['manual_review_queue'] = review_queue
    log_report['fallback_required'] = report['fallback_required']
    log_report['errors'] = [{'source': e.get('source'), 'error': e.get('error', '').split(':', 1)[0]} for e in errors]
    print('AMAPS_REPORT_V1 ' + json.dumps(log_report, ensure_ascii=False, separators=(',', ':')), flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as s:
            s.write('## АМАПС — поиск открытого прямого спроса\n\n')
            s.write(f"Доступно источников: {indexes_opened} из {len(SOURCES)}. Проверено карточек: {checked}. Новых предварительных сигналов: {report['new_preliminary_signals']}. ")
            s.write('Квалифицированных заказов без ручной проверки: **0**. Сообщений не отправлялось.\n\n')
            s.write('Вложение amaps-public-monitor содержит проверенные URL и причины ошибок.\n')
            for row in results[:8]:
                s.write('- ' + row['title'].replace('\n',' ')[:100] + ': ' + row['url'] + '\n')
    return 0 if report['scan_healthy'] else 2

def self_test():
    now = dt.date(2026, 11, 25)
    base = '<p>Описание заказа</p><p>Требуется изготовление корпуса Д16Т</p><p>Контакты заказчика</p><p>Окончание приема заявок: 30.11.2026</p>'
    url = 'https://example.org/orders/1'
    assert rfq_details(url, base, now)['eligible_preliminary']
    assert rfq_details(url, base.replace('Окончание приема заявок:', 'Сбор КП по:'), now)['eligible_preliminary']
    assert not rfq_details(url, base.replace('Окончание приема заявок:', 'Сбор КП по:').replace('30.11.2026', '20.11.2026'), now)['eligible_preliminary']
    assert 'deadline_expired' in rfq_details(url, base.replace('30.11.2026', '20.11.2026'), now)['rejection_reasons']
    assert 'contact_hidden' in rfq_details(url, base + ' контакты заказчика скрыты', now)['rejection_reasons']
    assert 'order_scope_unrecognized' in rfq_details(url, base.replace('Описание заказа', 'Меню'), now)['rejection_reasons']
    assert 'material_or_edm_missing' in rfq_details(url, base.replace('Д16Т', 'пластик'), now)['rejection_reasons']
    assert not rfq_details(url, base + ' закупка завершена', now)['eligible_preliminary']
    metal_url = 'https://metalloobrabotchiki.ru/orders/18356'
    metal_base = '<p>Заказ №18356</p>' + base
    assert rfq_details(metal_url, metal_base, now)['eligible_preliminary']
    assert not rfq_details(metal_url, metal_base + '<p>Закрыт</p>', now)['eligible_preliminary']
    mixed = metal_base + '<p>Заказ №18389</p><p>Сбор КП по: 11.12.2026</p>'
    assert 'ambiguous_order_page' in rfq_details(metal_url, mixed, now)['rejection_reasons']
    assert not rfq_details(metal_url, mixed, now)['eligible_preliminary']
    assert not rfq_details(metal_url, base, now)['eligible_preliminary']
    assert not rfq_details(url, base + '<p>Сбор КП по: 11.12.2026</p>', now)['eligible_preliminary']
    assert not rfq_details(url, base.replace('30.11.2026', '25.11.2026'), now)['eligible_preliminary']
    assert not rfq_details(url, base.replace('Требуется', 'Предлагаем услуги. Требуется'), now)['eligible_preliminary']
    assert not rfq_details(url, base.replace('корпуса', 'корпуса военного назначения'), now)['eligible_preliminary']
    assert rfq_details(url, base + '<a href="/drawing.pdf">КД</a>', now)['document_links'] == ['https://example.org/drawing.pdf']
    assert not rfq_details(url, base + '<a href="https://other.example/drawing.pdf">КД</a>', now)['document_links']
    assert len(rfq_details(url, base, now)['evidence_sha256']) == 64
    print('AMAPS_SELFTEST_OK 20', flush=True)

if __name__ == '__main__':
    self_test()
    if '--self-test' not in sys.argv:
        sys.exit(run())
