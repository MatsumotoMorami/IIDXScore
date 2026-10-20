"""Drag a CSV onto the executable, or double-click and choose a file."""
import configparser
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import import_csv as core


def settings(ini):
    ini = Path(ini).resolve(strict=True)
    raw = ini.read_bytes()
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_string(raw.decode(encoding))
    section = cfg['Score']
    def path(key, default):
        p = Path(section.get(key, default).strip().strip('"'))
        return (ini.parent / p).resolve() if not p.is_absolute() else p.resolve()
    return (path('DatabasePath', 'scores/IIDXScore.sqlite3'),
            path('MusicDatabase', 'data_mods/ominimix/info/1/music_omni.bin'),
            section.get('ServerKey', 'default'))


def players(db, scope):
    with core.connection(db.resolve(strict=True).as_uri()+'?mode=ro', uri=True) as c:
        return [r[0] for r in c.execute('SELECT DISTINCT player FROM best WHERE scope=? ORDER BY player', (scope,))
                if re.fullmatch(r'[0-9]{1,15}', r[0] or '')]


def game_running():
    if sys.platform != 'win32':
        return False
    result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    return any(name in result.stdout.lower() for name in (b'"spice64.exe"', b'"spice.exe"'))


class Window:
    def __init__(self, root, csv_path='', ini_path=''):
        self.root, self.report, self.target = root, None, None
        root.title('IIDXScore CSV 导入')
        root.geometry('800x570')
        root.minsize(700, 480)
        box = ttk.Frame(root, padding=18)
        box.pack(fill='both', expand=True)
        box.columnconfigure(1, weight=1)
        self.csv = tk.StringVar(value=csv_path)
        self.ini = tk.StringVar(value=ini_path)
        self.style = tk.StringVar(value='DP' if re.search(r'(^|[-_])dp([._-]|$)', Path(csv_path).name.lower()) else 'SP')
        self.player = tk.StringVar()
        for i, (label, variable, suffix) in enumerate((('成绩 CSV', self.csv, '*.csv'), ('IIDXScore.ini', self.ini, '*.ini'))):
            ttk.Label(box, text=label).grid(row=i, column=0, sticky='w', pady=7)
            ttk.Entry(box, textvariable=variable).grid(row=i, column=1, sticky='ew', padx=8)
            ttk.Button(box, text='选择…', command=lambda v=variable,s=suffix:self.browse(v,s)).grid(row=i,column=2)
        ttk.Label(box,text='游玩类型').grid(row=2,column=0,sticky='w',pady=7)
        ttk.Combobox(box,textvariable=self.style,values=['SP','DP'],state='readonly',width=9).grid(row=2,column=1,sticky='w',padx=8)
        ttk.Label(box,text='目标玩家 ID').grid(row=3,column=0,sticky='w',pady=7)
        self.combo=ttk.Combobox(box,textvariable=self.player,state='readonly')
        self.combo.grid(row=3,column=1,sticky='ew',padx=8)
        ttk.Label(box,text='先预览，再确认导入。只导入自己的成绩；写入前自动备份。').grid(row=4,column=0,columnspan=3,sticky='w',pady=10)
        self.info=tk.Text(box,wrap='word',height=14,state='disabled')
        self.info.grid(row=5,column=0,columnspan=3,sticky='nsew')
        box.rowconfigure(5,weight=1)
        actions=ttk.Frame(box)
        actions.grid(row=6,column=0,columnspan=3,sticky='ew',pady=12)
        ttk.Button(actions,text='预览 / 重新匹配',command=self.preview).pack(side='left')
        self.save=ttk.Button(actions,text='保存详细报告',command=self.save_report,state='disabled')
        self.save.pack(side='left',padx=10)
        self.import_button=ttk.Button(actions,text='导入所选玩家',command=self.apply,state='disabled')
        self.import_button.pack(side='right')
        for var in (self.csv,self.ini,self.style):
            var.trace_add('write',self.invalidate)
        self.say('将 CSV 拖到 EXE 图标上即可打开预览，也可以点击“选择…”。\n请核对 SP/DP；默认根据文件名提示，无法识别时为 SP。')
        if csv_path and ini_path:
            root.after(150,self.preview)

    def say(self,text):
        self.info.configure(state='normal')
        self.info.delete('1.0','end')
        self.info.insert('1.0',text)
        self.info.configure(state='disabled')

    def invalidate(self,*_):
        self.report=None
        self.target=None
        self.player.set('')
        self.combo.configure(values=[])
        self.import_button.configure(state='disabled')
        self.save.configure(state='disabled')

    def browse(self,var,suffix):
        path=filedialog.askopenfilename(filetypes=[('文件',suffix)])
        if path:var.set(path)

    def preview(self):
        self.invalidate()
        try:
            db,catalog,key=settings(self.ini.get())
            report=core.preview(self.csv.get(),catalog,self.style.get())
            scope=key+'/iidx33/'+report['catalog_sha256']
            ids=players(db,scope)
            self.report,self.target=report,(db,catalog,key,scope)
            self.combo.configure(values=ids)
            if len(ids)==1:self.player.set(ids[0])
            counts=report['counts']
            self.say(f"曲库：{catalog}\n数据库：{db}\n服务器：{key}\n类型：{report['style']}\n\n"
                     f"可导入：{counts['accepted']} 条谱面成绩\n跳过异常：{len(report['issues'])} 条\n"
                     f"无游玩记录：{counts.get('no_play',0)} 条\n数据警告：{len(report['warnings'])} 条（详细报告可查看）\n\n"
                     "EX / 灯取较好值，BP 取已知较低值。不会降低已有最佳。\nCSV 没有逐段 ghost，不能恢复分数柱曲线。\n"
                     + ('请选择目标玩家，核对后点击导入。' if ids else '当前曲库没有可导入的玩家；请核对 INI 和曲库，并先正常游玩保存一次。'))
            self.save.configure(state='normal')
            if ids and report['records']:self.import_button.configure(state='normal')
        except Exception as e:
            messagebox.showerror('预览失败',str(e))

    def save_report(self):
        if not self.report:return
        path=filedialog.asksaveasfilename(defaultextension='.json',initialfile='IIDXScore导入报告.json',filetypes=[('JSON','*.json')])
        if path:
            try:
                with Path(path).open('x',encoding='utf-8') as f:json.dump(self.report,f,ensure_ascii=False,indent=2)
            except Exception as e:messagebox.showerror('报告未保存',str(e))

    def apply(self):
        if not self.report or not self.target:return
        try:
            db,catalog,key,scope=self.target
            player=self.player.get()
            if player not in players(db,scope):raise ValueError('请先选择当前曲库下的目标玩家')
            if game_running():raise ValueError('请先退出游戏，再导入成绩')
            # Re-read the inputs before confirmation: never import a stale preview.
            current=core.preview(self.csv.get(),catalog,self.style.get())
            if current!=self.report or settings(self.ini.get())!=(db,catalog,key):
                self.invalidate()
                raise ValueError('CSV、曲库或 INI 已变更，请重新预览')
            if not messagebox.askyesno('确认导入自己的成绩',
                f"玩家 ID：{player}\n类型：{current['style']}\n数据库：{db}\n\n"
                f"导入 {len(current['records'])} 条成绩，跳过 {len(current['issues'])} 条异常。\n"
                f"另有 {len(current['warnings'])} 条数据警告，可取消并保存报告检查。\n\n确认这是你自己的成绩并导入？"):
                return
            if game_running():raise ValueError('游戏已启动，请退出后重新导入')
            result=core.apply(current,db,player,key)
            self.import_button.configure(state='disabled')
            self.say(f"导入完成：{len(current['records'])} 条成绩。\n玩家：{player}\n\n备份：{result['backup']}\n\n下次启动游戏后读取。")
            messagebox.showinfo('导入完成','成绩已合并，原数据库已备份。')
        except Exception as e:messagebox.showerror('未完成导入',str(e))


def self_test(output):
    # This path has no discovery of real settings, players or game databases.
    with tempfile.TemporaryDirectory() as folder:
        p=Path(folder)/'test.sqlite3'
        report=dict(source_sha256='0'*64,catalog_sha256='1'*64,style='SP',records=[
            dict(row=2,music=1000,chart=3,ex=100,pg=50,great=0,bp=-1,lamp=5)])
        core.sandbox(report,p)
        with core.connection(p) as db:
            assert db.execute('SELECT ex,bp,lamp FROM best').fetchone()==(100,-1,5)
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='attempts'").fetchone()
        root=tk.Tk()
        root.withdraw()
        Window(root)
        root.update()
        root.destroy()
    Path(output).write_text('PASS: frozen importer, isolated SQLite write, no fabricated attempt, Tk window construction',encoding='utf-8')


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--self-test':
        self_test(sys.argv[2])
    else:
        root=tk.Tk()
        home=Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve().parent
        candidates=[home/'IIDXScore.ini',Path('C:/IIDX/IIDXScore.ini')]
        ini=next((str(p) for p in candidates if p.is_file()),'')
        Window(root,sys.argv[1] if len(sys.argv)==2 else '',ini)
        root.mainloop()
