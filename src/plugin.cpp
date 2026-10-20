#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <cstdio>
#include <map>
#include <tuple>
#include "store.h"
namespace App {
using Method=bool(*)(void*,void*);
static Method originalRank=nullptr,originalReg=nullptr,originalAppoint=nullptr;
static Score::Database database;
static std::mutex gate;
static std::wstring directory,iniPath;
static std::string scope;
static int musicLimit=0;
static bool overlay=true;
static bool ghostOverlay=true;
static std::map<std::tuple<std::string,int,int>,int> observedEx;
static HMODULE self;
static void log(const char* text){
 auto p=directory+L"\\IIDXScore.log";FILE* f=_wfopen(p.c_str(),L"ab");if(!f)return;
 SYSTEMTIME t;GetLocalTime(&t);fprintf(f,"%04u-%02u-%02u %02u:%02u:%02u %s\r\n",t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond,text);fclose(f);
}
static std::string utf8(const std::wstring& s){int n=WideCharToMultiByte(CP_UTF8,0,s.c_str(),(int)s.size(),nullptr,0,nullptr,nullptr);std::string r(n,0);WideCharToMultiByte(CP_UTF8,0,s.c_str(),(int)s.size(),r.data(),n,nullptr,nullptr);return r;}
static std::wstring config(const wchar_t* key,const wchar_t* fallback){wchar_t b[32768];GetPrivateProfileStringW(L"Score",key,fallback,b,32768,iniPath.c_str());return b;}
static bool read(const void* p,void* out,size_t n){SIZE_T got=0;return p&&ReadProcessMemory(GetCurrentProcess(),p,out,n,&got)&&got==n;}
template<class T> static bool readAt(const void* p,size_t off,T& out){return p&&read((const BYTE*)p+off,&out,sizeof(out));}
static bool writable(void* p,size_t n){
 MEMORY_BASIC_INFORMATION m{};if(!VirtualQuery(p,&m,sizeof(m))||m.State!=MEM_COMMIT||(m.Protect&(PAGE_GUARD|PAGE_NOACCESS)))return false;
 if(!(m.Protect&(PAGE_READWRITE|PAGE_WRITECOPY|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY)))return false;
 return (uintptr_t)p>=(uintptr_t)m.BaseAddress&&n<=m.RegionSize&&((uintptr_t)p-(uintptr_t)m.BaseAddress)<=m.RegionSize-n;
}
static std::string playerId(const unsigned char* p){
 size_t n=0;while(n<16&&p[n]){if(p[n]<'0'||p[n]>'9')return {};n++;}if(n<1||n>=16)return {};return {(const char*)p,n};
}
static int integer(const unsigned char* p,size_t off){int v;memcpy(&v,p+off,4);return v;}
static bool capture(void* ctx,Score::Attempt& a){
 void* req=nullptr;if(!readAt(ctx,0x70,req)||!read(req,a.raw.data(),a.raw.size()))return false;
 a.player=playerId(a.raw.data());a.music=integer(a.raw.data(),0x10);a.chart=(int8_t)a.raw[0x14];
 a.pg=integer(a.raw.data(),0x1c);a.great=integer(a.raw.data(),0x20);a.bp=integer(a.raw.data(),0x24);a.lamp=(int8_t)a.raw[0x28];
 a.nocount=integer(a.raw.data(),0x374);a.grade=integer(a.raw.data(),0x378);a.nosave=a.raw[0x7fe];
 return a.valid()&&a.music<musicLimit;
}
struct CacheRow {int ex[5];int bp[5];unsigned char lamp[5];unsigned char present;unsigned char pad[2];};
static_assert(sizeof(CacheRow)==0x30);
static size_t mergeCache(void* ctx){
 void* req=nullptr;unsigned char head[0x30]{};
 if(!readAt(ctx,0x70,req)||!read(req,head,sizeof(head)))return 0;
 auto player=playerId(head);int style=integer(head,0x10);if(player.empty()||style<0||style>1)return 0;
 CacheRow* cache=nullptr;memcpy(&cache,head+0x20+style*8,8);if(!cache)return 0;
 auto rows=database.rows(scope,player);size_t changed=0;
 // Record the native cache before this overlay; it is NOT proof of remote persistence.
 database.exec("BEGIN IMMEDIATE");
 try{
  struct Pending{CacheRow* address;CacheRow after;};std::vector<Pending> pending;
  for(const auto& row:rows){
   if(row.music<0||row.music>=musicLimit||row.chart/5!=style)continue;
   int chart=row.chart%5;CacheRow current{};auto dest=(CacheRow*)((uintptr_t)cache+(size_t)row.music*sizeof(CacheRow));
   if(!read(dest,&current,sizeof(current))||!writable(dest,sizeof(current)))continue;
   if(current.present>1||current.lamp[chart]>7||current.ex[chart]<-1||current.ex[chart]>300000||current.bp[chart]<-1||current.bp[chart]>100000)continue;
   Score::Best remote=current.present?Score::Best{current.ex[chart],current.bp[chart],current.lamp[chart]}:Score::Best{};
   observedEx[{player,row.music,row.chart}]=remote.ex;
   database.observe(scope,player,row,remote);
   auto merged=Score::merge(remote,row.best);
   // Multiple charts for the same song must accumulate in a single pending row.
   auto it=std::find_if(pending.begin(),pending.end(),[&](auto& p){return p.address==dest;});
   if(it!=pending.end())current=it->after;
   current.ex[chart]=merged.ex;current.bp[chart]=merged.bp;current.lamp[chart]=(unsigned char)merged.lamp;current.present=1;
   if(it!=pending.end())it->after=current;else pending.push_back({dest,current});
  }
  database.exec("COMMIT");
  for(auto& p:pending){if(writable(p.address,sizeof(p.after))){memcpy(p.address,&p.after,sizeof(p.after));changed++;}}
 }catch(...){database.exec("ROLLBACK");throw;}
 return changed;
}
static bool regHook(void* ctx,void* node){
 try{std::lock_guard<std::mutex> lock(gate);Score::Attempt a;if(capture(ctx,a)){database.save(scope,a);log(a.eligible()?"Local score committed (remote persistence unknown).":"Attempt archived; excluded from best due to native flags.");}else log("Score snapshot rejected; original request continues.");}
 catch(const std::exception& e){log(e.what());}
 return originalReg(ctx,node);
}
static bool rankHook(void* ctx,void* node){
 bool ok=originalRank(ctx,node);if(!ok||!overlay)return ok;
 try{std::lock_guard<std::mutex> lock(gate);auto n=mergeCache(ctx);if(n)log("Local best merged into native personal score cache.");}
 catch(const std::exception& e){log(e.what());}return ok;
}
static bool mergeGhost(void* ctx){
 void* req=nullptr;BYTE* response=nullptr;unsigned char head[0x120]{};
 if(!readAt(ctx,0x70,req)||!read(req,head,sizeof(head))||!readAt(ctx,0x78,response))return false;
 auto player=playerId(head+0x110);int music=integer(head,0),chart=integer(head,4);
 if(player.empty()||music<0||music>=musicLimit||chart<0||chart>9)return false;
 // Native appoint reader: mydata at +0x65, present at +0xa5.
 unsigned char current[65]{};
 if(!response||!read(response+0x65,current,sizeof(current))||current[64]>1||!writable(response,0x6d9))return false;
 Score::Ghost best;if(!database.ghost(scope,player,music,chart,best))return false;
 auto known=observedEx.find({player,music,chart});if(known!=observedEx.end()&&known->second>best.ex)return false;
 int remote=0;for(int i=0;i<64;i++)remote+=current[i];
 if(current[64]&&remote>=best.ex)return false; // Keep server curve on equal or better EX.
 memcpy(response+0x65,best.data.data(),64);response[0xa5]=1;
 memcpy(response+0x3a8,best.gauge.data(),0x300);response[0x6a8]=1;
 // The older snapshot does not establish the server's my_option semantics.
 // Mark options absent rather than attach another attempt's options to this ghost.
 response[0x6d8]=0;
 return true;
}
static bool appointHook(void* ctx,void* node){
 bool ok=originalAppoint(ctx,node);if(!ok||!overlay||!ghostOverlay)return ok;
 try{std::lock_guard<std::mutex> lock(gate);if(mergeGhost(ctx))log("Local personal-best ghost restored into native appoint response.");}
 catch(const std::exception& e){log(e.what());}return ok;
}
struct Target{DWORD rva,end,slot;uint64_t hash;};
static constexpr Target targetsApril[]={
 {0xab3240,0xab3d1f,0x10535f8,0x8339e8ace3822b18ULL},
 {0xab6d60,0xab8564,0x10536b0,0x0efed1b1d521df99ULL},
 {0xab1a80,0xab2b00,0x1053718,0x6b479a0b39f11295ULL}
};
static constexpr Target targetsAugust[]={
 {0xad7ad0,0xad85af,0x1090d88,0x4be2b745a742428aULL},
 {0xadb5f0,0xadcdf4,0x1090e40,0xa586ab6fd816877cULL},
 {0xad6310,0xad7390,0x1090ea8,0xb5291c30cead53e7ULL}
};
static constexpr Target targetsSeptember[]={
 {0xabf4a0,0xac0080,0x10540e8,0x21048fdf5a9cf31dULL},
 {0xac2a30,0xac4234,0x10541a0,0x0488067a4d603138ULL},
 {0xabda50,0xabead0,0x1054208,0x14b9251013ef812dULL}
};
static const Target* targets=targetsApril;
static bool latestBuild=false,currentBuild=false;
static uint64_t fnv(const BYTE* p,size_t n){uint64_t h=14695981039346656037ULL;for(size_t i=0;i<n;i++)h=(h^p[i])*1099511628211ULL;return h;}
static bool validate(BYTE* base){
 IMAGE_DOS_HEADER dos{};IMAGE_NT_HEADERS64 nt{};
 if(!read(base,&dos,sizeof(dos))||dos.e_magic!=IMAGE_DOS_SIGNATURE||dos.e_lfanew<0||dos.e_lfanew>0x1000||!read(base+dos.e_lfanew,&nt,sizeof(nt)))return false;
 if(nt.Signature!=IMAGE_NT_SIGNATURE||nt.FileHeader.Machine!=IMAGE_FILE_MACHINE_AMD64)return false;
 currentBuild=nt.FileHeader.TimeDateStamp==0x6aa23d57&&nt.OptionalHeader.SizeOfImage==0xc5ff000;
 latestBuild=nt.FileHeader.TimeDateStamp==0x6a8bafd1&&nt.OptionalHeader.SizeOfImage==0xbb93000;
 if(!currentBuild&&!latestBuild&&(nt.FileHeader.TimeDateStamp!=0x69ddf5f8||nt.OptionalHeader.SizeOfImage!=0xbb47000))return false;
 targets=currentBuild?targetsSeptember:(latestBuild?targetsAugust:targetsApril);
 BYTE rankPrefix[32],regPrefix[32];
 if(!read(base+targets[0].rva,rankPrefix,32)||!read(base+targets[1].rva,regPrefix,32))return false;
 if(fnv(rankPrefix,32)!=(currentBuild?0x883334e56ba575deULL:(latestBuild?0xc0cb74d4de56ce96ULL:0x8fc05328d6b09b84ULL)))return false;
 BYTE appointPrefix[32];if(!read(base+targets[2].rva,appointPrefix,32)||fnv(appointPrefix,32)!=0xc9a66f5264ba7c6eULL)return false;
 // A pre-existing reg detour is allowed only in a recognized entry-jump form.
 // We call that entry, preserving the other module's hook chain.
 if(fnv(regPrefix,32)!=0x3ab0a9489048097eULL && regPrefix[0]!=0xe9 &&
    !(regPrefix[0]==0xff&&regPrefix[1]==0x25))return false;
 for(size_t i=0;i<3;i++){const auto& t=targets[i];void* ptr=nullptr;if(!readAt(base,t.slot,ptr)||ptr!=base+t.rva)return false;
  std::vector<BYTE> code(t.end-t.rva-32);if(!read(base+t.rva+32,code.data(),code.size())||fnv(code.data(),code.size())!=t.hash)return false;
 }
 return true;
}
static bool replace(void** slot,void* expected,void* replacement){
 DWORD old;if(!VirtualProtect(slot,sizeof(void*),PAGE_READWRITE,&old))return false;
 auto before=InterlockedCompareExchangePointer(slot,replacement,expected);DWORD ignored;VirtualProtect(slot,sizeof(void*),old,&ignored);return before==expected;
}
static bool install(BYTE* base){
 if(!validate(base))return false;
 originalRank=(Method)(base+targets[0].rva);originalReg=(Method)(base+targets[1].rva);
 auto rank=(void**)(base+targets[0].slot),reg=(void**)(base+targets[1].slot);
 if(!replace(rank,(void*)originalRank,(void*)rankHook))return false;
 if(!replace(reg,(void*)originalReg,(void*)regHook)){replace(rank,(void*)rankHook,(void*)originalRank);return false;}
 originalAppoint=(Method)(base+targets[2].rva);
 if(!replace((void**)(base+targets[2].slot),(void*)originalAppoint,(void*)appointHook)){
  replace(reg,(void*)regHook,(void*)originalReg);replace(rank,(void*)rankHook,(void*)originalRank);return false;
 }return true;
}
static std::string hashFile(const std::filesystem::path& path){
 std::ifstream f(path,std::ios::binary);if(!f)throw std::runtime_error("MusicDatabase cannot be read; refusing to mix unknown catalogs");
 BCRYPT_ALG_HANDLE alg=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;
 if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)throw std::runtime_error("SHA256 unavailable");
 if(BCryptCreateHash(alg,&hash,nullptr,0,nullptr,0,0)<0){BCryptCloseAlgorithmProvider(alg,0);throw std::runtime_error("SHA256 creation failed");}
 unsigned char digest[32];char buffer[65536];bool ok=true;
 while(f){f.read(buffer,sizeof(buffer));auto n=f.gcount();if(n&&BCryptHashData(hash,(PUCHAR)buffer,(ULONG)n,0)<0){ok=false;break;}}
 if(f.bad())ok=false;if(BCryptFinishHash(hash,digest,sizeof(digest),0)<0)ok=false;BCryptDestroyHash(hash);BCryptCloseAlgorithmProvider(alg,0);
 if(!ok)throw std::runtime_error("Catalog hash failed");std::string out;const char* hex="0123456789abcdef";for(auto b:digest){out+=hex[b>>4];out+=hex[b&15];}return out;
}
struct CatalogSong {std::array<unsigned char,256> title{};std::array<unsigned char,10> levels{};};
static std::map<int,CatalogSong> readCatalog(const std::filesystem::path& path,int expectedVersion){
 std::ifstream file(path,std::ios::binary|std::ios::ate);if(!file)throw std::runtime_error("Migration catalog cannot be opened");
 auto length=file.tellg();file.seekg(0);int header[4]{};file.read((char*)header,sizeof(header));
 if(!file||memcmp(header,"IIDX",4)||header[1]!=expectedVersion||header[2]<1||header[2]>35000||header[3]<1||header[3]>100000||length!=std::streamoff(16LL+4LL*header[3]+2040LL*header[2]))throw std::runtime_error("Migration catalog layout mismatch");
 file.seekg(16LL+4LL*header[3]);std::map<int,CatalogSong> songs;
 for(int i=0;i<header[2];i++){
  std::array<unsigned char,2040> row{};file.read((char*)row.data(),row.size());if(!file)throw std::runtime_error("Migration catalog truncated");
  int id;memcpy(&id,row.data()+0x67c,4);if(id<0||id>=header[3])throw std::runtime_error("Migration catalog has invalid song ID");
  CatalogSong song;memcpy(song.title.data(),row.data(),song.title.size());memcpy(song.levels.data(),row.data()+0x3ec,song.levels.size());
  if(!songs.emplace(id,song).second)throw std::runtime_error("Migration catalog has duplicate song ID");
 }
 return songs;
}
static int catalogVersion(const std::filesystem::path& path){
 int header[2]{};std::ifstream file(path,std::ios::binary);file.read((char*)header,sizeof(header));
 if(!file||memcmp(header,"IIDX",4)||(header[1]!=33&&header[1]!=34))throw std::runtime_error("Invalid IIDX catalog version");
 return header[1];
}
static void addOmniSongs(std::map<int,CatalogSong>& ac,const std::map<int,CatalogSong>& omni){
 for(const auto& [id,song]:omni){
  auto found=ac.find(id);
  if(found==ac.end()){ac.emplace(id,song);continue;}
  if(found->second.title!=song.title)continue;
  for(int chart=0;chart<10;chart++)if(!found->second.levels[chart])found->second.levels[chart]=song.levels[chart];
 }
}
static std::map<int,CatalogSong> combinedCatalog(const std::filesystem::path& acPath,int acVersion,const std::filesystem::path& omniPath,int omniVersion){
 auto ac=readCatalog(acPath,acVersion);addOmniSongs(ac,readCatalog(omniPath,omniVersion));
 return ac;
}
static std::vector<Score::ChartMap> matchingCharts(const std::map<int,CatalogSong>& oldSongs,const std::map<int,CatalogSong>& newSongs){
 std::vector<Score::ChartMap> matches;
 std::map<std::array<unsigned char,256>,int> oldTitleCounts,targetTitleCounts,targetTitleIds;
 for(const auto& [id,song]:oldSongs)oldTitleCounts[song.title]++;
 for(const auto& [id,song]:newSongs){targetTitleCounts[song.title]++;targetTitleIds[song.title]=id;}
 for(const auto& [id,oldSong]:oldSongs){
  auto it=newSongs.find(id);
  if(it==newSongs.end()){
   // A pre-release song can be assigned a new ID in the next AC version.
   // Require the title to be unique in both catalogs before rekeying.
   if(oldTitleCounts[oldSong.title]!=1||targetTitleCounts[oldSong.title]!=1)continue;
   it=newSongs.find(targetTitleIds[oldSong.title]);
  }
  if(it==newSongs.end()||oldSong.title!=it->second.title)continue;
  for(int chart=0;chart<10;chart++)if(oldSong.levels[chart]&&oldSong.levels[chart]==it->second.levels[chart])matches.push_back({id,it->first,chart});
 }
 return matches;
}
static std::vector<Score::ChartMap> matchingCharts(const std::filesystem::path& oldPath,const std::filesystem::path& newPath,int oldVersion,int newVersion){
 return matchingCharts(readCatalog(oldPath,oldVersion),readCatalog(newPath,newVersion));
}
static DWORD WINAPI worker(void*){
 try{
  wchar_t own[MAX_PATH];if(!GetModuleFileNameW(self,own,MAX_PATH))return 0;directory=std::filesystem::path(own).parent_path().wstring();iniPath=directory+L"\\IIDXScore.ini";
  if(!GetPrivateProfileIntW(L"Score",L"Enabled",1,iniPath.c_str()))return 0;
  overlay=GetPrivateProfileIntW(L"Score",L"Overlay",1,iniPath.c_str())!=0;
  ghostOverlay=GetPrivateProfileIntW(L"Score",L"GhostOverlay",1,iniPath.c_str())!=0;
  auto catalog=std::filesystem::path(config(L"MusicDatabase",L"data/info/0/music_data.bin"));if(catalog.is_relative())catalog=std::filesystem::path(directory)/catalog;
  std::ifstream f(catalog,std::ios::binary);int header[4]{};f.read((char*)header,sizeof(header));
  if(!f||memcmp(header,"IIDX",4)||(header[1]!=33&&header[1]!=34)||header[3]<=0||header[3]>100000)throw std::runtime_error("Invalid music database header");musicLimit=header[3];
  scope=utf8(config(L"ServerKey",L"default"))+"/iidx"+std::to_string(header[1])+"/"+hashFile(catalog);
  auto acScope=scope;
  auto omniName=config(L"OmniMusicDatabase",L"");std::filesystem::path omniCatalog;int omniVersion=0;std::string omniPureScope;
  if(!omniName.empty()){
   omniCatalog=std::filesystem::path(omniName);if(omniCatalog.is_relative())omniCatalog=std::filesystem::path(directory)/omniCatalog;
   omniVersion=catalogVersion(omniCatalog);readCatalog(omniCatalog,omniVersion);
   auto omniHash=hashFile(omniCatalog);
   omniPureScope=utf8(config(L"ServerKey",L"default"))+"/iidx"+std::to_string(omniVersion)+"/"+omniHash;
   scope+="/omni/"+omniHash;
  }
  auto path=std::filesystem::path(config(L"DatabasePath",L"scores/IIDXScore.sqlite3"));if(path.is_relative())path=std::filesystem::path(directory)/path;
  std::filesystem::create_directories(path.parent_path());database.open(utf8(path.wstring()));
  BYTE* base=nullptr;for(int i=0;i<600&&!base;i++){base=(BYTE*)GetModuleHandleW(L"bm2dx.dll");if(!base)Sleep(100);}
  if(!base||!validate(base)){log("Unsupported or modified score ABI; no hooks installed.");return 0;}
  if((currentBuild&&header[1]!=34)||(!currentBuild&&header[1]!=33)){log("Game and music database versions differ; no hooks installed.");return 0;}
  if(omniVersion&&omniVersion!=header[1])log("Omni index version differs from game; score matching only, game song availability unverified.");
  if(!omniCatalog.empty()){
   auto targetCatalog=combinedCatalog(catalog,header[1],omniCatalog,omniVersion);
   auto charts=matchingCharts(readCatalog(catalog,header[1]),targetCatalog);
   auto counts=database.migrateMapped(acScope,scope,charts);
   auto message="AC score migration into AC+Omni: "+std::to_string(counts.first)+" best rows, "+std::to_string(counts.second)+" attempts copied.";log(message.c_str());
   if(omniPureScope!=acScope){
    charts=matchingCharts(readCatalog(omniCatalog,omniVersion),targetCatalog);
    counts=database.migrateMapped(omniPureScope,scope,charts);
    message="Omni score migration into AC+Omni: "+std::to_string(counts.first)+" best rows, "+std::to_string(counts.second)+" attempts copied.";log(message.c_str());
   }
  }
  auto oldName=config(L"LegacyMusicDatabase",L"");
  if(!oldName.empty()){
   auto oldCatalog=std::filesystem::path(oldName);if(oldCatalog.is_relative())oldCatalog=std::filesystem::path(directory)/oldCatalog;
   int oldVersion=catalogVersion(oldCatalog);
   auto previous=utf8(config(L"ServerKey",L"default"))+"/iidx"+std::to_string(oldVersion)+"/"+hashFile(oldCatalog);
   if(previous!=scope&&previous!=omniPureScope){auto charts=omniCatalog.empty()?matchingCharts(oldCatalog,catalog,oldVersion,header[1]):matchingCharts(readCatalog(oldCatalog,oldVersion),combinedCatalog(catalog,header[1],omniCatalog,catalogVersion(omniCatalog)));auto counts=database.migrateMapped(previous,scope,charts);
    auto message="Legacy score migration: "+std::to_string(charts.size())+" matching charts, "+std::to_string(counts.first)+" best rows, "+std::to_string(counts.second)+" attempts copied.";log(message.c_str());}
  }
  if(!install(base)){log("Score hook installation failed; no hooks installed.");return 0;}
  HMODULE pinned;GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,(LPCWSTR)&worker,&pinned);
  log("IIDXScore 0.5.3: capture, personal getrank, appoint ghost and AC+Omni migration support installed.");
 }catch(const std::exception& e){log(e.what());}return 0;
}
}
#ifndef IIDX_SCORE_TEST
BOOL WINAPI DllMain(HINSTANCE instance,DWORD reason,LPVOID){if(reason==DLL_PROCESS_ATTACH){App::self=instance;DisableThreadLibraryCalls(instance);HANDLE h=CreateThread(nullptr,0,App::worker,nullptr,0,nullptr);if(h)CloseHandle(h);}return TRUE;}
#endif

