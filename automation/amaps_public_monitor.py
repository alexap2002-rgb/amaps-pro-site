#!/usr/bin/env python3
"""Монитор открытых производственных запросов.

Миграция state.json с ревизии 3 на 4 сохраняет URL из обоих старых
списков истории. Карточки загружаются заново, поскольку старые контрольные
суммы описывали весь HTML. Новый файл хранит URL, контрольные суммы, сроки,
признаки проверки и причины ручной проверки. Состояние заменяется атомарно.

При отсутствии или повреждении истории цикл заново собирает базу по каждому
распознанному индексу. Признак новизны относится к впервые замеченному URL
в индексе, доступном и в предыдущем цикле. Дата публикации и наличие заказа
требуют отдельного подтверждения. Знакомые URL сохраняются при исчезновении
из индекса. Очередь ручной проверки хранится до повторной успешной загрузки
и оценки карточки, в том числе если ссылка временно исчезла из индекса.

Префикс и прежние поля AMAPS_REPORT_V1 сохраняются. Значение null у числа
новых сигналов означает, что нулевой результат не установлен. Положительное
число при неполном охвате учитывает только наблюдённые сигналы.
"""
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo
from collections import Counter
import json
import os
import re
import sys
import tempfile
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
NEGATIVE = ('сварка', 'чугун', 'сталь 20', 'сталь 45', 'пружин', 'гибка', 'отливк', 'литьё')
UA = 'AMAPS-PublicDemandMonitor/1.0 (public pages only; no automation of messages)'
MONTHS = dict(zip(
    'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split(),
    range(1, 13)))
DATE = r'(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:' + '|'.join(MONTHS) + r')\s+\d{4})'
DEADLINE = (r'окончание\s+при[её]ма(?:\s+(?:заявок|предложений))?|'
            r'при[её]м\s+(?:заявок|предложений)\s+до|'
            r'срок\s+подачи(?:\s+(?:заявок|предложений))?|подать\s+до|'
            r'дата\s+окончания(?:\s+при[её]ма\s+(?:заявок|предложений))?|'
            r'сбор\s+(?:кп|предложений)\s+(?:по|до)')
DESCRIPTION = r'\bописание\s+заказа\b'
RELATED_ORDERS = r'\b(?:похожие|другие|рекомендуемые)\s+заказы\b'
DESCRIPTION_END = (r'\b(?:контакты\s+заказчика|дата\s+публикации|опубликовано|'
                   r'статус(?:\s+заказа)?(?=\s*:)|документы\s+заказа|похожие\s+заказы|'
                   r'другие\s+заказы|' + DEADLINE + r')\b')
FORMAT_GAPS = {'order_scope_unrecognized', 'deadline_unrecognized',
               'deadline_today_time_unverified', 'ambiguous_order_page'}
HARD_REJECTIONS = {'closed', 'deadline_expired', 'restricted_end_use', 'supplier_ad'}


def parse_date(raw):
    try:
        if '.' in raw:
            day, month, year = map(int, raw.split('.'))
        elif '-' in raw:
            year, month, day = map(int, raw.split('-'))
        else:
            day, month_word, year = raw.split()
            day, month, year = int(day), MONTHS[month_word], int(year)
        return dt.date(year, month, day)
    except ValueError:
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

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
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as rsp:
        if rsp.status != 200:
            raise ValueError('HTTP ' + str(rsp.status))
        if canonical_url(rsp.geturl()) != canonical_url(url):
            raise ValueError('Redirect to a different resource')
        typ = rsp.headers.get('Content-Type', '')
        if 'html' not in typ:
            raise ValueError('Not HTML: ' + typ[:60])
        body = rsp.read(1_500_001)
        if len(body) > 1_500_000:
            raise ValueError('HTML exceeds the scan limit')
        return body.decode('utf-8', errors='replace')

