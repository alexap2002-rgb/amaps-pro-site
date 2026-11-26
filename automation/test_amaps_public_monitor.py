import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfoNotFoundError

import amaps_public_monitor as monitor


BASE = 'https://synthetic.example/orders'
CARD = BASE + '/1'
PATTERN = r'^/orders/\d+$'
DESCRIPTION = '<p>Описание заказа</p><p>Требуется изготовление корпуса Д16Т</p>'
LABEL = '<p>Окончание приёма заявок: '


def card(deadline='30 ноября 2099'):
    return DESCRIPTION + LABEL + deadline + '</p>'


def index(*urls):
    return ''.join(f'<a href="{url}">Синтетическая карточка</a>' for url in urls)


class CardTests(unittest.TestCase):
    def parse(self, html):
        return monitor.rfq_details(CARD, html, dt.date(2026, 11, 25))

    def test_civil_scope_with_secondary_process(self):
        parsed = self.parse(card().replace('Д16Т', 'Д16Т для пищевого оборудования, затем сварка'))
        self.assertTrue(parsed['eligible_preliminary'])

    def test_known_format_title_is_only_a_hint_and_unclear_end_use_is_excluded(self):
        date = dt.date(2026, 11, 25)
        parsed = monitor.rfq_details(CARD, card().replace('Д16Т', ''), date, 'Корпус Д16Т')
        self.assertIn('title_profile_hint', parsed['rejection_reasons'])
        self.assertFalse(parsed['eligible_preliminary'])
        restricted = self.parse(card() + '<p>Назначение: не указано</p>')
        self.assertIn('restricted_end_use', restricted['rejection_reasons'])
        self.assertFalse(restricted['eligible_preliminary'])

    def test_unverified_source_profile_and_attachment_only_signal(self):
        parsed = monitor.rfq_details(
            'https://energybase.ru/tender/synthetic',
            '<p>Предмет закупки</p><p>Требуется изготовление корпуса</p>'
            + '<a href="/drawing.pdf">Корпус Д16Т</a>' + LABEL + '30.11.2026</p>',
            dt.date(2026, 11, 25))
        self.assertFalse(parsed['eligible_preliminary'])
        self.assertIn('source_format_unverified', parsed['rejection_reasons'])
        self.assertIn('attachment_profile_hint', parsed['rejection_reasons'])
        self.assertFalse(parsed['aluminum_or_edm_in_order_description'])
        self.assertEqual(parsed['source_card_format'], 'Н/Д')

    def test_explicit_dates_and_unrelated_dates(self):
        for date in ('30 ноября 2026', '30.11.2026', '2026-11-30'):
            with self.subTest(date=date):
                result = self.parse('<p>Опубликовано 20.11.2026</p>' + card(date))
                self.assertTrue(result['eligible_preliminary'])
                self.assertFalse(result['buyer_and_live_need_confirmed'])
                self.assertEqual(result['deadline_dates'], ['2026-11-30'])
        self.assertFalse(self.parse(DESCRIPTION + '<p>Дата публикации: 30.11.2026</p>')['eligible_preliminary'])
        for date in ('20 ноября 2026', '25 ноября 2026', '31 ноября 2026', '30.11',
                     'уточняется, опубликовано 30.11.2026'):
            with self.subTest(date=date):
                self.assertFalse(self.parse(card(date))['eligible_preliminary'])

    def test_conflicting_labels_and_ambiguous_cards(self):
        original = card('30.11.2026')
        for html in (original + LABEL + '01.12.2026</p>', original + LABEL + 'уточняется</p>',
                     card('30.11.2026 или 01.12.2026'), original + DESCRIPTION):
            with self.subTest(html=html):
                self.assertFalse(self.parse(html)['eligible_preliminary'])
        self.assertTrue(self.parse(original + LABEL + '30 ноября 2026</p>')['eligible_preliminary'])

    def test_metal_card_dates_and_conflicts(self):
        url = 'https://metalloobrabotchiki.ru/orders/900000000001'
        header = '<p>Заказ №900000000001 от 24.11.2026</p>'
        body = DESCRIPTION + '<p>Количество 2</p><p>Цена</p><p>Сбор КП по: 30.11.2026</p>'
        now = dt.date(2026, 11, 25)
        parsed = monitor.rfq_details(url, header + body, now)
        self.assertEqual(parsed['publication_date'], '2026-11-24')
        self.assertEqual(parsed['deadline_dates'], ['2026-11-30'])
        self.assertTrue(parsed['eligible_preliminary'])
        for bad_header in (header.replace('24.11', '31.11'), header.replace('24.11', '01.12'),
                           header + '<p>Опубликовано 20.11.2026</p>'):
            with self.subTest(header=bad_header):
                parsed = monitor.rfq_details(url, bad_header + body, now)
                self.assertIsNone(parsed['publication_date'])
                self.assertEqual(parsed['deadline_dates'], [])
                self.assertIn('publication_date_conflict', parsed['technical_reasons'])
        listing = monitor.rfq_details(url.rsplit('/', 1)[0], header + body, now)
        self.assertFalse(listing['eligible_preliminary'])
        self.assertEqual(listing['deadline_dates'], [])
        conflict = monitor.rfq_details(url, header + body + LABEL + '20.11.2026</p>', now)
        self.assertNotIn('deadline_expired', conflict['commercial_reasons'])

    def test_description_boundary_closure_and_restricted_use(self):
        html = '<p>Описание заказа</p><p>Статус: открыт</p><p>Требуется корпус Д16Т</p>' + LABEL + '30.11.2026</p>'
        self.assertFalse(self.parse(html)['eligible_preliminary'])
        self.assertFalse(self.parse(DESCRIPTION + '<p>Похожие заказы</p>' + LABEL + '30.11.2026</p>')['eligible_preliminary'])
        self.assertIn('closed', self.parse(card() + '<p>Заказ отменён</p>')['rejection_reasons'])
        self.assertIn('restricted_end_use', self.parse(card() + '<p>Применение двойного назначения</p>')['rejection_reasons'])


