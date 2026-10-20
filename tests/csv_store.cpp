// Run only against the isolated database produced by import_csv.py --test-db.
#include "../src/store.h"
#include <iostream>
int main(int argc,char** argv) {
 try {
  if(argc!=4)return 2;
  std::string scope=argv[2];
  if(scope.rfind("CSV_TEST_ONLY/iidx33/",0)!=0)return 2;
  Score::Database db;db.open(argv[1]);
  auto rows=db.rows(scope,"TEST_ONLY_NOT_A_PLAYER");
  if(rows.size()!=std::stoul(argv[3]))throw std::runtime_error("row count mismatch");
  for(const auto& row:rows){
   if(row.best.ex<0 || row.best.bp<-1 || row.best.lamp<0 || row.best.lamp>7)
    throw std::runtime_error("invalid imported best");
   Score::Ghost ghost;
   if(db.ghost(scope,"TEST_ONLY_NOT_A_PLAYER",row.music,row.chart,ghost))
    throw std::runtime_error("import must not fabricate a ghost");
  }
  std::cout<<"PASS: native Score::Database reads "<<rows.size()<<" imported best rows; no fabricated ghosts\n";
  return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
