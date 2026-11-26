import datetime as dt
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

import amaps_public_monitor as monitor


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
