#!/usr/bin/env python3
"""One-click, backed-up AC+Omni score migration for IIDXScore 0.5.3.

Unlike preview_ac_omni_migration.py, this program writes the score database
and the adjacent INI. It refuses incomplete catalog discovery or skipped best
rows, and leaves the source score scopes intact.
"""

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import sys

from preview_ac_omni_migration import (
    SCOPE_PATTERN, active_omni_candidates, config_path,
    old_index_candidates, preview, read_catalog, read_ini, resolve_target,
    combined_catalog,
)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def require_game_closed():
    if sys.platform != "win32":
        return
    result = subprocess.run(["tasklist", "/M", "bm2dx.dll", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise ValueError("无法检查游戏是否退出；请先退出游戏后重试")
    if any(line.lstrip().startswith('"') for line in result.stdout.splitlines()):
        raise ValueError("检测到正在运行的 bm2dx.dll；请先完全退出游戏")


def plugin_directory(root):
    choices = [root] + sorted(path for path in root.iterdir() if path.is_dir())
    choices = [path for path in choices if (path / "IIDXScore.dll").is_file()]
    if len(choices) != 1:
        raise ValueError(f"需要在游戏根目录或直属子目录找到唯一的 IIDXScore.dll；找到 {len(choices)} 个")
    if b"IIDXScore 0.5.3" not in (choices[0] / "IIDXScore.dll").read_bytes():
        raise ValueError("当前 IIDXScore.dll 不是本次迁移所需的 0.5.3 版本")
    return choices[0]


def select_catalogs(root, plugin, settings, database):
    ac = root / "data" / "info" / "0" / "music_data.bin"
    if not ac.is_file() or read_catalog(ac)["version"] != 34:
        raise ValueError("找不到 IIDX34 AC 索引 data/info/0/music_data.bin")
    configured = config_path(plugin, settings, "OmniMusicDatabase")
    options = [path for path in active_omni_candidates(root)
               if read_catalog(path)["version"] == 34]
    if configured and configured.is_file():
        if read_catalog(configured)["version"] != 34:
            raise ValueError("OmniMusicDatabase 必须指向当前 IIDX34 索引")
        omni = configured
    elif options:
        hashes = {read_catalog(path)["sha256"] for path in options}
        if len(hashes) != 1:
            raise ValueError("找到多个不同的 IIDX34 Omni 索引；请先在 INI 设置 OmniMusicDatabase")
        omni = options[0]
    else:
        raise ValueError("找不到当前 IIDX34 Omni 索引")
    ac_info, omni_info = read_catalog(ac), read_catalog(omni)
    if ac_info["sha256"] == omni_info["sha256"]:
        raise ValueError("AC 与 Omni 索引不能是同一个文件")

    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("数据库完整性检查未通过")
        scopes = {row[0] for row in db.execute(
            "SELECT scope FROM best UNION SELECT scope FROM attempts")}
    parsed = [(scope, SCOPE_PATTERN.fullmatch(scope)) for scope in scopes]
    servers = sorted({match.group(1) for _, match in parsed if match})
    configured_server = settings.get("serverkey", "").strip()
    if configured_server and configured_server not in servers and servers:
        raise ValueError("INI 的 ServerKey 与数据库成绩作用域不符")
    if not servers:
        raise ValueError("数据库没有可迁移的成绩作用域")
    if len(servers) != 1:
        raise ValueError("数据库包含多个 ServerKey；请使用手动迁移方式")
    server = servers[0]

    configured_old = config_path(plugin, settings, "LegacyMusicDatabase")
    wanted = {match.group(3).lower() for _, match in parsed if match}
    by_hash = {}
    for path in old_index_candidates(root, options + [omni], configured_old):
        try:
            info = read_catalog(path)
        except (OSError, ValueError):
            continue
        if info["sha256"] in wanted:
            by_hash.setdefault(info["sha256"], path)
    old = []
    missing = []
    for scope, match in parsed:
        if not match or scope.endswith("/omni/" + omni_info["sha256"]):
            continue
        digest = match.group(3).lower()
        if digest in (ac_info["sha256"], omni_info["sha256"]):
            continue
        path = by_hash.get(digest)
        if path and path not in old:
            old.append(path)
        elif not path:
            missing.append(scope)
    if missing:
        raise ValueError("找不到旧成绩对应的曲库索引：" + ", ".join(sorted(missing)))
    if len(old) > 1:
        raise ValueError("找到多个不同的旧曲库作用域；自动配置只支持一个，改用手动方式")
    return ac, omni, old, server


def migration_specs(server, ac, omni, old):
    ac_info, omni_info = read_catalog(ac), read_catalog(omni)
    target = f"{server}/iidx34/{ac_info['sha256']}/omni/{omni_info['sha256']}"
    specs = [(f"{server}/iidx34/{ac_info['sha256']}", ac_info),
             (f"{server}/iidx34/{omni_info['sha256']}", omni_info)]
    specs.extend((f"{server}/iidx{info['version']}/{info['sha256']}", info)
                 for info in (read_catalog(path) for path in old))
    return target, list(dict((scope, info) for scope, info in specs).items())


def chart_map(source, target):
    mapping = {}
    for music, source_song in source["songs"].items():
        for chart, level in enumerate(source_song[1]):
            if not level:
                continue
            target_music, reason = resolve_target(music, source_song, chart,
                                                  source["songs"], target)
            if reason is None:
                mapping[(music, chart)] = target_music
    return mapping


BEST_UPSERT = """INSERT INTO best(scope,player,music,chart,ex,bp,lamp)
VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope,player,music,chart) DO UPDATE SET
ex=max(best.ex,excluded.ex),lamp=max(best.lamp,excluded.lamp),
bp=CASE WHEN excluded.bp<0 THEN best.bp WHEN best.bp<0 THEN excluded.bp
        ELSE min(best.bp,excluded.bp) END"""


def copy_scope(db, source_scope, target_scope, mapping):
    best_count = attempt_count = 0
    best_rows = db.execute("SELECT player,music,chart,ex,bp,lamp FROM best WHERE scope=?",
                           (source_scope,)).fetchall()
    for player, music, chart, ex, bp, lamp in best_rows:
        to_music = mapping.get((music, chart))
        if to_music is None:
            raise ValueError(f"迁移期间发现未匹配的最佳成绩：{source_scope}/{music}/{chart}")
        db.execute(BEST_UPSERT, (target_scope, player, to_music, chart, ex, bp, lamp))
        best_count += 1
    rows = db.execute("""SELECT id,player,music,chart,pg,great,bp,lamp,eligible,
        nocount,grade,nosave,raw,created FROM attempts WHERE scope=? AND eligible=1""",
                      (source_scope,)).fetchall()
    for row in rows:
        source_id, player, music, chart = row[:4]
        to_music = mapping.get((music, chart))
        if to_music is None or db.execute(
                "SELECT 1 FROM migrated_attempts WHERE source_scope=? AND source_id=? AND target_scope=?",
                (source_scope, source_id, target_scope)).fetchone():
            continue
        raw = bytearray(row[12])
        if music != to_music:
            if len(raw) != 0xea0 or struct.unpack_from("<I", raw, 0x10)[0] != music:
                raise ValueError(f"改号游玩快照无效：{source_scope}/{music}/{chart}")
            struct.pack_into("<I", raw, 0x10, to_music)
        cursor = db.execute("""INSERT INTO attempts(scope,player,music,chart,pg,great,bp,
            lamp,eligible,nocount,grade,nosave,raw,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (target_scope, player, to_music, chart, *row[4:12], bytes(raw), row[13]))
        db.execute("INSERT INTO migrated_attempts VALUES(?,?,?,?)",
                   (source_scope, source_id, target_scope, cursor.lastrowid))
        attempt_count += 1
    return best_count, attempt_count


def backup_database(database, timestamp):
    backup = database.with_name(f"IIDXScore-backup-{timestamp}.sqlite3")
    if backup.exists():
        raise ValueError(f"备份文件已存在：{backup}")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("数据库备份完整性检查失败")
    return backup


def ini_text(plugin, settings, database, ac, omni, old, server):
    path = plugin / "IIDXScore.ini"
    if path.exists():
        raw = path.read_bytes()
        encodings = ("utf-8-sig",) if raw.startswith(b"\xef\xbb\xbf") else (
            ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8", "cp936"))
        for encoding in encodings:
            try:
                content = raw.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            raise ValueError("无法读取 INI 编码")
    else:
        encoding = "utf-8"
        content = "[Score]\nEnabled=1\nOverlay=1\nGhostOverlay=1\n"
    newline = "\r\n" if "\r\n" in content else "\n"
    def relative(path):
        return os.path.relpath(path, plugin).replace("\\", "/")
    values = {
        "databasepath": settings.get("databasepath", relative(database)),
        "serverkey": settings.get("serverkey", server),
        "musicdatabase": relative(ac),
        "omnimusicdatabase": relative(omni),
        "legacymusicdatabase": relative(old[0]) if old else "",
    }
    lines = content.splitlines()
    output = []
    in_score = False
    seen = set()
    for line in lines:
        section = re.match(r"^\s*\[([^]]+)\]\s*$", line)
        if section:
            if in_score:
                output.extend(f"{key}={values[key.lower()]}" for key in
                              ("DatabasePath", "ServerKey", "MusicDatabase",
                               "OmniMusicDatabase", "LegacyMusicDatabase")
                              if key.lower() not in seen)
            in_score = section.group(1).lower() == "score"
        key = re.match(r"^\s*([^;#=]+?)\s*=", line)
        if in_score and key and key.group(1).strip().lower() in values:
            normalized = key.group(1).strip().lower()
            if normalized not in seen:
                output.append(f"{key.group(1).strip()}={values[normalized]}")
                seen.add(normalized)
            continue
        output.append(line)
    if in_score:
        output.extend(f"{key}={values[key.lower()]}" for key in
                      ("DatabasePath", "ServerKey", "MusicDatabase",
                       "OmniMusicDatabase", "LegacyMusicDatabase")
                      if key.lower() not in seen)
    if not any(re.match(r"^\s*\[Score\]\s*$", line, re.I) for line in output):
        output.extend(["[Score]"] + [f"{key}={values[key.lower()]}" for key in
                                      ("DatabasePath", "ServerKey", "MusicDatabase",
                                       "OmniMusicDatabase", "LegacyMusicDatabase")])
    return path, (newline.join(output) + newline).encode(encoding)


def apply(root):
    root = root.resolve()
    if not (root / "bm2dx.dll").is_file():
        raise ValueError("请把迁移工具放到与 bm2dx.dll 同目录的游戏根目录")
    require_game_closed()
    plugin = plugin_directory(root)
    settings = read_ini(plugin)
    database = config_path(plugin, settings, "DatabasePath") or root / "scores" / "IIDXScore.sqlite3"
    if not database.is_file():
        raise ValueError(f"找不到成绩数据库：{database}")
    ac, omni, old, server = select_catalogs(root, plugin, settings, database)
    if not old and not settings.get("omnimusicdatabase"):
        print("没有找到旧版索引；若要迁移 IIDX33 成绩，请将旧 music_omni.bin 放入 IIDXScoreLegacy")
    args = argparse.Namespace(db=database, ac=ac, omni=omni,
                              legacy_catalogs=[omni] + old, server_key=server)
    prediction = preview(args)
    if prediction["skipped_rows"]:
        raise ValueError(f"有 {len(prediction['skipped_rows'])} 条成绩无法匹配；自动迁移已停止，请先检查只读报告")
    target_scope, specs = migration_specs(server, ac, omni, old)
    target_songs = combined_catalog(read_catalog(ac), read_catalog(omni))
    maps = {scope: chart_map(catalog, target_songs) for scope, catalog in specs}
    ini_path, new_ini = ini_text(plugin, settings, database, ac, omni, old, server)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for suffix in range(100):
        timestamp = stamp if suffix == 0 else f"{stamp}-{suffix}"
        report_path = root / f"IIDXScore-自动迁移报告-{timestamp}.json"
        backup_path = database.with_name(f"IIDXScore-backup-{timestamp}.sqlite3")
        ini_backup_path = ini_path.with_name(f"IIDXScore.ini.before-migration-{timestamp}.bak")
        if not any(path.exists() for path in (report_path, backup_path, ini_backup_path)):
            break
    else:
        raise ValueError("迁移报告或备份文件名称已用尽")
    backup = backup_database(database, timestamp)
    ini_backup = None
    if ini_path.exists():
        ini_backup = ini_path.with_name(f"IIDXScore.ini.before-migration-{timestamp}.bak")
        shutil.copy2(ini_path, ini_backup)
    print("数据库备份:", backup)
    with closing(sqlite3.connect(database)) as db:
        db.execute("PRAGMA busy_timeout=1500")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS migrated_attempts(
                source_scope TEXT NOT NULL,source_id INTEGER NOT NULL,
                target_scope TEXT NOT NULL,target_id INTEGER NOT NULL,
                PRIMARY KEY(source_scope,source_id,target_scope))""")
            counts = {}
            for scope, _ in specs:
                if scope != target_scope:
                    counts[scope] = copy_scope(db, scope, target_scope, maps[scope])
            total = db.execute("SELECT count(*) FROM best WHERE scope=?", (target_scope,)).fetchone()[0]
            if total != prediction["predicted_target_rows"]:
                raise ValueError(f"写入后数量 {total} 与只读预测 {prediction['predicted_target_rows']} 不一致")
            db.commit()
        except Exception:
            db.rollback()
            raise
    temp_ini = ini_path.with_name("IIDXScore.ini.migration-tmp")
    try:
        with temp_ini.open("xb") as output:
            output.write(new_ini)
        os.replace(temp_ini, ini_path)
    except Exception:
        temp_ini.unlink(missing_ok=True)
        raise ValueError("成绩已迁移，但 INI 更新失败；数据库备份仍在，请检查 INI 路径后重运行")
    report = {"mode": "AUTO_MIGRATION_APPLIED", "game_root": str(root),
              "database": str(database), "database_backup": str(backup),
              "ini": str(ini_path), "ini_backup": str(ini_backup) if ini_backup else None,
              "target_scope": target_scope, "predicted_target_rows": prediction["predicted_target_rows"],
              "actual_target_rows": total, "copied_by_source": {
                  key: {"best_rows": value[0], "attempts": value[1]} for key, value in counts.items()},
              "remapped_rows": prediction["remapped_rows"]}
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        print("成绩和 INI 已迁移，但报告写入失败：", error, file=sys.stderr)
    print("迁移完成；目标成绩:", total, "条")
    print("INI 已更新:", ini_path)
    print("报告:", report_path)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="自动备份并迁移 IIDXScore AC+Omni 成绩")
    parser.add_argument("--root", type=Path, help="包含 bm2dx.dll 的游戏根目录；双击时自动取 EXE 所在目录")
    options = parser.parse_args(argv)
    root = options.root or Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent
    try:
        apply(root)
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print("自动迁移失败：", error, file=sys.stderr)
        return 1
    finally:
        if options.root is None:
            try:
                input("按回车关闭窗口……")
            except EOFError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
