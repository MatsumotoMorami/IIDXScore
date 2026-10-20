import hashlib
import importlib.util
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
import sys
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("auto_migrate", TOOLS / "auto_migrate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_catalog(path, version, songs):
    maximum = 35000 if version == 34 else 34000
    data = bytearray(b"IIDX" + struct.pack("<III", version, len(songs), maximum))
    data.extend(b"\0" * (maximum * 4))
    for music, title, level in songs:
        row = bytearray(2040)
        encoded = title.encode("utf-16-le")
        row[:len(encoded)] = encoded
        row[0x3EC + 3] = level
        struct.pack_into("<I", row, 0x67C, music)
        data.extend(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class AutoMigrationTest(unittest.TestCase):
    def test_one_click_migrates_renamed_and_omni_scores_with_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bm2dx.dll").write_bytes(b"fixture")
            plugin = root / "plugins"
            plugin.mkdir()
            (plugin / "IIDXScore.dll").write_bytes(b"fixture IIDXScore 0.5.3")
            ini = plugin / "IIDXScore.ini"
            ini.write_text("[Score]\nEnabled=1\nDatabasePath=../scores/IIDXScore.sqlite3\n"
                           "ServerKey=default\nMusicDatabase=../data_mods/omnimix/info/0/music_omni.bin\n",
                           encoding="utf-8")
            ac = root / "data/info/0/music_data.bin"
            omni = root / "data_mods/omnimix/info/0/music_omni.bin"
            old = root / "IIDXScoreLegacy/music_omni.bin"
            ac_hash = make_catalog(ac, 34, [(34078, "Ex-Otogibanashi", 10)])
            omni_hash = make_catalog(omni, 34, [(34078, "Ex-Otogibanashi", 10), (2, "Extra", 8)])
            old_hash = make_catalog(old, 33, [(33131, "Ex-Otogibanashi", 10), (2, "Extra", 8)])
            database = root / "scores/IIDXScore.sqlite3"
            database.parent.mkdir()
            old_scope = f"default/iidx33/{old_hash}"
            omni_scope = f"default/iidx34/{omni_hash}"
            target = f"default/iidx34/{ac_hash}/omni/{omni_hash}"
            raw = bytearray(0xea0)
            struct.pack_into("<I", raw, 0x10, 33131)
            raw[0x14] = 3
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE best(scope TEXT,player TEXT,music INTEGER,chart INTEGER,ex INTEGER,bp INTEGER,lamp INTEGER,PRIMARY KEY(scope,player,music,chart))")
                db.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY,scope TEXT,player TEXT,music INTEGER,chart INTEGER,pg INTEGER,great INTEGER,bp INTEGER,lamp INTEGER,eligible INTEGER,nocount INTEGER,grade INTEGER,nosave INTEGER,raw BLOB,created TEXT)")
                db.executemany("INSERT INTO best VALUES(?,?,?,?,?,?,?)", [
                    (old_scope, "player", 33131, 3, 2500, 15, 4),
                    (old_scope, "player", 2, 3, 1200, 18, 3),
                    (omni_scope, "player", 2, 3, 1300, 20, 5),
                ])
                db.execute("INSERT INTO attempts(scope,player,music,chart,pg,great,bp,lamp,eligible,nocount,grade,nosave,raw,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (old_scope, "player", 33131, 3, 1200, 100, 15, 4, 1, 0, 0, 0, raw, "2026-10-20"))
                db.commit()
            missing = old.with_name("old-index-hidden.bin")
            old.rename(missing)
            with self.assertRaisesRegex(ValueError, "找不到旧成绩对应的曲库索引"):
                module.apply(root)
            self.assertNotIn("OmniMusicDatabase=", ini.read_text(encoding="utf-8"))
            missing.rename(old)
            with closing(sqlite3.connect(database)) as db:
                db.execute("UPDATE attempts SET raw=?", (bytes(0xea0),))
                db.commit()
            with self.assertRaisesRegex(ValueError, "改号游玩快照无效"):
                module.apply(root)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM best WHERE scope=?", (target,)).fetchone()[0], 0)
                db.execute("UPDATE attempts SET raw=?", (raw,))
                db.commit()
            self.assertNotIn("OmniMusicDatabase=", ini.read_text(encoding="utf-8"))
            packaged = os.environ.get("IIDX_SCORE_MIGRATOR_EXE")
            if packaged:
                process = subprocess.run([packaged, "--root", str(root)], capture_output=True,
                                         text=True, encoding="utf-8", errors="replace")
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                report = next(root.glob("IIDXScore-自动迁移报告-*.json"))
                result = json.loads(report.read_text(encoding="utf-8"))
            else:
                result = module.apply(root)
            self.assertEqual(result["actual_target_rows"], 2)
            self.assertEqual(result["predicted_target_rows"], 2)
            self.assertEqual(len(result["remapped_rows"]), 1)
            with closing(sqlite3.connect(database)) as db:
                rows = db.execute("SELECT music,ex,bp,lamp FROM best WHERE scope=? ORDER BY music", (target,)).fetchall()
                self.assertEqual(rows, [(2, 1300, 18, 5), (34078, 2500, 15, 4)])
                self.assertEqual(db.execute("SELECT count(*) FROM best WHERE scope=?", (old_scope,)).fetchone()[0], 2)
                copied = db.execute("SELECT music,raw FROM attempts WHERE scope=?", (target,)).fetchone()
                self.assertEqual(copied[0], 34078)
                self.assertEqual(struct.unpack_from("<I", copied[1], 0x10)[0], 34078)
            with closing(sqlite3.connect(result["database_backup"])) as backup:
                self.assertEqual(backup.execute("SELECT count(*) FROM best WHERE scope=?", (target,)).fetchone()[0], 0)
            text = ini.read_text(encoding="utf-8")
            self.assertIn("MusicDatabase=../data/info/0/music_data.bin", text)
            self.assertIn("OmniMusicDatabase=../data_mods/omnimix/info/0/music_omni.bin", text)
            self.assertIn("LegacyMusicDatabase=../IIDXScoreLegacy/music_omni.bin", text)
            self.assertEqual(text.count("MusicDatabase="), 3)  # includes Omni/Legacy-prefixed keys
            rerun = module.apply(root)
            self.assertEqual(rerun["actual_target_rows"], 2)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM attempts WHERE scope=?", (target,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
