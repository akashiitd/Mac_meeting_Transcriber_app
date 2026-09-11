import builtins
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from simple_recorder import cli


class MeetingLoadingTests(unittest.TestCase):
    def test_history_reads_each_file_once_and_sorts_meetings(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path('output').mkdir()
            for name, date in [('older', '2026-01-01'), ('newer', '2026-09-11')]:
                Path(f'output/{name}_summary.json').write_text(json.dumps({
                    'session_info': {'name': name, 'processed_at': date},
                    'transcript': f'Transcript for {name}',
                }))
            original_open = builtins.open
            reads = []

            def counted_open(file, *args, **kwargs):
                if str(file).endswith('_summary.json'):
                    reads.append(str(file))
                return original_open(file, *args, **kwargs)

            with patch('builtins.open', side_effect=counted_open):
                result = runner.invoke(cli, ['list-meetings'])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertEqual(['newer', 'older'], [m['session_info']['name'] for m in json.loads(result.output)])
            self.assertEqual(2, len(reads), f'Read each meeting once: {reads}')

    def test_corrupt_meeting_does_not_hide_valid_history(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path('output').mkdir()
            Path('output/broken_summary.json').write_text('{incomplete')
            Path('output/dated_summary.json').write_text(json.dumps({
                'session_info': {'name': 'dated', 'processed_at': '2026-09-11T12:00:00Z'},
                'transcript': 'Saved transcript',
            }))
            missing_date = Path('output/undated_summary.json')
            missing_date.write_text(json.dumps({'session_info': {'name': 'undated'}}))
            os.utime(missing_date, (0, 0))
            result = runner.invoke(cli, ['list-meetings'])
            self.assertEqual(0, result.exit_code, result.output)
            meetings = json.loads(result.output)
            self.assertEqual(['dated', 'undated'], [m['session_info']['name'] for m in meetings])
            self.assertEqual('Saved transcript', meetings[0]['transcript'])


if __name__ == '__main__':
    unittest.main()