class StructuredSourceTests(unittest.TestCase):
    CEH = monitor.SOURCES[2][1] + '/synthetic0001'
    TOMSK = monitor.SOURCES[3]
    TENDER = '/tender/00000000-0000-4000-8000-000000000001'
    ANNOUNCED = '<p>Дата объявления: 24 ноября 2099 г., 09:10</p>'
    DEADLINE = '<p>Дата и время окончания подачи заявок: 30 ноября 2099 г., 12:45</p>'
    TABLE = ('<table><caption>Производственные заказы (2099)</caption>'
             '<tr><th>Номер</th><th>Предмет</th></tr>{}</table>')

    def ceh(self, material, extra=''):
        html = ('<article><h1>Корпус из алюминия</h1>'
                '<section><h2>ИЗДЕЛИЕ И ЗАДАЧА</h2><p>Требуется изготовление корпуса</p></section>'
                f'<section><h2>МАТЕРИАЛ И ОБЪЁМ</h2><p>Материал: {material}</p>'
                '<p>Объём: 10 шт.</p></section>'
                '<section><h2>ТРЕБОВАНИЯ И ОСОБЕННОСТИ</h2><p>Синтетический маркер</p></section>'
                '<section><h2>ОЖИДАНИЯ ОТ ИСПОЛНИТЕЛЯ В ОТКЛИКЕ</h2><p>Указать срок</p></section>'
                '<p>Приём заявок до: 30.11.2099</p><p>Создан: 24.11.2099</p>'
                f'{extra}</article>')
        return monitor.rfq_details(self.CEH, html, dt.date(2099, 11, 25), 'Корпус из алюминия')

    def test_ceh_uses_material_field_and_keeps_card_unverified(self):
        matching = self.ceh('Д16Т')
        self.assertTrue(matching['structured_sections_detected'])
        self.assertTrue(matching['aluminum_or_edm_in_order_description'])
        self.assertEqual(matching['source_card_format'], 'Н/Д')
        self.assertEqual(matching['deadline_dates'], [])
        self.assertIsNone(matching['publication_date'])
        self.assertEqual(matching['structured_date_labels'], ['created', 'submission_deadline'])
        self.assertEqual(matching['commercial_decision'], 'not_accepted')
        self.assertFalse(matching['buyer_and_live_need_confirmed'])
        self.assertFalse(matching['eligible_preliminary'])
        conflict = self.ceh('пластик')
        self.assertFalse(conflict['aluminum_or_edm_in_order_description'])
        self.assertIn('conflicting_material', conflict['rejection_reasons'])
        mixed = self.ceh('Д16Т или пластик')
        self.assertFalse(mixed['aluminum_or_edm_in_order_description'])
        self.assertIn('conflicting_material', mixed['rejection_reasons'])

    def test_ceh_refuses_duplicate_sections_and_restricted_scope(self):
        duplicate = self.ceh('Д16Т', '<section><h2>МАТЕРИАЛ И ОБЪЁМ</h2>'
                             '<p>Материал: пластик</p></section>')
        self.assertFalse(duplicate['structured_sections_detected'])
        self.assertFalse(duplicate['aluminum_or_edm_in_order_description'])
        restricted = self.ceh('Д16Т', '<p>Назначение: не указано</p>'
                              '<p>Контакты заказчика скрыты</p>')
        self.assertIn('restricted_end_use', restricted['rejection_reasons'])
        self.assertIn('contact_hidden', restricted['rejection_reasons'])

    def test_tomsk_rows_have_bounded_redacted_evidence(self):
        def diagnose(subject, count):
            rows = ''.join(f'<tr><td>{i}</td><td>{subject}</td></tr>' for i in range(count))
            return monitor.index_observations('tomsk_subcontract', self.TABLE.format(rows))['tomsk_catalog']

        result = diagnose('SYNTHETIC_PRIVATE  предмет', 25)
        self.assertEqual(result['status'], 'Н/Д')
        self.assertTrue(result['sample_truncated'])
        self.assertEqual(len(result['sample']), 20)
        row = result['sample'][0]
        self.assertEqual(row['row_position'], 1)
        self.assertEqual(len(row['fields_sha256']), 64)
        self.assertEqual(row, diagnose('synthetic_private предмет', 1)['sample'][0])
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))
        self.assertIn('Предмет', result['manual_check'])

    def test_energybase_requires_one_unique_link_and_both_dates_in_one_block(self):
        link = f'<a href="{self.TENDER}">Синтетический предмет</a>'
        complete = f'<article>{link}{self.ANNOUNCED}{self.DEADLINE}</article>'
        row = (f'<table><tr><td>{link}</td><td>{self.ANNOUNCED}</td>'
               f'<td>{self.DEADLINE}</td></tr></table>')
        split = f'<article>{link}{self.ANNOUNCED}</article><article>{self.DEADLINE}</article>'
        duplicate = complete + f'<article>{link}</article>'
        nested = f'<article>{link}{self.ANNOUNCED}<article>{self.DEADLINE}</article></article>'
        for html, expected in ((complete, 1), (row, 1), (split, None),
                               (duplicate, None), (nested, None)):
            with self.subTest(html=html):
                stats = monitor.index_observations('energybase_parts', html)
                self.assertEqual(stats['energybase_index_date_pairs'], expected)


