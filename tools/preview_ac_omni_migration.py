#!/usr/bin/env python3
"""Read-only preview of IIDXScore AC + Omni score migration.

This program has no apply mode. It only opens SQLite with mode=ro and runs
SELECT statements. A JSON report is written only when --report is supplied.
"""

import argparse
import collections
import configparser
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import sys

if sys.platform == "win32":
    # Make Chinese output readable in PowerShell/cmd even when the system ACP is GBK.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


RECORD_BYTES = 2040
TITLE_BYTES = 256
LEVEL_OFFSET = 0x3EC
ID_OFFSET = 0x67C


def read_catalog(path):
    raw = path.read_bytes()
    if len(raw) < 16 or raw[:4] != b"IIDX":
        raise ValueError(f"不是 IIDX 曲库索引：{path}")
    version, count, maximum = struct.unpack_from("<III", raw, 4)
    start = 16 + maximum * 4
    if version not in (33, 34) or not 0 < count <= 35000 or not 0 < maximum <= 100000:
        raise ValueError(f"曲库头无效：{path}")
    if len(raw) != start + count * RECORD_BYTES:
        raise ValueError(f"曲库结构与当前脚本不兼容：{path}")
    songs = {}
    for index in range(count):
        off = start + index * RECORD_BYTES
        music = struct.unpack_from("<I", raw, off + ID_OFFSET)[0]
        if music >= maximum or music in songs:
            raise ValueError(f"曲库歌曲 ID 无效或重复：{path}, {music}")
        songs[music] = (
            raw[off:off + TITLE_BYTES],
            bytes(raw[off + LEVEL_OFFSET:off + LEVEL_OFFSET + 10]),
        )
    return {"path": str(path), "version": version, "sha256": hashlib.sha256(raw).hexdigest(), "songs": songs}


def display_title(raw):
    return raw.decode("utf-16-le", "replace").split("\0", 1)[0]


def combined_catalog(ac, omni):
    songs = {music: (title, bytearray(levels)) for music, (title, levels) in ac["songs"].items()}
    for music, (title, levels) in omni["songs"].items():
        existing = songs.get(music)
        if existing is None:
            songs[music] = (title, bytearray(levels))
        elif existing[0] == title:
            for chart in range(10):
                if not existing[1][chart]:
                    existing[1][chart] = levels[chart]
    return songs


def match_reason(source_song, target_song, chart):
    if target_song is None:
        return "目标索引没有歌曲 ID"
    if source_song[0] != target_song[0]:
        return "同 ID 歌曲标题不同"
    if not 0 <= chart < 10:
        return "谱面序号无效"
    if not source_song[1][chart]:
        return "旧索引没有该谱面"
    if not target_song[1][chart]:
        return "目标索引没有该谱面"
    if source_song[1][chart] != target_song[1][chart]:
        return "谱面等级不同"
    return None


def resolve_target(music, source_song, chart, source_songs, target_songs):
    target_song = target_songs.get(music)
    if target_song is not None:
        return music, match_reason(source_song, target_song, chart)
    # Pre-release songs can receive a new ID in the next AC version. Only
    # accept a unique title in both catalogs with the same chart level.
    same_source_title = [song for song in source_songs.values() if song[0] == source_song[0]]
    candidates = [(target_id, song) for target_id, song in target_songs.items()
                  if song[0] == source_song[0]]
    if len(same_source_title) != 1 or len(candidates) != 1:
        return None, "目标索引没有歌曲 ID（标题匹配不唯一）" if candidates else "目标索引没有歌曲 ID"
    target_id, target_song = candidates[0]
    reason = match_reason(source_song, target_song, chart)
    # match_reason checks title and chart identity, independently of song ID.
    return (target_id, reason) if reason is None else (None, reason)


def read_best(db, scope):
    rows = db.execute(
        "SELECT player,music,chart,ex,bp,lamp FROM best WHERE scope=?",
        (scope,),
    ).fetchall()
    return {(player, music, chart): (ex, bp, lamp) for player, music, chart, ex, bp, lamp in rows}


