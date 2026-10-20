# Current build evidence

Original baseline: bm2dx.dll, PE timestamp 0x69ddf5f8, SizeOfImage 0xbb47000. Current supported builds are listed below.

music.reg writer: RVA 0xab6d60..0xab8564; dispatch slot 0x10536b0.
getrank reader: RVA 0xab3240..0xab3d1f; dispatch slot 0x10535f8.
These are XRPC dispatch descriptors, not object virtual tables. Hooking their slots avoids replacing the function entry that omnifix detours. Existing original function is still invoked.

Request pointer at context+0x70; response pointer +0x78. reg request: DJ ID char[16], music+0x10, chart signed byte+0x14, PG+0x1c, GREAT+0x20, BP+0x24, clear byte+0x28; nocount+0x374, grade+0x378, is_no_save byte+0x7fe. Snapshot length 0xea0.

getrank request: player char[16], style+0x10, self-cache pointers at +0x20/+0x28. Row indexed by music ID, stride 0x30: int score[5], int miss[5], byte clear[5], present byte at +0x2d. Rival pointers are different and are not touched. getranksub parser deals with rivals/subtypes and is not hooked in this version.

Actual chart converter RVA 0x815250 executes in isolated tests. chart 0..4 => SP 0..4, DP 5..9. The submit caller at RVA 0x8f6903 invokes this converter and passes its result to request builder 0x5a5ff0, which writes clid at +0x14.

Game files never bundled. Tests map a separate PE with DONT_RESOLVE_DLL_REFERENCES and stub AVS import slots only in that private mapping. No process attach or injection.

0.2 appoint reader: RVA 0xab1a80..0xab2b00, descriptor slot 0x1053718. Writer at 0xab2b00 establishes request music int+0, clid int+4, iidxid char[16]+0x110. Reader establishes response mydata[64]+0x65 and present byte+0xa5; my_gauge_data[0x300]+0x3a8 and present+0x6a8; my_option/my_option2+0x6c8/+0x6d0 and options-present+0x6d8. Only own fields are overlaid after a successful native parse. sdata target/rival fields are left intact. Options are marked absent on local replacement because the saved option encoding has not been verified against my_option semantics.

reg per-play ghost is raw+0x34 (64 bytes); gauge is raw+0x74 (0x300 bytes). Query attempts attaining best.ex in ascending attempt ID, check full snapshot size, player/music/chart/flags/PG/GREAT and ghost sum. Never choose a lower-score ghost if highest EX snapshot is absent/corrupt. Existing v0.1 database schema is unchanged. Tests execute the actual appoint parser with both missing and present server ghost and check preservation/override policy, plus existing getrank tests.

User confirmed score persistence and selection-screen display in v0.1. Remaining gameplay checks for v0.2: ghost graph display/refresh, native no-save coverage, different modes, and real server behavior. Static/isolated success does not prove these.

0.3 adds timestamp 0x6a8bafd1 / image size 0xbb93000. New rank RVA 0xad7ad0..0xad85af, slot 0x1090d88; reg 0xadb5f0..0xadcdf4, slot 0x1090e40; appoint 0xad6310..0xad7390, slot 0x1090ea8. Full instruction sequences normalized only for RIP-relative addresses and branch/call destinations match the April functions; data field offsets and constants are unchanged. Separate code hashes and rank entry hash remain mandatory. Native chart converter moves to 0x82cce0. Test AVS import slots move by 0x2e000 (verified by DLL+ordinal identity). Both April and August real parsers pass isolated tests, including the locally patched August game image. Database namespace still depends on catalog, not executable timestamp; changing only game DLL does not fork saved scores.

0.4 adds the locally patched `2026091600-010` IIDX 34 PE (timestamp `0x6aa23d57`, image `0xc5ff000`). Rank is RVA `0xabf4a0..0xac0080`, dispatch slot `0x10540e8`; reg is `0xac2a30..0xac4234`, slot `0x10541a0`; appoint is `0xabda50..0xabead0`, slot `0x1054208`. Whole-function hashes and entry hashes are checked. The reg and appoint instruction sequences match the August functions after relocation normalization. Rank retains the personal score cache fields but adds a separate native cache pointer at request offsets `+0x40/+0x48`, which Score does not modify. The isolated test supplies this new field and runs the actual rank/appoint parsers, including server-better and local-fallback cases; it also verifies the native chart converter at `0x81a5c0`. Imports are matched by `avs2-core.dll` ordinal, not assumed uniform RVA shifts. The stock catalog is `data/info/0/music_data.bin` (header version 34); scope now includes the catalog version and hash. No live game injection or server upload was used for this validation.
