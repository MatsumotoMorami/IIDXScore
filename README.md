# IIDXScore

独立保存 IIDX 33/34 的个人游玩成绩，在游戏读取个人成绩时，将本地最高 EX 分、最好灯和最低有效 BP 合并到原生个人成绩缓存。

0.2 增加个人最佳 ghost 回显：读入本地最高 EX 对应的真实分段数据，让游玩中的个人最佳分数柱与本地成绩一起恢复。已有完整游玩快照可直接使用，无需重新打出成绩。

不依赖 IIDXRecorder、IIDXLive 或 omnifix DLL；SQLite 静态编译在插件内。不连接第三方服务，不更换游戏服务器，不改变音频设置。

## 当前版本范围

0.5.3 支持两种 IIDX 33 构建（PE 时间戳/映像大小 `69ddf5f8 / 0xbb47000`、`6a8bafd1 / 0xbb93000`）及本机 patch 后的 IIDX 34 `2026091600-010`（`6aa23d57 / 0xc5ff000`），并加入可选的 AC+Omni 旧曲库成绩迁移。启动时检查成绩函数代码和 XRPC 分发表，未知或不匹配版本拒绝挂接；游戏版本与 AC 曲库版本不一致也拒绝挂接。

- 在 `music.reg` 组包入口备份成绩；提交函数失败也不会撤销已落盘的备份。
- 在原生 `getrank` 解析完成后，仅合并当前玩家的个人成绩缓存；不修改 rival 数组、远端排行榜或返回包。
- SP/DP、五档难度、不同玩家及不同曲库分别存储。
- 完整请求数据快照留作补充分析；不把原始进程指针当作可恢复对象。
- 数据库采用 WAL 和 FULL 同步。不要在游戏运行时只复制主数据库文件：备份时应退出游戏，或使用 SQLite 的备份功能。

旧构建的成绩保存和选曲回显已由用户实机验证；个人 ghost 回显及 0916 新构建适配通过隔离测试，仍需实机验收。只补入自己的最高分曲线及同次游玩的 gauge，不修改 rival/全国目标曲线、段位证书、竞技排名或统计雷达。没有触发 `music.reg` 的游玩暂时无法由此入口保存；强退前尚未结算也没有成绩记录。

本地 ghost 必须来自有效游玩，且 64 段合计等于最高 EX。低分获得更好灯或 BP 不会替换最高分曲线；同分保留最早的有效本地曲线，服务器已有同分或更高曲线时优先保留服务器数据。若原生个人成绩缓存显示更高的分数，也不会用较低本地曲线补齐。缺少最高分原始曲线时不会按最终分数拼造。回填时将未核实含义的历史选项标记为不可用，避免显示另一局的选项。

原生标记 `nocnt`、`is_grade` 或 `is_no_save` 的请求会存历史，但保守地不更新本地最佳。此规则需要实机核对，不能把所有辅助或特殊模式成绩一律当作正常成绩。

原生个人缓存可能同时含本次会话已有成绩，所以 `cache_observations` 只表示“合并前缓存”，**不作为服务器已保存的证据**。插件不自行重传成绩或构造上传请求；游戏后续是否把个人最佳作为提交元数据使用，仍需实机检查。

## 使用（本包不会自动安装）

1. 等游戏退出后，将 `IIDXScore.dll`、`IIDXScore.ini` 放在 `bm2dx.dll` 同目录。升级时保留自己的数据库，并核对已有 INI。
2. 核对 INI 的 `MusicDatabase`，0916 原版曲库应指向 `data/info/0/music_data.bin`。此前使用 Omnimix 的 INI 若仍指向已停用的 `data_mods/ominimix`，必须改为当前实际曲库。
3. 下次启动前，由你在 Spice 的 DLL 注入列表中添加 `IIDXScore.dll`。
4. 查看 `IIDXScore.log` 是否记录 installed。先测试一张可存分曲和一张存不上分曲，再退出重登确认显示。

禁用：从注入列表移除 DLL，或将 `Enabled=0`。已有 SQLite 数据保留。

## 配置

所有设置在 `[Score]` 中，修改后重启游戏生效。相对路径以 DLL 所在目录为基准。

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| Enabled | 1 | 1 启用；0 不挂接 |
| Overlay | 1 | 1 回显本地最佳；0 只备份 |
| GhostOverlay | 1 | 1 回显本地个人最佳 ghost；0 关闭。需要 Overlay=1；只在读谱面数据时访问数据库，不在逐帧或判定回调中查询 |
| DatabasePath | scores/IIDXScore.sqlite3 | 本地数据库路径 |
| ServerKey | default | 手动指定服务器作用域；换服务器时更换此名称 |
| MusicDatabase | data/info/0/music_data.bin | 实际曲库文件；仅接受 IIDX 33/34，且曲库版本必须与游戏版本一致 |
| OmniMusicDatabase | 空 | 可选本地 Omni 索引；对当前 AC 曲库补充缺失歌曲／谱面，形成独立的 AC+Omni 成绩作用域 |
| LegacyMusicDatabase | 空 | 可选旧曲库索引；匹配旧作用域并受限迁移成绩，空值关闭 |