def robots_allowed(url, cache):
    u = urllib.parse.urlparse(url)
    host = u.scheme + '://' + u.netloc
    if host not in cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(host + '/robots.txt')
        try:
            req = urllib.request.Request(rp.url, headers={'User-Agent': UA})
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=16) as response:
                content = response.read(512_001)
                if response.status != 200 or len(content) > 512_000:
                    raise ValueError('Invalid robots.txt response')
                robots = content.decode('utf-8-sig')
                if robots.lstrip().startswith('<'):
                    raise ValueError('Expected robots.txt, got markup')
                rp.parse(robots.splitlines())
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
        if p.scheme != 'https' or p.netloc != base_host or not re.fullmatch(pattern, p.path, flags=re.I):
            continue
        u = p._replace(query='', fragment='').geturl()
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
    related = re.search(RELATED_ORDERS, plain, flags=re.I)
    if related:
        plain = plain[:related.start()]
    normalized = plain.lower()
    descriptions = list(re.finditer(DESCRIPTION, normalized))
    ambiguous_page = ambiguous_page or len(descriptions) > 1
    scope = ''
    if len(descriptions) == 1:
        tail = plain[descriptions[0].end():]
        end = re.search(DESCRIPTION_END, tail, flags=re.I)
        scope = (tail[:end.start()] if end else tail).strip(' :—-')
    lowered = scope.lower()
    date_end = r'(?![\w/-]|\.\d)'
    dates = {parse_date(raw) for raw in re.findall(r'(?<![\w./-])' + DATE + date_end, normalized)}
    deadline_dates = set()
    deadline_invalid = False
    for label in re.finditer(r'\b(?:' + DEADLINE + r')\b', normalized):
        tail = normalized[label.end():]
        value = re.match(r'\s*(?::\s*)?(?:до\s+)?(' + DATE + r')' + date_end, tail)
        date = parse_date(value.group(1)) if value else None
        if date is None:
            deadline_invalid = True
            continue
        deadline_dates.add(date)
        if re.match(r'\s*(?:[/–—-]|или\b|либо\b|,?\s*и\b)\s*(?:до\s+)?\d', tail[value.end():]):
            deadline_invalid = True
    ambiguous_page = ambiguous_page or len(deadline_dates) > 1
    recent = bool(deadline_dates) and not deadline_invalid and not ambiguous_page and min(deadline_dates) > now
    closed = bool(re.search(
        r'при[её]м (?:заявок|предложений) (?:заверш[её]н|закрыт|прекращ[её]н)|'
        r'(?:закупка|процедура) (?:завершена|отменена)|заказ (?:закрыт|отмен[её]н)|'
        r'\bзакрыт\b|договор заключ[её]н', normalized))
    recent = recent and not closed
    material = any(t in lowered for t in ('алюмини', 'д16т', 'амг', 'ад31', 'ад0', '6061', '7075'))
    edm = 'электроэроз' in lowered or 'проволочн' in lowered
    relevance = (3 if material else 0) + (3 if edm else 0)
    if material or edm:
        relevance += sum(1 for x in ('фрезер', 'точен', 'токар', 'корпус', 'крышк', 'плит') if x in lowered)
    hidden = any(b in plain.lower() for b in BLOCKS)
    supplier_ad = bool(re.search(r'предлагаем услуги|оказываем услуги|принимаем заказы|ищем заказчиков|наше производство', lowered))
    sensitive_or_unknown = bool(re.search(
        r'военн|боеприпас|вооруж[её]н|оборонн|ракетн|двойн\w+ назначен|'
        r'\bгоз\b|ограниченн\w+ применен|экспортн\w+ контрол', normalized))
    attachments = []
    for link in page.links:
        target = urllib.parse.urljoin(url, link['href'])
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme == 'https' and parsed.netloc == urllib.parse.urlparse(url).netloc and not parsed.query and not parsed.fragment and re.search(r'\.(?:pdf|zip|rar|7z|step|stp|dwg|dxf|xlsx|docx)$', parsed.path, re.I):
            attachments.append(target)
    negative = any(b in lowered for b in NEGATIVE)
    demand = bool(re.search(r'изготовлен|требуется|закупк|запрос\s+кп|ищем\s+подрядчика|заказ\s+на', lowered))
    scoped = bool(scope)
    reasons = []
    if ambiguous_page: reasons.append('ambiguous_order_page')
    if not scoped: reasons.append('order_scope_unrecognized')
    if not deadline_dates or deadline_invalid: reasons.append('deadline_unrecognized')
    if deadline_dates and min(deadline_dates) < now: reasons.append('deadline_expired')
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
            'recent_date_detected': recent, 'dates_on_page': sorted(str(d) for d in dates if d)[:10],
            'buyer_contact_hidden': hidden, 'buyer_and_live_need_confirmed': False,
            'eligible_preliminary': eligible, 'rejection_reasons': reasons, 'deadline_dates': [str(d) for d in sorted(deadline_dates)],
            'negative_material_or_process': negative, 'explicit_order_scope': scoped,
            'status': status,
            'document_links': sorted(set(attachments))[:20], 'documents_downloaded': 0,
            'evidence_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
            'details_sha256': hashlib.sha256(json.dumps([
                lowered, sorted(str(d) for d in deadline_dates), sorted(set(attachments)),
                closed, hidden, sensitive_or_unknown, deadline_invalid, ambiguous_page,
            ], ensure_ascii=False).encode('utf-8')).hexdigest(),
            'observed_utc': dt.datetime.now(dt.timezone.utc).isoformat()}

