# CSV 成绩导入

当前工具只识别 IIDX 33 曲库，不支持把成绩导入 IIDX 34 的 0916 曲库作用域。

## Windows EXE（推荐）

将自己的 CSV 拖到 `IIDXScoreImport.exe` **文件图标**上（不是窗口内部），或双击后选择 CSV。EXE 已包含 Python，无需另装运行环境。

它优先读取 EXE 同目录的 `IIDXScore.ini`，其次尝试 `C:/IIDX/IIDXScore.ini`；找不到时点击选择。自动读取数据库、曲库及 ServerKey，预览后列出该作用域已有的玩家 ID。单玩家自动选中，多玩家需手动选择。请核对 SP/DP（文件名带 dp 时提示 DP，否则为 SP）。

点击“导入所选玩家”并确认，才会写入并备份。可先保存详细报告检查异常。游戏运行时拒绝导入；操作完成后重新启动游戏。**不要将他人的 CSV 正式导入你的玩家。**

源码为 `tools/import_gui.py`，可通过 `tools/build-import-exe.ps1 -Python Python路径` 重建。

`import_csv.py` 是独立的离线导入工具，Python 3.9 以上、仅使用标准库。现有 IIDXScore 0.3 DLL 可读取导入的最佳成绩，不需要替换 DLL。

支持本次样本这种日文表头格式：每首歌一行，BEGINNER/NORMAL/HYPER/ANOTHER/LEGGENDARIA 各有难度、スコア、PGreat、Great、ミスカウント、クリアタイプ。支持 UTF-8（含 BOM）及 CP932。

## 预览（默认，不打开成绩数据库）

```powershell
python tools/import_csv.py "自己的成绩.csv" --catalog "C:/IIDX/data_mods/ominimix/info/1/music_omni.bin" --style SP --report "预览.json"
```

`--catalog` 必须选实际游戏加载且与 IIDXScore.ini 的 MusicDatabase 相同的 IIDX 33 曲库。`--style` 必须显式选 SP 或 DP；不会根据文件名猜测。报告文件必须不存在，避免覆盖其他文件。

## 隔离试导入

在预览命令后添加 `--test-db "测试.sqlite3"`。只允许新建文件，使用 `CSV_TEST_ONLY` 作用域和非玩家标识，不读取或定位真实玩家数据库。测试文件不要放入游戏 scores 目录，也不要将 INI 指向它。

## 正式导入自己的成绩

先退出游戏，检查预览结果，再显式指定目标数据库、插件实际记录的玩家 ID、INI 中的 ServerKey：

```powershell
python tools/import_csv.py "自己的成绩.csv" --catalog "实际使用的music_omni.bin" --style SP --report "正式导入报告.json" --apply "实际的IIDXScore.sqlite3" --player "实际玩家ID" --server-key "default"
```

玩家 ID 是数据库里的 `player` 字段，不应拿卡号或显示昵称猜测。导入要求该玩家及曲库/服务器作用域已在目标 `best` 表中存在；不会自动选择玩家或迁移曲库作用域。正式导入会用 SQLite 备份 API 创建 `.before-csv-随机标识.sqlite3`，再以事务写入，出错回滚。

## 匹配与合并

- 精确匹配曲名（仅去首尾空白、Unicode NFC），并核对曲目 ID 的版本段与 CSV 数字版本、谱面等级。不做模糊匹配。这个版本规则适用于本次样本的数字版本格式；移植曲和重新编号曲可能被保守跳过。
- 未匹配、同名不唯一、版本/等级冲突、无谱面却有成绩、无效值都会跳过，在报告 `issues` 列出。重复目标谱面的所有候选都跳过，避免取第一条。
- 以 `スコア` 为最高 EX。PGreat/Great 与 EX 不一致时标入 `warnings`，仍保留明确的 EX，不用判定数重算成绩。
- `NO PLAY` 且没有任何成绩的数据跳过；若同时有分数，保留分数和原灯值 0，并报告警告，不推断一个灯。
- BP 的 `---`/空值表示未知（-1），不当成 0。EX/灯取较高值，BP 取已知较低值；不会降低已有最佳。重复导入不会让 best 变差，每次导入都会有独立审计记录。
- 导入写入 `best`、`csv_imports`、`csv_import_records`，不伪造 `attempts`。CSV 缺少真实逐段 ghost，不能凭总分恢复游玩中的个人最佳分数柱曲线。
- 播放次数、DJ LEVEL、最终游玩时间不转换成逐局记录；它们不用于最佳成绩合并。工具不联网，不上传成绩。

导入后需要重启游戏读取本地最佳。本工具只能验证磁盘曲库匹配，不能证明服务器是否保存过成绩或游戏是否已解锁该谱面。
