#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <stdexcept>
#include "sqlite3.h"
namespace Score {
struct Best { int ex=-1,bp=-1,lamp=0; };
inline Best merge(Best a,Best b) {
 a.ex=std::max(a.ex,b.ex); a.lamp=std::max(a.lamp,b.lamp);
 if(b.bp>=0 && (a.bp<0||b.bp<a.bp))a.bp=b.bp; return a;
}
struct Attempt {
 std::string player; int music=-1,chart=-1,pg=-1,great=-1,bp=-1,lamp=-1;
 int nocount=0,grade=0,nosave=0; std::array<unsigned char,0xea0> raw{};
 bool valid()const {return !player.empty()&&music>=0&&music<100000&&chart>=0&&chart<=9&&pg>=0&&pg<=100000&&great>=0&&great<=100000&&bp>=-1&&bp<=100000&&lamp>=0&&lamp<=7;}
 bool eligible()const{return valid()&&!nocount&&!grade&&!nosave;}
 Best best()const{return {pg*2+great,bp,lamp};}
};
struct Row {int music,chart;Best best;};
struct Ghost {int ex=-1;std::array<unsigned char,64> data{};std::array<unsigned char,0x300> gauge{};};
struct Statement {
 sqlite3_stmt* s=nullptr;
 Statement(sqlite3* db,const char* sql){if(sqlite3_prepare_v2(db,sql,-1,&s,nullptr)!=SQLITE_OK)throw std::runtime_error(sqlite3_errmsg(db));}
 ~Statement(){sqlite3_finalize(s);}
 void text(int n,const std::string& v){if(sqlite3_bind_text(s,n,v.c_str(),-1,SQLITE_TRANSIENT)!=SQLITE_OK)throw std::runtime_error("bind text");}
 void num(int n,int v){sqlite3_bind_int(s,n,v);}
 void done(){if(sqlite3_step(s)!=SQLITE_DONE)throw std::runtime_error("SQLite write failed");}
};
class Database {
 sqlite3* db=nullptr;
 public:
 ~Database(){if(db)sqlite3_close(db);}
 void exec(const char* q){char* err=nullptr;if(sqlite3_exec(db,q,nullptr,nullptr,&err)!=SQLITE_OK){std::string msg=err?err:"SQLite error";sqlite3_free(err);throw std::runtime_error(msg);}}
 void open(const std::string& path){
  if(sqlite3_open_v2(path.c_str(),&db,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_FULLMUTEX,nullptr)!=SQLITE_OK)throw std::runtime_error("Cannot open score database");
  sqlite3_busy_timeout(db,1500);
  exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;");
  exec("CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,scope TEXT NOT NULL,player TEXT NOT NULL,music INTEGER NOT NULL,chart INTEGER NOT NULL,pg INTEGER,great INTEGER,bp INTEGER,lamp INTEGER,eligible INTEGER,nocount INTEGER,grade INTEGER,nosave INTEGER,raw BLOB NOT NULL,created TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')));"
       "CREATE TABLE IF NOT EXISTS best(scope TEXT,player TEXT,music INTEGER,chart INTEGER,ex INTEGER,bp INTEGER,lamp INTEGER,PRIMARY KEY(scope,player,music,chart));"
       "CREATE TABLE IF NOT EXISTS cache_observations(scope TEXT,player TEXT,music INTEGER,chart INTEGER,ex INTEGER,bp INTEGER,lamp INTEGER,observed TEXT DEFAULT CURRENT_TIMESTAMP);"
       "CREATE INDEX IF NOT EXISTS attempts_player ON attempts(scope,player,music,chart);");
 }
 void save(const std::string& scope,const Attempt& a){
  if(!a.valid())throw std::runtime_error("Invalid score snapshot");
  exec("BEGIN IMMEDIATE");
  try {
   Statement q(db,"INSERT INTO attempts(scope,player,music,chart,pg,great,bp,lamp,eligible,nocount,grade,nosave,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)");
   q.text(1,scope);q.text(2,a.player);int vals[]={a.music,a.chart,a.pg,a.great,a.bp,a.lamp,a.eligible(),a.nocount,a.grade,a.nosave};
   for(int i=0;i<10;i++)q.num(i+3,vals[i]);
   sqlite3_bind_blob(q.s,13,a.raw.data(),(int)a.raw.size(),SQLITE_TRANSIENT);q.done();
   if(a.eligible()){
    Statement b(db,"INSERT INTO best VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope,player,music,chart) DO UPDATE SET ex=max(ex,excluded.ex),lamp=max(lamp,excluded.lamp),bp=CASE WHEN excluded.bp<0 THEN bp WHEN bp<0 THEN excluded.bp ELSE min(bp,excluded.bp) END");
    b.text(1,scope);b.text(2,a.player);b.num(3,a.music);b.num(4,a.chart);b.num(5,a.pg*2+a.great);b.num(6,a.bp);b.num(7,a.lamp);b.done();
   }
   exec("COMMIT");
  }catch(...){exec("ROLLBACK");throw;}
 }
 std::vector<Row> rows(const std::string& scope,const std::string& player){
  Statement q(db,"SELECT music,chart,ex,bp,lamp FROM best WHERE scope=? AND player=? ORDER BY music,chart");q.text(1,scope);q.text(2,player);
  std::vector<Row> rows;int status;
  while((status=sqlite3_step(q.s))==SQLITE_ROW)rows.push_back({sqlite3_column_int(q.s,0),sqlite3_column_int(q.s,1),{sqlite3_column_int(q.s,2),sqlite3_column_int(q.s,3),sqlite3_column_int(q.s,4)}});
  if(status!=SQLITE_DONE)throw std::runtime_error("SQLite read failed");return rows;
 }
 void observe(const std::string& scope,const std::string& player,const Row& r,Best remote){
  Statement q(db,"INSERT INTO cache_observations(scope,player,music,chart,ex,bp,lamp) VALUES(?,?,?,?,?,?,?)");
  q.text(1,scope);q.text(2,player);q.num(3,r.music);q.num(4,r.chart);q.num(5,remote.ex);q.num(6,remote.bp);q.num(7,remote.lamp);q.done();
 }
 bool ghost(const std::string& scope,const std::string& player,int music,int chart,Ghost& out){
  // Only an actual attempt attaining the highest EX can supply its progress curve.
  // Existing v0.1 snapshots already contain this data; no fabricated/migrated curve.
  Statement q(db,"SELECT a.raw,b.ex FROM best b JOIN attempts a ON a.scope=b.scope AND a.player=b.player AND a.music=b.music AND a.chart=b.chart AND a.pg*2+a.great=b.ex WHERE b.scope=? AND b.player=? AND b.music=? AND b.chart=? AND a.eligible=1 AND a.nocount=0 AND a.grade=0 AND a.nosave=0 ORDER BY a.id ASC");
  q.text(1,scope);q.text(2,player);q.num(3,music);q.num(4,chart);int status;
  while((status=sqlite3_step(q.s))==SQLITE_ROW){
   if(sqlite3_column_bytes(q.s,0)!=0xea0)continue;
   auto raw=(const unsigned char*)sqlite3_column_blob(q.s,0);int ex=sqlite3_column_int(q.s,1);
   auto val=[&](size_t off){int n;memcpy(&n,raw+off,4);return n;};
   if(player.size()>=16||memcmp(raw,player.c_str(),player.size()+1)||val(0x10)!=music||raw[0x14]!=chart||val(0x374)||val(0x378)||raw[0x7fe])continue;
   int pg=val(0x1c),gr=val(0x20);if(pg<0||pg>100000||gr<0||gr>100000||pg*2+gr!=ex)continue;
   int sum=0;for(size_t i=0x34;i<0x74;i++)sum+=raw[i];if(sum!=ex)continue;
   Ghost result;result.ex=ex;memcpy(result.data.data(),raw+0x34,64);memcpy(result.gauge.data(),raw+0x74,0x300);out=result;return true;
  }
  if(status!=SQLITE_DONE)throw std::runtime_error("SQLite ghost read failed");return false;
 }
};
}

