"""Dedicated dashboard for goalkeeper, defender, and attacker trial runs."""
from __future__ import annotations
import argparse, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse

HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Coach Silvs Role Trials</title><style>:root{font-family:system-ui;color:#e8f2ea;background:#0b1510}body{margin:20px}.cards{display:grid;grid-template-columns:repeat(2,minmax(320px,1fr));gap:16px}.card{background:#15241b;border:1px solid #31503b;border-radius:12px;padding:16px}.wide{grid-column:1/-1}canvas{width:100%;background:#0e1c14;border-radius:8px}table{width:100%;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #294332;text-align:left}.metrics{display:flex;gap:24px;flex-wrap:wrap}.metrics b{display:block;font-size:1.5rem}.controls{display:flex;gap:10px;align-items:center;margin-bottom:10px}button,select,input{font:inherit}.replay-meta{min-height:24px;margin:8px 0;color:#bcd5c2}@media(max-width:800px){.cards{grid-template-columns:1fr}.wide{grid-column:auto}}</style></head><body><h1>Coach Silvs — Role Trial Monitor</h1><div id="metrics" class="card metrics"></div><div class="cards"><section class="card wide"><h2>Total loss by generation</h2><canvas id="loss" width="1100" height="430"></canvas></section><section class="card wide"><h2>Loss components</h2><canvas id="components" width="1100" height="430"></canvas></section><section class="card wide"><h2>Best defender replay</h2><div class="controls"><label>Generation <select id="replayGeneration"></select></label><button id="replayPlay">Play</button><button id="replayPause">Pause</button><button id="openWebots">Open replay in 3D Webots</button><input id="replayTimeline" type="range" min="0" max="0" value="0"></div><div id="replayMeta" class="replay-meta">Waiting for a completed generation.</div><canvas id="replay" width="1100" height="620"></canvas></section><section class="card"><h2>Current ranking</h2><table><thead><tr><th>#</th><th>Candidate</th><th>Total</th><th>Outcome</th><th>Interception</th><th>Reaction</th><th>Redirection</th></tr></thead><tbody id="ranking"></tbody></table></section><section class="card"><h2>Best parameters</h2><pre id="parameters"></pre></section></div><script>
const api=async p=>(await fetch(p,{cache:'no-store'})).json(),colors=['#ffb04a','#72b8ff','#e985ff','#75e59a'];
function formatDuration(value){if(value===null||value===undefined||!Number.isFinite(Number(value)))return'calculating…';let seconds=Math.max(0,Math.round(Number(value))),hours=Math.floor(seconds/3600);seconds%=3600;const minutes=Math.floor(seconds/60),rest=seconds%60;return hours?`${hours}h ${String(minutes).padStart(2,'0')}m`:`${minutes}m ${String(rest).padStart(2,'0')}s`}
function chart(canvas,series){const x=canvas.getContext('2d'),w=canvas.width,h=canvas.height,p=55;x.fillStyle='#0e1c14';x.fillRect(0,0,w,h);const values=series.flatMap(s=>s.values).filter(Number.isFinite),max=Math.max(.001,...values);x.font='14px system-ui';x.strokeStyle='#365544';x.fillStyle='#a9c4b0';for(let i=0;i<5;i++){const y=p+(h-2*p)*i/4;x.beginPath();x.moveTo(p,y);x.lineTo(w-p,y);x.stroke();x.fillText((max*(4-i)/4).toFixed(3),5,y+5)}series.forEach((s,j)=>{const color=s.color||colors[j],points=s.values.map((v,i)=>({px:s.values.length===1?w/2:p+(w-2*p)*i/(s.values.length-1),py:h-p-(h-2*p)*v/max,v}));x.strokeStyle=color;x.lineWidth=3;x.beginPath();points.forEach((q,i)=>i?x.lineTo(q.px,q.py):x.moveTo(q.px,q.py));x.stroke();x.fillStyle=color;points.forEach(q=>{x.beginPath();x.arc(q.px,q.py,6,0,Math.PI*2);x.fill();x.fillText(q.v.toFixed(4),q.px+10,q.py-8)});x.fillText(s.name,p+j*190,25)});x.fillStyle='#a9c4b0';x.fillText('generation',w/2-32,h-12)}
let replayData={frames:[]},replayIndex=0,replayTimer=null,replayGenerationLoaded=null;
function drawReplay(){const c=replay,x=c.getContext('2d'),w=c.width,h=c.height,p=45,fw=w-2*p,fh=h-2*p;x.fillStyle='#0b542c';x.fillRect(p,p,fw,fh);x.strokeStyle='#fff';x.lineWidth=3;x.strokeRect(p,p,fw,fh);x.beginPath();x.moveTo(w/2,p);x.lineTo(w/2,h-p);x.stroke();x.strokeRect(p-32,h/2-95,32,190);x.strokeRect(w-p,h/2-95,32,190);const f=replayData.frames[replayIndex];if(!f){replayMeta.textContent='No physical replay recorded for this generation.';return}const sx=v=>p+(v+.75)/1.5*fw,sy=v=>p+(.65-v)/1.3*fh;x.fillStyle='#55aaff';(f.blue||[]).slice(0,1).forEach(r=>{x.beginPath();x.arc(sx(r.x),sy(r.y),18,0,Math.PI*2);x.fill()});x.fillStyle='#fff';x.beginPath();x.arc(sx(f.ball.x),sy(f.ball.y),10,0,Math.PI*2);x.fill();const field=f.defender_field||{},target=field.intercept,corridor=field.corridor,active=!!target;if(active){x.strokeStyle='rgba(89,240,230,.55)';x.lineWidth=8;x.beginPath();x.moveTo(sx(-.75),sy(0));x.lineTo(sx(f.ball.x),sy(f.ball.y));x.stroke();x.lineWidth=3;x.strokeStyle='#59f0e6';x.setLineDash([8,5]);const robot=(f.blue||[])[0];if(robot){x.beginPath();x.moveTo(sx(robot.x),sy(robot.y));x.lineTo(sx(target.x),sy(target.y));x.stroke()}x.setLineDash([]);x.beginPath();x.arc(sx(target.x),sy(target.y),12,0,Math.PI*2);x.stroke();if(corridor){x.fillStyle='#ffb04a';x.beginPath();x.arc(sx(corridor.x),sy(corridor.y),7,0,Math.PI*2);x.fill()}}replayTimeline.value=replayIndex;replayMeta.textContent=`Generation ${Number(replayData.generation)+1} | ${replayData.candidate_id} | loss ${Number(replayData.loss).toFixed(4)} | scenario ${f.period} | ${Number(f.time).toFixed(2)} s | frame ${replayIndex+1}/${replayData.frames.length} | earliest interception ${active?'ACTIVE':'inactive'} | ball a=(${Number(f.ball.ax||0).toFixed(2)}, ${Number(f.ball.ay||0).toFixed(2)}) m/s²${active&&field.intercept_time!==null?` | τ=${Number(field.intercept_time).toFixed(2)} s`:''}`}
async function loadReplay(generation){clearInterval(replayTimer);replayTimer=null;replayData=await api(`/api/replay?generation=${generation}`);replayIndex=0;replayGenerationLoaded=String(generation);replayTimeline.max=Math.max(0,replayData.frames.length-1);drawReplay()}
replayPlay.onclick=()=>{if(replayTimer||!replayData.frames.length)return;replayTimer=setInterval(()=>{if(replayIndex>=replayData.frames.length-1){clearInterval(replayTimer);replayTimer=null;return}replayIndex=Math.min(replayData.frames.length-1,replayIndex+3);drawReplay()},30)};replayPause.onclick=()=>{clearInterval(replayTimer);replayTimer=null};replayTimeline.oninput=()=>{replayIndex=Number(replayTimeline.value);drawReplay()};replayGeneration.onchange=()=>loadReplay(replayGeneration.value);
openWebots.onclick=async()=>{if(replayGenerationLoaded===null)return;openWebots.disabled=true;const response=await fetch(`/api/open-webots?generation=${replayGenerationLoaded}`,{method:'POST'}),result=await response.json();replayMeta.textContent=result.message||result.error;openWebots.disabled=false};
async function refresh(){const [config,live,eta,history,rank]=await Promise.all(['/api/config','/api/live','/api/eta','/api/history','/api/ranking'].map(api));metrics.innerHTML=`<span>Role<b>${config.role}</b></span><span>Backend<b>${config.backend||'proxy'}</b></span><span>Status<b>${live.status}</b></span><span>Generation<b>${Number(live.generation)+1}/${config.generations}</b></span><span>Candidates<b>${live.completed}/${live.total}</b></span><span>Scenarios/trial<b>${config.scenarios}</b></span><span>Generation remaining<b>${formatDuration(eta.generation_eta_seconds)}</b></span><span>Total remaining<b>${formatDuration(eta.run_eta_seconds)}</b></span>`;chart(loss,[{name:'best total loss',values:history.map(g=>g.best_loss)}]);const keys=['outcome','interception','reaction_time','redirection'];chart(components,keys.map((k,i)=>({name:k,values:history.map(g=>g.loss_components[k]),color:colors[i]})));ranking.innerHTML=rank.map((r,i)=>`<tr><td>${i+1}</td><td>${r.candidate_id}</td><td>${r.loss.toFixed(4)}</td>${keys.map(k=>`<td>${r.loss_components[k].toFixed(4)}</td>`).join('')}</tr>`).join('');parameters.textContent=rank.length?JSON.stringify(rank[0].parameters,null,2):'Waiting for first generation';const selected=replayGeneration.value;replayGeneration.innerHTML=history.map(g=>`<option value="${g.generation}">Generation ${g.generation+1}</option>`).join('');if(history.length){replayGeneration.value=history.some(g=>String(g.generation)===selected)?selected:String(history.at(-1).generation);if(replayGenerationLoaded!==replayGeneration.value)loadReplay(replayGeneration.value)}}refresh();setInterval(refresh,2000)</script></body></html>'''

HTML = (
    HTML.replace("Best defender replay", "Best role candidate replay")
    .replace(
        "const field=f.defender_field||{},target=field.intercept",
        "const role=replayData.role||'defender',field=f[role+'_field']||{},target=field.intercept",
    )
    .replace(
        "x.beginPath();x.moveTo(sx(-.75),sy(0));x.lineTo(sx(f.ball.x),sy(f.ball.y));x.stroke();",
        "x.beginPath();if(role==='goalkeeper'){x.moveTo(sx(target.x),sy(-.2));x.lineTo(sx(target.x),sy(.2))}else{x.moveTo(sx(-.75),sy(0));x.lineTo(sx(f.ball.x),sy(f.ball.y))}x.stroke();",
    )
    .replace(
        "earliest interception ${active?'ACTIVE':'inactive'}",
        "${role==='goalkeeper'?'goalkeeper line':'earliest interception'} ${active?'ACTIVE':'inactive'}",
    )
    .replace(
        "x.fill()});x.fillStyle='#fff';x.beginPath();x.arc(sx(f.ball.x),sy(f.ball.y),10,0,Math.PI*2);x.fill();",
        "x.fill();x.strokeStyle='#eaf5ff';x.lineWidth=4;x.beginPath();x.moveTo(sx(r.x),sy(r.y));x.lineTo(sx(r.x+.045*Math.cos(r.orientation||0)),sy(r.y+.045*Math.sin(r.orientation||0)));x.stroke()});x.fillStyle='#fff';x.beginPath();x.arc(sx(f.ball.x),sy(f.ball.y),10,0,Math.PI*2);x.fill();",
    )
)

class Handler(BaseHTTPRequestHandler):
    run_dir=Path('.')
    travesim_root=Path('.')
    webots='webots'
    def send(self,value,content='application/json'):
        data=(value if isinstance(value,str) else json.dumps(value)).encode();self.send_response(200);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def read(self,name,default):
        try:return json.loads((self.run_dir/name).read_text())
        except (OSError,json.JSONDecodeError):return default
    def do_GET(self):
        request=urlparse(self.path);path=request.path
        if path=='/':return self.send(HTML,'text/html; charset=utf-8')
        if path=='/api/config':return self.send(self.read('config.json',{}))
        if path=='/api/live':return self.send(self.read('live.json',{}))
        if path=='/api/eta':
            config=self.read('config.json',{});live=self.read('live.json',{})
            try:
                started=(self.run_dir/'config.json').stat().st_mtime
                updated=(self.run_dir/'live.json').stat().st_mtime
            except OSError:started=updated=time.time()
            return self.send(estimate_eta(config,live,started,updated,time.time()))
        if path=='/api/ranking':return self.send(self.read('ranking.json',[]))
        if path=='/api/replay':
            try:generation=max(0,int(parse_qs(request.query).get('generation',['0'])[0]))
            except ValueError:generation=0
            return self.send(self.read(f'replays/generation-{generation:04d}.json',{'generation':generation,'frames':[]}))
        if path=='/api/history':
            rows=[]
            for file in sorted((self.run_dir/'generations').glob('generation-*.json')):
                data=self.read(str(file.relative_to(self.run_dir)),[])
                if data:rows.append({'generation':len(rows),'best_loss':data[0]['loss'],'loss_components':data[0]['loss_components']})
            return self.send(rows)
        self.send_error(404)
    def do_POST(self):
        request=urlparse(self.path)
        if request.path!='/api/open-webots':return self.send_error(404)
        try:generation=max(0,int(parse_qs(request.query).get('generation',['0'])[0]))
        except ValueError:return self.send({'error':'Invalid generation'})
        replay=self.run_dir/'replays'/f'generation-{generation:04d}.json'
        if not replay.is_file():return self.send({'error':'Replay is not available yet'})
        try:
            world=render_replay_world((self.travesim_root/'worlds'/'Match3v3.wbt').read_text(),replay.resolve())
            world_path=self.travesim_root/'worlds'/f'.coach-role-replay-{generation}-{time.time_ns()}.wbt'
            world_path.write_text(world)
            process=subprocess.Popen([self.webots,'--mode=realtime',str(world_path)],cwd=self.travesim_root,start_new_session=True)
            threading.Thread(target=lambda:(process.wait(),world_path.unlink(missing_ok=True)),daemon=True).start()
            return self.send({'message':f'Opening generation {generation+1} in Webots'})
        except (OSError,ValueError) as error:return self.send({'error':str(error)})
    def log_message(self,*_):pass

def render_replay_world(template: str, replay_path: Path) -> str:
    referee=f'''VssReferee {{
  robotsPerTeam 3
  controller "role_replay_controller"
  role_scenarios_path "{replay_path.as_posix()}"
}}'''
    world,count=re.subn(r'VssReferee \{.*?\n\}',referee,template,count=1,flags=re.DOTALL)
    if count!=1:raise ValueError('VssReferee block not found')
    return re.sub(r'controller "vss_robot_controller"', 'controller "<none>"', world)

def estimate_eta(config: dict, live: dict, started: float, updated: float, now: float) -> dict:
    if live.get('status') == 'complete':
        return {'generation_eta_seconds': 0.0, 'run_eta_seconds': 0.0, 'source': 'complete'}
    candidates=max(1,int(config.get('candidates',live.get('total',1))))
    generations=max(1,int(config.get('generations',1)))
    generation=max(0,int(live.get('generation',0)))
    completed=max(0,int(live.get('completed',0)))
    completed_global=min(candidates*generations,generation*candidates+completed)
    since_update=max(0.0,now-updated)
    if completed_global:
        throughput=completed_global/max(1.0,updated-started)
        source='observed'
    else:
        workers=max(1,int(config.get('workers',1)))
        scenario_time=max(0.01,float(config.get('scenario_duration',1)))
        scenarios=max(1,int(config.get('scenarios',1)))
        candidate_wall=scenarios*scenario_time+8.0
        throughput=workers/candidate_wall
        source='configured'
    generation_eta=max(0.0,(candidates-completed)/throughput-since_update)
    run_eta=max(0.0,(candidates*generations-completed_global)/throughput-since_update)
    return {'generation_eta_seconds':generation_eta,'run_eta_seconds':run_eta,'source':source}

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8100);p.add_argument('--travesim-root',default='.');p.add_argument('--webots',default='webots');a=p.parse_args();Handler.run_dir=Path(a.run);Handler.travesim_root=Path(a.travesim_root).resolve();Handler.webots=a.webots;print(f'Role dashboard: http://{a.host}:{a.port}',flush=True);ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()
if __name__=='__main__':main()
