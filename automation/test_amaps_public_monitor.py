# Независимый архитектурный аудит АМАПС
#
# Краткий вывод
# Оставить один сборщик. Поручить действующему диспетчеру Windows контроль сроков,
# хранить сигналы в локальной транзакционной очереди, а GitHub Actions использовать
# для проверок на синтетике. Первая защищённая комиссия требует подтверждённого
# гражданского спроса, законного канала контакта, оформленной защиты до раскрытия
# и фактического поступления денег. Обнаружение карточки открывает только проверку.
#
# Причины и границы доказательств
# По вводным владельца в регламенте 30 контрольных источников, у сборщика шесть
# конфигураций с зеркалом, доступны пять индексов, полных охватов в последнем
# цикле было ноль. Плановые интервалы доходят до 11,55 часа. Фоновые коммерческие
# задачи прерывают чтение регламента, фоновые записи в CRM заблокированы,
# сквозной приём сайта и почты не доказан, финансовой конверсии нет. Эти данные
# характеризуют эксплуатацию и ничего не говорят о наличии спроса на рынке.
# Локально изучены история, регламент в репозитории, сборщик, тесты, workflow
# и код приёма сайта. Рабочая вершина db159b6 исключает карточки каталогов,
# 59d9ab5 добавляет повторную квалификацию и резервные задания, 423aa7d внедряет
# поисковый модуль. 0be543e4 непосредственно следует за db159b6 и расширяет
# диагностику форматов. Код формы и почтового транспорта устанавливает
# предполагаемый маршрут. Приём, доставка и доступность на практике неизвестны.
# Локальные испытания на Windows с блокировкой сетевых вызовов Python дали
# 35 unittest на рабочей версии и 40 на 0be543e4, плюс по 20 встроенных проверок.
# Указанный владельцем прогон на Linux в этом аудите не повторялся.
#
# Workflow запускает три попытки в час, сериализует выполнения, ограничивает
# задание 12 минутами и вычисляет час UTC при старте. При неполном результате
# скрипт возвращает 2, артефакт сохраняется по условию always(), а маркер
# успешного часа создаётся только после успешных шагов. Задержка запуска
# сдвигает вычисляемый час, а кеш маркера не ведёт учёт пропусков и хранит
# отметку для набора конфигураций сборщика. Даже успешный проход этого набора
# не измеряет охват всех 30 источников. Кеш состояния и артефакт с хранением
# семь дней пригодны для диагностики. Очередь с подтверждением требует
# отдельного долговечного состояния. Текущий JSON уже содержит историю,
# повторные проверки и восстановление неопубликованного отчёта. Это основа
# миграции, а гарантии доставки и согласования решений требуют проверки.
# Регламент нужен при изменении политики. Повторное чтение полного текста
# при каждом старте расходует время и контекст и конкурирует с самим сбором.
# Из утверждённой редакции следует один раз вывести версионированный реестр
# и краткие исполняемые правила, проверять их соответствие при изменении.
#
# Вариант 1. Windows ведёт расписание, сбор и очередь
# Действующий диспетчер запускает один сборщик по короткому такту, например
# раз в 20 минут, с отдельным бюджетом времени на источник. Коммерческая
# работа выполняется отдельно и не занимает интервал сбора. Диспетчер
# контролирует журнал слотов UTC с уникальным ключом «источник + слот»,
# временем старта, завершения и исходом. Источники с наибольшим возрастом
# доказательств получают приоритет. Просроченный слот записывается как
# пропуск, после пробуждения выполняется один свежий проход по долгам,
# повтор слота использует тот же ключ. Время простоя видно после возврата.
# Срок измеряется от последней доказанной полной проверки каждого источника,
# отдельно от последней попытки. Раннее предупреждение на 90 минутах,
# нарушение на 120 минутах. Плановые попытки сами по себе срок не гарантируют.
# GitHub Actions выполняет автономные проверки синтетических фикстур на
# Windows и Linux. Переход на единственного владельца рабочего запуска
# требует будущего отдельного допуска владельца к переключению расписания.
# При выключенном ПК сбор останавливается, поэтому норматив зависит
# от доступности ПК, диспетчера и разрешённого источника.
#
# Вариант 2. GitHub Actions собирает, Windows принимает и контролирует
# Единственный рабочий сбор остаётся в Actions. Windows сверяет ожидаемые
# слоты и принимает только разрешённый минимальный пакет с идентификатором
# запуска, версией схемы и контрольной суммой. Слот без результата остаётся
# открытым до разбирательства. Повторный импорт использует стабильный ключ.
# Передачу можно рассматривать только при подтверждённом разрешённом
# доступе к артефактам, допустимости их содержания и доступности импорта
# до окончания хранения. Возможен подтверждённый ручной импорт разрешённого
# пакета. Отсутствие артефакта, вытеснение очереди или задержка cron создают
# пробел, который Windows отмечает. При выключенном ПК облачный сбор может
# продолжаться, при этом приём откладывается. Появляются дополнительная
# зависимость от GitHub, задержка передачи и риск потери по сроку хранения.
#
# Рекомендуемый вариант и модель данных
# Выбрать вариант 1. Минимум компонентов — действующий диспетчер, один
# сборщик, одна база SQLite из стандартной библиотеки Python и
# интерактивное рассмотрение. CI остаётся контуром верификации.
# Реестр всех 30 источников хранит редакцию правил, соответствие зеркал,
# допустимый способ просмотра, признаки полного охвата, технический статус
# и коммерческую пригодность как разные поля. Индекс открыт, часть карточек
# просмотрена, полная проверка, доступ ограничен и формат не распознан —
# отдельные результаты с причиной, временем и объёмом остатка.
# Полный охват требует доказанной полноты индекса, включая применимую
# пагинацию, и свежих исходов всей очереди в заданном окне. Проверенный
# пустой индекс допустим только при доказуемой полноте и распознанном
# пустом состоянии. Отчитываться отдельно по 30 источникам и по активным
# конфигурациям, учитывая зеркало через основной идентификатор.
# Разрешённый резервный поиск выдаёт задание ручной проверки по источнику,
# причине и интервалу. Уполномоченный оператор может использовать публичный
# поиск или уже разрешённый коннектор в пределах правил площадки. При
# CAPTCHA, запрете robots.txt, платном доступе или иных ограничениях
# фиксируется недоступность. Результат резерва остаётся частичным наблюдением,
# пока каждая часть установленного объёма проверки не подтверждена.
#
# В одной транзакции хранить исход попытки, изменение курсора и событие
# очереди. Стабильный ключ карточки берётся из основного источника и номера
# либо канонического URL, с явным соответствием зеркал. Отдельная версия
# доказательства создаёт событие изменения, повтор обработки даёт тот же ID.
# Независимый потребитель берёт запись с ограниченной по времени арендой.
# Подтверждение приёма означает долговечную запись ID и результата проверки
# у получателя либо фиксацию ручного решения в той же БД. При сбое или
# отсутствии подтверждения аренда истекает и запись доступна снова.
# Повтор доставки допустим, повтор применения предотвращается по ID.
# Закрытие, новое состояние и повторное открытие сохраняются в истории.
# Просроченные и конфликтные записи остаются видимыми в статусе разбора.
#
# Пакет для разрешённой аналитики содержит ID, версию схемы, код источника,
# тип сигнала, время наблюдения, срок, укрупнённый профиль, причины и
# идентификатор локального доказательства. Контакты, тексты карточек,
# чертежи, секреты и идентифицирующие ссылки исключаются из передачи.
# Проверяющий сверяет доказательство отдельно в пределах своих прав и
# фиксирует решение с временем и версией записи. ChatGPT Plus, Codex и
# уже разрешённые коннекторы могут использоваться интерактивно в пределах
# допуска для минимального пакета и синтетики. Работа очереди от них
# независима. CRM служит для ручной регистрации после разрешения.
# При недоступности CRM решение хранится локально, запись остаётся
# ожидающей регистрации до сверки по ID и явного подтверждения.
#
# Порядок внедрения, обратимость и критерии приёмки
# 1. Зафиксировать базовую версию, утвердить реестр и семантику охвата.
#    В синтетической матрице все 30 источников учтены, правила для зеркала,
#    неизвестного формата, ложного пустого индекса, пагинации, отказа и
#    частичного резерва дают ожидаемые статусы. Возврат к прежней редакции
#    реестра сохраняет историю с исходной версией. Риск — трудоёмкая сверка
#    и расхождение правил источника с регламентом.
# 2. Проверить журнал слотов и бюджет на синтетическом времени до переключения
#    рабочего запуска. При нормальной доступности моделируемая проверка
#    укладывается в 120 минут для каждого доступного источника. Сон,
#    пропуск, медленный источник, конкуренция запусков и перезапуск дают
#    видимые пробелы, один логический исход на слот и отсутствие голодания
#    остальных источников. Измерять долю проверок в срок и максимальный
#    возраст, отдельно по доступным, недоступным и ненастроенным источникам.
#    Для отката остановить новый режим до восстановления прежнего запуска,
#    сохранить журнал. Риск — отключение ПК и нехватка пропускной способности.
# 3. Ввести очередь и миграцию из копии старого состояния после проверки
#    формата. Испытать прерывание до и после записи, сбой после приёма до
#    подтверждения, потерю процесса, блокировку БД, переполнение диска,
#    дубли зеркал и простой CRM. Каждый зафиксированный сигнал должен
#    остаться восстанавливаемым и примениться у получателя однократно.
#    Проверить восстановление из согласованной копии БД и сверку журнала
#    без сброса очереди. Откатить обработчик, сохранив БД и
#    подтверждения для сверки. Риски — диск, ошибочная миграция и рост очереди.
# 4. Проверить контракт минимального пакета и маршрут ручного решения
#    на синтетике, включая недопустимые поля, повтор сообщения, потерю
#    подтверждения и недоступность CRM. Запись видна до приёма, после него
#    решение прослеживается, регистрация в CRM учитывается отдельно.
#    Откат — остановка импорта при сохранении очереди. Сквозной приём
#    сайта и почты требует отдельной разрешённой приёмки с подтверждением
#    получения и регистрацией. Данный аудит её не проводит.
# 5. Допустить коммерческую работу только после независимого подтверждения
#    открытой потребности, гражданского назначения, возможности выбора
#    исполнителя, законного контакта и ценности посредничества. Военные,
#    двойные и неясные применения исключаются. Владелец вручную принимает
#    решение по каждой операции. До раскрытия идентификаторов исполнитель
#    получает только допустимый минимум для проверки компетенции, затем
#    проверяется документально оформленная защита конкретной возможности.
#    При отсутствии условий решение останавливается до раскрытия. Код
#    не создаёт согласия, права на контакт, свободного спроса, договорной
#    защиты и оплаты. Факт первой комиссии — подтверждённое поступление
#    по защищённой возможности. Отметить отдельно этапы проверки, допуска,
#    защиты, возникновения права на комиссию и поступления. Раскрытие
#    необратимо, поэтому допуск фиксируется заранее.
#
# Стоимость и работа с Codex
# Для варианта 1 условная стоимость новых лицензий и API равна нулю при
# наличии работающего ПК и текущих инструментов. Плановая оценка для
# прототипа и локальной приёмки — 4–7 рабочих дней разработки и проверки
# плюс труд владельца на источники и коммерческие решения. Для варианта 2
# ориентир 5–9 дней из-за контракта передачи и сверки артефактов. Это оценки,
# а не измерения. В обоих вариантах учитываются время оператора, диск,
# обслуживание и доступность уже предоставленных сервисов и квот.
# Разбить работу для Codex на завершённые участки с закреплённой базой,
# контрактом, синтетическими сценариями отказов и независимой проверкой
# diff и результатов на Windows и Linux. Принимать участок только по
# указанным критериям с одним проверенным способом возврата. Сводить
# дефекты к нарушенному контракту этапа и повторять весь сценарий после
# исправления, включая старое состояние, повтор и восстановление.
#
# Условный допуск изменений 0be543e4
# После независимой регрессии допустимы синтетические тесты и изолированная
# диагностика. Для Томска — ограниченная агрегированная выборка каталога
# без выдачи строки за заказ. Для Energybase — подсчёт пар дат в одном
# однозначном блоке с одной ссылкой, с проверкой дубликатов и конфигурации
# источника. Счётчик остаётся признаком индекса. Для «Цеха» — распознавание
# секций, проверка точного поля материала и конфликтов, с сохранением
# непроверенного статуса. Эту часть принимать отдельно, поскольку она
# влияет на очередь предметной проверки и отпечаток карточки. Проверить
# миграцию старой истории, конфликт материалов, ограниченное назначение,
# ложные обновления, нагрузку HTML и отсутствие утечек в JSON и логах.
# Не переносить служебный .github/amaps-result.json в рабочий контур.
# Ни диагностические счётчики, ни успешная синтетика не подтверждают
# полный охват, допуск к контакту или готовность к развёртыванию.
#

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
