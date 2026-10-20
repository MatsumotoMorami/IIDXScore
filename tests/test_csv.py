import csv
import importlib.util
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('import_csv', Path(__file__).parents[1] / 'tools/import_csv.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CsvImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = self.root / 'music.bin'
        data = bytearray(16 + 2000*4 + 2040)
        struct.pack_into('<4sIII', data, 0, b'IIDX', 33, 1, 2000)
        off = 16+2000*4
        title = 'Test, 曲'.encode('utf-16-le')
        data[off:off+len(title)] = title
        struct.pack_into('<I', data, off+0x67c, 1000)
        data[off+0x3ec:off+0x3f6] = bytes([5]*10)
        self.catalog.write_bytes(data)
        self.row = {'タイトル': 'Test, 曲', 'バージョン': '1'}
        for d in m.DIFFICULTIES:
            for f, v in zip(m.FIELDS, ('5','0','0','0','---','NO PLAY')):
                self.row[f'{d} {f}'] = v
        self.row.update({'ANOTHER スコア':'100','ANOTHER PGreat':'50',
                         'ANOTHER Great':'0','ANOTHER クリアタイプ':'HARD CLEAR'})

    def report(self, rows=None, style='SP', encoding='utf-8-sig'):
        path = self.root/'input.csv'
        with path.open('w', encoding=encoding, newline='') as f:
            w = csv.DictWriter(f, fieldnames=self.row.keys())
            w.writeheader()
            w.writerows(rows or [self.row])
        return m.preview(path, self.catalog, style)

    def test_encoding_mapping_and_missing_bp(self):
        for encoding in ('utf-8-sig', 'cp932'):
            r = self.report(style='DP', encoding=encoding)['records'][0]
            self.assertEqual((r['music'],r['chart'],r['ex'],r['bp'],r['lamp']), (1000,8,100,-1,5))

    def test_mismatch_uses_explicit_score_without_fake_attempt(self):
        self.row['ANOTHER PGreat'] = '70'
        report = self.report()
        self.assertEqual(report['records'][0]['ex'], 100)
        self.assertEqual(len(report['warnings']), 1)
        db_path = self.root/'sandbox.sqlite3'
        m.sandbox(report, db_path)
        with m.connection(db_path) as db:
            self.assertEqual(db.execute('SELECT ex FROM best').fetchone()[0], 100)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='attempts'").fetchone())
        with self.assertRaises(FileExistsError):
            m.sandbox(report, db_path)

    def test_unknown_ambiguous_and_version(self):
        self.row['タイトル'] = 'Unknown'
        self.assertEqual(self.report()['counts']['unmatched_title'], 1)
        self.row['タイトル'] = 'Test, 曲'
        self.row['バージョン'] = '2'
        self.assertEqual(self.report()['counts']['version_mismatch'], 1)
        self.row['バージョン'] = '1'
        data = bytearray(self.catalog.read_bytes())
        entry = data[-2040:]
        struct.pack_into('<I', entry, 0x67c, 1001)
        data += entry
        struct.pack_into('<I', data, 8, 2)
        self.catalog.write_bytes(data)
        self.assertEqual(self.report()['counts']['ambiguous_title'], 1)

    def test_duplicates_not_first_wins(self):
        report = self.report([self.row, self.row])
        self.assertEqual(report['counts']['accepted'], 0)
        self.assertEqual(report['counts']['duplicate_chart'], 2)

    def test_invalid_and_absent_chart(self):
        self.row['ANOTHER 難易度'] = '0'
        self.assertEqual(self.report()['counts']['absent_chart_with_score'], 1)
        self.row['ANOTHER 難易度'] = '6'
        self.assertEqual(self.report()['counts']['chart_level_mismatch'], 1)
        self.row['ANOTHER スコア'] = '-2'
        self.assertEqual(self.report()['counts']['invalid_value'], 1)

    def test_apply_backup_merge_identity_and_atomic_rollback(self):
        report = self.report()
        path = self.root/'existing.sqlite3'
        m.sandbox(report, path)
        scope = 'server/iidx33/' + report['catalog_sha256']
        with m.connection(path) as db:
            db.execute('INSERT INTO best VALUES(?,?,?,?,?,?,?)', (scope,'123',1000,3,200,10,3))
        with self.assertRaises(ValueError):
            m.apply(report, path, '999', 'server')
        result = m.apply(report, path, '123', 'server')
        with m.connection(path) as db:
            self.assertEqual(db.execute('SELECT ex,bp,lamp FROM best WHERE player=?', ('123',)).fetchone(), (200,10,5))
        with m.connection(result['backup']) as db:
            self.assertEqual(db.execute('SELECT lamp FROM best WHERE player=?', ('123',)).fetchone()[0], 3)
        with m.connection(path) as db:
            db.execute("CREATE TRIGGER reject_import BEFORE INSERT ON csv_import_records BEGIN SELECT RAISE(ABORT,'test'); END")
            before = db.execute('SELECT count(*) FROM csv_imports').fetchone()
        report['records'][0]['ex'] = 300
        with self.assertRaises(sqlite3.IntegrityError):
            m.apply(report, path, '123', 'server')
        with m.connection(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM csv_imports').fetchone(), before)
            self.assertEqual(db.execute("SELECT ex FROM best WHERE player='123'").fetchone()[0], 200)


if __name__ == '__main__':
    unittest.main()
