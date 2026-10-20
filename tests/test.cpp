#define IIDX_SCORE_TEST
#include "../src/plugin.cpp"
#include <iostream>
#include <cstdlib>
static void check(bool ok,const char* msg){if(!ok){fprintf(stderr,"FAIL: %s\n",msg);exit(1);}}
static uintptr_t mappedGame=0;
static LONG CALLBACK diagnostic(EXCEPTION_POINTERS* e){
 if(e->ExceptionRecord->ExceptionCode!=EXCEPTION_ACCESS_VIOLATION)return EXCEPTION_CONTINUE_SEARCH;
 fprintf(stderr,"Test exception %08lx at %p (game RVA %llx)\n",e->ExceptionRecord->ExceptionCode,e->ExceptionRecord->ExceptionAddress,(unsigned long long)((uintptr_t)e->ExceptionRecord->ExceptionAddress-mappedGame));fflush(stderr);return EXCEPTION_CONTINUE_SEARCH;
}
template<class T> void put(unsigned char* p,size_t offset,T value){memcpy(p+offset,&value,sizeof(value));}
static bool stubReg(void*,void*){return false;}
static void* propertyStub(void*){return nullptr;}
static bool hasServerRow=false;
static int serverRow[17];
static bool hasServerGhost=false;
static std::array<unsigned char,64> serverGhost{};
static void* searchStub(void*,void*,const char* name){
 if(hasServerGhost&&name&&!strcmp(name,"mydata"))return serverGhost.data();
 return hasServerRow&&name&&!strcmp(name,"m")?serverRow:nullptr;
}
static int nodeSizeStub(void*){return 64;}
static int attributeStub(void*,void*,const char*,int,void*,unsigned){return -1;}
static char* stringCopyStub(char* out,const char* in,size_t n){return strncpy(out,in,n);}
static int nodeReadStub(void* node,int type,void* out,unsigned bytes){
 if(node==serverGhost.data()&&type==10&&bytes==64){memcpy(out,serverGhost.data(),64);return 64;}
 if(node!=serverRow||type!=6||bytes!=sizeof(serverRow))return -1;
 memcpy(out,serverRow,bytes);return (int)bytes;
}
static void* nextStub(void*,int){return nullptr;}
static void setPointer(void* at,void* value){DWORD old;check(VirtualProtect(at,8,PAGE_READWRITE,&old),"protect IAT");memcpy(at,&value,8);DWORD tmp;VirtualProtect(at,8,old,&tmp);}
int main(int argc,char** argv){
 AddVectoredExceptionHandler(1,diagnostic);
 check(argc==3,"usage: test-score.exe bm2dx.dll test-output-directory");
 auto path=std::filesystem::absolute(argv[2]);std::filesystem::create_directories(path);App::directory=path.wstring();
 auto dbPath=path/"scores.sqlite3";check(!std::filesystem::exists(dbPath),"use fresh test directory");
 App::database.open(dbPath.u8string());App::scope="test-server/catalog-a";App::musicLimit=20;
 Score::Attempt a;a.player="12345678";a.music=3;a.chart=3;a.pg=1000;a.great=200;a.bp=30;a.lamp=4;
 App::database.save(App::scope,a);a.pg=900;a.great=0;a.bp=20;a.lamp=5;App::database.save(App::scope,a);
 auto rows=App::database.rows(App::scope,a.player);check(rows.size()==1&&rows[0].best.ex==2200&&rows[0].best.bp==20&&rows[0].best.lamp==5,"independent best fields");
 a.chart=4;a.bp=-1;App::database.save(App::scope,a);a.chart=9;App::database.save(App::scope,a);
 a.chart=2;a.grade=1;App::database.save(App::scope,a);a.grade=0;a.nosave=1;App::database.save(App::scope,a);a.nosave=0;a.nocount=1;App::database.save(App::scope,a);a.nocount=0;
 check(App::database.rows(App::scope,a.player).size()==3,"native excluded flags archive only");
 check(App::database.rows("test-server/catalog-b",a.player).empty(),"catalog isolation");check(App::database.rows(App::scope,"87654321").empty(),"player isolation");
 {Score::Database reopened;reopened.open(dbPath.u8string());check(reopened.rows(App::scope,a.player).size()==3,"durable reopen");}
 Score::Best unknown{-1,-1,0};check(Score::merge(unknown,{0,0,1}).bp==0,"zero BP valid");check(Score::merge({3000,5,7},{2000,-1,4}).ex==3000,"server better retained");
 std::array<unsigned char,0x80> ctx{};std::array<unsigned char,0xea0> req{};std::array<unsigned char,16> response{};
 memcpy(req.data(),"12345678",9);put(ctx.data(),0x70,(void*)req.data());put(ctx.data(),0x78,(void*)response.data());
 std::array<App::CacheRow,22> cache{},dp{};memset(cache.data(),0,sizeof(cache));
 for(auto& r:cache){for(int i=0;i<5;i++){r.ex[i]=-1;r.bp[i]=-1;}}
 for(auto& r:dp){for(int i=0;i<5;i++){r.ex[i]=-1;r.bp[i]=-1;}}
 cache[0].pad[0]=0x7b;cache[21].pad[0]=0x7c;
 put(req.data(),0x20,(void*)(cache.data()+1));put(req.data(),0x28,(void*)(dp.data()+1));put(req.data(),0x10,0);
 cache[4].present=1;cache[4].ex[3]=2500;cache[4].bp[3]=25;cache[4].lamp[3]=4;
 check(App::mergeCache(ctx.data())==1,"one song multi-chart merged");check(cache[4].ex[3]==2500&&cache[4].bp[3]==20&&cache[4].lamp[3]==5,"remote/local combination");
 check(cache[4].ex[4]==1800&&cache[4].bp[4]==-1,"second chart retained; unknown BP preserved");check(cache[0].pad[0]==0x7b&&cache[21].pad[0]==0x7c,"cache canaries");
 put(req.data(),0x10,1);check(App::mergeCache(ctx.data())==1&&dp[4].ex[4]==1800,"DP separated");
 memcpy(req.data(),"87654321",9);check(App::mergeCache(ctx.data())==0,"switch player no overlay");
 memcpy(req.data(),"12345678",9);put(req.data(),0x10,3);req[0x14]=1;put(req.data(),0x1c,1234);put(req.data(),0x20,12);put(req.data(),0x24,4);req[0x28]=6;
 App::originalReg=stubReg;check(!App::regHook(ctx.data(),nullptr),"original return unchanged");rows=App::database.rows(App::scope,"12345678");check(rows.size()==4,"save despite unsuccessful original callback");
 Score::Attempt snap;check(App::capture(ctx.data(),snap)&&snap.best().ex==2480&&snap.chart==1,"native request layout");
 App::database.exec("CREATE TRIGGER fail_save BEFORE INSERT ON attempts BEGIN SELECT RAISE(ABORT,'injected disk-write failure'); END;");
 put(req.data(),0x1c,2000);check(!App::regHook(ctx.data(),nullptr),"storage error cannot suppress original callback");
 auto unchanged=App::database.rows(App::scope,"12345678");
 for(auto& r:unchanged)if(r.chart==1)check(r.best.ex==2480,"failed transaction does not update best");
 App::database.exec("DROP TRIGGER fail_save;");
 // Map the PE without running its entry point or resolving imports. This process is not the game.
 auto game=(BYTE*)LoadLibraryExA(argv[1],nullptr,DONT_RESOLVE_DLL_REFERENCES);check(game!=nullptr,"map game PE without init");
 mappedGame=(uintptr_t)game;
 check(App::validate(game),"real PE fingerprints");
 auto iat=[](DWORD old)->DWORD{
  if(App::currentBuild){switch(old){
   case 0xc61dd0:return 0xc66de8; // avs2-core ordinal 175
   case 0xc61f50:return 0xc66f60; // ordinal 162
   case 0xc61eb8:return 0xc67088; // ordinal 172
   case 0xc62060:return 0xc67070; // ordinal 167
   case 0xc61f58:return 0xc66f68; // ordinal 171
   case 0xc61ee0:return 0xc67220; // ordinal 176
   case 0xc61f80:return 0xc66f90; // ordinal 224
  }}
  return old+(App::latestBuild?0x2e000:0);
 };
 auto slot=(void**)(game+App::targets[0].slot);auto original=*slot;setPointer(slot,nullptr);check(!App::validate(game),"changed dispatch rejected");setPointer(slot,original);
 auto instruction=game+App::targets[0].rva+40;DWORD old;VirtualProtect(instruction,1,PAGE_READWRITE,&old);BYTE prior=*instruction;*instruction^=1;check(!App::validate(game),"changed ABI rejected");*instruction=prior;DWORD unused;VirtualProtect(instruction,1,old,&unused);
 check(App::install(game),"install in isolated PE only");
 setPointer(game+iat(0xc61dd0),(void*)propertyStub);setPointer(game+iat(0xc61f50),(void*)searchStub);
 // Actual native getrank reader receives an empty server response; then the hook fills local best.
 memset(req.data(),0,req.size());memcpy(req.data(),"12345678",9);put(req.data(),0x20,(void*)(cache.data()+1));put(req.data(),0x28,(void*)(dp.data()+1));
 std::array<unsigned char,22*0x18> extraSp{},extraDp{};
 if(App::currentBuild){put(req.data(),0x40,(void*)(extraSp.data()+0x18));put(req.data(),0x48,(void*)(extraDp.data()+0x18));}
 cache[4].ex[1]=-1;cache[4].bp[1]=-1;cache[4].lamp[1]=0;
 check(((App::Method)*slot)(ctx.data(),nullptr),"execute actual native getrank parser + overlay");
 check(cache[4].ex[1]==2480&&cache[4].bp[1]==4&&cache[4].lamp[1]==6,"actual parser empty-server fallback");
 setPointer(game+iat(0xc61eb8),(void*)nodeReadStub);setPointer(game+iat(0xc62060),(void*)nextStub);
 std::array<unsigned char,22*0x48> nativeArenaCache{};
 put(req.data(),0x30,(void*)nativeArenaCache.data());
 serverRow[0]=-1;serverRow[1]=3;
 for(int i=0;i<5;i++){serverRow[2+i]=4;serverRow[7+i]=3000;serverRow[12+i]=10;}
 hasServerRow=true;
 check(((App::Method)*slot)(ctx.data(),nullptr),"actual parser populated-server response");
 check(cache[4].ex[1]==3000&&cache[4].bp[1]==4&&cache[4].lamp[1]==6,"real parser retains remote score and local lamp/BP");
 // Verify actual game's chart conversion: B/N/H/A/L and SP/DP use consecutive groups of five.
 auto convert=(int(*)(int,int))(game+(App::currentBuild?0x81a5c0:(App::latestBuild?0x82cce0:0x815250)));
 for(int style=0;style<2;style++)for(int chart=0;chart<5;chart++)check(convert(chart,style)==style*5+chart,"native chart mapping");
 // Recover exact ghost bytes from an old-format snapshot, not from the independent lamp/BP best.
 Score::Attempt g;g.player="12345678";g.music=7;g.chart=3;g.pg=900;g.great=123;g.bp=80;g.lamp=2;
 auto snapshot=[&](){
  g.raw.fill(0);memcpy(g.raw.data(),g.player.c_str(),g.player.size()+1);
  put(g.raw.data(),0x10,g.music);g.raw[0x14]=(unsigned char)g.chart;
  put(g.raw.data(),0x1c,g.pg);put(g.raw.data(),0x20,g.great);put(g.raw.data(),0x24,g.bp);g.raw[0x28]=(unsigned char)g.lamp;
  put(g.raw.data(),0x374,g.nocount);put(g.raw.data(),0x378,g.grade);g.raw[0x7fe]=(unsigned char)g.nosave;
  int left=g.best().ex;for(int i=0;i<64;i++){int n=std::min(left,20+i);g.raw[0x34+i]=(unsigned char)n;left-=n;}
  check(left==0,"test fixture ghost capacity");for(int i=0;i<0x300;i++)g.raw[0x74+i]=(unsigned char)(i%97);
 };
 snapshot();App::database.save(App::scope,g);auto expected=g.raw;
 g.pg=800;g.bp=10;g.lamp=5;snapshot();App::database.save(App::scope,g);
 Score::Ghost ghost;check(App::database.ghost(App::scope,g.player,7,3,ghost)&&ghost.ex==1923&&!memcmp(ghost.data.data(),expected.data()+0x34,64),"highest EX ghost survives lower score better lamp/BP");
 check(!App::database.ghost("other-catalog",g.player,7,3,ghost)&&!App::database.ghost(App::scope,"87654321",7,3,ghost)&&!App::database.ghost(App::scope,g.player,7,8,ghost),"ghost catalog/player/SP-DP isolation");
 g.pg=900;snapshot();g.raw[0x34]--;g.raw[0x35]++;App::database.save(App::scope,g);
 check(App::database.ghost(App::scope,g.player,7,3,ghost)&&!memcmp(ghost.data.data(),expected.data()+0x34,64),"equal local EX keeps earliest valid curve");
 g.music=8;g.grade=1;snapshot();App::database.save(App::scope,g);check(!App::database.ghost(App::scope,g.player,8,3,ghost),"excluded ghost rejected");g.grade=0;
 g.music=9;snapshot();g.raw[0x34]++;App::database.save(App::scope,g);check(!App::database.ghost(App::scope,g.player,9,3,ghost),"corrupt ghost sum rejected");
 App::database.exec("UPDATE attempts SET raw=x'00' WHERE music=9");check(!App::database.ghost(App::scope,g.player,9,3,ghost),"truncated snapshot rejected");
 g.music=10;snapshot();App::database.save(App::scope,g);g.pg=950;snapshot();g.raw[0x34]++;App::database.save(App::scope,g);
 check(!App::database.ghost(App::scope,g.player,10,3,ghost),"missing best ghost cannot fall back to lower EX curve");
 // Run actual appoint parser with empty and populated own-ghost responses.
 std::array<unsigned char,0x6ae8+2> appointResponse{};
 memset(req.data(),0,req.size());put(req.data(),0,7);put(req.data(),4,3);memcpy(req.data()+0x110,"12345678",9);
 put(ctx.data(),0x78,(void*)(appointResponse.data()+1));auto res=appointResponse.data()+1;
 appointResponse.front()=0x7b;appointResponse.back()=0x7c;res[0x25]=0x51;
 setPointer(game+iat(0xc61f58),(void*)nodeSizeStub);setPointer(game+iat(0xc61ee0),(void*)attributeStub);
 setPointer(game+iat(0xc61f80),(void*)stringCopyStub);
 auto appoint=*(App::Method*)(game+App::targets[2].slot);hasServerRow=false;hasServerGhost=false;
 check(appoint(ctx.data(),nullptr),"actual appoint empty-server parse + overlay");
 check(res[0xa5]==1&&!memcmp(res+0x65,expected.data()+0x34,64)&&res[0x6a8]==1&&!memcmp(res+0x3a8,expected.data()+0x74,0x300),"native own ghost/gauge from same attempt");
 check(res[0x25]==0x51&&appointResponse.front()==0x7b&&appointResponse.back()==0x7c,"target ghost and response canaries untouched");
 hasServerGhost=true;serverGhost.fill(40);check(appoint(ctx.data(),nullptr)&&!memcmp(res+0x65,serverGhost.data(),64),"better server ghost preserved");
 memcpy(serverGhost.data(),expected.data()+0x34,64);serverGhost[0]--;serverGhost[1]++;
 check(appoint(ctx.data(),nullptr)&&!memcmp(res+0x65,serverGhost.data(),64),"equal server ghost preserved");
 serverGhost.fill(10);check(appoint(ctx.data(),nullptr)&&!memcmp(res+0x65,expected.data()+0x34,64),"lower server ghost replaced by actual local best");
 App::observedEx[{g.player,7,3}]=3000;hasServerGhost=false;
 check(appoint(ctx.data(),nullptr)&&res[0xa5]==0,"higher getrank score with absent server ghost not downgraded");App::observedEx.clear();
 App::ghostOverlay=false;check(appoint(ctx.data(),nullptr)&&res[0xa5]==0,"ghost setting disables fallback");App::ghostOverlay=true;
 App::overlay=false;check(appoint(ctx.data(),nullptr)&&res[0xa5]==0,"backup-only disables ghost");App::overlay=true;
 auto savedAppoint=App::originalAppoint;App::originalAppoint=stubReg;res[0xa5]=0;check(!App::appointHook(ctx.data(),nullptr)&&res[0xa5]==0,"failed native response not altered");App::originalAppoint=savedAppoint;
 memcpy(req.data()+0x110,"87654321",9);check(appoint(ctx.data(),nullptr)&&res[0xa5]==0,"actual parser different player no fallback");
 puts("PASS: exact legacy ghost recovery, invalid data rejection, best/tie policy, own/rival isolation, real PE appoint parser and configuration.");
 puts("PASS: SQLite durability, best merging, exclusions, player/catalog isolation, SP/DP, canaries, native snapshot, build guards, real PE getrank + local fallback, actual chart mapping.");
 return 0;
}