def merge_best(current, incoming):
    if current is None:
        return incoming
    ex = max(current[0], incoming[0])
    lamp = max(current[2], incoming[2])
    candidates = [bp for bp in (current[1], incoming[1]) if bp >= 0]
    bp = min(candidates) if candidates else -1
    return (ex, bp, lamp)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="只读试算 IIDXScore 的旧成绩、当前 AC 成绩合并到 AC+Omni 后的结果；没有正式迁移功能。"
    )
    parser.add_argument("--db", type=Path, required=True, help="IIDXScore.sqlite3 数据库文件")
    parser.add_argument("--ac", type=Path, required=True, help="当前 IIDX34 AC 的 music_data.bin")
    parser.add_argument("--omni", type=Path, required=True, help="要使用的本地最新 Omni 索引")
    parser.add_argument("--legacy", type=Path, required=True, help="旧成绩当时使用的曲库索引")
    parser.add_argument("--server-key", required=True, help="与 IIDXScore.ini 的 ServerKey 完全相同")
    parser.add_argument("--report", type=Path, help="可选：将详细结果另存为 JSON；目标文件必须不存在")
    return parser.parse_args(argv)


SCOPE_PATTERN = re.compile(r"^(.+)/iidx(33|34)/([0-9a-fA-F]{64})(?:/omni/[0-9a-fA-F]{64})?$")


def read_ini(root):
    path = root / "IIDXScore.ini"
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp936"):
        try:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(raw.decode(encoding))
            return dict(parser["Score"]) if parser.has_section("Score") else {}
        except (UnicodeError, configparser.Error):
            continue
    raise ValueError("无法读取 IIDXScore.ini 的编码")


def config_path(root, settings, key):
    value = settings.get(key.lower(), "").strip().strip('"')
    if not value:
        return None
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def active_omni_candidates(root):
    candidates = []
    for base in (root / "data_mods", root / "omnimix", root / "ominimix", root / "data" / "info"):
        if base.is_dir():
            candidates.extend(base.rglob("music_omni.bin"))
    return sorted({p.resolve() for p in candidates})


def old_index_candidates(root, active, configured):
    paths = set(active)
    if configured and configured.is_file():
        paths.add(configured)
    for base in (root / "backups", root / "IIDXScoreLegacy", root / "contents" / "data" / "info"):
        if base.is_dir():
            paths.update(p.resolve() for pattern in ("music_omni.bin", "music_data.bin") for p in base.rglob(pattern))
    paths.update(p.resolve() for p in (root / "data" / "info").rglob("music_data.bin"))
    for base in root.glob("omni-backup*"):
        if base.is_dir():
            paths.update(p.resolve() for p in base.rglob("music_omni.bin"))
    return sorted(paths)