def load_state():
    try:
        saved = json.loads(STATE.read_text(encoding='utf-8'))
        revision = saved['parser_revision']
        known = saved['seen_urls']
        history = saved['checked_urls']
        if revision not in (3, 4) or not isinstance(known, list) or not isinstance(history, dict):
            raise ValueError('Unknown state schema')
        if not all(isinstance(url, str) for url in [*known, *history]):
            raise ValueError('Invalid URL history')
        if revision == 3:
            return 'migrated', set(known) | set(history), {}, set()
        baselines = saved['source_baselines']
        if not isinstance(baselines, list) or not all(isinstance(key, str) for key in baselines):
            raise ValueError('Invalid source baselines')
        for row in history.values():
            if (not isinstance(row['checked_at'], (int, float))
                    or not isinstance(row['title_sha256'], str)
                    or not isinstance(row['details_sha256'], str)
                    or not isinstance(row['eligible'], bool)
                    or not isinstance(row['format_gap'], bool)
                    or not isinstance(row['review_reasons'], list)
                    or not all(isinstance(reason, str) for reason in row['review_reasons'])
                    or not isinstance(row['evidence_sha256'], str)
                    or not isinstance(row['deadline_dates'], list)):
                raise ValueError('Invalid detail history')
            for value in row['deadline_dates']:
                dt.date.fromisoformat(value)
        return 'restored', set(known) | set(history), history, set(baselines)
    except FileNotFoundError:
        return 'missing', set(), {}, set()
    except (OSError, ValueError, TypeError, KeyError):
        return 'invalid', set(), {}, set()


