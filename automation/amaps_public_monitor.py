#!/usr/bin/env python3
"""Монитор открытых производственных запросов.

Миграция state.json с ревизии 3 сохраняет URL из обоих старых
списков истории. Карточки загружаются заново, поскольку старые контрольные
суммы описывали весь HTML. Новый файл хранит URL, контрольные суммы, сроки,
признаки проверки и причины ручной проверки. Состояние заменяется атомарно.

При отсутствии или повреждении истории цикл заново собирает базу по каждому
распознанному индексу. Признак новизны относится к впервые замеченному URL
в индексе с ранее сохранённой исходной выборкой. Дата публикации и наличие заказа
требуют отдельного подтверждения. Знакомые URL сохраняются при исчезновении
из индекса. Очередь ручной проверки хранится до повторной успешной загрузки
и оценки карточки, в том числе если ссылка временно исчезла из индекса.

Очередь хранит URL, источник, оценку заголовка и время попытки до успешной
оценки, в том числе при повторной проверке знакомой карточки. Состояние
хранит обезличенный отчёт до его записи и вывода в журнал. Счётчик впервые замеченных URL относится к индексу
с сохранённой исходной выборкой. Даты выхода новых публикаций цикл
не подтверждает, поэтому new_publications имеет значение null.
Префикс и прежние поля AMAPS_REPORT_V1 сохраняются. Значение null у числа
новых сигналов означает, что нулевой результат не установлен. Положительное
число при неполном охвате учитывает только наблюдённые сигналы.
"""
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
CARD_FRESH_SECONDS = 7200
REJECTION_RECHECK_SECONDS = 86400
RUN_BUDGET_SECONDS = 480
SOURCE_BUDGET_SECONDS = 70
# Synthetic hypotheses. These labels cannot establish eligibility.
UNVERIFIED_SCOPE_LABELS = {
    'xn----7sbocllqn1abl5bl1g.xn--p1ai': 'описание заявки',
    'subcontract.tomsk.ru': 'производственное задание',
    'energybase.ru': 'предмет закупки',
    'zakupki.kontur.ru': 'объект закупки',
}
PROFILE_MATERIAL = r'алюмини|\b(?:д16т|амг\d*|ад31|ад0|6061|7075)\b'
PROFILE_EDM = r'электроэроз|проволочн\w*\s+эдм'
DOCUMENT_PATH = re.compile(r'\.(?:pdf|zip|rar|7z|step|stp|dwg|dxf|xlsx|docx)$', re.I)
CARD_POLICY_REVISION = 2
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
                   r'статус(?:\s+заказа)?(?=\s*:)|документы\s+заказа|вложения|похожие\s+заказы|'
                   r'другие\s+заказы|' + DEADLINE + r')\b')
FORMAT_GAPS = {'order_scope_unrecognized', 'deadline_unrecognized',
               'deadline_today_time_unverified', 'ambiguous_order_page', 'source_format_unverified',
               'publication_date_conflict'}
HARD_REJECTIONS = {'closed', 'deadline_expired', 'restricted_end_use', 'supplier_ad', 'contact_hidden'}
ENERGYBASE_INDEX_FIELDS = {
    'announced_dates_recognized': r'дата объявления',
    'submission_deadlines_recognized': r'дата и время окончания подачи заявок',
}
INDEX_DATE = r'(\d{1,2}\s+(?:' + '|'.join(MONTHS) + r')\s+\d{4})'
INDEX_TIME = r'\s+г\.,\s+(?:[01]\d|2[0-3]):[0-5]\d(?![\d:])'
PROFILE_WORDS = ('алюмини', 'д16т', 'амг', 'ад31', 'корпус', 'крышк', 'фрезер', 'электроэроз')


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
            if self.active is None or not DOCUMENT_PATH.search(urllib.parse.urlsplit(self.active['href']).path):
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
        # Сам каталог и его варианты с параметрами не являются карточками.
        if p.path.rstrip('/') == urllib.parse.urlparse(base).path.rstrip('/'):
            continue
        if u not in found:
            found[u] = link['text'].strip()[:180]
    return list(found.items())

def canonical_url(url):
    p = urllib.parse.urlparse(url)
    if p.hostname == 'metalloobrabotchiki.ru' or (p.hostname or '').endswith('.metalloobrabotchiki.ru'):
        return 'https://metalloobrabotchiki.ru' + p.path
    return p.scheme + '://' + p.netloc + p.path

