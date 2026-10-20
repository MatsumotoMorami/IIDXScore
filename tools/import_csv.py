"""Offline IIDX score CSV importer. Default: preview; never discovers a player's DB."""
import argparse
import collections
from contextlib import contextmanager
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import struct
import unicodedata
import uuid

DIFFICULTIES = ('BEGINNER', 'NORMAL', 'HYPER', 'ANOTHER', 'LEGGENDARIA')
LAMPS = dict(zip(('NO PLAY', 'FAILED', 'ASSIST CLEAR', 'EASY CLEAR', 'CLEAR',
                  'HARD CLEAR', 'EX HARD CLEAR', 'FULL COMBO'), range(8)))
FIELDS = ('難易度', 'スコア', 'PGreat', 'Great', 'ミスカウント', 'クリアタイプ')
UPSERT = '''INSERT INTO best(scope,player,music,chart,ex,bp,lamp) VALUES(?,?,?,?,?,?,?)
ON CONFLICT(scope,player,music,chart) DO UPDATE SET
ex=max(best.ex,excluded.ex),lamp=max(best.lamp,excluded.lamp),
bp=CASE WHEN excluded.bp<0 THEN best.bp WHEN best.bp<0 THEN excluded.bp
ELSE min(best.bp,excluded.bp) END'''


@contextmanager
def connection(*args, **kwargs):
    db = sqlite3.connect(*args, **kwargs)
    try:
        with db:
            yield db
    finally:
        db.close()


def title_key(text):
    # No fuzzy matching, case folding, punctuation removal, or compatibility folding.
    return unicodedata.normalize('NFC', text.strip())


def catalog(path):
    data = Path(path).read_bytes()
    if len(data) < 16 or data[:4] != b'IIDX':
        raise ValueError('Not an IIDX catalog')
    version, count, maximum = struct.unpack_from('<III', data, 4)
    start = 16 + maximum * 4
    if version != 33 or not 0 < count <= 34000 or len(data) != start + count * 2040:
        raise ValueError('Unsupported IIDX 33 catalog layout')
    songs, ids = collections.defaultdict(list), set()
    for i in range(count):
        offset = start + i * 2040
        music = struct.unpack_from('<I', data, offset + 0x67c)[0]
        if music >= maximum or music in ids:
            raise ValueError('Invalid or duplicate catalog ID')
        ids.add(music)
        title = data[offset:offset+256].decode('utf-16-le').split('\0')[0]
        songs[title_key(title)].append((music, list(data[offset+0x3ec:offset+0x3f6])))
    return songs, hashlib.sha256(data).hexdigest()


def number(value, maximum=300000):
    if not re.fullmatch(r'[0-9]+', value or ''):
        raise ValueError('Expected unsigned integer')
    result = int(value)
    if result > maximum:
        raise ValueError('Value out of range')
    return result