游戏代次和整个曲库索引的 SHA256 仍用于隔离成绩。若需要把旧 IIDX 33/Omnimix 成绩带到 IIDX 34，可配置 `LegacyMusicDatabase` 执行受限迁移；未配置时旧作用域保持隔离。

## 旧曲库成绩迁移

先退出游戏，用 SQLite 备份功能备份完整数据库；不要只复制正在使用 WAL 的主文件。`MusicDatabase` 指向当前 IIDX 34 AC 曲库；若实际使用 Omni，再让 `OmniMusicDatabase` 指向本地 Omni 索引，组成 AC+Omni 成绩作用域：相同歌曲 ID 优先使用 AC 标题及已有谱面，Omni 补充 AC 缺失的歌曲或谱面。`LegacyMusicDatabase` 指向产生旧成绩时使用的**确切** `music_omni.bin` 或 `music_data.bin`，并保持原来的 `DatabasePath` 和 `ServerKey`。启动时插件把同服务器的已有 AC 成绩、当前 Omni 索引对应的独立作用域成绩，以及指定旧曲库的成绩合并到 AC+Omni 作用域；歌曲 ID 相同时要求标题原始字节、对应 SP/DP 档位和难度等级一致。旧歌曲 ID 在目标索引不存在时，只有新旧索引中的标题各自唯一且谱面等级一致，才按目标 ID 合并。EX、灯和 BP 各自取较优值；旧作用域不删除。此后新游玩直接写入 AC+Omni 作用域。迁移可重复执行，原始游玩快照不会被重复复制。

`OmniMusicDatabase` 只用于成绩匹配，不安装、启用或验证游戏中的 Omni 曲包。如果游戏实际未加载这些歌曲，迁移出的成绩会留在数据库中，但游戏不会显示不存在的歌曲。不要把旧 IIDX 33 Omni 索引误当作已适配 IIDX 34 的游戏曲包。

对于 IIDX 34 AC 索引在 `data/info/0`、当前 Omni 索引在 `data_mods/omnimix/info/0`、旧版 IIDX 33 索引放在 `IIDXScoreLegacy` 的布局，可在保留原有 `DatabasePath` 和 `ServerKey` 的前提下设置：

```ini
MusicDatabase=data/info/0/music_data.bin
OmniMusicDatabase=data_mods/omnimix/info/0/music_omni.bin
LegacyMusicDatabase=IIDXScoreLegacy/music_omni.bin
```

启用这三项并加载 DLL 后，下次启动会**正式写入迁移结果**。先运行 Release 中的 `IIDXScoreDryRun.exe` 核对预测，再备份数据库。

无法确认对应关系的成绩保持在旧作用域，不会回显到新曲库。相同标题与等级并不能严格证明谱面音符完全一致；如需更严格的逐谱面迁移，仍需谱面数据指纹。CSV 导入产生的最佳成绩通常没有原始游玩快照，因此可以迁移分数、灯和 BP，但无法凭空恢复个人 ghost 分数柱。迁移过程及计数写入 `IIDXScore.log`。

## CSV 导入工具

离线工具 `tools/import_csv.py` 目前仅支持 IIDX 33 曲库的日文表头 SP/DP 成绩 CSV，**不能导入到 0916 的 IIDX 34 曲库作用域**。默认仅生成预览报告；正式写入必须显式指定数据库、玩家 ID 和服务器作用域，并自动备份。详见 [CSV 导入说明](tools/CSV导入说明.md)。

## 数据结构

- `attempts`：每次捕获到的提交快照、判定、BP、灯及有效性标记。网络重试可能产生重复快照，不应直接作为游玩次数。
- `best`：按作用域、玩家、曲目和谱面分别维护最佳分数、灯及 BP。
- `cache_observations`：回显前的原生缓存值，不代表服务器确认。

## 构建

需要 Zig 0.14.1。SQLite 3.50.4 源码位于 `vendor/sqlite`。

```powershell
./build.ps1 -ZigPath C:/tools/zig/zig.exe
```

离线测试（第二个参数须为新目录）：

```powershell
./build/test-score.exe C:/IIDX/bm2dx.dll ./build/test-run-1
```

测试在自己的进程中以 `DONT_RESOLVE_DLL_REFERENCES` 映射磁盘 PE，不执行游戏入口，不连接运行中的游戏。对该隔离映像的导入函数提供模拟接口，再执行真实的成绩读取函数；绝不向现有游戏进程注入。

## 第一次实机验收

- 单曲结算后检查数据库记录、选曲界面的 EX 分/灯/BP。
- 退出重登及完整重启，检查不能存分的谱面仍显示本地最佳。
- 再打低分/高分验证各项独立更新；检查 SP/DP 和换卡隔离。
- 确认普通服务器成绩不降低、竞争对手数据不变化。
- 0.2：重开曾经存不上分的谱面，确认个人最佳柱随真实历史进度增长；日志应出现 `Local personal-best ghost restored`。再检查更低分未覆盖旧曲线、服务器更高曲线被保留。
- 单独检查跳关、PF、Step Up、段位及 Arena 的原生有效性标记。

本项目源码使用 MIT 许可证；SQLite 属于公有领域，见 `THIRD_PARTY.md`。