def index_observations(key, html):
    if key not in ('ceh', 'energybase_parts'):
        return {}
    page = Page()
    page.feed(html)
    plain = re.sub(r'\s+', ' ', page.text_value().lower())
    if key == 'ceh':
        return {'dynamic_index_placeholder': bool(re.search(r'\bзагрузка\s*(?:\.{3}|…)', plain))}
    return {
        field: sum(parse_date(value) is not None for value in re.findall(
            r'\b' + label + r'\s*:?\s*' + INDEX_DATE + INDEX_TIME, plain))
        for field, label in ENERGYBASE_INDEX_FIELDS.items()
    }


def rfq_details(url, html, now, title=''):
    page = Page()
    page.feed(html)
    plain = re.sub(r'\s+', ' ', page.text_value())
    # Never qualify a listing or a page combining several RFQs as a single order.
    order_ids = set(re.findall(r'заказ\s*№\s*(\d+)', plain, flags=re.I))
    expected = re.fullmatch(r'/orders/(\d+)', urllib.parse.urlparse(url).path)
    is_metalorders = (urllib.parse.urlparse(canonical_url(url)).hostname == 'metalloobrabotchiki.ru')
    ambiguous_page = len(order_ids) > 1 or (
        is_metalorders and (expected is None or order_ids != {expected.group(1)}))
    related = re.search(RELATED_ORDERS, plain, flags=re.I)
    if related:
        plain = plain[:related.start()]
    normalized = plain.lower()
    scope_label = UNVERIFIED_SCOPE_LABELS.get(urllib.parse.urlsplit(url).hostname)
    description = DESCRIPTION if scope_label is None else rf'\b{scope_label}\b'
    descriptions = list(re.finditer(description, normalized))
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
    closed = bool(re.search(
        r'при[её]м (?:заявок|предложений) (?:заверш[её]н|закрыт|прекращ[её]н)|'
        r'(?:закупка|процедура) (?:завершена|отменена)|заказ (?:закрыт|отмен[её]н)|'
        r'\bзакрыт\b|договор заключ[её]н', normalized))
    material = bool(re.search(PROFILE_MATERIAL, lowered))
    edm = bool(re.search(PROFILE_EDM, lowered))
    profile_hint = re.compile(PROFILE_MATERIAL + '|' + PROFILE_EDM, re.I)
    publication_dates = {
        parse_date(value) for value in re.findall(
            r'\b(?:дата публикации|опубликовано)\s*:?\s*(' + DATE + ')' + date_end, normalized)
    }
    if is_metalorders and expected:
        header = r'\bзаказ\s*№\s*' + expected.group(1) + r'\s+от\s+'
        for label in re.finditer(header, normalized):
            date_match = re.match(r'\d{1,2}\.\d{1,2}\.\d{4}' + date_end,
                                  normalized[label.end():])
            publication_dates.add(parse_date(date_match.group()) if date_match else None)
    publication_conflict = bool(publication_dates) and (
        len(publication_dates) != 1 or None in publication_dates
        or (len(deadline_dates) == 1 and next(iter(publication_dates)) > next(iter(deadline_dates))))
    deadline_verified = (scope_label is None and bool(scope) and not ambiguous_page
                         and not deadline_invalid and not publication_conflict and bool(deadline_dates))
    recent = deadline_verified and not closed and min(deadline_dates) > now
    relevance = (3 if material else 0) + (3 if edm else 0)
    if material or edm:
        relevance += sum(1 for x in ('фрезер', 'точен', 'токар', 'корпус', 'крышк', 'плит') if x in lowered)
        relevance += 2 * bool(edm or (material and re.search(r'корпус|крышк', lowered)))
    hidden = any(b in plain.lower() for b in BLOCKS)
    supplier_ad = bool(re.search(r'предлагаем услуги|оказываем услуги|принимаем заказы|ищем заказчиков|наше производство', lowered))
    sensitive_or_unknown = bool(re.search(
        r'военн|боеприпас|вооруж[её]н|оборонн|ракетн|двойн\w+ назначен|'
        r'\bгоз\b|ограниченн\w+ применен|экспортн\w+ контрол|'
        r'(?:неясн|неизвестн|неопредел[её]н)\w* назначен|назначение\s*:\s*не (?:указано|определено)',
        normalized))
    attachments = []
    attachment_hint = False
    for link in page.links:
        target = urllib.parse.urljoin(url, link['href'])
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme == 'https' and parsed.netloc == urllib.parse.urlparse(url).netloc and not parsed.query and not parsed.fragment and DOCUMENT_PATH.search(parsed.path):
            attachments.append(target)
            attachment_hint |= bool(profile_hint.search(link['text'] + ' ' + urllib.parse.unquote(parsed.path)))
    negative = any(b in lowered for b in NEGATIVE)
    conflicting_material = material and any(word in lowered for word in ('чугун', 'сталь 20', 'сталь 45'))
    demand = bool(re.search(r'изготовлен|требуется|закупк|запрос\s+кп|ищем\s+подрядчика|заказ\s+на', lowered))
    scoped = bool(scope)
    reasons = []
    if ambiguous_page: reasons.append('ambiguous_order_page')
    if not scoped: reasons.append('order_scope_unrecognized')
    if scope_label is not None: reasons.append('source_format_unverified')
    if conflicting_material: reasons.append('conflicting_material')
    if not (material or edm):
        if profile_hint.search(title): reasons.append('title_profile_hint')
        if attachment_hint: reasons.append('attachment_profile_hint')
    if publication_conflict: reasons.append('publication_date_conflict')
    if not deadline_verified: reasons.append('deadline_unrecognized')
    if deadline_verified and min(deadline_dates) < now: reasons.append('deadline_expired')
    if deadline_verified and min(deadline_dates) == now: reasons.append('deadline_today_time_unverified')
    if closed: reasons.append('closed')
    if not (material or edm): reasons.append('material_or_edm_missing')
    if not demand: reasons.append('demand_missing')
    if hidden: reasons.append('contact_hidden')
    if negative: reasons.append('negative_process_or_material')
    if supplier_ad: reasons.append('supplier_ad')
    if sensitive_or_unknown: reasons.append('restricted_end_use')
    # Conservative preliminary filter, never automatic qualification.
    eligible = (recent and (material or edm) and demand and not hidden and not conflicting_material
                and scoped and scope_label is None and not supplier_ad and not sensitive_or_unknown)
    technical_reasons = sorted(set(reasons) & FORMAT_GAPS)
    commercial_reasons = sorted(set(reasons) - FORMAT_GAPS)
    civil_hint = bool(re.search(r'гражданск|пищев\w+ оборудован', lowered))
    process_hint = bool(re.search(r'фрезер|токар|чпу|механообработ|точени', lowered))
    subject_review = (not eligible and not (set(reasons) & HARD_REJECTIONS)
                      and not ambiguous_page and civil_hint and demand
                      and (material or edm or process_hint or profile_hint.search(title) or attachment_hint)
                      and (not negative or conflicting_material))
    commercial_decision = ('preliminary_signal' if eligible else
                           'manual_review' if subject_review else 'not_accepted')
    if hidden:
        status = 'НЕ ПРИНЯТА — сведения заказчика скрыты; открытый маршрут не установлен'
    elif eligible or subject_review:
        status = 'СИГНАЛ — требуется предметная проверка'
    else:
        status = 'НЕ ПРИНЯТА'
    return {'url': canonical_url(url), 'title': title or scope[:150], 'relevance': relevance,
            'aluminum_or_edm_in_order_description': material or edm,
            'source_card_format': 'Н/Д' if scope_label is not None or not scoped or ambiguous_page else 'recognized',
            'publication_date': (str(next(iter(publication_dates)))
                                 if scoped and not publication_conflict and not ambiguous_page and scope_label is None and len(publication_dates) == 1
                                 and None not in publication_dates else None),
            'technical_reasons': technical_reasons, 'commercial_reasons': commercial_reasons,
            'commercial_decision': commercial_decision,
            'recent_date_detected': recent, 'dates_on_page': sorted(str(d) for d in dates if d)[:10],
            'buyer_contact_hidden': hidden, 'buyer_and_live_need_confirmed': False,
            'eligible_preliminary': eligible, 'rejection_reasons': reasons, 'deadline_dates': [str(d) for d in sorted(deadline_dates)] if deadline_verified else [],
            'negative_material_or_process': negative, 'explicit_order_scope': scoped,
            'status': status,
            'document_links': sorted(set(attachments))[:20], 'documents_downloaded': 0,
            'evidence_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
            'details_sha256': hashlib.sha256(json.dumps([
                lowered, sorted(str(d) for d in deadline_dates), sorted(set(attachments)),
                closed, hidden, sensitive_or_unknown, deadline_invalid, ambiguous_page,
                sorted(str(d) for d in publication_dates),
            ], ensure_ascii=False).encode('utf-8')).hexdigest(),
            'observed_utc': dt.datetime.now(dt.timezone.utc).isoformat()}