def auto_context(root):
    if not (root / "bm2dx.dll").is_file():
        raise ValueError("请把 IIDXScoreDryRun.exe 放到 IIDX 根目录（与 bm2dx.dll 同目录）")
    settings = read_ini(root)
    database = config_path(root, settings, "DatabasePath") or root / "scores" / "IIDXScore.sqlite3"
    if not database.is_file():
        other = root / "IIDXScore.sqlite3"
        if other.is_file():
            database = other
        else:
            raise ValueError(f"找不到成绩数据库：{database}")
    configured_ac = config_path(root, settings, "MusicDatabase")
    fallback_ac = root / "data" / "info" / "0" / "music_data.bin"
    ac = configured_ac if configured_ac and configured_ac.is_file() and read_catalog(configured_ac)["version"] == 34 else fallback_ac
    if not ac.is_file() or read_catalog(ac)["version"] != 34:
        raise ValueError("找不到 IIDX34 AC 索引 data/info/0/music_data.bin")

    configured_omni = config_path(root, settings, "OmniMusicDatabase")
    active = active_omni_candidates(root)
    if configured_omni and configured_omni.is_file():
        omni = configured_omni
    elif len(active) == 1:
        omni = active[0]
    elif not active:
        raise ValueError("找不到启用中的 Omni 索引；请确认曲包位于 IIDX/data_mods 下")
    else:
        hashes = {read_catalog(path)["sha256"] for path in active}
        if len(hashes) != 1:
            raise ValueError("找到多个不同的 Omni 索引；请在 IIDXScore.ini 设置 OmniMusicDatabase")
        omni = active[0]
    if not omni.is_file():
        raise ValueError(f"Omni 索引不存在：{omni}")

    uri = database.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        scopes = [row[0] for row in db.execute("SELECT DISTINCT scope FROM best")]
    parsed = [(scope, SCOPE_PATTERN.fullmatch(scope)) for scope in scopes]
    servers = sorted({match.group(1) for _, match in parsed if match})
    configured_server = settings.get("serverkey", "").strip()
    if not servers:
        servers = [configured_server or "default"]

    configured_old = config_path(root, settings, "LegacyMusicDatabase")
    wanted_hashes = {match.group(3).lower() for _, match in parsed if match}
    by_hash = {}
    for path in old_index_candidates(root, active + [omni], configured_old):
        try:
            catalog = read_catalog(path)
        except (OSError, ValueError):
            continue
        if catalog["sha256"] in wanted_hashes:
            by_hash.setdefault(catalog["sha256"], path)
    ac_hash = read_catalog(ac)["sha256"]
    omni_hash = read_catalog(omni)["sha256"]
    legacy = []
    missing = []
    for scope, match in parsed:
        if not match or match.group(3).lower() == ac_hash or scope.endswith("/omni/" + omni_hash):
            continue
        digest = match.group(3).lower()
        path = by_hash.get(digest)
        if path and path not in legacy:
            legacy.append(path)
        elif not path:
            missing.append(scope)
    return {"root": root, "db": database, "ac": ac, "omni": omni,
            "legacy": legacy, "servers": servers, "missing_catalog_scopes": sorted(set(missing))}