def preview(csv_path, catalog_path, style):
    songs, catalog_hash = catalog(catalog_path)
    raw = Path(csv_path).read_bytes()
    try:
        text, encoding = raw.decode('utf-8-sig'), 'utf-8-sig'
    except UnicodeDecodeError:
        text, encoding = raw.decode('cp932'), 'cp932'
    reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
    required = ['タイトル', 'バージョン'] + [f'{d} {f}' for d in DIFFICULTIES for f in FIELDS]
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or any(h not in headers for h in required):
        raise ValueError('Missing or duplicated CSV columns')
    records, issues, warnings, counts = [], [], [], collections.Counter()
    for line, row in enumerate(reader, 2):
        counts['songs'] += 1
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f'CSV row {line}: incorrect column count')
        title = row['タイトル']
        version = number(row['バージョン'].strip(), 33)
        title_matches = songs.get(title_key(title), [])
        matches = [s for s in title_matches if s[0] // 1000 == version]
        for index, difficulty in enumerate(DIFFICULTIES):
            def get(field):
                return row[f'{difficulty} {field}'].strip()
            def reject(reason):
                counts[reason] += 1
                issues.append(dict(row=line, title=title, difficulty=difficulty, reason=reason))
            def warn(reason):
                warnings.append(dict(row=line, title=title, difficulty=difficulty, reason=reason))
            try:
                level = number(get('難易度'), 12)
                ex = number(get('スコア'))
                pg, great = number(get('PGreat'), 100000), number(get('Great'), 100000)
                bp = -1 if get('ミスカウント') in ('', '---') else number(get('ミスカウント'), 100000)
                lamp = LAMPS[get('クリアタイプ')]
            except (ValueError, KeyError):
                reject('invalid_value')
                continue
            if lamp == 0:
                if ex or pg or great or bp >= 0:
                    warn('no_play_with_score_preserved')
                else:
                    counts['no_play'] += 1
                    continue
            if level == 0:
                reject('absent_chart_with_score')
                continue
            if ex != 2 * pg + great:
                warn('ex_judgement_mismatch_score_column_used')
            if len(matches) != 1:
                reject('version_mismatch' if title_matches and not matches else
                       'unmatched_title' if not matches else 'ambiguous_title')
                continue
            music, levels = matches[0]
            chart = index + (5 if style == 'DP' else 0)
            if levels[chart] != level:
                reject('chart_level_mismatch')
                continue
            records.append(dict(row=line, title=title, music=music, chart=chart,
                                ex=ex, pg=pg, great=great, bp=bp, lamp=lamp))
    multiplicity = collections.Counter((r['music'], r['chart']) for r in records)
    duplicates = [r for r in records if multiplicity[r['music'], r['chart']] > 1]
    for r in duplicates:
        issues.append(dict(row=r['row'], title=r['title'], difficulty=DIFFICULTIES[r['chart'] % 5], reason='duplicate_chart'))
    if duplicates:
        counts['duplicate_chart'] = len(duplicates)
    records = [r for r in records if multiplicity[r['music'], r['chart']] == 1]
    counts['accepted'] = len(records)
    counts['warnings'] = len(warnings)
    return dict(source_sha256=hashlib.sha256(raw).hexdigest(), catalog_sha256=catalog_hash,
                style=style, encoding=encoding, counts=dict(counts), issues=issues, warnings=warnings, records=records)


def write_records(db, report, scope, player):
    # Import summaries separately: never manufacture attempts or ghost snapshots.
    db.execute('''CREATE TABLE IF NOT EXISTS csv_imports(
      batch TEXT PRIMARY KEY, source_sha256 TEXT, catalog_sha256 TEXT,
      scope TEXT, player TEXT, style TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)''')
    db.execute('''CREATE TABLE IF NOT EXISTS csv_import_records(
      batch TEXT, source_row INTEGER, music INTEGER, chart INTEGER, ex INTEGER,
      pg INTEGER, great INTEGER, bp INTEGER, lamp INTEGER)''')
    batch = uuid.uuid4().hex
    db.execute('INSERT INTO csv_imports(batch,source_sha256,catalog_sha256,scope,player,style) VALUES(?,?,?,?,?,?)',
               (batch, report['source_sha256'], report['catalog_sha256'], scope, player, report['style']))
    for r in report['records']:
        db.execute(UPSERT, (scope, player, r['music'], r['chart'], r['ex'], r['bp'], r['lamp']))
        db.execute('INSERT INTO csv_import_records VALUES(?,?,?,?,?,?,?,?,?)',
                   (batch, r['row'], r['music'], r['chart'], r['ex'], r['pg'], r['great'], r['bp'], r['lamp']))
    return batch


def sandbox(report, path):
    path = Path(path)
    # Exclusive creation: --test-db cannot overwrite any existing file, including a real DB.
    with path.open('xb'):
        pass
    with connection(path) as db:
        db.execute('CREATE TABLE best(scope TEXT,player TEXT,music INTEGER,chart INTEGER,ex INTEGER,bp INTEGER,lamp INTEGER,PRIMARY KEY(scope,player,music,chart))')
        scope = 'CSV_TEST_ONLY/iidx33/' + report['catalog_sha256']
        write_records(db, report, scope, 'TEST_ONLY_NOT_A_PLAYER')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Sandbox integrity check failed')


def apply(report, path, player, server_key):
    if not re.fullmatch(r'[0-9]{1,15}', player or ''):
        raise ValueError('Explicit native player ID required (1..15 digits)')
    path = Path(path).resolve(strict=True)
    scope = server_key + '/iidx33/' + report['catalog_sha256']
    # mode=rw refuses to create a mistaken target path.
    with connection(path.as_uri() + '?mode=rw', uri=True, timeout=2) as db:
        if [r[1] for r in db.execute('PRAGMA table_info(best)')] != ['scope','player','music','chart','ex','bp','lamp']:
            raise ValueError('Not a supported IIDXScore database')
        if not db.execute('SELECT 1 FROM best WHERE scope=? AND player=? LIMIT 1', (scope, player)).fetchone():
            raise ValueError('Player/catalog/server scope does not exist in target DB; no changes made')
        backup = path.with_name(path.name + '.before-csv-' + uuid.uuid4().hex + '.sqlite3')
        with connection(backup) as destination:
            db.backup(destination)
        db.execute('BEGIN IMMEDIATE')
        try:
            batch = write_records(db, report, scope, player)
            db.commit()
        except Exception:
            db.rollback()
            raise
    return dict(batch=batch, backup=str(backup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--style', choices=['SP', 'DP'], required=True)
    parser.add_argument('--report', type=Path, required=True, help='New JSON report path')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--test-db', type=Path, help='Create a NEW isolated test DB, with a non-player identity')
    mode.add_argument('--apply', type=Path, help='Explicitly write to an EXISTING IIDXScore DB after backup')
    parser.add_argument('--player')
    parser.add_argument('--server-key')
    args = parser.parse_args()
    if args.apply and (not args.player or not args.server_key):
        parser.error('--apply requires --player and --server-key')
    # Reports also refuse overwrite, preventing accidental use of an input or database path.
    with args.report.open('x', encoding='utf-8') as output:
        report = preview(args.csv, args.catalog, args.style)
        report['mode'] = 'preview'
        if args.test_db:
            sandbox(report, args.test_db)
            report['mode'] = 'isolated_test'
        elif args.apply:
            report['applied'] = apply(report, args.apply, args.player, args.server_key)
            report['mode'] = 'apply'
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({'mode': report['mode'], **report['counts']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