def load_state(metadata=None):
    try:
        saved = json.loads(STATE.read_text(encoding='utf-8'))
        revision = saved['parser_revision']
        known = saved['seen_urls']
        history = saved['checked_urls']
        if revision not in (3, 4, 5) or not isinstance(known, list) or not isinstance(history, dict):
            raise ValueError('Unknown state schema')
        if not all(isinstance(url, str) for url in [*known, *history]):
            raise ValueError('Invalid URL history')
        if revision == 3:
            return 'migrated', {canonical_url(url) for url in [*known, *history]}, {}, set(), {}
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
        pending = saved['pending_urls'] if revision == 5 else {}
        if not isinstance(pending, dict):
            raise ValueError('Invalid pending queue')
        for entry in pending.values():
            if not isinstance(entry, list) or len(entry) != 4:
                raise ValueError('Invalid pending entry')
            source, priority, attempted_at, is_new = entry
            if (not isinstance(source, str) or type(priority) is not int
                    or not 0 <= priority <= len(PROFILE_WORDS)
                    or (attempted_at is not None and not isinstance(attempted_at, (int, float)))
                    or type(is_new) is not bool):
                raise ValueError('Invalid pending entry')
        if metadata is not None:
            metadata.update({key: saved[key] for key in
                             ('next_source', 'coverage_epochs', 'index_epochs', 'first_seen', 'unpublished_report')
                             if key in saved})
        return 'restored', set(known) | set(history) | set(pending), history, set(baselines), pending
    except FileNotFoundError:
        return 'missing', set(), {}, set(), {}
    except (OSError, ValueError, TypeError, KeyError):
        return 'invalid', set(), {}, set(), {}