def preview(args):
    ac = read_catalog(args.ac.resolve())
    omni = read_catalog(args.omni.resolve())
    legacy_paths = getattr(args, "legacy_catalogs", None)
    if legacy_paths is None:
        legacy_paths = [args.legacy]
    legacy_catalogs = [read_catalog(path.resolve()) for path in legacy_paths]
    if ac["version"] != 34:
        raise ValueError("--ac 必须是当前 IIDX34 AC 索引")
    target_songs = combined_catalog(ac, omni)
    server = args.server_key
    ac_scope = f"{server}/iidx{ac['version']}/{ac['sha256']}"
    target_scope = f"{ac_scope}/omni/{omni['sha256']}"

    # SQLite URI mode=ro, plus query_only, ensures no DML can be run on the source DB.
    database_url = args.db.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(database_url, uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("数据库完整性检查未通过")
        existing = read_best(db, target_scope)
        predicted = dict(existing)
        sources = {}
        skipped = []
        remapped = []
        specifications = [("AC", ac_scope, ac)]
        for catalog in legacy_catalogs:
            label = "legacy" if len(legacy_catalogs) == 1 else "legacy:" + catalog["sha256"][:8]
            specifications.append((label, f"{server}/iidx{catalog['version']}/{catalog['sha256']}", catalog))
        for label, scope, catalog in specifications:
            rows = read_best(db, scope)
            matched = 0
            reasons = collections.Counter()
            for (player, music, chart), score in rows.items():
                source_song = catalog["songs"].get(music)
                if source_song is None:
                    reason = "源索引没有歌曲 ID"
                    song_title = ""
                else:
                    target_music, reason = resolve_target(music, source_song, chart,
                                                            catalog["songs"], target_songs)
                    song_title = display_title(source_song[0])
                if reason:
                    reasons[reason] += 1
                    skipped.append({"source": label, "player": player, "music": music,
                                    "chart": chart, "title": song_title, "reason": reason})
                    continue
                matched += 1
                if target_music != music:
                    remapped.append({"source": label, "from_music": music,
                                     "to_music": target_music, "chart": chart, "title": song_title})
                key = (player, target_music, chart)
                predicted[key] = merge_best(predicted.get(key), score)
            sources[label] = {"scope": scope, "best_rows": len(rows), "matched": matched,
                              "skipped": len(rows) - matched, "skip_reasons": dict(reasons)}
        new_keys = sum(key not in existing for key in predicted)
        improved_keys = sum(key in existing and score != existing[key] for key, score in predicted.items())
        result = {
            "mode": "DRY_RUN_READ_ONLY", "database": str(args.db.resolve()),
            "ac_catalog": {key: ac[key] for key in ("path", "version", "sha256")},
            "omni_catalog": {key: omni[key] for key in ("path", "version", "sha256")},
            "legacy_catalogs": [{key: catalog[key] for key in ("path", "version", "sha256")}
                                for catalog in legacy_catalogs],
            "target_scope": target_scope, "sources": sources,
            "existing_target_rows": len(existing), "would_add_keys": new_keys,
            "would_improve_existing_keys": improved_keys,
            "predicted_target_rows": len(predicted), "skipped_rows": skipped,
            "remapped_rows": remapped,
            "note": "只读试算；未调用 INSERT、UPDATE、DELETE，也未修改原数据库。",
        }
        return result
    finally:
        db.close()


def show_result(result):
    print("AC 索引版本:", result["ac_catalog"]["version"],
          "Omni 索引版本:", result["omni_catalog"]["version"])
    for label, item in result["sources"].items():
        print(f"{label}: 已有 {item['best_rows']} 条；可匹配 {item['matched']} 条；跳过 {item['skipped']} 条")
        for reason, count in item["skip_reasons"].items():
            print(f"  - {reason}: {count}")
    print("目标作用域原有:", result["existing_target_rows"],
          "新增键:", result["would_add_keys"],
          "改善已有键:", result["would_improve_existing_keys"],
          "预计目标总数:", result["predicted_target_rows"])
    if result["remapped_rows"]:
        print("其中歌曲 ID 改号匹配:", len(result["remapped_rows"]), "条")


def automatic_preview(root):
    context = auto_context(root)
    print("只读试迁移（自动检测，原数据库未修改）")
    print("数据库:", context["db"])
    print("AC 索引:", context["ac"])
    print("Omni 索引:", context["omni"])
    results = []
    for server in context["servers"]:
        args = argparse.Namespace(db=context["db"], ac=context["ac"], omni=context["omni"],
                                  legacy_catalogs=context["legacy"], server_key=server)
        result = preview(args)
        results.append(result)
        print("\n服务器作用域:", server)
        show_result(result)
    if context["missing_catalog_scopes"]:
        print("\n注意：有", len(context["missing_catalog_scopes"]),
              "组旧成绩找不到对应的旧曲库索引，当前预测不包含它们。")
        print("可将旧 music_omni.bin 放进 IIDXScoreLegacy 文件夹后重新双击测试。")
    report = {"mode": "DRY_RUN_READ_ONLY", "game_root": str(root),
              "missing_catalog_scopes": context["missing_catalog_scopes"], "results": results}
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for index in range(100):
        suffix = "" if index == 0 else f"-{index}"
        report_path = root / f"IIDXScore-试迁移报告-{timestamp}{suffix}.json"
        try:
            with report_path.open("x", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
                output.write("\n")
            print("\n详细报告:", report_path)
            break
        except FileExistsError:
            continue
        except OSError as exc:
            print("无法保存报告，但试算已完成：", exc, file=sys.stderr)
            break
    return 0


def main(argv=None):
    supplied = sys.argv[1:] if argv is None else argv
    automatic = not supplied
    try:
        if automatic:
            root = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
            return automatic_preview(root)
        args = parse_args(supplied)
        result = preview(args)
        print("只读试迁移结果（原数据库未修改）")
        show_result(result)
        if args.report:
            with args.report.open("x", encoding="utf-8") as output:
                json.dump(result, output, ensure_ascii=False, indent=2)
                output.write("\n")
            print("详细报告:", args.report)
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print("试迁移失败：", exc, file=sys.stderr)
        return 1
    finally:
        if automatic:
            try:
                input("按回车关闭窗口……")
            except EOFError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