class AccessTests(unittest.TestCase):
    def test_robots_rules_and_failed_fetch(self):
        with patch.object(monitor.urllib.request, 'build_opener') as build:
            opener = build.return_value
            response = opener.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'User-agent: *\nDisallow: /orders/1\n'
            cache = {}
            self.assertTrue(monitor.robots_allowed(BASE, cache))
            self.assertFalse(monitor.robots_allowed(CARD, cache))
            opener.open.assert_called_once()
            self.assertEqual(opener.open.call_args.args[0].full_url, 'https://synthetic.example/robots.txt')
            opener.open.side_effect = OSError('offline')
            self.assertFalse(monitor.robots_allowed(BASE, {}))

    def test_redirect_is_not_followed(self):
        self.assertIsNone(monitor.NoRedirect().redirect_request(
            MagicMock(), None, 302, 'Found', {}, 'https://other.example/orders/1',
        ))


class CycleTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.state = Path(directory) / 'state.json'
        self.output = Path(directory) / 'report.json'
        self.pages = {BASE: index(CARD), CARD: card()}
        self.sources = [('synthetic', BASE, PATTERN)]
        self.fetch = self.enterContext(patch.object(monitor, 'get', side_effect=self.pages.__getitem__))
        self.robots = self.enterContext(patch.object(monitor, 'robots_allowed', return_value=True))
        self.enterContext(patch.object(monitor.urllib.request, 'build_opener', side_effect=AssertionError('Network')))
        self.enterContext(patch.object(monitor, 'STATE', self.state))
        self.enterContext(patch.object(monitor, 'OUTPUT', self.output))
        self.enterContext(patch.object(monitor, 'SOURCES', self.sources))
        self.enterContext(patch.object(monitor.time, 'sleep'))
        self.epoch = self.enterContext(patch.object(monitor.time, 'time', return_value=100_000))
        self.enterContext(patch.dict(os.environ, GITHUB_STEP_SUMMARY=''))

    def cycle(self, expected_code=0):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(monitor.run(now=dt.date(2026, 11, 25)), expected_code)
        report = json.loads(self.output.read_text(encoding='utf-8'))
        line = next(line for line in stdout.getvalue().splitlines() if line.startswith('AMAPS_REPORT_V1 '))
        log = json.loads(line.split(' ', 1)[1])
        for key in ('manual_review_queue', 'fallback_required', 'history_status', 'changed_cards',
                    'source_stats', 'new_preliminary_signals'):
            self.assertEqual(log[key], report[key])
        self.log_line = line
        return report

    def test_known_card_retried_after_it_disappears_from_index(self):
        self.cycle()
        self.epoch.return_value += 100_000
        del self.pages[CARD]
        self.cycle(2)
        other = BASE + '/2'
        self.pages.update({BASE: index(other), other: card(), CARD: card()})
        report = self.cycle()
        self.assertEqual({row['url'] for row in report['items']}, {CARD, other})

    def test_report_failure_replays_new_signal_and_review(self):
        self.cycle()
        new, review = BASE + '/2', BASE + '/3'
        self.pages.update({BASE: index(CARD, new, review), new: card(), review: DESCRIPTION})
        write = monitor.write_atomic

        def fail_report(path, data):
            if path == self.output:
                raise OSError('synthetic interruption')
            write(path, data)

        with patch.object(monitor, 'write_atomic', side_effect=fail_report):
            with self.assertRaises(OSError):
                monitor.run(now=dt.date(2026, 11, 25))
        self.fetch.reset_mock()
        report = self.cycle(2)
        self.fetch.assert_not_called()
        self.assertTrue(report['replayed_report'])
        self.assertEqual(report['new_preliminary_signals'], 1)
        self.assertEqual(report['manual_review_queue'][0]['url'], review)
        self.assertIn(new, {row['url'] for row in report['qualification_queue']})
        self.assertNotIn('document_links', self.state.read_text(encoding='utf-8'))

    def test_slow_source_yields_budget_and_rotates(self):
        late = 'https://late.example/orders'
        urls = [f'{BASE}/{n}' for n in range(1, 17)]
        self.sources.append(('late', late, PATTERN))
        self.pages.update(dict.fromkeys([*urls, late + '/1'], card()))
        self.pages.update({BASE: index(*urls), late: index(late + '/1')})
        tick = 0

        def fetch(url):
            nonlocal tick
            if url in urls:
                tick += 80
            return self.pages[url]

        self.fetch.side_effect = fetch
        with patch.object(monitor.time, 'monotonic', side_effect=lambda: tick):
            report = self.cycle(2)
            self.assertEqual(report['source_stats']['synthetic']['remaining_queue'], 15)
            self.assertEqual(report['source_stats']['late']['checked'], 1)
            self.fetch.reset_mock()
            second = self.cycle(2)
            self.assertEqual(self.fetch.call_args_list[0].args[0], late)
            self.assertEqual(second['source_stats']['late']['coverage_age_seconds'], 0)

    def test_queued_mirror_is_attempted_once_per_run(self):
        self.sources.clear()
        for host in ('https://metalloobrabotchiki.ru', 'https://msk.metalloobrabotchiki.ru'):
            base = host + '/orders'
            self.sources.append((host, base, PATTERN))
            self.pages[base] = index(base + '/7')
        report = self.cycle(2)
        self.assertEqual(report['detail_pages_checked'], 1)
        self.assertEqual(len(report['fallback_required']), 2)

    def test_second_mirror_can_resolve_queue_left_by_first(self):
        self.sources.clear()
        for host in ('https://metalloobrabotchiki.ru', 'https://msk.metalloobrabotchiki.ru'):
            base = host + '/orders'
            self.sources.append((host, base, PATTERN))
            self.pages.update({base: index(base + '/7'), base + '/7': '<p>Заказ №7</p>' + card()})
        tick = 0
        (first_key, first, _), (second_key, _, _) = self.sources

        def fetch(url):
            nonlocal tick
            if url == first:
                tick += 80
            return self.pages[url]

        self.fetch.side_effect = fetch
        with patch.object(monitor.time, 'monotonic', side_effect=lambda: tick):
            report = self.cycle()
        self.assertEqual(report['source_stats'][first_key]['remaining_queue'], 0)
        self.assertEqual(report['source_stats'][second_key]['checked'], 1)

    def test_current_index_does_not_refresh_card_evidence(self):
        self.pages[CARD] = '<p>Опубликовано 20.11.2026</p>' + card()
        first = self.cycle()
        item = first['items'][0]
        self.assertEqual(item['publication_date'], '2026-11-20')
        self.assertEqual(item['first_seen_utc'], first['source_stats']['synthetic']['last_successful_coverage_utc'])
        self.epoch.return_value += 3600
        cached = self.cycle()
        self.assertEqual(cached['detail_pages_checked'], 0)
        self.assertEqual(cached['source_stats']['synthetic']['coverage_age_seconds'], 3600)
        del self.pages[BASE]
        self.epoch.return_value += 3601
        unavailable = self.cycle(2)
        self.assertEqual(unavailable['source_stats']['synthetic']['coverage_age_seconds'], 7201)
        self.assertIsNone(unavailable['source_stats']['synthetic']['remaining_queue'])

    def test_changes_closure_and_reactivation_at_same_url(self):
        self.assertFalse(self.cycle()['items'][0]['new_since_previous_run'])
        self.epoch.return_value += 100_000
        self.pages[CARD] = card().replace('корпуса', 'крышки')
        updated = self.cycle()['items'][0]
        self.assertTrue(updated['details_changed'])
        self.assertFalse(updated['new_since_previous_run'])
        self.epoch.return_value += 100_000
        self.pages[CARD] += '<p>Заказ закрыт</p>'
        closed = self.cycle()
        self.assertEqual(closed['items'], [])
        self.assertFalse(closed['changed_cards'][0]['eligible_preliminary'])
        self.epoch.return_value += 100_000
        self.pages[CARD] = card()
        reopened = self.cycle()['items'][0]
        self.assertTrue(reopened['reactivated'])
        self.assertFalse(reopened['new_since_previous_run'])

    def test_mirror_deduplicated(self):
        for scheme_host in ('https://metalloobrabotchiki.ru', 'https://msk.metalloobrabotchiki.ru'):
            url = scheme_host + '/orders'
            self.sources.append((scheme_host, url, PATTERN))
            self.pages[url] = index(url + '/7', url + '/7?trace=1')
            self.pages[url + '/7'] = '<p>Заказ №7</p>' + card()
        self.sources.pop(0)
        report = self.cycle()
        self.assertEqual(len(report['items']), 1)
        self.assertEqual(report['successful_details'], 1)
        self.assertEqual(report['fallback_required'], [])

    def test_unavailable_source_and_detail_error_are_distinct(self):
        self.sources.append(('unavailable', 'https://unavailable.example/orders', PATTERN))
        self.robots.side_effect = lambda url, cache: 'unavailable' not in url
        del self.pages[CARD]
        report = self.cycle(2)
        self.assertEqual({entry['source']: entry['reasons'] for entry in report['fallback_required']},
                         {'unavailable': ['source_unavailable'], 'synthetic': ['detail_error']})
        self.assertIsNone(report['new_preliminary_signals'])
        self.assertNotIn('https://unavailable.example/orders', [call.args[0] for call in self.fetch.call_args_list])

    def test_catalog_self_links_do_not_become_orders(self):
        pattern = r'^/orders/[^#?]*$'
        html = index(BASE + '/', BASE + '/?page=2', CARD)
        self.assertEqual([url for url, _ in monitor.find_links(html, BASE + '/', pattern)], [CARD])

    def test_old_catalog_entry_is_removed_without_losing_real_cards(self):
        catalog = BASE + '/'
        self.sources[:] = [('synthetic', catalog, r'^/orders/[^#?]*$')]
        self.pages[catalog] = index(catalog, CARD)
        self.cycle()
        saved = json.loads(self.state.read_text(encoding='utf-8'))
        saved['seen_urls'].append(catalog)
        saved['checked_urls'][catalog] = dict(saved['checked_urls'][CARD],
                                              review_reasons=['order_scope_unrecognized'],
                                              format_gap=True)
        saved['pending_urls'][catalog] = ['synthetic', 0, None, False]
        saved['first_seen'][catalog] = 100000
        self.state.write_text(json.dumps(saved), encoding='utf-8')
        self.fetch.reset_mock()
        report = self.cycle()
        self.assertEqual(report['source_stats']['synthetic']['links_found'], 1)
        self.assertEqual(report['manual_review_queue'], [])
        self.fetch.assert_called_once_with(catalog)
        restored = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertIn(CARD, restored['checked_urls'])
        for key in ('seen_urls', 'checked_urls', 'pending_urls', 'first_seen'):
            self.assertNotIn(catalog, restored[key])

    def test_unknown_index(self):
        self.pages[BASE] = '<p>Неизвестный формат списка</p>'
        report = self.cycle(2)
        self.assertEqual(report['fallback_required'][0]['reasons'], ['unknown_index_format'])
        self.assertIsNone(report['source_stats']['synthetic']['new_publications'])

    def test_queue_limit_then_next_cycle(self):
        urls = [f'{BASE}/{number}' for number in range(1, 18)]
        self.pages[BASE] = index(*urls)
        self.pages.update(dict.fromkeys(urls, card()))
        stats = self.cycle(2)['source_stats']['synthetic']
        self.assertEqual(stats['remaining_queue'], 1)
        self.assertEqual(stats['coverage_gaps'], ['queue_remaining'])
        self.assertIsNone(stats['new_publications'])
        report = self.cycle()
        self.assertEqual(report['detail_pages_checked'], 1)
        self.assertFalse(report['items'][0]['new_since_previous_run'])

    def test_review_queue_survives_cache_and_unavailable_index(self):
        self.pages[CARD] = DESCRIPTION
        first = self.cycle(2)
        self.assertEqual(first['manual_review_count'], 1)
        cached = self.cycle(2)
        self.assertEqual(cached['detail_pages_checked'], 0)
        self.assertEqual(cached['manual_review_queue'], first['manual_review_queue'])
        original_index = self.pages.pop(BASE)
        self.assertEqual(self.cycle(2)['manual_review_queue'], first['manual_review_queue'])
        self.pages[BASE] = original_index
        self.pages[CARD] = card()
        self.epoch.return_value += 100_000
        report = self.cycle()
        self.assertEqual(report['manual_review_queue'], [])
        self.assertFalse(report['items'][0]['new_since_previous_run'])

    def test_separate_decisions_and_queue_lifecycle(self):
        hidden, expired, irrelevant, unknown, subject = (BASE + '/' + str(n) for n in range(2, 7))
        lock = '<p>Контакты заказчика: подключите подходящий «Тариф» или получите «Разовый доступ»</p>'
        civilian = DESCRIPTION.replace('корпуса Д16Т', 'детали для пищевого оборудования методом фрезерования')
        self.pages.update({
            BASE: index(CARD, hidden, expired, irrelevant, unknown, subject),
            hidden: card() + lock,
            expired: card('20.11.2026'),
            irrelevant: card().replace('корпуса Д16Т', 'стальной пружины'),
            unknown: DESCRIPTION + lock,
            subject: civilian,
        })
        first = self.cycle(2)
        self.assertEqual({row['url'] for row in first['manual_review_queue']}, {unknown, subject})
        self.assertEqual([row['url'] for row in first['subject_review_queue']], [subject])
        for row in first['manual_review_queue']:
            self.assertEqual(row['reasons'], ['deadline_unrecognized'])
            self.assertEqual(len(row['evidence_sha256']), 64)
        saved = json.loads(self.state.read_text(encoding='utf-8'))['checked_urls']
        self.assertEqual(saved[hidden]['commercial_reasons'], ['contact_hidden'])
        self.assertEqual(saved[hidden]['commercial_decision'], 'not_accepted')
        self.assertIn('открытый маршрут не установлен', saved[hidden]['status'])
        self.pages.pop(BASE)
        cached = self.cycle(2)
        for field in ('manual_review_queue', 'subject_review_queue'):
            self.assertEqual(cached[field], first[field])
        self.pages.update({BASE: index(CARD, subject), subject: civilian + '<p>Назначение: не указано</p>'})
        self.epoch.return_value += 7201
        updated = self.cycle(2)
        self.assertEqual(updated['subject_review_queue'], [])
        self.assertIn(subject, {row['url'] for row in updated['manual_review_queue']})

    def test_cached_rejection_does_not_refresh_sample_or_card(self):
        self.pages[CARD] = card() + '<p>Контакты заказчика скрыты</p>'
        first = self.cycle()['source_stats']['synthetic']
        self.assertEqual(first['unknown_details'], 0)
        self.epoch.return_value += 7201
        self.fetch.reset_mock()
        stats = self.cycle(2)['source_stats']['synthetic']
        self.fetch.assert_called_once_with(BASE)
        self.assertEqual(stats['index_age_seconds'], 0)
        self.assertEqual(stats['fresh_card_checks'], 0)
        self.assertEqual(stats['oldest_card_age_seconds'], 7201)
        self.assertEqual(stats['coverage_age_seconds'], 7201)
        self.assertEqual(stats['cached_commercial_rejections'], 1)
        self.assertIn('coverage_older_than_two_hours', stats['coverage_gaps'])

    def test_energybase_index_labels_stay_on_index(self):
        base = 'https://energybase.ru/tender/catalog/parts-manufacturing'
        url = 'https://energybase.ru/tender/00000000-0000-4000-8000-000000000000'
        self.sources[:] = [('energybase_parts', base, r'^/tender/[0-9a-f-]{20,}$')]
        self.pages.update({base: index(url) + '<p>Дата объявления 24 ноября 2026 г., 09:10</p>'
                           '<p>Дата и время окончания подачи заявок 30 ноября 2026 г., 12:45</p>'
                           '<p>Дата объявления 31 ноября 2026 г., 09:10</p>'
                           '<p>Дата и время окончания подачи заявок 30 ноября 2026 г., 25:00</p>',
                           url: card()})
        report = self.cycle(2)
        stats = report['source_stats']['energybase_parts']
        self.assertEqual(stats['announced_dates_recognized'], 1)
        self.assertEqual(stats['submission_deadlines_recognized'], 1)
        self.assertEqual(report['items'], [])
        self.assertIn('source_format_unverified', report['manual_review_queue'][0]['reasons'])
        saved = json.loads(self.state.read_text(encoding='utf-8'))['checked_urls'][url]
        self.assertIsNone(saved['publication_date'])
        self.assertEqual(saved['deadline_dates'], [])

    def test_tomsk_catalog_sample_does_not_create_orders_or_publish_content(self):
        key, base, pattern = StructuredSourceTests.TOMSK
        self.sources[:] = [(key, base, pattern)]
        self.pages[base] = (f'<a href="{base}?synthetic=1">Каталог</a>'
                            + StructuredSourceTests.TABLE.format(
                                '<tr><td>43210</td><td>SYNTHETIC_PRIVATE</td></tr>'))
        report = self.cycle(2)
        stats = report['source_stats'][key]
        self.fetch.assert_called_once_with(base)
        self.assertEqual(len(stats['tomsk_catalog']['sample']), 1)
        self.assertIsNone(stats['new_publications'])
        self.assertIsNone(stats['first_observed_urls'])
        self.assertIsNone(report['new_preliminary_signals'])
        self.assertEqual(report['items'], [])
        self.assertEqual(report['manual_review_queue'], [])
        for payload in (self.log_line, self.output.read_text(encoding='utf-8'),
                        self.state.read_text(encoding='utf-8')):
            self.assertNotIn('SYNTHETIC_PRIVATE', payload)
            self.assertNotIn('43210', payload)

    def test_dynamic_index_does_not_claim_coverage(self):
        self.sources[:] = [('ceh', BASE, PATTERN)]
        self.pages[BASE] = '<p>Загрузка...</p><p>Справочный текст синтетической витрины</p>'
        stats = self.cycle(2)['source_stats']['ceh']
        self.assertTrue(stats['dynamic_index_placeholder'])
        self.assertIn('dynamic_index_unverified', stats['coverage_gaps'])
        self.assertIsNone(stats['unknown_details'])
        self.assertIsNone(stats['fresh_card_checks'])

    def test_public_outputs_only_keep_safe_evidence(self):
        markers = ('SYNTHETIC_DESCRIPTION', 'SYNTHETIC_BUYER', 'SYNTHETIC_CONTACT',
                   'SYNTHETIC_DOCUMENT', 'SYNTHETIC_TOKEN')
        description, buyer, contact, document, token = markers
        second = BASE + '/2'
        self.pages[BASE] = index(CARD) + f'<a href="{second}?token={token}">{buyer}</a>'
        html = card() + (f'<p>{description}</p><p>Контакты заказчика {buyer} {contact}</p>'
                         f'<a href="/{document}.pdf?token={token}">{document}</a>')
        self.pages.update({CARD: html, second: html + '<p>Контакты заказчика скрыты</p>'})
        summary = self.output.with_name('summary.md')
        with patch.dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)):
            self.cycle()
        for payload in (self.state.read_text(encoding='utf-8'), self.output.read_text(encoding='utf-8'),
                        self.log_line, summary.read_text(encoding='utf-8')):
            for marker in markers:
                self.assertNotIn(marker, payload)
        self.assertIn(second, [call.args[0] for call in self.fetch.call_args_list])

    def test_invalid_state_rebuilds_without_claiming_new_signals(self):
        for content in ('{broken', '{"parser_revision":4,"seen_urls":[],"checked_urls":{"x":null},"source_baselines":[]}'):
            with self.subTest(content=content):
                self.state.write_text(content, encoding='utf-8')
                report = self.cycle()
                self.assertEqual(report['history_status'], 'invalid')
                self.assertIsNone(report['new_preliminary_signals'])
                self.assertFalse(report['items'][0]['new_since_previous_run'])

    def test_migration_and_new_index_observation(self):
        self.state.write_text(json.dumps({
            'parser_revision': 3, 'seen_urls': [], 'checked_urls': {CARD: {'title': 'legacy'}},
        }), encoding='utf-8')
        self.assertEqual(self.cycle()['history_status'], 'migrated')
        saved = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(saved['parser_revision'], 5)
        self.assertNotIn('title', saved['checked_urls'][CARD])
        second = BASE + '/2'
        self.pages[BASE] = index(CARD, second)
        self.pages[second] = card()
        report = self.cycle()
        self.assertEqual(report['new_preliminary_signals'], 1)
        self.assertIsNone(report['source_stats']['synthetic']['new_publications'])
        self.assertEqual(report['source_stats']['synthetic']['first_observed_urls'], 1)

    def test_profile_priority_and_rotation_with_more_than_sixteen_cards(self):
        self.cycle()
        ordinary = [f'{BASE}/{number}' for number in range(2, 19)]
        profile = BASE + '/19'
        self.pages.update(dict.fromkeys([*ordinary, profile], card()))
        self.pages[BASE] = index(CARD, *ordinary) + (
            f'<a href="{profile}">Фрезеровка алюминиевого корпуса и крышки, электроэрозия</a>')
        self.fetch.reset_mock()
        report = self.cycle(2)
        self.assertEqual(self.fetch.call_args_list[1].args[0], profile)
        self.assertEqual(report['source_stats']['synthetic']['first_observed_urls'], 18)
        self.assertEqual(report['source_stats']['synthetic']['remaining_queue'], 2)
        # Ссылки из остатка исчезают с индекса, но сохраняются до оценки.
        self.pages[BASE] = index(CARD)
        report = self.cycle()
        self.assertEqual({row['url'] for row in report['items']}, set(ordinary[-2:]))
        self.assertEqual(report['new_preliminary_signals'], 2)
        self.assertEqual(report['source_stats']['synthetic']['first_observed_urls'], 0)
        self.assertEqual(self.cycle()['new_preliminary_signals'], 0)

    def test_failed_first_fetch_keeps_only_safe_pending_state(self):
        self.cycle()
        second = BASE + '/2'
        marker = 'synthetic-private-marker'
        self.pages[BASE] = index(CARD) + f'<a href="{second}?secret={marker}">{marker}</a>'
        failed = self.cycle(2)
        self.assertEqual(failed['source_stats']['synthetic']['first_observed_urls'], 1)
        self.assertIsNone(failed['source_stats']['synthetic']['new_publications'])
        self.assertIsNone(failed['new_preliminary_signals'])
        self.assertIs(failed['fallback_required'][0]['verification_performed'], False)
        self.assertEqual(failed['confirmed_qualified_orders'], 0)
        saved = self.state.read_text(encoding='utf-8')
        self.assertNotIn(marker, saved)
        self.assertNotIn(marker, self.log_line)
        self.assertIn(second, json.loads(saved)['pending_urls'])
        self.pages[BASE] = index(CARD)
        self.pages[second] = card()
        restored = self.cycle()
        self.assertEqual(restored['source_stats']['synthetic']['first_observed_urls'], 0)
        self.assertEqual(restored['new_preliminary_signals'], 1)

    def test_lost_history_and_v4_pending_baseline_do_not_create_new_orders(self):
        self.cycle()
        self.state.unlink()
        del self.pages[CARD]
        missing = self.cycle(2)
        self.assertIsNone(missing['source_stats']['synthetic']['first_observed_urls'])
        self.pages[CARD] = card()
        self.assertFalse(self.cycle()['items'][0]['new_since_previous_run'])
        self.state.write_text(json.dumps({
            'parser_revision': 4, 'seen_urls': [CARD], 'checked_urls': {},
            'source_baselines': ['synthetic'],
        }), encoding='utf-8')
        migrated = self.cycle()
        self.assertEqual(migrated['source_stats']['synthetic']['first_observed_urls'], 0)
        self.assertFalse(migrated['items'][0]['new_since_previous_run'])