def write_atomic(path, data):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def moscow_today() -> dt.date:
    """??????? ???? ??????. ??? tzdata ???????????? UTC+03:00.

    ????????????? ???????? ??????????? ?????? ? ??????? ????.
    """
    try:
        moscow = ZoneInfo('Europe/Moscow')
    except ZoneInfoNotFoundError:
        moscow = dt.timezone(dt.timedelta(hours=3))
    return dt.datetime.now(moscow).date()


def run(now=None):
    if now is None:
        now = moscow_today()
    metadata = {}
    history_status, known, history, baselines, pending = load_state(metadata)
    if metadata.get('unpublished_report'):
        report = metadata.pop('unpublished_report')
        report['replayed_report'] = True
        publish_report(report, make_state(known, history, baselines, pending, metadata))
        return 2  # A historical report cannot mark the current hour as scanned.
    # Удаляем только ошибочные записи самих каталогов из старого состояния.
    index_paths = {canonical_url(source[1]).rstrip('/') for source in SOURCES}
    def is_index(identity):
        return canonical_url(identity).rstrip('/') in index_paths
    known = {url for url in known if not is_index(url)}
    history = {url: row for url, row in history.items() if not is_index(url)}
    pending = {url: row for url, row in pending.items() if not is_index(url)}
    if 'first_seen' in metadata:
        metadata['first_seen'] = {url: value for url, value in metadata['first_seen'].items()
                                  if not is_index(url)}
    coverage_epochs = metadata.get('coverage_epochs', {})
    index_epochs = metadata.get('index_epochs', {})
    first_seen = metadata.get('first_seen', {})
    offset = next((i for i, source in enumerate(SOURCES) if source[0] == metadata.get('next_source')), 0)
    sources = SOURCES[offset:] + SOURCES[:offset]
    if sources:
        metadata['next_source'] = sources[1 % len(sources)][0]
    observed = set()
    next_baselines = set()
    scan_epoch = time.time()
    rejection_counts = Counter()
    source_stats = {}
    source_card_ids = {}
    index_card_ids = {}
    skipped_recent = 0
    robots_cache = {}
    indexes_opened = 0
    attempted_in_run = set()
    run_deadline = time.monotonic() + RUN_BUDGET_SECONDS
    errors, results, changes, checked = [], [], [], 0
    successful_details = 0
    rejected = 0
    for key, base, path_pattern in sources:
        stats = source_stats[key] = {
            'index_opened': False, 'links_found': 0, 'due_links': 0, 'checked': 0,
            'rejected': 0, 'errors': 0, 'skipped_recent': 0, 'new_publications': None,
            'first_observed_urls': None,
            'coverage': 'Н/Д', 'remaining_queue': None, 'detail_errors': 0,
            'unknown_details': None, 'coverage_gaps': [], 'pending_retries': 0,
        }
        listed = {}
        source_card_ids[key] = listed_ids = set()
        source_deadline = min(run_deadline, time.monotonic() + SOURCE_BUDGET_SECONDS)
        try:
            if time.monotonic() >= source_deadline:
                stats['coverage_gaps'].append('run_budget_exhausted')
                raise TimeoutError('Run budget exhausted before index')
            if not robots_allowed(base, robots_cache):
                raise ValueError('robots.txt: prohibited or unavailable')
            if time.monotonic() >= source_deadline:
                stats['coverage_gaps'].append('source_budget_exhausted')
                raise TimeoutError('Budget exhausted before index')
            html = get(base)
            index_epochs[key] = scan_epoch
            indexes_opened += 1
            stats['index_opened'] = True
            urls = find_links(html, base, path_pattern)
            stats['links_found'] = len(urls)
            stats['index_evidence_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
            stats['index_observed_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
            stats.update(index_observations(key, html))
            if stats.get('dynamic_index_placeholder'):
                stats['coverage_gaps'].append('dynamic_index_unverified')
            if not urls:
                stats['errors'] += 1
                stats['coverage_gaps'].append('unknown_index_format')
                errors.append({'source': key, 'error': 'No recognized order links; coverage unverified'})
            else:
                next_baselines.add(key)
            identities = {canonical_url(url) for url, _ in urls}
            index_card_ids[key] = identities
            first_observed = identities - known - observed
            if urls and key in baselines:
                stats['first_observed_urls'] = len(first_observed)
            for identity in identities - known - observed:
                first_seen[identity] = scan_epoch
            observed.update(identities)
            # Карточка из очереди может исчезнуть из индекса до успешной оценки.
            base_parts = urllib.parse.urlsplit(base)
            listed = dict(urls)
            for identity, (source, _, _, _) in pending.items():
                path = urllib.parse.urlsplit(identity).path
                if source == key and re.fullmatch(path_pattern, path, flags=re.I):
                    url = base_parts._replace(path=path, query='', fragment='').geturl()
                    if canonical_url(url) == identity:
                        listed.setdefault(url, '')
            due = []
            for url, title in listed.items():
                identity = canonical_url(url)
                listed_ids.add(identity)
                previous = history.get(identity, {})
                if identity in attempted_in_run:
                    if identity in pending:
                        stats['detail_errors'] += 1
                        stats['errors'] += 1
                    continue
                if not previous and identity not in pending:
                    title_lower = title.lower()
                    pending[identity] = [
                        key, sum(word in title_lower for word in PROFILE_WORDS), None,
                        key in baselines and identity in first_observed,
                    ]
                title_digest = hashlib.sha256(title.encode('utf-8')).hexdigest()
                recheck_seconds = (REJECTION_RECHECK_SECONDS
                                   if previous.get('commercial_decision') == 'not_accepted'
                                   else CARD_FRESH_SECONDS)
                if (identity not in pending and previous.get('card_policy_revision') == CARD_POLICY_REVISION
                        and previous.get('title_sha256') == title_digest
                        and 0 <= scan_epoch - previous['checked_at'] < recheck_seconds
                        and not (previous['eligible'] and
                                 any(date <= now.isoformat() for date in previous['deadline_dates']))):
                    stats['skipped_recent'] += 1
                    skipped_recent += 1
                    continue
                queued = pending.setdefault(identity, [key, 0, previous.get('checked_at'), False])
                attempted_at = queued[2]
                priority = queued[1] if attempted_at is None else 0
                age = float('-inf') if attempted_at is None else attempted_at
                due.append((url, title, title_digest, priority, age))
            pending_order = {identity: position for position, identity in enumerate(pending)}

            def rotation_order(item):
                return item[4], pending_order.get(canonical_url(item[0]), -1)

            stats['due_links'] = len(due)
            # Свежие профильные карточки идут первыми. Одно место получает
            # самая давняя попытка из остатка, даже при постоянном притоке.
            due.sort(key=lambda item: (-item[3], rotation_order(item)))
            batch = due[:15]
            if len(due) > 15:
                batch.append(min(due[15:], key=rotation_order))
            for url, title, title_digest, _, _ in batch:
                if time.monotonic() >= source_deadline:
                    break
                identity = canonical_url(url)
                attempted_in_run.add(identity)
                queued = pending[identity]
                queued[0] = key
                queued[2] = scan_epoch
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
                    format_gap = bool(result['technical_reasons'])
                    previous = history.get(identity)
                    result['details_changed'] = bool(previous and previous['details_sha256'] != result['details_sha256'])
                    result['reactivated'] = bool(previous and not previous['eligible'] and result['eligible_preliminary'])
                    if result['details_changed'] or result['reactivated']:
                        changes.append({field: result[field] for field in (
                            'url', 'details_changed', 'reactivated', 'eligible_preliminary',
                        )})
                    history[identity] = {
                        'checked_at': scan_epoch, 'title_sha256': title_digest,
                        'card_policy_revision': CARD_POLICY_REVISION,
                        'publication_date': result['publication_date'],
                        'eligible': result['eligible_preliminary'],
                        'evidence_sha256': result['evidence_sha256'],
                        'details_sha256': result['details_sha256'],
                        'deadline_dates': result['deadline_dates'],
                        'format_gap': format_gap, 'review_reasons': result['technical_reasons'],
                        'commercial_decision': result['commercial_decision'],
                        'commercial_reasons': result['commercial_reasons'],
                        'status': result['status'],
                    }
                    pending.pop(identity, None)
                    if result['eligible_preliminary']:
                        result['new_since_previous_run'] = queued[3]
                        result['first_seen_utc'] = utc_epoch(first_seen.get(identity))
                        results.append({field: result[field] for field in (
                            'url', 'relevance', 'deadline_dates', 'publication_date', 'first_seen_utc',
                            'observed_utc', 'source_card_format', 'new_since_previous_run',
                            'eligible_preliminary', 'buyer_and_live_need_confirmed', 'details_changed',
                            'reactivated', 'evidence_sha256', 'status',
                        )})
                    else:
                        rejected += 1
                        stats['rejected'] += 1
                        rejection_counts.update(result['rejection_reasons'])
                except Exception as exc:
                    stats['errors'] += 1
                    stats['detail_errors'] += 1
                    errors.append({'source': key, 'url': url, 'error': type(exc).__name__})
                time.sleep(1.2)
        except Exception as exc:
            stats['errors'] += 1
            if not stats['coverage_gaps']:
                stats['coverage_gaps'].append('source_unavailable' if not stats['index_opened'] else 'unknown_index_format')
            errors.append({'source': key, 'error': type(exc).__name__})
    for key, stats in source_stats.items():
        listed_ids = source_card_ids[key]
        unresolved = listed_ids.intersection(pending)
        stats['remaining_queue'] = (len(unresolved - attempted_in_run)
                                    if stats['index_opened'] and stats['links_found'] else None)
        stats['pending_retries'] = len(unresolved & attempted_in_run)
        stats['known_pending_cards'] = sum(entry[0] == key for entry in pending.values())
        index_epoch = index_epochs.get(key)
        stats['last_index_check_utc'] = utc_epoch(index_epoch)
        stats['index_age_seconds'] = max(0, scan_epoch - index_epoch) if index_epoch is not None else None
        current_cards = [history[url] for url in index_card_ids.get(key, ())
                         if url in history and history[url].get('card_policy_revision') == CARD_POLICY_REVISION]
        stats['unknown_details'] = (sum(row['format_gap'] for row in current_cards)
                                    if stats['index_opened'] and stats['links_found'] else None)
        stats['fresh_card_checks'] = (sum(0 <= scan_epoch - row['checked_at'] < CARD_FRESH_SECONDS
                                          for row in current_cards)
                                      if stats['index_opened'] and stats['links_found'] else None)
        stats['oldest_card_age_seconds'] = (max(scan_epoch - row['checked_at'] for row in current_cards)
                                           if current_cards else None)
        stats['cached_commercial_rejections'] = (sum(
            scan_epoch - row['checked_at'] >= CARD_FRESH_SECONDS and not row['format_gap']
            and row.get('commercial_decision') == 'not_accepted' for row in current_cards)
            if stats['index_opened'] and stats['links_found'] else None)
        for field, reason in (('remaining_queue', 'queue_remaining'), ('detail_errors', 'detail_error'),
                              ('unknown_details', 'unknown_detail_format')):
            if stats[field]:
                stats['coverage_gaps'].append(reason)
        if (not stats['coverage_gaps'] and current_cards
                and stats['fresh_card_checks'] == stats['links_found']):
            coverage_epochs[key] = min(row['checked_at'] for row in current_cards)
        last_coverage = coverage_epochs.get(key)
        stats['last_successful_coverage_utc'] = utc_epoch(last_coverage)
        stats['coverage_age_seconds'] = max(0, scan_epoch - last_coverage) if last_coverage is not None else None
        stats['coverage_within_two_hours'] = (
            stats['coverage_age_seconds'] < CARD_FRESH_SECONDS if last_coverage is not None else None)
        if stats['fresh_card_checks'] is not None and any(
                not 0 <= scan_epoch - row['checked_at'] < CARD_FRESH_SECONDS for row in current_cards):
            stats['coverage_gaps'].append('coverage_older_than_two_hours')
        if not stats['coverage_gaps']:
            stats['coverage'] = 'Индекс и доступная очередь проверены'
        else:
            stats['coverage'] = 'Н/Д — неполная проверка'
    results.sort(key=lambda x:(x.get('new_since_previous_run', False), x['relevance']), reverse=True)
    complete = all(not stats['coverage_gaps'] for stats in source_stats.values())
    new_signals = sum(row['new_since_previous_run'] for row in results)
    review_queue = [
        {'url': url, 'reasons': sorted(set(row['review_reasons']) & FORMAT_GAPS),
         'evidence_sha256': row['evidence_sha256'],
         'first_seen_utc': utc_epoch(first_seen.get(url)), 'publication_date': row.get('publication_date'),
         'card_checked_utc': utc_epoch(row['checked_at']),
         'status': 'Н/Д — требуется проверка формата или срока'}
        for url, row in sorted(history.items()) if set(row['review_reasons']) & FORMAT_GAPS
    ]
    subject_review_queue = [
        {'url': url, 'reasons': row['commercial_reasons'], 'evidence_sha256': row['evidence_sha256'],
         'card_checked_utc': utc_epoch(row['checked_at']), 'retry_pending': url in pending,
         'deadline_dates': row['deadline_dates'], 'status': row['status']}
        for url, row in sorted(history.items())
        if row.get('commercial_decision') == 'manual_review'
        and (not row['deadline_dates'] or min(row['deadline_dates']) > now.isoformat())
    ]
    qualification_queue = [
        {'url': url, 'deadline_dates': row['deadline_dates'], 'evidence_sha256': row['evidence_sha256'],
         'card_checked_utc': utc_epoch(row['checked_at']), 'retry_pending': url in pending}
        for url, row in sorted(history.items())
        if row['eligible'] and row.get('card_policy_revision') == CARD_POLICY_REVISION
        and row['deadline_dates'] and min(row['deadline_dates']) > now.isoformat()
    ]
    report = {'time_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'mode': 'public-read-only',
              'sources_configured': len(SOURCES), 'indexes_opened': indexes_opened, 'detail_pages_checked': checked,
              'new_preliminary_signals': new_signals if new_signals or (
                  complete and next_baselines <= baselines) else None,
              'history_status': history_status,
              'confirmed_qualified_orders': 0, 'messages_sent': 0,
              'successful_details': successful_details, 'rejected_details': rejected,
              'scan_healthy': complete, 'coverage_complete': complete,
              'skipped_recent': skipped_recent, 'rejection_counts': dict(rejection_counts),
              'source_stats': source_stats,
              'items': results, 'errors': errors, 'changed_cards': changes,
              'manual_review_queue': review_queue, 'manual_review_count': len(review_queue),
              'subject_review_queue': subject_review_queue,
              'subject_review_count': len(subject_review_queue),
              'qualification_queue': qualification_queue,
              'qualification_requires': ['первоисточник', 'срок', 'гражданское назначение', 'заказчик',
                                         'ТЗ', 'законный письменный канал', 'возможность коммерческой защиты'],
              'fallback_required': [
                  {'source': key, 'index': base, 'domain': urllib.parse.urlparse(base).hostname,
                   'reasons': source_stats[key]['coverage_gaps'],
                   'verification_performed': False,
                   'status': 'Н/Д — требуется адресная проверка разрешённым способом'}
                  for key, base, _ in SOURCES if source_stats[key]['coverage_gaps']
              ]}
    metadata.update(coverage_epochs=coverage_epochs, index_epochs=index_epochs, first_seen=first_seen)
    state = make_state(known | observed, history, baselines | next_baselines, pending, metadata)
    write_atomic(STATE, {**state, 'unpublished_report': report})
    return publish_report(report, state)


def utc_epoch(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat() if value is not None else None


def make_state(known, history, baselines, pending, metadata):
    return {'seen_urls': sorted(known), 'checked_urls': history, 'parser_revision': 5,
            'source_baselines': sorted(baselines), 'pending_urls': pending, **metadata}


def publish_report(report, state):
    write_atomic(OUTPUT, report)
    print(json.dumps({'configured': report['sources_configured'], 'indexes_opened': report['indexes_opened'],
                      'details': report['detail_pages_checked'], 'preliminary': report['new_preliminary_signals'],
                      'qualified': 0, 'errors': len(report['errors'])}, ensure_ascii=False))
    # Machine-readable fallback for read-only consumers without ZIP extraction.
    # Do not log page extracts, contact details, credentials, or nonpublic data.
    log_report = {k: report[k] for k in (
        'time_utc', 'sources_configured', 'indexes_opened',
        'detail_pages_checked', 'successful_details', 'rejected_details',
        'new_preliminary_signals', 'confirmed_qualified_orders',
        'messages_sent', 'scan_healthy', 'coverage_complete', 'skipped_recent', 'rejection_counts', 'source_stats',
        'history_status')}
    if report.get('replayed_report'):
        log_report['replayed_report'] = True
    log_report['items'] = [{k: row.get(k) for k in (
        'url', 'relevance', 'deadline_dates', 'new_since_previous_run',
        'eligible_preliminary', 'buyer_and_live_need_confirmed', 'details_changed', 'reactivated',
        'publication_date', 'first_seen_utc', 'observed_utc', 'source_card_format')} for row in report['items']]
    log_report['manual_review_count'] = report['manual_review_count']
    log_report['manual_review_queue'] = report['manual_review_queue']
    log_report['subject_review_queue'] = report.get('subject_review_queue', [])
    log_report['subject_review_count'] = report.get('subject_review_count', 0)
    log_report['qualification_queue'] = report['qualification_queue']
    log_report['qualification_requires'] = report['qualification_requires']
    log_report['changed_cards'] = report['changed_cards']
    log_report['fallback_required'] = report['fallback_required']
    log_report['errors'] = [{'source': e['source'], 'error': e['error']} for e in report['errors']]
    print('AMAPS_REPORT_V1 ' + json.dumps(log_report, ensure_ascii=False, separators=(',', ':')), flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as s:
            s.write('## АМАПС — поиск открытого прямого спроса\n\n')
            new_display = report['new_preliminary_signals']
            if new_display is None:
                new_display = 'Н/Д'
            if report.get('replayed_report'):
                s.write(f"Восстановлен отчёт цикла {report['time_utc']}.\n\n")
            s.write(f"Открыто индексов: {report['indexes_opened']} из {report['sources_configured']}. "
                    f"Попыток загрузки карточек: {report['detail_pages_checked']}. "
                    f"Новых предварительных сигналов: {new_display}. ")
            s.write('Квалифицированных заказов без ручной проверки: **0**. Сообщений не отправлялось.\n\n')
            s.write('Вложение amaps-public-monitor содержит проверенные URL и причины ошибок.\n')
            for row in report['items'][:8]:
                s.write('- ' + row['url'] + '\n')
    write_atomic(STATE, state)
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
