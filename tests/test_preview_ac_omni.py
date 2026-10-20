import hashlib
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import tempfile
import types
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "preview_ac_omni_migration.py"
spec = importlib.util.spec_from_file_location("migration_preview", SCRIPT)
preview_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview_module)


def catalog(path, version, songs):
    maximum = 35000 if version == 34 else 34000
    data = bytearray(b"IIDX" + struct.pack("<III", version, len(songs), maximum))
    data.extend(b"\0" * (maximum * 4))
    for music, title, levels in songs:
        row = bytearray(2040)
        encoded = title.encode("utf-16-le")
        row[:len(encoded)] = encoded
        row[0x3EC:0x3F6] = bytes(levels)
        struct.pack_into("<I", row, 0x67C, music)
        data.extend(row)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class PreviewTest(unittest.TestCase):
    def test_prerelease_song_is_matched_by_unique_title_and_chart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ac, omni, old, database = (root / name for name in ("ac.bin", "omni.bin", "old.bin", "scores.sqlite3"))
            levels = [1, 3, 6, 10, 0, 0, 3, 6, 10, 0]
            catalog(ac, 34, [(34078, "Ex-Otogibanashi", levels)])
            catalog(omni, 34, [(34078, "Ex-Otogibanashi", levels)])
            old_hash = catalog(old, 33, [(33131, "Ex-Otogibanashi", levels)])
            old_scope = "server/iidx33/" + old_hash
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE best(scope TEXT,player TEXT,music INTEGER,chart INTEGER,ex INTEGER,bp INTEGER,lamp INTEGER,PRIMARY KEY(scope,player,music,chart))")
                db.execute("INSERT INTO best VALUES(?,?,?,?,?,?,?)", (old_scope, "player", 33131, 3, 2400, 12, 4))
                db.commit()
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            args = types.SimpleNamespace(db=database, ac=ac, omni=omni, legacy=old, server_key="server")
            result = preview_module.preview(args)
            self.assertEqual(before, hashlib.sha256(database.read_bytes()).hexdigest())
            self.assertEqual(result["would_add_keys"], 1)
            self.assertEqual(result["skipped_rows"], [])
            self.assertEqual(result["remapped_rows"][0]["to_music"], 34078)

    def test_ac_and_legacy_scores_merge_without_database_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ac, omni, old, database = (root / name for name in ("ac.bin", "omni.bin", "old.bin", "scores.sqlite3"))
            levels_ac = [0] * 10
            levels_ac[3] = 10
            levels_extra = [0] * 10
            levels_extra[3] = 8
            ac_hash = catalog(ac, 34, [(1, "A", levels_ac)])
            omni_hash = catalog(omni, 33, [(1, "A", levels_ac), (2, "B", levels_extra)])
            old_hash = catalog(old, 33, [(1, "A", levels_ac), (2, "B", levels_extra)])
            ac_scope = "server/iidx34/" + ac_hash
            old_scope = "server/iidx33/" + old_hash
            target_scope = ac_scope + "/omni/" + omni_hash
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE best(scope TEXT,player TEXT,music INTEGER,chart INTEGER,ex INTEGER,bp INTEGER,lamp INTEGER,PRIMARY KEY(scope,player,music,chart))")
                db.executemany("INSERT INTO best VALUES(?,?,?,?,?,?,?)", [
                    (ac_scope, "player", 1, 3, 200, 30, 3),
                    (old_scope, "player", 1, 3, 190, 20, 2),
                    (old_scope, "player", 2, 3, 100, 8, 1),
                    (target_scope, "player", 1, 3, 180, 15, 4),
                ])
                db.commit()
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            args = types.SimpleNamespace(db=database, ac=ac, omni=omni, legacy=old, server_key="server")
            result = preview_module.preview(args)
            after = hashlib.sha256(database.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(result["sources"]["AC"]["matched"], 1)
            self.assertEqual(result["sources"]["legacy"]["matched"], 2)
            self.assertEqual(result["would_add_keys"], 1)
            self.assertEqual(result["would_improve_existing_keys"], 1)
            self.assertEqual(result["predicted_target_rows"], 2)


if __name__ == "__main__":
    unittest.main()