class MoscowTodayTests(unittest.TestCase):
    def test_available_zone_preserves_previous_date_calculation(self):
        instant = dt.datetime(2026, 11, 25, 21, tzinfo=dt.timezone.utc)
        moscow = dt.timezone(dt.timedelta(hours=3), 'Europe/Moscow')
        expected = instant.astimezone(moscow).date()

        with patch.object(monitor, 'ZoneInfo', return_value=moscow) as zone_info:
            with patch.object(monitor.dt, 'datetime') as clock:
                clock.now.side_effect = instant.astimezone
                self.assertEqual(monitor.moscow_today(), expected)
                self.assertIs(clock.now.call_args.args[0], moscow)

        zone_info.assert_called_once_with('Europe/Moscow')

    def test_missing_zone_uses_moscow_midnight(self):
        cases = (
            (dt.datetime(2026, 11, 25, 20, 59, 59, tzinfo=dt.timezone.utc),
             dt.date(2026, 11, 25)),
            (dt.datetime(2026, 11, 25, 21, tzinfo=dt.timezone.utc),
             dt.date(2026, 11, 26)),
        )
        for instant, expected in cases:
            with self.subTest(instant=instant):
                with patch.object(monitor, 'ZoneInfo', side_effect=ZoneInfoNotFoundError):
                    with patch.object(monitor.dt, 'datetime') as clock:
                        clock.now.side_effect = instant.astimezone
                        self.assertEqual(monitor.moscow_today(), expected)


if __name__ == '__main__':
    unittest.main()
