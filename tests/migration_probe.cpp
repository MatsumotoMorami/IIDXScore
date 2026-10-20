#define IIDX_SCORE_TEST
#include "../src/plugin.cpp"
#include <iostream>
int main(int argc,char** argv){
 if(argc!=6&&argc!=7){std::cerr<<"usage: migration_probe copied-db old-catalog ac-catalog server-key target-version [omni-catalog]\n";return 2;}
 try{
  auto old=std::filesystem::u8path(argv[2]),current=std::filesystem::u8path(argv[3]);
  int version=std::stoi(argv[5]);int oldVersion=33;
  auto previous=std::string(argv[4])+"/iidx33/"+App::hashFile(old);
  auto acScope=std::string(argv[4])+"/iidx"+std::to_string(version)+"/"+App::hashFile(current);
  auto target=acScope;
  auto charts=App::matchingCharts(old,current,oldVersion,version);
  if(argc==7){auto omni=std::filesystem::u8path(argv[6]);target+="/omni/"+App::hashFile(omni);
   charts=App::matchingCharts(App::readCatalog(old,oldVersion),App::combinedCatalog(current,version,omni,App::catalogVersion(omni)));}
  Score::Database db;db.open(argv[1]);
  if(argc==7){auto omni=std::filesystem::u8path(argv[6]);auto combined=App::combinedCatalog(current,version,omni,App::catalogVersion(omni));
   auto acCharts=App::matchingCharts(App::readCatalog(current,version),combined);
   auto acResult=db.migrateMapped(acScope,target,acCharts);std::cout<<"AC best rows="<<acResult.first<<" copied attempts="<<acResult.second<<"\n";
   auto omniScope=std::string(argv[4])+"/iidx"+std::to_string(App::catalogVersion(omni))+"/"+App::hashFile(omni);
   if(omniScope!=acScope){auto omniCharts=App::matchingCharts(App::readCatalog(omni,App::catalogVersion(omni)),combined);
    auto omniResult=db.migrateMapped(omniScope,target,omniCharts);std::cout<<"Omni best rows="<<omniResult.first<<" copied attempts="<<omniResult.second<<"\n";}}
  auto result=db.migrateMapped(previous,target,charts);
  std::cout<<"matching charts="<<charts.size()<<" legacy best rows="<<result.first<<" copied attempts="<<result.second<<"\n";
  return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}