def write_atomic(path, data):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def run(now=None):
    if now is None:
        now = dt.datetime.now(ZoneInfo('Europe/Moscow')).date()
    history_status, known, history, baselines = load_state()
    observed = set()
    next_baselines = set()
    scan_epoch = time.time()
    rejection_counts = Counter()
    source_stats = {}
    skipped_recent = 0
    robots_cache = {}
    indexes_opened = 0
    checked_in_run = set()
    run_deadline = time.monotonic() + 480
    errors, results, changes, checked = [], [], [], 0
    successful_details = 0
    rejected = 0
    for key, base, path_pattern in SOURCES:
        stats = source_stats[key] = {
            'index_opened': False, 'links_found': 0, 'due_links': 0, 'checked': 0,
            'rejected': 0, 'errors': 0, 'skipped_recent': 0, 'new_publications': None,
            'coverage': 'Н/Д', 'remaining_queue': 0, 'detail_errors': 0,
            'unknown_details': 0, 'coverage_gaps': [],
        }
        try:
            if not robots_allowed(base, robots_cache):
                raise ValueError('robots.txt: prohibited or unavailable')
            html = get(base)
            indexes_opened += 1
            stats['index_opened'] = True
            urls = find_links(html, base, path_pattern)
            stats['links_found'] = len(urls)
            stats['index_evidence_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
            stats['index_observed_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
            if not urls:
                stats['errors'] += 1
                stats['coverage_gaps'].append('unknown_index_format')
                errors.append({'source': key, 'error': 'No recognized order links; coverage unverified'})
                continue
            next_baselines.add(key)
            identities = {canonical_url(url) for url, _ in urls}
            observed.update(identities)
            due = []
            for url, title in urls:
                identity = canonical_url(url)
                previous = history.get(identity, {})
                if identity in checked_in_run:
                    stats['unknown_details'] += previous['format_gap']
                    continue
                title_digest = hashlib.sha256(title.encode('utf-8')).hexdigest()
                if (previous.get('title_sha256') == title_digest
                        and 0 <= scan_epoch - previous['checked_at'] < (7200 if previous['eligible'] else 86400)
                        and not (previous['eligible'] and
                                 any(date <= now.isoformat() for date in previous['deadline_dates']))):
                    stats['skipped_recent'] += 1
                    skipped_recent += 1
                    stats['unknown_details'] += previous['format_gap']
                    continue
                due.append((url, title, title_digest))
            stats['due_links'] = len(due)
            # Unseen and changed listings before old rechecks; rotate through the entire index.
            due.sort(key=lambda item: (
                canonical_url(item[0]) in history,
                history.get(canonical_url(item[0]), {}).get('checked_at', 0)))
            stats['remaining_queue'] = len(due)
            for url, title, title_digest in due[:16]:
                if time.monotonic() >= run_deadline:
                    break
                stats['remaining_queue'] -= 1
                identity = canonical_url(url)
                if not robots_allowed(url, robots_cache):
                    stats['errors'] += 1
                    stats['detail_errors'] += 1
                    errors.append({'source': key, 'url': url, 'error': 'robots.txt: detail prohibited or unavailable'})
                    continue
                checked += 1
                stats['checked'] += 1
                try:
                    result = rfq_details(url, get(url), now, title)
                    successful_details += 1
                    checked_in_run.add(identity)
                    gaps = set(result['rejection_reasons'])
                    format_gap = bool(gaps & FORMAT_GAPS and not gaps & HARD_REJECTIONS)
                    stats['unknown_details'] += format_gap
                    previous = history.get(identity)
                    result['details_changed'] = bool(previous and previous['details_sha256'] != result['details_sha256'])
                    result['reactivated'] = bool(previous and not previous['eligible'] and result['eligible_preliminary'])
                    if result['details_changed'] or result['reactivated']:
                        changes.append({field: result[field] for field in (
                            'url', 'details_changed', 'reactivated', 'eligible_preliminary',
                        )})
                    history[identity] = {
                        'checked_at': scan_epoch, 'title_sha256': title_digest,
                        'eligible': result['eligible_preliminary'],
                        'evidence_sha256': result['evidence_sha256'],
                        'details_sha256': result['details_sha256'],
                        'deadline_dates': result['deadline_dates'],
                        'format_gap': format_gap, 'review_reasons': sorted(gaps) if format_gap else [],
                    }
                    if result['eligible_preliminary']:
                        result['new_since_previous_run'] = key in baselines and identity not in known
                        results.append(result)
                    else:
                        rejected += 1
                        stats['rejected'] += 1
                        rejection_counts.update(result['rejection_reasons'])
                except Exception as exc:
                    stats['errors'] += 1
                    stats['detail_errors'] += 1
                    errors.append({'source': key, 'url': url, 'error': type(exc).__name__ + ': ' + str(exc)[:130]})
                time.sleep(1.2)
        except Exception as exc:
            stats['errors'] += 1
            stats['coverage_gaps'].append('source_unavailable' if not stats['index_opened'] else 'unknown_index_format')
            errors.append({'source': key, 'error': type(exc).__name__ + ': ' + str(exc)[:130]})
        for field, reason in (('remaining_queue', 'queue_remaining'), ('detail_errors', 'detail_error'),
                              ('unknown_details', 'unknown_detail_format')):
            if stats[field]:
                stats['coverage_gaps'].append(reason)
        if not stats['coverage_gaps']:
            stats['coverage'] = 'Индекс и доступная очередь проверены'
            if key in baselines:
                stats['new_publications'] = len(identities - known)
        else:
            stats['coverage'] = 'Н/Д — неполная проверка'
    results.sort(key=lambda x:(x.get('new_since_previous_run', False), x['relevance']), reverse=True)
    complete = all(not stats['coverage_gaps'] for stats in source_stats.values())
    new_signals = sum(row['new_since_previous_run'] for row in results)
    review_queue = [
        {'url': url, 'reasons': row['review_reasons'], 'evidence_sha256': row['evidence_sha256'],
         'status': 'НЕ ПРИНЯТА — требуется проверка первоисточника'}
        for url, row in sorted(history.items()) if row['review_reasons']
    ]
    report = {'time_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'mode': 'public-read-only',
              'sources_configured': len(SOURCES), 'indexes_opened': indexes_opened, 'detail_pages_checked': checked,
              'new_preliminary_signals': new_signals if new_signals or (complete and baselines == next_baselines) else None,
              'history_status': history_status,
              'confirmed_qualified_orders': 0, 'messages_sent': 0,
              'successful_details': successful_details, 'rejected_details': rejected,
              'scan_healthy': complete, 'coverage_complete': complete,
              'skipped_recent': skipped_recent, 'rejection_counts': dict(rejection_counts),
              'source_stats': source_stats,
              'items': results, 'errors': errors, 'changed_cards': changes,
              'manual_review_queue': review_queue, 'manual_review_count': len(review_queue),
              'fallback_required': [
                  {'source': key, 'index': base, 'domain': urllib.parse.urlparse(base).hostname,
                   'reasons': source_stats[key]['coverage_gaps'],
                   'status': 'Н/Д — требуется адресная проверка разрешённым способом'}
                  for key, base, _ in SOURCES if source_stats[key]['coverage_gaps']
              ]}
    write_atomic(STATE, {'seen_urls': sorted(known | observed), 'checked_urls': history,
                         'parser_revision': 4, 'source_baselines': sorted(next_baselines)})
    write_atomic(OUTPUT, report)
    print(json.dumps({'configured': len(SOURCES), 'indexes_opened': indexes_opened, 'details': checked, 'preliminary': report['new_preliminary_signals'],
                      'qualified': 0, 'errors': len(errors)}, ensure_ascii=False))
    # Machine-readable fallback for read-only consumers without ZIP extraction.
    # Do not log page extracts, contact details, credentials, or nonpublic data.
    log_report = {k: report[k] for k in (
        'time_utc', 'sources_configured', 'indexes_opened',
        'detail_pages_checked', 'successful_details', 'rejected_details',
        'new_preliminary_signals', 'confirmed_qualified_orders',
        'messages_sent', 'scan_healthy', 'coverage_complete', 'skipped_recent', 'rejection_counts', 'source_stats',
        'history_status')}
    log_report['items'] = [{k: row.get(k) for k in (
        'url', 'relevance', 'deadline_dates', 'new_since_previous_run',
        'eligible_preliminary', 'buyer_and_live_need_confirmed', 'details_changed', 'reactivated')} for row in report['items']]
    log_report['manual_review_count'] = report['manual_review_count']
    log_report['manual_review_queue'] = review_queue
    log_report['changed_cards'] = changes
    log_report['fallback_required'] = report['fallback_required']
    log_report['errors'] = [{'source': e.get('source'), 'error': e.get('error', '').split(':', 1)[0]} for e in errors]
    print('AMAPS_REPORT_V1 ' + json.dumps(log_report, ensure_ascii=False, separators=(',', ':')), flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as s:
            s.write('## АМАПС — поиск открытого прямого спроса\n\n')
            new_display = report['new_preliminary_signals']
            if new_display is None:
                new_display = 'Н/Д'
            s.write(f"Доступно источников: {indexes_opened} из {len(SOURCES)}. Проверено карточек: {checked}. Новых предварительных сигналов: {new_display}. ")
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
