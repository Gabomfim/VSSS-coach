"""Zero-dependency monitoring dashboard for evolution run artifacts."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
from socketserver import TCPServer
import statistics
import threading
import time
from urllib.parse import parse_qs, urlparse

REPLAY_FPS = 30



HTML = r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VSSS Coach Monitor</title><style>
:root{font-family:Inter,system-ui,sans-serif;color:#e7f0e8;background:#0c1510}body{margin:0;padding:20px}h1{margin:0 0 16px}.grid{display:grid;grid-template-columns:repeat(2,minmax(320px,1fr));gap:16px}.card{background:#15241b;border:1px solid #2d4a36;border-radius:12px;padding:14px;overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:7px;border-bottom:1px solid #294332}canvas{display:block;width:100%;height:auto;aspect-ratio:15/13;background:#193b23;border-radius:8px}canvas.chart{aspect-ratio:2/1;background:#101d15}.pitch-wrap{position:relative;line-height:0}.pitch-wrap svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.goal-frame{fill:none;stroke-width:5;stroke-linejoin:round}.goal-post{stroke:#223a29;stroke-width:1.5}.goal-label{font-size:16px;font-weight:800;paint-order:stroke;stroke:#0c1510;stroke-width:3px}.goal-events,.team-legend{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin:8px 0}.goal-events button{border-color:#d89b45}.team-badge{padding:7px 10px;border-radius:7px;background:#0f1d14;font-weight:800;border:2px solid}.team-yellow{color:#ffdf3e;border-color:#ffdf3e}.team-blue{color:#72b8ff;border-color:#53a6ff}.metrics,.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.replay-status{min-height:42px;align-content:center;overflow:hidden}.metric b{display:block;font-size:1.5rem}.scoreboard{margin:10px 0;padding:8px 12px;border-radius:8px;background:#0f1d14;font-size:1.05rem;font-weight:700}.score-row{display:grid;grid-template-columns:minmax(90px,1fr) 110px;align-items:center;padding:5px 0;border-bottom:1px solid #294332}.score-row:last-child{border-bottom:0;font-size:1.18rem}.score-period{text-align:left}.score-value{text-align:center;white-space:nowrap}.score-yellow{color:#ffdf3e}.score-blue{color:#72b8ff}label{display:flex;gap:6px;align-items:center}select,button,input{background:#0f1d14;color:#fff;padding:7px;border:1px solid #477455;border-radius:6px}button{cursor:pointer}.wide{grid-column:1/-1}.hidden{display:none}pre{white-space:pre-wrap}@media(max-width:850px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}}</style></head><body>
<h1>VSSS Coach — Evolution Monitor</h1><div id="metrics" class="card metrics"></div><div class="grid">
<section class="card"><h2>Ranking</h2><table><thead><tr><th>#</th><th>Candidato</th><th>Score</th><th>V/E/D</th><th>Saldo</th><th>Penalidade comportamental</th></tr></thead><tbody id="ranking"></tbody></table><p>● placar provisório de partida em andamento; penalidades comportamentais entram após a conclusão.</p></section>
<section class="card"><h2>Workers VPN</h2><div id="distributedWorkers">Execução local</div></section>
<section class="card wide"><h2>Tempo de bola parada por geração</h2><canvas id="ballStallChart" class="chart" width="1000" height="500"></canvas><p id="ballStallSummary">Aguardando partidas gravadas.</p></section>
<section class="card"><h2>Partida gravada</h2><div class="controls"><label>Geração <select id="generations"></select></label><label>Par de candidatos <select id="candidatePairs"></select></label><label>Jogo <select id="matches"></select></label></div><div class="pitch-wrap"><canvas id="match" width="750" height="650"></canvas><svg viewBox="0 0 750 650" aria-label="Traves dos gols"><g id="leftGoal"><path class="goal-frame" d="M20 231 H2 V419 H20"/><circle class="goal-post" cx="20" cy="231" r="5"/><circle class="goal-post" cx="20" cy="419" r="5"/></g><g id="rightGoal"><path class="goal-frame" d="M730 231 H748 V419 H730"/><circle class="goal-post" cx="730" cy="231" r="5"/><circle class="goal-post" cx="730" cy="419" r="5"/></g><text id="leftGoalLabel" class="goal-label" x="5" y="215">🟦</text><text id="rightGoalLabel" class="goal-label" x="718" y="215">🟨</text></svg></div><div id="recordedScore" class="scoreboard">Placar indisponível</div><div class="controls replay-status"><input id="timeline" type="range" min="0" max="0" value="0"><strong id="periodLabel">1º tempo — 05:00</strong><span id="frameLabel">Quadro 0/0</span><span id="scoreLabel"></span></div><div class="controls"><button id="play">▶ Reproduzir</button><button id="followLive">● Ao vivo</button><button id="firstPeriod">1º tempo</button><button id="secondPeriod">2º tempo</button><label>Velocidade <select id="speed"><option value="0.5">0,5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label></div><div id="goalEvents" class="goal-events">Nenhum gol gravado</div></section>
<section class="card wide"><h2>Campo vetorial por objeto</h2><div class="controls"><label>Candidato <select id="candidates"></select></label><label id="playerControl">Jogador observado <select id="fieldPlayers"></select></label><label>Objeto <select id="fieldObjects"></select></label><label id="sourceControl" class="hidden">Instância <select id="fieldSources"></select></label></div><p id="fieldMeaning"></p><p id="fieldStatus"></p><canvas id="field" width="750" height="650"></canvas><p><strong>○ origem → ▲ destino:</strong> a ponta triangular mostra o sentido; cor e comprimento representam objeto e intensidade.</p></section>
<section class="card wide"><h2>Fórmula e parâmetros selecionados</h2><pre id="formula"></pre></section></div>
<script>
const api=async p=>(await fetch(p,{cache:'no-store'})).json();
document.querySelector('#ranking').closest('table').querySelectorAll('th:last-child').forEach(cell=>cell.remove());document.querySelectorAll('p').forEach(item=>{if(item.textContent.includes('penalidades comportamentais'))item.remove()});const rankingTableStyle=document.createElement('style');rankingTableStyle.textContent='#ranking td:last-child{display:none}';document.head.append(rankingTableStyle);fieldMeaning.style.cssText='height:42px;min-height:42px;overflow:hidden;line-height:21px';fieldStatus.style.cssText='height:24px;min-height:24px;overflow:hidden;line-height:24px;white-space:nowrap';
function formatDuration(value){if(value===null||value===undefined||!Number.isFinite(Number(value)))return'calculando…';let seconds=Math.max(0,Math.round(Number(value))),hours=Math.floor(seconds/3600);seconds%=3600;const minutes=Math.floor(seconds/60),rest=seconds%60;return hours?`${hours}h ${String(minutes).padStart(2,'0')}m`:`${minutes}m ${String(rest).padStart(2,'0')}s`}
const objects=['ball','ally','enemy','wall','wall_decluster','ally_goal','enemy_goal','goal_triangle_clearance','shot_clearance','corner_shot_gate','goal_post_escape','attacking_corner_escape','goal_escape'];let rankingData=[],candidateData=[],matchCatalog=[],configData={},matchData=null,loadedMatchId='',frameIndex=0,playing=false,lastTick=0,playbackTime=0,animationId=0;const uiState={candidate:'',generation:'',pair:'',match:'',player:'',object:'',source:'',followLive:true};
function setOptions(select,values,preferred){const signature=values.join('\u0000');if(select.dataset.signature!==signature){select.innerHTML=values.map(v=>`<option value="${v}">${v}</option>`).join('');select.dataset.signature=signature}const selected=values.includes(preferred)?preferred:(values[0]||'');if(selected)select.value=selected;return selected}
function setLabeledOptions(select,items,preferred){const signature=items.map(item=>`${item.value}\u0001${item.label}`).join('\u0000');if(select.dataset.signature!==signature){select.innerHTML=items.map(item=>`<option value="${item.value}">${item.label}</option>`).join('');select.dataset.signature=signature}const values=items.map(item=>item.value),selected=values.includes(preferred)?preferred:(values[0]||'');if(selected)select.value=selected;return selected}
function pairKey(item){return[item.home,item.away].sort().join(' × ')}
function configureReplayMenus(followLatest=false){if(!matchCatalog.length){uiState.generation=uiState.pair=uiState.match='';generations.innerHTML=candidatePairs.innerHTML=matches.innerHTML='';return}const latest=matchCatalog.at(-1);if(followLatest){uiState.generation=String(latest.generation);uiState.pair=pairKey(latest);uiState.match=latest.match_id}const generationItems=[...new Set(matchCatalog.map(item=>String(item.generation)))].map(value=>({value,label:`Geração ${Number(value)+1}`}));uiState.generation=setLabeledOptions(generations,generationItems,uiState.generation);const inGeneration=matchCatalog.filter(item=>String(item.generation)===uiState.generation),pairValues=[...new Set(inGeneration.map(pairKey))],pairItems=pairValues.map(value=>({value,label:value}));uiState.pair=setLabeledOptions(candidatePairs,pairItems,uiState.pair);const inPair=inGeneration.filter(item=>pairKey(item)===uiState.pair),gameItems=inPair.map((item,index)=>({value:item.match_id,label:`Jogo ${Number(item.repetition??index)+1}${item.live?' — ao vivo':''} — 🟨 ${item.home} × ${item.away} 🟦`}));uiState.match=setLabeledOptions(matches,gameItems,uiState.match)}
function ballStallGenerationStats(catalog){const groups=new Map;for(const item of catalog){const generation=Number(item.generation);if(!Number.isFinite(generation))continue;const values=groups.get(generation)||[];values.push(Math.max(0,Number(item.ball_stationary_seconds??item.ball_stall_seconds)||0));groups.set(generation,values)}return[...groups].sort((a,b)=>a[0]-b[0]).map(([generation,values])=>{const mean=values.reduce((sum,value)=>sum+value,0)/values.length,variance=values.reduce((sum,value)=>sum+(value-mean)**2,0)/values.length;return{generation,mean,standardDeviation:Math.sqrt(variance),matches:values.length}})}
function drawBallStallChart(){const stats=ballStallGenerationStats(matchCatalog),canvas=ballStallChart,x=canvas.getContext('2d'),w=canvas.width,h=canvas.height,pad={left:75,right:30,top:35,bottom:65};x.fillStyle='#101d15';x.fillRect(0,0,w,h);x.font='22px sans-serif';x.fillStyle='#dce9df';if(!stats.length){x.fillText('Aguardando partidas gravadas',pad.left,h/2);ballStallSummary.textContent='Aguardando partidas gravadas.';return}const upper=Math.max(1,...stats.map(row=>row.mean+row.standardDeviation)),plotW=w-pad.left-pad.right,plotH=h-pad.top-pad.bottom,toX=index=>stats.length===1?pad.left+plotW/2:pad.left+plotW*index/(stats.length-1),toY=value=>pad.top+plotH*(1-value/upper);x.strokeStyle='#496653';x.lineWidth=1;x.fillStyle='#aac0af';x.font='18px sans-serif';x.textAlign='right';for(let tick=0;tick<=4;tick++){const value=upper*tick/4,py=toY(value);x.beginPath();x.moveTo(pad.left,py);x.lineTo(w-pad.right,py);x.stroke();x.fillText(`${value.toFixed(1)} s`,pad.left-10,py+6)}x.strokeStyle='#ffb04a';x.fillStyle='#ffb04a';x.lineWidth=4;x.textAlign='center';x.beginPath();stats.forEach((row,index)=>{const px=toX(index),py=toY(row.mean);index?x.lineTo(px,py):x.moveTo(px,py)});x.stroke();for(let index=0;index<stats.length;index++){const row=stats[index],px=toX(index),meanY=toY(row.mean),top=toY(Math.min(upper,row.mean+row.standardDeviation)),bottom=toY(Math.max(0,row.mean-row.standardDeviation));x.strokeStyle='#72b8ff';x.lineWidth=3;x.beginPath();x.moveTo(px,top);x.lineTo(px,bottom);x.moveTo(px-10,top);x.lineTo(px+10,top);x.moveTo(px-10,bottom);x.lineTo(px+10,bottom);x.stroke();x.fillStyle='#ffb04a';x.beginPath();x.arc(px,meanY,7,0,Math.PI*2);x.fill();x.fillStyle='#dce9df';x.fillText(`G${row.generation+1}`,px,h-pad.bottom+30);x.font='16px sans-serif';x.fillText(`${row.mean.toFixed(1)} ± ${row.standardDeviation.toFixed(1)} s`,px,Math.max(20,top-12));x.font='18px sans-serif'}x.textAlign='left';x.fillStyle='#ffb04a';x.fillRect(pad.left,h-28,24,4);x.fillStyle='#dce9df';x.fillText('média',pad.left+32,h-20);x.strokeStyle='#72b8ff';x.lineWidth=3;x.beginPath();x.moveTo(pad.left+120,h-28);x.lineTo(pad.left+144,h-28);x.stroke();x.fillText('± desvio padrão',pad.left+152,h-20);ballStallSummary.textContent=stats.map(row=>`Geração ${row.generation+1}: ${row.mean.toFixed(2)} ± ${row.standardDeviation.toFixed(2)} s (${row.matches} partidas)`).join(' · ')}
const fieldColors={ball:'#ffb04a',ally:'#66d9ff',enemy:'#ff6b6b',wall:'#d4ff72',wall_bottom:'#d4ff72',wall_top:'#d4ff72',wall_left:'#d4ff72',wall_right:'#d4ff72',wall_decluster:'#5fffd2',ally_goal:'#70b7ff',enemy_goal:'#ff79c6',goal_triangle_clearance:'#54f0ff',shot_clearance:'#ffb04a',corner_shot_gate:'#ffd166',goal_post_escape:'#ffffff',attacking_corner_escape:'#c89cff',goal_escape:'#ffffff'},fieldDescriptions={ball:'Campo da bola específico da função observada',ally:'Repulsão e circulação ao redor dos aliados',enemy:'Repulsão e circulação ao redor dos adversários',wall:'Repulsão das paredes próximas',wall_bottom:'Influência local da parede inferior',wall_top:'Influência local da parede superior',wall_left:'Influência local da parede esquerda',wall_right:'Influência local da parede direita',wall_decluster:'Campo-base local de desaglomeração de aliados nas paredes',ally_goal:'Repulsão e proteção do gol aliado',enemy_goal:'Atração e orientação ao centro do gol adversário',goal_triangle_clearance:'Desobstrução prioritária do triângulo entre bola e traves adversárias',shot_clearance:'Desobstrução evoluída de chute em direção ao gol adversário',corner_shot_gate:'Gate lateral evoluído para finalização junto às quinas do gol',goal_post_escape:'Override determinístico de recuperação das quatro traves',attacking_corner_escape:'Override determinístico dos bolsões laterais do ataque',goal_escape:'Override prioritário de saída do interior de qualquer gol'};
function configuredMatchDuration(){const rules=configData.competition_rules||{};return Number(matchData?.match_duration)||Number(configData.match_duration)||Math.max(1,rules.periods||2)*(rules.period_duration||300)}
function frameTime(frame){if(Number.isFinite(frame?.time))return Number(frame.time);if(Number.isFinite(frame?.step))return Number(frame.step)/30;if(Number.isFinite(frame?.time_remaining))return Math.max(0,configuredMatchDuration()-Number(frame.time_remaining));return 0}
function matchElapsedTime(frame){if(Number.isFinite(frame?.time))return Number(frame.time);if(Number.isFinite(frame?.time_remaining))return Math.max(0,configuredMatchDuration()-Number(frame.time_remaining));return Number(frame?.step||0)/30}
function replayState(){const rules=configData.competition_rules||{},periods=Math.max(1,rules.periods||2),totalDuration=Math.max(1,configuredMatchDuration()),periodDuration=totalDuration/periods,frames=matchData?.frames?.length||1,progress=frames<=1?0:frameIndex/(frames-1),f=matchData?.frames?.[frameIndex]||{},timeRemaining=Number.isFinite(f.time_remaining)?f.time_remaining:totalDuration*(1-progress),elapsed=Math.max(0,totalDuration-timeRemaining),period=Math.min(periods-1,Math.floor(elapsed/periodDuration)),isAway=uiState.candidate&&uiState.candidate===matchData?.away,initialAttackSign=isAway?1:-1,attackSign=period%2===0?initialAttackSign:-initialAttackSign,goalsFor=isAway?(f.goals_blue??0):(f.goals_yellow??0),goalsAgainst=isAway?(f.goals_yellow??0):(f.goals_blue??0);return{periods,periodDuration,totalDuration,timeRemaining,period,attackSign,isAway,goalDifference:goalsFor-goalsAgainst,goalsFor,goalsAgainst}}
function pointCoords(q){return Array.isArray(q)?q:[q?.x,q?.y]}
function worldToField(q,w,h){const[a,b]=pointCoords(q);return[20+(a+.75)/1.5*(w-40),20+(b+.65)/1.3*(h-40)]}
function fieldToWorld(px,py,w,h){return[(px-20)/(w-40)*1.5-.75,(py-20)/(h-40)*1.3-.65]}
function replayEntities(state){const f=matchData?.frames?.[frameIndex]||{},allies=state.isAway?(f.blue||[]):(f.yellow||[]),enemies=state.isAway?(f.yellow||[]):(f.blue||[]);return{frame:f,allies,enemies}}
function ballMotion(){const frames=matchData?.frames||[],current=frames[frameIndex];if(!current?.ball)return null;const[bx,by]=pointCoords(current.ball),recordedVx=Number(current.ball?.vx),recordedVy=Number(current.ball?.vy);if(Number.isFinite(recordedVx)&&Number.isFinite(recordedVy))return{bx,by,vx:recordedVx,vy:recordedVy};if(frames.length<2)return null;const before=frames[Math.max(0,frameIndex-1)],after=frames[Math.min(frames.length-1,frameIndex+1)],dt=frameTime(after)-frameTime(before);if(dt<=1e-6)return null;const[x0,y0]=pointCoords(before.ball),[x1,y1]=pointCoords(after.ball);return{bx,by,vx:(x1-x0)/dt,vy:(y1-y0)/dt}}
function shotClearanceParams(g={}){const source=Object.keys(g).length?g:(candidateData.find(v=>v.candidate_id===uiState.candidate)?.genome||{});return{threshold:Math.max(.02,Math.min(1.5,Number(source.ball_goal_min_speed??.05))),radius:Math.max(.08,Math.min(.30,Number(source.shot_clearance_radius??.14))),gain:Math.max(.30,Math.min(3,Number(source.shot_clearance_gain??1.7)))}}
function ballHeadingToEnemyGoal(state,g={}){const motion=ballMotion();if(!motion)return false;const{bx,by,vx,vy}=motion,forward=vx*state.attackSign,speed=Math.hypot(vx,vy),{threshold}=shotClearanceParams(g);if(forward<=0||speed<threshold)return false;const rules=configData.competition_rules||{},goalX=state.attackSign*(Number(rules.field_length)||1.5)/2,goalWidth=Number(rules.goal_width)||.4,time=(goalX-bx)/vx,crossingY=by+vy*time;return time>0&&Number.isFinite(crossingY)&&Math.abs(crossingY)<=goalWidth/2}
function shotClearanceVector(px,py,w,h,state,g={}){const motion=ballMotion();if(!motion||!ballHeadingToEnemyGoal(state,g))return null;const{bx,by,vx,vy}=motion,speed=Math.hypot(vx,vy),ux=vx/speed,uy=vy/speed,[rx,ry]=fieldToWorld(px,py,w,h),dx=rx-bx,dy=ry-by,forward=dx*ux+dy*uy,goalX=state.attackSign*((Number((configData.competition_rules||{}).field_length)||1.5)/2),goalDistance=(goalX-bx)/ux,lateral=ux*dy-uy*dx,{radius,gain}=shotClearanceParams(g);if(forward<=0||forward>=goalDistance||Math.abs(lateral)>=radius)return null;const tieSide=selectedIndex(uiState.player)%2===0?1:-1,side=Math.abs(lateral)>1e-9?Math.sign(lateral):tieSide,strength=gain*(1-Math.abs(lateral)/radius);return[-side*uy*strength,side*ux*strength]}
function shotClearanceGrid(w,h,state,g={}){const motion=ballMotion();if(!motion||!ballHeadingToEnemyGoal(state,g))return[];const{bx,by,vx,vy}=motion,speed=Math.hypot(vx,vy),ux=vx/speed,uy=vy/speed,goalX=state.attackSign*((Number((configData.competition_rules||{}).field_length)||1.5)/2),distance=(goalX-bx)/ux,{radius}=shotClearanceParams(g),points=[];for(let step=1;step<=9;step++){const forward=Math.max(.03,distance*step/10);for(const fraction of[-.75,-.375,0,.375,.75]){const lateral=radius*fraction;points.push(worldToField([bx+ux*forward-uy*lateral,by+uy*forward+ux*lateral],w,h))}}return points}
function drawShotCorridor(x,w,h,state,g={}){const motion=ballMotion();if(!motion||!ballHeadingToEnemyGoal(state,g))return;const{bx,by,vx,vy}=motion,speed=Math.hypot(vx,vy),ux=vx/speed,uy=vy/speed,goalX=state.attackSign*((Number((configData.competition_rules||{}).field_length)||1.5)/2),distance=(goalX-bx)/ux,{radius:r}=shotClearanceParams(g),corners=[[bx-uy*r,by+ux*r],[bx+ux*distance-uy*r,by+uy*distance+ux*r],[bx+ux*distance+uy*r,by+uy*distance-ux*r],[bx+uy*r,by-ux*r]].map(p=>worldToField(p,w,h));x.save();x.fillStyle='rgba(255,176,74,.16)';x.strokeStyle='#ffb04a';x.lineWidth=2;x.setLineDash([8,6]);x.beginPath();corners.forEach(([px,py],i)=>i?x.lineTo(px,py):x.moveTo(px,py));x.closePath();x.fill();x.stroke();x.restore()}
function cornerGateParams(g={}){return{goalDistance:Number(g.corner_gate_goal_distance??.45),crossingBand:Number(g.corner_gate_crossing_band??.12),playerAngle:Number(g.corner_gate_player_angle??.05),retreatGain:Number(g.corner_gate_retreat_gain??.55)}}
function cornerApproachSide(state,g={}){const motion=ballMotion();if(!motion)return null;const{bx,by,vx,vy}=motion,speed=Math.hypot(vx,vy),threshold=shotClearanceParams(g).threshold;if(speed<threshold||vx*state.attackSign<=0)return null;const rules=configData.competition_rules||{},goalX=state.attackSign*(Number(rules.field_length)||1.5)/2,goalWidth=Number(rules.goal_width)||.4,distance=(goalX-bx)*state.attackSign,{goalDistance,crossingBand}=cornerGateParams(g);if(distance<=0||distance>goalDistance)return null;const time=(goalX-bx)/vx,crossing=(by+vy*time)*state.attackSign;if(time<=0||Math.abs(Math.abs(crossing)-goalWidth/2)>crossingBand)return null;return Math.sign(crossing)||1}
function cornerGateVector(px,py,w,h,state,g={}){const side=cornerApproachSide(state,g),motion=ballMotion();if(side===null||!motion)return[0,0];const[rx,ry]=fieldToWorld(px,py,w,h),longitudinal=(rx-motion.bx)*state.attackSign,lateral=(ry-motion.by)*state.attackSign,{playerAngle,retreatGain}=cornerGateParams(g),angle=Math.atan2(lateral,Math.abs(longitudinal)+1e-9);return side*angle>=playerAngle?[0,0]:[-state.attackSign*retreatGain,0]}
function selectedIndex(value){const n=Number(String(value||'').split('_').pop());return Number.isInteger(n)?n:0}
function ballReturnParams(candidate,index){const g=candidate?.genome||{},power=Number(g[`ball_return_power_${index}`]??1);return{gain:Math.max(.2,Number(g[`ball_return_gain_${index}`]??1)),power:power<1.5?1:2,law:power<1.5?'linear':'quadratic'}}
function playerBallVector(px,py,w,h,state,candidate){const index=selectedIndex(uiState.player),g=candidate?.genome||{},entities=replayEntities(state),[bx,by]=pointCoords(entities.frame.ball||[0,0]),[rx,ry]=fieldToWorld(px,py,w,h),rules=configData.competition_rules||{},goalX=state.attackSign*(Number(rules.field_length)||1.5)/2,wallDistance=(goalX-bx)*state.attackSign,forward=(rx-bx)*state.attackSign;if(wallDistance<=1e-6||forward<=0||forward>=wallDistance)return null;const progress=Math.max(0,Math.min(1,forward/wallDistance)),params=ballReturnParams(candidate,index),strength=params.gain*Math.pow(progress,params.power),clearance=Number(g.return_ball_clearance_radius??.13),lateral=ry-by;if(Math.abs(lateral)<clearance){const side=Math.abs(lateral)>1e-9?Math.sign(lateral):(index%2===0?1:-1),bypass=Number(g.return_ball_bypass_gain??1.25)*(1-Math.abs(lateral)/clearance);return[-state.attackSign*.15*strength,side*bypass*strength]}const tx=bx-state.attackSign*Number(g.behind_ball_distance??.12),ty=by,dx=tx-rx,dy=ty-ry,n=Math.hypot(dx,dy)||1;return[dx/n*strength,dy/n*strength]}
function roleInfo(candidate,index){return{role:'',number:index+1,count:3,letter:'',label:`Jogador ${index+1}`}}
function fieldTargets(w,h,state){const entities=replayEntities(state),sourceIndex=selectedIndex(uiState.source),ball=worldToField(entities.frame.ball||[0,0],w,h),ally=worldToField(entities.allies[sourceIndex]||entities.allies[0]||[-.35,0],w,h),enemy=worldToField(entities.enemies[sourceIndex]||entities.enemies[0]||[.35,0],w,h);return{ball,ally,enemy,ally_goal:[state.attackSign>0?20:w-20,h*.50],enemy_goal:[state.attackSign>0?w-20:20,h*.50]}}
function evaluateExpression(expression,context){if(!expression)return 1;let value;if(Object.hasOwn(expression,'const'))value=Number(expression.const);else if(expression.feature)value=Number(context[expression.feature]||0);else if(expression.op==='add')value=(expression.args||[]).reduce((sum,arg)=>sum+evaluateExpression(arg,context),0);else if(expression.op==='mul')value=(expression.args||[]).reduce((product,arg)=>product*evaluateExpression(arg,context),1);else if(expression.op==='neg')value=-evaluateExpression(expression.arg,context);else if(expression.op==='tanh')value=Math.tanh(evaluateExpression(expression.arg,context));else if(expression.op==='clip')value=Math.max(Number(expression.min??-3),Math.min(Number(expression.max??3),evaluateExpression(expression.arg,context)));else value=1;return Number.isFinite(value)?value:0}
function wallGeometry(object,px,py,w,h,gain=1){const left=px-20,right=w-20-px,bottom=py-20,top=h-20-py,ix=.10/1.5*(w-40),iy=.10/1.3*(h-40),one=(distance,limit,vx,vy)=>[distance,distance<limit?vx*gain*(1-distance/limit):0,distance<limit?vy*gain*(1-distance/limit):0];if(object==='wall_left')return one(left,ix,1,0);if(object==='wall_right')return one(right,ix,-1,0);if(object==='wall_bottom')return one(bottom,iy,0,1);if(object==='wall_top')return one(top,iy,0,-1);const candidates=[one(left,ix,1,0),one(right,ix,-1,0),one(bottom,iy,0,1),one(top,iy,0,-1)];return candidates.reduce((best,item)=>item[0]<best[0]?item:best)}
function fieldVector(object,g,block,px,py,w,h,state){const targets=fieldTargets(w,h,state),selfExcluded=object==='ally'&&selectedIndex(uiState.player)===selectedIndex(uiState.source),isWall=object==='wall'||object.startsWith('wall_');if(selfExcluded)return[0,0];if(object==='ball'&&ballHeadingToEnemyGoal(state,g))return shotClearanceVector(px,py,w,h,state,g)||[0,0];if(object==='ball'){const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate),returnVector=playerBallVector(px,py,w,h,state,candidate);if(returnVector)return returnVector}const diagonal=Math.hypot(w-40,h-40),target=targets[object]||targets.ball,wall=wallGeometry(object,px,py,w,h,g.wall_gain||1),rawDistance=isWall?wall[0]:Math.hypot(target[0]-px,target[1]-py),scale=evaluateExpression(block?.expression,{distance:Math.max(0,Math.min(1,rawDistance/diagonal)),time_remaining:Math.max(0,Math.min(1,state.timeRemaining/state.totalDuration)),goal_difference:Math.tanh(state.goalDifference/3),attack_sign:state.attackSign});if(isWall)return[scale*wall[1],scale*wall[2]];const t=target,dx=t[0]-px,dy=t[1]-py,n=Math.hypot(dx,dy)||1;if(object==='ally'||object==='enemy'){const prefix=object==='ally'?'ally':'enemy',radial=g[`${prefix}_obstacle_radial_gain`]??g.obstacle_radial_gain??1,circular=g[`${prefix}_obstacle_circular_gain`]??g.obstacle_circular_gain??0;return[scale*(-radial*dx/n+circular*(-dy/n)),scale*(-radial*dy/n+circular*(dx/n))]}if(object==='ally_goal'){const gain=Math.abs(g.wall_gain||1);return[-scale*gain*dx/n,-scale*gain*dy/n]}let gain=1;if(object==='ball')gain=(g.approach_gain||1)+(g.shot_gain||1);return[scale*gain*dx/n,scale*gain*dy/n]}
function playerBallParameters(candidate){const g=candidate?.genome||{},i=selectedIndex(uiState.player);return{player:i,de:Number(g[`ball_spiral_radius_${i}`]??g.ball_spiral_radius??.11),kr:Number(g[`ball_spiral_smoothing_${i}`]??g.ball_spiral_smoothing??.08),gain:Number(g[`approach_gain_${i}`]??g.approach_gain??1)}}
function univectorBallVector(px,py,w,h,state,candidate){const targets=fieldTargets(w,h,state),ball=targets.ball,goal=targets.enemy_goal,sx=goal[0]-ball[0],sy=goal[1]-ball[1],sn=Math.hypot(sx,sy)||1,ux=-sx/sn,uy=-sy/sn,lx=-uy,ly=ux,rx=px-ball[0],ry=py-ball[1],xx=rx*ux+ry*uy,yy=rx*lx+ry*ly,p=playerBallParameters(candidate),de=Math.max(.001,p.de)/1.5*(w-40),kr=Math.max(0,p.kr)/1.5*(w-40),spiral=(x,y,cw)=>{const rho=Math.hypot(x,y),theta=Math.atan2(y,x),bend=rho>de?Math.PI/2*(2-(de+kr)/(rho+kr)):Math.PI/2*Math.sqrt(rho/de),phi=theta+(cw?bend:-bend);return[Math.cos(phi),Math.sin(phi)]};let local;if(yy<-de)local=spiral(xx,yy-de,true);else if(yy>=de)local=spiral(xx,yy+de,false);else{const a=spiral(xx,yy-de,false),b=spiral(xx,yy+de,true),yl=Math.abs(yy+de),yr=Math.abs(yy-de);local=[(yl*a[0]+yr*b[0])/(2*de),(yl*a[1]+yr*b[1])/(2*de)];const n=Math.hypot(...local)||1;local=[local[0]/n,local[1]/n]}return[(ux*local[0]+lx*local[1])*2.2*p.gain,(uy*local[0]+ly*local[1])*2.2*p.gain]}
const geometricFieldVector=fieldVector;function formulaFieldVector(object,g,block,px,py,w,h,state){if(object==='ball'&&ballHeadingToEnemyGoal(state,g))return shotClearanceVector(px,py,w,h,state,g)||[0,0];if(object==='ball'){const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate);return univectorBallVector(px,py,w,h,state,candidate)}if(!block?.components)return geometricFieldVector(object,g,block,px,py,w,h,state);const base=geometricFieldVector(object,g,null,px,py,w,h,state),targets=fieldTargets(w,h,state),target=targets[object]||targets.ball,diagonal=Math.hypot(w-40,h-40),isWall=object==='wall'||object.startsWith('wall_'),distance=isWall?wallGeometry(object,px,py,w,h)[0]:Math.hypot(target[0]-px,target[1]-py),context={distance:Math.max(0,Math.min(1,distance/diagonal)),time_remaining:Math.max(0,Math.min(1,state.timeRemaining/state.totalDuration)),goal_difference:Math.tanh(state.goalDifference/3),attack_sign:state.attackSign};return[base[0]*evaluateExpression(block.components.x?.expression,context),base[1]*evaluateExpression(block.components.y?.expression,context)]}fieldVector=formulaFieldVector;
function fieldGrid(w,h){const columns=13,rows=11,inset=65,points=[];for(let column=0;column<columns;column++)for(let row=0;row<rows;row++)points.push([inset+(w-2*inset)*column/(columns-1),inset+(h-2*inset)*row/(rows-1)]);return points}
function readableVectorFormula(block){if(!block)return'Fx = 1 | Fy = 1';if(block.model==='ifac2008_univector'||block.model==='ifac2008_univector_rotated')return`φ_TUF = ∠[|y+dₑ|Nₕ(CCW)(pₗ,dₑ) + |y-dₑ|Nₕ(CW)(pᵣ,dₑ)]/(2dₑ) — implementação VSSS da Eq. 4`;if(!block.components)return`Fx = (${block.text||'1'}) · direção_x | Fy = (${block.text||'1'}) · direção_y`;return`Fx = (${block.components.x?.text||'1'}) · base_x | Fy = (${block.components.y?.text||'1'}) · base_y`}
const originalDrawField=drawField;drawField=()=>{originalDrawField();const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate),architecture=candidate?.formula?.field_strategy||'legacy',player=uiState.player||'ally_0',group=candidate?.formula?.fields?.[architecture==='shared'?'shared':player]||{},block=group[uiState.object]||null,state=replayState(),clearanceActive=uiState.object==='ball'&&ballHeadingToEnemyGoal(state);if(candidate){fieldMeaning.innerHTML+=`<br><code>${clearanceActive?'F_clear = perpendicular(v_ball) · 1.7 · (1 - |d_lateral| / 0.14)':readableVectorFormula(block)}</code>`;fieldStatus.textContent=fieldStatus.textContent.replace(' | tracejado cinza: base, colorido: fórmula aplicada','')+` | tempo normalizado ${Math.max(0,Math.min(1,state.timeRemaining/state.totalDuration)).toFixed(3)}`}};
function insideGoal(position){const[x,y]=pointCoords(position),rules=configData.competition_rules||{},length=Number(rules.field_length)||1.5,width=Number(rules.goal_width)||.4;return Math.abs(x)>length/2&&Math.abs(y)<width/2}
function goalEscapeVector(px,py,w,h){const mouthTop=20+(.65-.2)/1.3*(h-40),mouthBottom=20+(.65+.2)/1.3*(h-40);if(py<=mouthTop||py>=mouthBottom)return[0,0];let tx;if(px<20)tx=20+.19/1.5*(w-40);else if(px>w-20)tx=w-20-.19/1.5*(w-40);else return[0,0];const dx=tx-px,dy=h/2-py,n=Math.hypot(dx,dy)||1;return[1.7*dx/n,1.7*dy/n]}
function goalEscapeGrid(w,h){const points=[];for(const px of[8,w-8])for(let row=0;row<7;row++)points.push([px,h*.5-85+170*row/6]);return points}
function drawArrow(x,px,py,vx,vy,color){const magnitude=Math.hypot(vx,vy);if(magnitude<1e-6)return;const length=Math.min(44,8+10*magnitude),ux=vx/magnitude,uy=vy/magnitude,ex=px+ux*length,ey=py+uy*length,head=9;x.strokeStyle=color;x.fillStyle=color;x.lineWidth=2.5;x.beginPath();x.moveTo(px,py);x.lineTo(ex,ey);x.stroke();x.beginPath();x.moveTo(ex,ey);x.lineTo(ex-head*ux+head*.55*uy,ey-head*uy-head*.55*ux);x.lineTo(ex-head*ux-head*.55*uy,ey-head*uy+head*.55*ux);x.closePath();x.fill();x.strokeStyle='#eef7ef';x.lineWidth=1.2;x.beginPath();x.arc(px,py,3,0,Math.PI*2);x.stroke()}
const drawFormulaArrow=drawArrow;drawArrow=(x,px,py,vx,vy,color)=>{if(color==='#b9c7bd')return;drawFormulaArrow(x,px,py,vx,vy,color)};
function drawField(){const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate);if(!candidate)return;const architecture=candidate.formula?.field_strategy||'legacy',player=uiState.player||'ally_0',role=roleInfo(candidate,selectedIndex(player)),formulaPlayer=architecture==='shared'?'shared':player,object=uiState.object||'ball',formulaObject=object==='ball'?`${role.role}_ball`:object,block=candidate.formula?.fields?.[formulaPlayer]?.[formulaObject]||candidate.formula?.fields?.[formulaPlayer]?.[object]||null,state=replayState(),entities=replayEntities(state),observerState=entities.allies[selectedIndex(player)]||entities.allies[0]||[0,0],overrideActive=insideGoal(observerState),clearanceActive=object==='ball'&&ballHeadingToEnemyGoal(state),effectiveObject=overrideActive?'goal_escape':object,color=fieldColors[effectiveObject]||'#d4ff72',x=field.getContext('2d'),w=field.width,h=field.height,selfExcluded=object==='ally'&&selectedIndex(player)===selectedIndex(uiState.source);x.fillStyle='#193b23';x.fillRect(0,0,w,h);x.strokeStyle='#fff';x.lineWidth=2;x.strokeRect(20,20,w-40,h-40);if(clearanceActive&&!overrideActive)drawShotCorridor(x,w,h,state);const grid=effectiveObject==='goal_escape'?goalEscapeGrid(w,h):clearanceActive?shotClearanceGrid(w,h,state):fieldGrid(w,h);for(const[px,py]of grid){if(effectiveObject==='goal_escape'){const[vx,vy]=goalEscapeVector(px,py,w,h);drawArrow(x,px,py,vx,vy,color);continue}const[baseX,baseY]=fieldVector(object,candidate.genome,null,px,py,w,h,state),[vx,vy]=fieldVector(object,candidate.genome,block,px,py,w,h,state);x.setLineDash([5,4]);drawArrow(x,px,py,baseX,baseY,'#b9c7bd');x.setLineDash([]);drawArrow(x,px,py,vx,vy,color)}const targets=fieldTargets(w,h,state),target=effectiveObject==='goal_escape'?null:targets[object],rawObserver=worldToField(observerState,w,h),observer=[Math.max(8,Math.min(w-8,rawObserver[0])),rawObserver[1]],observerDistance=(object==='wall'||object.startsWith('wall_'))?wallGeometry(object,observer[0],observer[1],w,h)[0]:Math.hypot((target?.[0]||observer[0])-observer[0],(target?.[1]||observer[1])-observer[1]),gpFactor=effectiveObject==='goal_escape'?1:evaluateExpression(block?.expression,{distance:Math.max(0,Math.min(1,observerDistance/Math.hypot(w-40,h-40))),time_remaining:Math.max(0,Math.min(1,state.timeRemaining/state.totalDuration)),goal_difference:Math.tanh(state.goalDifference/3),attack_sign:state.attackSign});if(target){x.fillStyle=color;x.beginPath();x.arc(target[0],target[1],9,0,Math.PI*2);x.fill();x.strokeStyle='#fff';x.lineWidth=2;x.stroke()}if(overrideActive){const direction=observer[0]<w/2?1.8:-1.8;drawArrow(x,observer[0],observer[1],direction,0,'#fff')}x.strokeStyle='#fff';x.lineWidth=3;x.beginPath();x.arc(observer[0],observer[1],13,0,Math.PI*2);x.stroke();x.fillStyle='#fff';x.font='bold 17px sans-serif';x.fillText(`${role.label} (${role.letter}) | campo: ${effectiveObject}${uiState.source?' / '+uiState.source:''} | ${state.period+1}º tempo`,32,43);x.font='bold 14px sans-serif';x.fillText(clearanceActive?'DESOBSTRUÇÃO DE CHUTE ATIVA — corredor 0.28 m':`fórmula GP: ${block?.text||'1'} | fator no jogador: ${gpFactor.toFixed(3)}`,32,63);fieldMeaning.innerHTML=`<strong style="color:${color}">${clearanceActive?'Desobstrução lateral do chute':fieldDescriptions[effectiveObject]||effectiveObject}</strong> — candidato ${candidate.candidate_id}`;fieldStatus.textContent=overrideActive?'OVERRIDE ATIVO: jogador dentro do gol; todos os demais campos foram suplantados.':clearanceActive?'DESOBSTRUÇÃO DE CHUTE ATIVA: vetores laterais retiram os jogadores do corredor bola–gol.':selfExcluded?'Campo próprio excluído: este aliado não influencia a si mesmo.':`${state.period+1}º tempo | quadro ${frameIndex+1} | saldo ${state.goalDifference>=0?'+':''}${state.goalDifference} | fator GP neste ponto ${gpFactor.toFixed(3)} | tracejado cinza: base, colorido: fórmula aplicada`;formula.textContent=JSON.stringify({candidate:candidate.candidate_id,roles:candidate.formula?.roles||[],observed_role:role,role_formula_object:formulaObject,field_strategy:architecture,self_field:candidate.formula?.self_field||'unknown',goal_escape_override:candidate.formula?.goal_escape_override||{priority:'absolute',activation:'abs(x) > field_length / 2 and abs(y) < goal_width / 2',deactivation:'robot center returns inside field',vector:'(-sign(x) * max_nominal_speed, 0)'},override_active:overrideActive,shot_clearance_active:clearanceActive,shot_clearance:clearanceActive?{radius_m:.14,diameter_m:.28,min_ball_speed_m_s:.05,formula:'perpendicular(v_ball) * 1.7 * (1 - abs(lateral_distance) / 0.14)'}:null,player,formula_player:formulaPlayer,object,effective_object:effectiveObject,source:uiState.source||object,frame:frameIndex+1,period:state.period+1,attack_sign:state.attackSign,goals_for:state.goalsFor,goals_against:state.goalsAgainst,goal_difference:state.goalDifference,self_excluded:selfExcluded,formula_text:block?.text||'1',formula_expression:block?.expression||{const:1},formula_factor_at_observer:gpFactor,formula_block:block,object_parameters:object==='ally'?{radial:candidate.genome.ally_obstacle_radial_gain,circular:candidate.genome.ally_obstacle_circular_gain}:object==='enemy'?{radial:candidate.genome.enemy_obstacle_radial_gain,circular:candidate.genome.enemy_obstacle_circular_gain}:candidate.genome},null,2)}
function configureSourceMenu(){const object=uiState.object,count=Math.max(1,Number(configData.robots_per_team)||3),needsSource=object==='ally'||object==='enemy',sources=needsSource?Array.from({length:count},(_,i)=>`${object}_${i}`):[],defaultSource=object==='ally'?(sources.find(source=>selectedIndex(source)!==selectedIndex(uiState.player))||sources[0]):sources[0];sourceControl.classList.toggle('hidden',!needsSource);uiState.source=needsSource?setOptions(fieldSources,sources,uiState.source||defaultSource):''}
function configureMatchCandidates(){const ids=[matchData?.home,matchData?.away].filter(Boolean),labels={};if(matchData?.home)labels[matchData.home]=`🟨 ${matchData.home}`;if(matchData?.away)labels[matchData.away]=`🟦 ${matchData.away}`;candidates.innerHTML=ids.map(id=>`<option value="${id}">${labels[id]}</option>`).join('');candidates.dataset.signature=ids.join('\u0000');uiState.candidate=ids.includes(uiState.candidate)?uiState.candidate:(ids[0]||'');if(uiState.candidate)candidates.value=uiState.candidate}
function configureFieldMenus(){const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate);if(!candidate){fieldMeaning.textContent=matchData?'Fórmula deste candidato indisponível para a partida selecionada.':'Selecione uma partida para visualizar seus campos vetoriais.';fieldStatus.textContent='';formula.textContent='';return}const fields=candidate.formula?.fields||{},architecture=candidate.formula?.field_strategy||'legacy',count=Math.max(1,Number(configData.robots_per_team)||3),availablePlayers=architecture==='individual'?Object.keys(fields):Array.from({length:count},(_,i)=>`ally_${i}`),playerItems=availablePlayers.map(value=>({value,label:roleInfo(candidate,selectedIndex(value)).label}));uiState.player=setLabeledOptions(fieldPlayers,playerItems.length?playerItems:[{value:'ally_0',label:'Atacante 1'}],uiState.player);const formulaPlayer=architecture==='shared'?'shared':uiState.player,group=fields[formulaPlayer]||fields[Object.keys(fields)[0]]||{},safety=['wall_decluster','goal_triangle_clearance','shot_clearance','corner_shot_gate','goal_post_escape','attacking_corner_escape','goal_escape'],evolved=Object.keys(group).filter(name=>!['attacker_ball','defender_ball'].includes(name)),available=[...new Set([...evolved,...safety])];uiState.object=setOptions(fieldObjects,available.length?available:objects,uiState.object);configureSourceMenu();drawField()}
const parameterizedDrawField=drawField;drawField=()=>{parameterizedDrawField();const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate);if(!candidate||uiState.object!=='ball')return;const state=replayState(),params=shotClearanceParams(candidate.genome),motion=ballMotion(),speed=motion?Math.hypot(motion.vx,motion.vy):0,active=ballHeadingToEnemyGoal(state,candidate.genome),activation=`Ativa se ||v_bola|| = ${speed.toFixed(3)} m/s ≥ threshold evoluído ${params.threshold.toFixed(3)} m/s e a trajetória cruza o gol`,equation=`F_clear = perpendicular(v_bola) · ${params.gain.toFixed(3)} · (1 - |d_lateral| / ${params.radius.toFixed(3)})`;const code=fieldMeaning.querySelector('code');if(code)code.textContent=`${activation} | ${equation}`;fieldStatus.textContent=`${active?'DESOBSTRUÇÃO ATIVA':'Desobstrução inativa'} | velocidade ${speed.toFixed(3)} m/s | threshold ${params.threshold.toFixed(3)} m/s | raio ${params.radius.toFixed(3)} m | ganho ${params.gain.toFixed(3)}`;if(active){const x=field.getContext('2d');x.fillStyle='#193b23';x.fillRect(25,48,700,20);x.fillStyle='#fff';x.font='bold 14px sans-serif';x.fillText(`DESOBSTRUÇÃO ATIVA — v ${speed.toFixed(3)} ≥ ${params.threshold.toFixed(3)} m/s | raio ${params.radius.toFixed(3)} m | ganho ${params.gain.toFixed(3)}`,32,63)}try{const payload=JSON.parse(formula.textContent);payload.shot_clearance_active=active;payload.shot_clearance={evolved:true,ball_speed_m_s:speed,threshold_m_s:params.threshold,radius_m:params.radius,diameter_m:2*params.radius,gain:params.gain,activation:'norm(v_ball) >= threshold and projected trajectory crosses opponent goal mouth',formula:'perpendicular(v_ball) * gain * (1 - abs(lateral_distance) / radius)'};formula.textContent=JSON.stringify(payload,null,2)}catch{}}
function wallDeclusterVector(px,py,w,h){const[rx,ry]=fieldToWorld(px,py,w,h),rules=configData.competition_rules||{},halfL=(Number(rules.field_length)||1.5)/2,halfW=(Number(rules.field_width)||1.3)/2,band=.14,neighborLimit=.24,walls=[{d:Math.abs(ry+halfW),t:[1,0],n:[0,1],q:[rx,-halfW]},{d:Math.abs(ry-halfW),t:[1,0],n:[0,-1],q:[rx,halfW]},{d:Math.abs(rx+halfL),t:[0,1],n:[1,0],q:[-halfL,ry]},{d:Math.abs(rx-halfL),t:[0,1],n:[-1,0],q:[halfL,ry]}],wall=walls.reduce((a,b)=>b.d<a.d?b:a);if(wall.d>=band)return[0,0];const allies=replayEntities(replayState()).allies||[],self=selectedIndex(uiState.player);let vx=0,vy=0;for(let i=0;i<allies.length;i++){if(i===self)continue;const ally=allies[i],allyDistance=wall.t[0]?Math.abs(ally[1]-wall.q[1]):Math.abs(ally[0]-wall.q[0]),along=(rx-ally[0])*wall.t[0]+(ry-ally[1])*wall.t[1];if(allyDistance>=band||Math.abs(along)>=neighborLimit)continue;const side=Math.abs(along)>1e-9?Math.sign(along):(self<i?1:-1),strength=(1-wall.d/band)*(1-Math.abs(along)/neighborLimit);vx+=wall.t[0]*side*.8*strength+wall.n[0]*.4*strength;vy+=wall.t[1]*side*.8*strength+wall.n[1]*.4*strength}return[vx,vy]}
function goalTriangleVector(px,py,w,h,state,g={}){const entities=replayEntities(state),[bx,by]=pointCoords(entities.frame.ball||[0,0]),[rx,ry]=fieldToWorld(px,py,w,h),rules=configData.competition_rules||{},length=Number(rules.field_length)||1.5,goalWidth=Number(rules.goal_width)||.4,goalX=state.attackSign*length/2,distance=(goalX-bx)*state.attackSign,forward=(rx-bx)*state.attackSign;if(distance<=1e-6||forward<=0||forward>=distance)return[0,0];const fraction=forward/distance,centre=by*(1-fraction),margin=Number(g.goal_triangle_margin??.04),half=fraction*goalWidth/2+margin,lateral=ry-centre;if(Math.abs(lateral)>=half)return[0,0];const side=Math.abs(lateral)>1e-9?Math.sign(lateral):(selectedIndex(uiState.player)%2===0?1:-1),strength=Number(g.goal_triangle_clearance_gain??1.4)*(1-Math.abs(lateral)/half),back=-state.attackSign*Number(g.goal_triangle_backward_gain??.35)*(1-fraction);return[back,side*strength]}
function deterministicSafetyVector(object,px,py,w,h,state,candidate){if(object==='wall_decluster')return wallDeclusterVector(px,py,w,h);if(object==='goal_triangle_clearance')return goalTriangleVector(px,py,w,h,state,candidate.genome);if(object==='shot_clearance')return shotClearanceVector(px,py,w,h,state,candidate.genome)||[0,0];if(object==='corner_shot_gate')return cornerGateVector(px,py,w,h,state,candidate.genome);const[rx,ry]=fieldToWorld(px,py,w,h),rules=configData.competition_rules||{},length=Number(rules.field_length)||1.5,width=Number(rules.field_width)||1.3,goalWidth=Number(rules.goal_width)||.4,goalDepth=Number(rules.goal_depth)||.1;if(object==='attacking_corner_escape'){const goalX=state.attackSign*length/2,behind=(goalX-rx)*state.attackSign,wallDistance=width/2-Math.abs(ry);if(behind < -goalDepth||behind > .24||wallDistance > .15||Math.abs(ry)<=goalWidth/2)return[0,0];const n=Math.hypot(.45,1);return[-state.attackSign*.45*.65/n,-Math.sign(ry)*.65/n]}if(object==='goal_post_escape'){const posts=[[-length/2,-goalWidth/2],[-length/2,goalWidth/2],[length/2,-goalWidth/2],[length/2,goalWidth/2]],post=posts.reduce((best,p)=>Math.hypot(rx-p[0],ry-p[1])<Math.hypot(rx-best[0],ry-best[1])?p:best),distance=Math.hypot(rx-post[0],ry-post[1]);if(distance>.19)return[0,0];const tx=post[0]-Math.sign(post[0])*.19,ty=post[1]-Math.sign(post[1])*.19*.65,dx=tx-rx,dy=ty-ry,n=Math.hypot(dx,dy)||1,speed=.55*(.45+.55*Math.min(1,distance/.19));return[dx/n*speed,dy/n*speed]}return goalEscapeVector(px,py,w,h)}
function drawGoalTriangle(x,w,h,state,g={}){const entities=replayEntities(state),ball=pointCoords(entities.frame.ball||[0,0]),rules=configData.competition_rules||{},length=Number(rules.field_length)||1.5,goalWidth=Number(rules.goal_width)||.4,goalX=state.attackSign*length/2,margin=Number(g.goal_triangle_margin??.04),points=[ball,[goalX,-goalWidth/2-margin],[goalX,goalWidth/2+margin]].map(p=>worldToField(p,w,h));x.save();x.fillStyle='rgba(84,240,255,.12)';x.strokeStyle='#54f0ff';x.lineWidth=2;x.setLineDash([7,5]);x.beginPath();points.forEach(([a,b],i)=>i?x.lineTo(a,b):x.moveTo(a,b));x.closePath();x.fill();x.stroke();x.restore()}
const allFieldsDrawField=drawField;drawField=()=>{const special=['wall_decluster','goal_triangle_clearance','shot_clearance','corner_shot_gate','goal_post_escape','attacking_corner_escape'];if(!special.includes(uiState.object)){allFieldsDrawField();return}const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate);if(!candidate)return;const state=replayState(),object=uiState.object,x=field.getContext('2d'),w=field.width,h=field.height,color=fieldColors[object];x.fillStyle='#193b23';x.fillRect(0,0,w,h);x.strokeStyle='#fff';x.lineWidth=2;x.strokeRect(20,20,w-40,h-40);if(object==='shot_clearance')drawShotCorridor(x,w,h,state,candidate.genome);if(object==='goal_triangle_clearance')drawGoalTriangle(x,w,h,state,candidate.genome);for(const[px,py]of fieldGrid(w,h)){const[vx,vy]=deterministicSafetyVector(object,px,py,w,h,state,candidate);drawArrow(x,px,py,vx,vy,color)}fieldMeaning.innerHTML=`<strong style="color:${color}">${fieldDescriptions[object]}</strong> — candidato ${candidate.candidate_id}`;const entities=replayEntities(state),observer=worldToField(entities.allies[selectedIndex(uiState.player)]||[0,0],w,h),observerVector=deterministicSafetyVector(object,observer[0],observer[1],w,h,state,candidate),active=object==='shot_clearance'?ballHeadingToEnemyGoal(state,candidate.genome):object==='corner_shot_gate'?cornerApproachSide(state,candidate.genome)!==null:object==='goal_triangle_clearance'?Math.hypot(...observerVector)>1e-6:object==='wall_decluster'?'depende da proximidade simultânea de aliados à mesma parede':'depende da posição e do estado do jogador';fieldStatus.textContent=`${state.period+1}º tempo | campo ${object} | ativo: ${active}`;formula.textContent=JSON.stringify({candidate:candidate.candidate_id,object,field_type:object==='shot_clearance'||object==='corner_shot_gate'||object==='goal_triangle_clearance'?'evolved_gate_and_parameters':object==='wall_decluster'?'deterministic_base_field':'deterministic_priority_override',active,attack_sign:state.attackSign,formula:object==='goal_triangle_clearance'?'inside_triangle ? lateral_nearest_exit + backward_component : 0':object==='wall_decluster'?'sum_neighbors[(0.8 * tangent_away + 0.4 * inward) * wall_weight * neighbor_weight]':object==='shot_clearance'?'perpendicular(v_ball) * gain * (1 - abs(lateral_distance) / radius)':object==='corner_shot_gate'?'wrong_side ? -attack_direction * retreat_gain : normal_ball_field':object==='goal_post_escape'?'v(d) * unit(target_inside_field_and_toward_goal_centre - robot_position)':'0.65 * unit(-0.45 * attack_direction + direction_to_centre)',parameters:object==='goal_triangle_clearance'?{margin_m:candidate.genome.goal_triangle_margin,lateral_gain:candidate.genome.goal_triangle_clearance_gain,backward_gain:candidate.genome.goal_triangle_backward_gain}:object==='wall_decluster'?{wall_band_m:.14,neighbor_distance_m:.24,tangent_gain:.8,inward_gain:.4,evolved:false}:object==='shot_clearance'?shotClearanceParams(candidate.genome):object==='corner_shot_gate'?cornerGateParams(candidate.genome):object==='goal_post_escape'?{activation_radius_m:.13,release_radius_m:.19,stall_speed_m_s:.05,stall_duration_s:.30,max_displacement_m:.015,max_speed_m_s:.55}:{depth_m:.24,wall_band_m:.15,speed_m_s:.65}},null,2)}
const accurateGoalWallDrawField=drawField;drawField=()=>{accurateGoalWallDrawField();if(uiState.object!=='goal_escape')return;try{const payload=JSON.parse(formula.textContent);payload.goal_escape_override={priority:'absolute_after_post_recovery',activation:'robot centre is behind either goal line and inside the mouth',deactivation:'robot centre returns to the playable field',vector:'max_nominal_speed * unit(inside_field_mouth_centre - robot_position)',purpose:'goal side and back walls guide the robot through the mouth centre'};formula.textContent=JSON.stringify(payload,null,2);fieldMeaning.innerHTML='<strong style="color:#fff">Saída guiada pelas paredes internas do gol</strong> — centro da boca e retorno ao campo';fieldStatus.textContent='Override determinístico: os cantos internos não são destinos estáveis.'}catch{}}
const playerReturnDrawField=drawField;drawField=()=>{playerReturnDrawField();if(uiState.object!=='ball')return;const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate);if(!candidate)return;const state=replayState(),index=selectedIndex(uiState.player),params=ballReturnParams(candidate,index),entities=replayEntities(state),[bx]=pointCoords(entities.frame.ball||[0,0]),[rx]=pointCoords(entities.allies[index]||[0,0]),goalX=state.attackSign*(Number((configData.competition_rules||{}).field_length)||1.5)/2,wallDistance=Math.max(1e-9,(goalX-bx)*state.attackSign),progress=Math.max(0,Math.min(1,(rx-bx)*state.attackSign/wallDistance)),strength=params.gain*Math.pow(progress,params.power),equation=`F_retorno_${index+1} = ${params.gain.toFixed(4)} · q^${params.power}, q = clip(((p_jogador-p_bola)·ataque)/distância(bola,parede_inimiga), 0, 1)`;fieldMeaning.innerHTML=`<strong style="color:${fieldColors.ball}">Campo comum da bola — Jogador ${index+1}</strong> — ${params.law}, ganho ${params.gain.toFixed(4)}<br><code>${equation}</code>`;fieldStatus.textContent=`${state.period+1}º tempo | q ${progress.toFixed(3)} | força de retorno ${strength.toFixed(3)} | desvio lateral evita tocar a bola durante o retorno`;try{const payload=JSON.parse(formula.textContent);delete payload.roles;delete payload.observed_role;delete payload.role_formula_object;payload.player_ball_return={player:index+1,base_field:'campo GP da bola comum aos três jogadores',activation:'0 < (player-ball)·attack < distance(ball, enemy goal wall)',progress,positive_gain:params.gain,power:params.power,law:params.law,strength,equation,bypass_clearance_radius_m:Number(candidate.genome.return_ball_clearance_radius??.13),bypass_gain:Number(candidate.genome.return_ball_bypass_gain??1.25),avoids_ball_contact_while_returning:true};formula.textContent=JSON.stringify(payload,null,2)}catch{}}
const specializedBallDrawField=drawField;drawField=()=>{const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate),index=selectedIndex(uiState.player),architecture=candidate?.formula?.field_strategy||'shared',group=candidate?.formula?.fields?.[architecture==='shared'?'shared':uiState.player],specialized=candidate?.formula?.player_ball_fields?.[index],original=group?.ball;if(uiState.object==='ball'&&group&&specialized)group.ball=specialized;specializedBallDrawField();if(group&&specialized)group.ball=original;if(uiState.object!=='ball'||!candidate)return;try{const payload=JSON.parse(formula.textContent),lineage=candidate.formula?.ball_player_specialization?.[index]||null;payload.ball_formula_block=specialized||original||null;payload.specialization=lineage;payload.goal_attribution='último aliado que tocou fisicamente na bola';payload.defense_attribution='bola projetava entrada no gol aliado, foi tocada por aliado e deixou de interceptar o gol';formula.textContent=JSON.stringify(payload,null,2);if(lineage)fieldMeaning.innerHTML+=`<br><strong>Especialização:</strong> ${lineage.role} — pais ${lineage.parents.join(' × ')}`}catch{}}
const sharedIfacBallDrawField=drawField;drawField=()=>{sharedIfacBallDrawField();if(uiState.object!=='ball')return;const candidate=candidateData.find(v=>v.candidate_id===uiState.candidate);if(!candidate)return;const state=replayState(),entities=replayEntities(state),ball=pointCoords(entities.frame.ball||[0,0]),targets=fieldTargets(field.width,field.height,state),goal=fieldToWorld(targets.enemy_goal[0],targets.enemy_goal[1],field.width,field.height),dx=goal[0]-ball[0],dy=goal[1]-ball[1],angle=Math.atan2(dy,dx),p=playerBallParameters(candidate);fieldMeaning.innerHTML=`<strong style="color:${fieldColors.ball}">Campo univetorial da bola — Jogador ${p.player+1}</strong> — mesma estrutura, parâmetros evoluídos independentemente`;fieldStatus.textContent=`${state.period+1}º tempo | direção de chute ${(angle*180/Math.PI).toFixed(1)}° | dₑ ${p.de.toFixed(4)} m | Kᵣ ${p.kr.toFixed(4)} m | ganho ${p.gain.toFixed(4)}`;formula.textContent=JSON.stringify({candidate:candidate.candidate_id,player:p.player+1,object:'ball',model:'ifac2008_univector_rotated',source:'Lim et al., IFAC 2008 + implementação operacional Ararabots VSSS',shared_structure_by_all_players:true,independent_parameters_by_player:true,canonical_axis:'u = -unit(opponent_goal_center - ball_position)',desired_kick_direction:'unit(opponent_goal_center - ball_position)',rotation_angle_rad:angle,parameters:{d_e_m:p.de,K_r_m:p.kr,approach_gain:p.gain},hyperbolic_spiral:'φ_h = θ ± (π/2)·(2-(dₑ+Kᵣ)/(ρ+Kᵣ)), se ρ>dₑ; θ ± (π/2)·sqrt(ρ/dₑ), caso contrário',central_blend:'φ_TUF = angle((|y+dₑ|N_CCW(p_l)+|y-dₑ|N_CW(p_r))/(2dₑ))',world_vector:'[u u_perp] · N_TUF'},null,2)}
function matchClock(){const state=replayState(),elapsed=Math.max(0,state.totalDuration-state.timeRemaining),remaining=Math.max(0,Math.ceil(state.periodDuration-(elapsed-state.period*state.periodDuration))),minutes=String(Math.floor(remaining/60)).padStart(2,'0'),seconds=String(remaining%60).padStart(2,'0');return`${state.period+1}º tempo — ${minutes}:${seconds}`}

function teamScoresAt(frame){const frames=matchData?.frames||[],duration=configuredMatchDuration(),half=duration/2,gy=Number(frame?.goals_yellow||0),gb=Number(frame?.goals_blue||0),i=frames.findIndex(f=>Number(f.period)>=2||(Number.isFinite(f.time_remaining)&&f.time_remaining<=half)),h=i>0?frames[i-1]:frames.find(f=>Number(f.period)===1)||null,hgy=Number(h?.goals_yellow||0),hgb=Number(h?.goals_blue||0),second=Number(frame?.period)>=2||(Number(frame?.time||0)>=half);return second?[hgy+Math.max(0,gb-hgb),hgb+Math.max(0,gy-hgy)]:[gy,gb]}
function periodScoreText(){const frames=matchData?.frames||[],mapped=teamScoresAt(frames.at(-1)),finalHome=mapped[0],finalAway=mapped[1],state=replayState(),halftimeIndex=frames.findIndex(frame=>Number(frame.period)>=2||(Number.isFinite(frame.time_remaining)&&frame.time_remaining<=state.periodDuration));const firstEnd=halftimeIndex>0?frames[halftimeIndex-1]:frames.find(frame=>Number(frame.period)===1)||null,firstHome=Number(firstEnd?.goals_yellow??0),firstAway=Number(firstEnd?.goals_blue??0),secondStarted=halftimeIndex>=0||state.period>0,score=(yellow,blue)=>`<span class="score-yellow">🟨 ${yellow}</span> × <span class="score-blue">${blue} 🟦</span>`,second=secondStarted?score(Math.max(0,finalHome-firstHome),Math.max(0,finalAway-firstAway)):'aguardando';return`<div class="score-row"><span class="score-period">1º tempo</span><span class="score-value">${score(firstHome,firstAway)}</span></div><div class="score-row"><span class="score-period">2º tempo</span><span class="score-value">${second}</span></div><div class="score-row"><span class="score-period">Final</span><span class="score-value">${score(finalHome,finalAway)}</span></div>`}
function goalMoments(){const events=[],frames=matchData?.frames||[];let yellow=0,blue=0;frames.forEach((frame,index)=>{const nextYellow=Number(frame.goals_yellow??yellow),nextBlue=Number(frame.goals_blue??blue),eventTime=matchElapsedTime(frame),second=Number(frame.period||1)>=2;for(let n=yellow;n<nextYellow;n++)events.push({index,team:second?`🟦 ${matchData.away||'time B'}`:`🟨 ${matchData.home||'time A'}`,yellow:yellow+(n-yellow)+1,blue:nextBlue,time:eventTime,period:Number(frame.period||1)});for(let n=blue;n<nextBlue;n++)events.push({index,team:second?`🟨 ${matchData.home||'time A'}`:`🟦 ${matchData.away||'time B'}`,yellow:nextYellow,blue:blue+(n-blue)+1,time:eventTime,period:Number(frame.period||1)});yellow=nextYellow;blue=nextBlue});return events.sort((a,b)=>a.index-b.index).map(event=>{const targetTime=Math.max(0,event.time-3);let preIndex=0;for(let i=event.index;i>=0;i--){if(matchElapsedTime(frames[i])<=targetTime){preIndex=i;break}}return{...event,index:preIndex,goalIndex:event.index}})}
function renderGoalEvents(){const events=goalMoments();goalEvents.innerHTML=events.length?`<strong>Ir para o gol:</strong>${events.map(event=>{const minutes=Math.floor(event.time/60),seconds=String(Math.floor(event.time%60)).padStart(2,'0');const [teamYellowScore,teamBlueScore]=teamScoresAt(matchData.frames[event.goalIndex]);return`<button data-goal-frame="${event.index}" title="Gol no quadro ${event.goalIndex+1}; iniciar 3 s antes">${event.team}, ${event.period}º tempo ${minutes}:${seconds} (${teamYellowScore} × ${teamBlueScore})</button>`}).join('')}`:'Nenhum gol gravado'}
function updateGoalColors(){const second=replayState().period%2===1,leftTeam=second?'yellow':'blue',rightTeam=second?'blue':'yellow',colors={yellow:'#ffdf3e',blue:'#53a6ff'},labels={yellow:'🟨',blue:'🟦'};for(const[id,team]of [['leftGoal',leftTeam],['rightGoal',rightTeam]]){const group=document.getElementById(id);group.querySelector('.goal-frame').style.stroke=colors[team];group.querySelectorAll('.goal-post').forEach(post=>post.style.fill=colors[team])}const leftLabel=document.getElementById('leftGoalLabel'),rightLabel=document.getElementById('rightGoalLabel');leftLabel.textContent=labels[leftTeam];leftLabel.style.fill=colors[leftTeam];rightLabel.textContent=labels[rightTeam];rightLabel.style.fill=colors[rightTeam]}
function drawMatchFrame(){if(!matchData?.frames?.length){frameLabel.textContent='Esta partida não possui quadros gravados';periodLabel.textContent='Tempo indisponível';recordedScore.textContent='Placar indisponível';return}frameIndex=Math.max(0,Math.min(frameIndex,matchData.frames.length-1));const f=matchData.frames[frameIndex],x=match.getContext('2d'),w=match.width,h=match.height;x.fillStyle='#193b23';x.fillRect(0,0,w,h);x.strokeStyle='#fff';x.lineWidth=2;x.strokeRect(20,20,w-40,h-40);const coords=q=>Array.isArray(q)?q:[q?.x,q?.y],map=q=>{const[a,b]=coords(q);return[20+(a+.75)/1.5*(w-40),20+(b+.65)/1.3*(h-40)]},trail=matchData.frames.slice(Math.max(0,frameIndex-14),frameIndex+1).map(v=>v.ball).filter(q=>{const[a,b]=coords(q);return Number.isFinite(a)&&Number.isFinite(b)});if(trail.length>1){x.strokeStyle='#ffb04a';x.lineWidth=4;x.globalAlpha=.5;x.beginPath();trail.forEach((q,i)=>{const[a,b]=map(q);i?x.lineTo(a,b):x.moveTo(a,b)});x.stroke();x.globalAlpha=1}const p=(q,col,r=9,label='')=>{const[qx,qy]=coords(q);if(!Number.isFinite(qx)||!Number.isFinite(qy))return;const[cx,cy]=map(q);x.fillStyle=col;x.strokeStyle='#fff';x.lineWidth=1.5;x.beginPath();x.arc(cx,cy,r,0,7);x.fill();x.stroke();if(label){x.fillStyle='#102418';x.font='bold 11px sans-serif';x.textAlign='center';x.textBaseline='middle';x.fillText(label,cx,cy);x.textAlign='start';x.textBaseline='alphabetic'}};const homeCandidate=candidateData.find(v=>v.candidate_id===matchData.home),awayCandidate=candidateData.find(v=>v.candidate_id===matchData.away);p(f.ball,'#ff8b4d',8);(f.yellow||[]).forEach((q,i)=>p(q,'#ffdf3e',11,roleInfo(homeCandidate,i).letter));(f.blue||[]).forEach((q,i)=>p(q,'#53a6ff',11,roleInfo(awayCandidate,i).letter));updateGoalColors();timeline.value=frameIndex;periodLabel.textContent=matchClock();frameLabel.textContent=`Quadro ${frameIndex+1}/${matchData.frames.length}`;const mappedGoals=teamScoresAt(f),homeGoals=mappedGoals[0],awayGoals=mappedGoals[1],balance=homeGoals-awayGoals,finalHome=Number(matchData.frames.at(-1)?.goals_yellow??matchData.home_goals??homeGoals),finalAway=Number(matchData.frames.at(-1)?.goals_blue??matchData.away_goals??awayGoals);recordedScore.innerHTML=periodScoreText(finalHome,finalAway);scoreLabel.textContent=`Saldo da casa no quadro: ${balance>=0?'+':''}${balance}`;drawField()}
function animate(now){if(!playing)return;if(!lastTick)lastTick=now;playbackTime+=(now-lastTick)/1000*Number(speed.value);lastTick=now;while(frameIndex<matchData.frames.length-1&&frameTime(matchData.frames[frameIndex+1])<=playbackTime)frameIndex+=1;if(frameIndex>=matchData.frames.length-1){drawMatchFrame();if(matchData.live){animationId=requestAnimationFrame(animate);return}setPlaying(false);return}drawMatchFrame();animationId=requestAnimationFrame(animate)}
function setPlaying(value){const starting=value&&!playing;playing=value;play.textContent=value?'⏸ Pausar':'▶ Reproduzir';cancelAnimationFrame(animationId);lastTick=0;if(starting&&matchData?.frames?.length)playbackTime=frameTime(matchData.frames[frameIndex]);if(value&&matchData?.frames?.length)animationId=requestAnimationFrame(animate)}
function jumpToPeriod(period){if(!matchData?.frames?.length)return;const state=replayState(),index=period===1?0:matchData.frames.findIndex(frame=>Number(frame.period)>=2||(Number.isFinite(frame.time_remaining)&&frame.time_remaining<=state.periodDuration));if(index<0){periodLabel.textContent='2º tempo ainda não foi gravado';return}setPlaying(false);frameIndex=index;drawMatchFrame()}
async function loadMatch(followLive=false){if(!followLive)setPlaying(false);if(!uiState.match){matchData=null;candidateData=[];loadedMatchId='';configureMatchCandidates();configureFieldMenus();frameLabel.textContent='Nenhuma partida gravada';goalEvents.textContent='Nenhum gol gravado';return}const requestedMatch=uiState.match,nextMatch=await api('/api/match?id='+encodeURIComponent(uiState.match));if(requestedMatch!==uiState.match||(followLive&&!uiState.followLive))return;matchData=nextMatch;candidateData=Array.isArray(matchData.candidates)?matchData.candidates:[];loadedMatchId=uiState.match;frameIndex=followLive?Math.max(0,(matchData.frames?.length||1)-1):0;timeline.max=Math.max(0,(matchData.frames?.length||1)-1);configureMatchCandidates();configureFieldMenus();renderGoalEvents();drawMatchFrame()}
async function refreshLiveMatch(){const requestedMatch=uiState.match,keptFrame=frameIndex,wasPlaying=playing,nextMatch=await api('/api/match?id='+encodeURIComponent(requestedMatch));if(requestedMatch!==uiState.match)return;matchData=nextMatch;candidateData=Array.isArray(matchData.candidates)?matchData.candidates:[];loadedMatchId=requestedMatch;frameIndex=uiState.followLive&&!wasPlaying?Math.max(0,(matchData.frames?.length||1)-1):Math.min(keptFrame,Math.max(0,(matchData.frames?.length||1)-1));timeline.max=Math.max(0,(matchData.frames?.length||1)-1);configureMatchCandidates();configureFieldMenus();renderGoalEvents();drawMatchFrame()}
let refreshing=false;async function refresh(){if(refreshing)return;refreshing=true;try{const rankingUrl='/api/ranking'+(uiState.generation!==''?'?generation='+encodeURIComponent(uiState.generation):''),[live,rank,catalog,config]=await Promise.all([api('/api/live'),api(rankingUrl),api('/api/match-catalog'),api('/api/config')]);rankingData=rank;matchCatalog=catalog;configData=config;drawBallStallChart();const runComplete=live.status==='complete',completedGames=runComplete?catalog.length:(live.completed_matches??0),totalGames=runComplete?catalog.length:(live.total_matches??0),activeGames=Number(live.active_matches||0),gamesLabel=`${completedGames}/${totalGames}${activeGames?` · ${activeGames} em andamento`:''}`,generationLabel=Number.isFinite(Number(live.generation))?Number(live.generation)+1:'-',real=config.backend==='travesim',backendLabel=real?'TRAVESIM REAL':'MOCK';metrics.innerHTML=`<span class=metric>Backend<b style="color:${real?'#72e59a':'#ffbd66'}">${backendLabel}</b></span><span class=metric>Estado<b>${live.status||'-'}</b></span><span class=metric>Geração<b>${generationLabel}</b></span><span class=metric>Jogos<b>${gamesLabel}</b></span><span class=metric>Fim da geração<b>${formatDuration(live.generation_eta_seconds)}</b></span><span class=metric>Fim de tudo<b>${formatDuration(live.run_eta_seconds)}</b></span><span class=metric>Melhor<b>${live.best_candidate||'-'}</b></span>`;ranking.innerHTML=rank.map(r=>`<tr><td>${r.rank}</td><td>${r.candidate_id}${r.provisional_matches?' ●':''}</td><td>${r.score.toFixed(1)}</td><td>${r.wins}/${r.draws}/${r.losses}</td><td>${r.goals_for-r.goals_against}</td><td title="Parado ${Number(r.stationary_seconds||0).toFixed(1)}s; preso ${Number(r.stuck_seconds||0).toFixed(1)}s; no gol ${Number(r.goal_seconds||0).toFixed(1)}s">-${Number(r.behavior_penalty||0).toFixed(2)}</td></tr>`).join('');configureReplayMenus(uiState.followLive);if(uiState.match!==loadedMatchId)await loadMatch(uiState.followLive);else if(matchData?.live)await refreshLiveMatch();else if(matchData)drawMatchFrame()}finally{refreshing=false}}
async function refreshDistributed(){const data=await api('/api/distributed'),workers=data.workers||[],counts=data.counts||{};distributedWorkers.innerHTML=workers.length?`<div class="metrics"><span class="metric">Ativos<b>${workers.length}</b></span><span class="metric">Na fila<b>${counts.queued||0}</b></span><span class="metric">Executando<b>${counts.leased||0}</b></span><span class="metric">Concluídos<b>${counts.complete||0}</b></span></div><table><thead><tr><th>Worker</th><th>Slots ativos</th><th>Atualização</th></tr></thead><tbody>${workers.map(w=>`<tr><td>${w.worker_id}</td><td>${w.active}</td><td>${w.updated_at||'-'}</td></tr>`).join('')}</tbody></table>`:'Execução local ou nenhum worker conectado'}
candidates.onchange=()=>{uiState.candidate=candidates.value;uiState.player='';uiState.object='';uiState.source='';configureFieldMenus()};fieldPlayers.onchange=()=>{uiState.player=fieldPlayers.value;drawField()};fieldObjects.onchange=()=>{uiState.object=fieldObjects.value;uiState.source='';configureSourceMenu();drawField()};fieldSources.onchange=()=>{uiState.source=fieldSources.value;drawField()};generations.onchange=()=>{uiState.generation=generations.value;uiState.pair='';uiState.match='';uiState.followLive=false;configureReplayMenus();loadMatch();refresh()};candidatePairs.onchange=()=>{uiState.pair=candidatePairs.value;uiState.match='';uiState.followLive=false;configureReplayMenus();loadMatch()};matches.onchange=()=>{uiState.match=matches.value;uiState.followLive=false;loadMatch()};play.onclick=()=>{uiState.followLive=false;if(!playing&&matchData?.frames?.length&&frameIndex>=matchData.frames.length-1){frameIndex=0;drawMatchFrame()}setPlaying(!playing)};followLive.onclick=()=>{uiState.followLive=true;setPlaying(false);configureReplayMenus(true);loadMatch(true)};firstPeriod.onclick=()=>{uiState.followLive=false;jumpToPeriod(1)};secondPeriod.onclick=()=>{uiState.followLive=false;jumpToPeriod(2)};timeline.oninput=()=>{uiState.followLive=false;frameIndex=Number(timeline.value);drawMatchFrame()};goalEvents.onclick=event=>{const button=event.target.closest('[data-goal-frame]');if(!button)return;uiState.followLive=false;setPlaying(false);frameIndex=Number(button.dataset.goalFrame);drawMatchFrame()};refresh();refreshDistributed();setInterval(refresh,3000);setInterval(refreshDistributed,3000);
</script></body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    run_dir: Path
    cache_lock = threading.Lock()
    live_cache: dict[str, dict[str, object]] = {}
    replay_cache: dict[str, tuple[int, int, dict[str, object]]] = {}
    ball_stall_cache: dict[str, dict[str, object]] = {}

    def send_json(self, payload: object) -> None:
        data = json.dumps(payload).encode()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # Browsers may cancel a polling request while changing replay.
            # The client is already gone; do not emit a noisy server traceback.
            return

    def read_json(self, name: str, default: object) -> object:
        path = self.run_dir / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def match_candidates(self, payload: dict[str, object]) -> list[dict[str, object]]:
        """Return only the two archived candidates that actually played this match."""
        generation = int(payload.get("generation", 0))
        candidates = self.read_json(f"generations/generation-{generation:04d}.json", None)
        if not isinstance(candidates, list):
            candidates = self.read_json("candidates.json", [])
        participants = {payload.get("home"), payload.get("away")}
        baseline = self.read_json("baseline.json", None)
        if isinstance(baseline, dict) and baseline.get("candidate_id") in participants:
            candidates = [*candidates, baseline]
        return [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("candidate_id") in participants
        ]

    def enrich_match(self, payload: dict[str, object]) -> dict[str, object]:
        payload["candidates"] = self.match_candidates(payload)
        return payload

    @staticmethod
    def sample_frames(
        frames: list[dict[str, object]], interval: float = 1.0 / REPLAY_FPS,
    ) -> list[dict[str, object]]:
        """Keep a dashboard-friendly replay while preserving score and period changes."""
        sampled: list[dict[str, object]] = []
        next_sample_time: float | None = None
        previous_state: tuple[object, object, object] | None = None
        for frame in frames:
            now = float(frame.get("time", frame.get("step", 0.0)))
            state = (frame.get("goals_yellow"), frame.get("goals_blue"), frame.get("period"))
            state_changed = previous_state is not None and state != previous_state
            if not sampled or state_changed or (next_sample_time is not None and now + 1e-9 >= next_sample_time):
                sampled.append(frame)
                if next_sample_time is None:
                    next_sample_time = now + interval
                else:
                    while next_sample_time <= now + 1e-9:
                        next_sample_time += interval
            previous_state = state
        if frames and (not sampled or sampled[-1] is not frames[-1]):
            sampled.append(frames[-1])
        return sampled

    def read_completed_match(self, match_id: str) -> dict[str, object] | None:
        path = self.run_dir / "matches" / f"{match_id}.json"
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        with self.cache_lock:
            cached = self.replay_cache.get(str(path))
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2]
        payload = self.read_json(f"matches/{match_id}.json", None)
        if not isinstance(payload, dict):
            return None
        frames = payload.get("frames", [])
        if isinstance(frames, list):
            payload["frames"] = self.sample_frames(frames)
        self.enrich_match(payload)
        with self.cache_lock:
            self.replay_cache[str(path)] = (stat.st_mtime_ns, stat.st_size, payload)
        return payload

    def read_match_summary(self, path: Path) -> dict[str, object]:
        """Read a compact sidecar, or extract scalar metadata without loading frames."""
        sidecar = path.with_suffix(".summary.json")
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            with path.open("rb") as stream:
                head = stream.read(65536)
                stream.seek(max(0, path.stat().st_size - 65536))
                text = (head + stream.read()).decode("utf-8", errors="ignore")
        except OSError:
            return {}
        result: dict[str, object] = {}
        for key in ("match_id", "home", "away", "backend"):
            match = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
            if match:
                result[key] = match.group(1)
        for key in ("generation", "home_goals", "away_goals", "repetition", "seed"):
            match = re.search(rf'"{key}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
            if match:
                result[key] = int(float(match.group(1)))
        return result

    def read_live_match_summary(self, path: Path) -> dict[str, object]:
        """Read only metadata and the most recent complete telemetry line."""
        match_id = path.name.removesuffix(".live.jsonl")
        metadata = self.read_json(f"matches/{match_id}.live.meta.json", {})
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                stream.seek(max(0, size - 65536))
                lines = stream.read().splitlines()
        except OSError:
            return metadata
        latest: dict[str, object] = {}
        for raw_line in reversed(lines):
            try:
                candidate = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(candidate, dict) and candidate.get("type") == "frame":
                latest = candidate
                break
        return {
            **metadata,
            "home_goals": int(latest.get("goals_yellow", 0)),
            "away_goals": int(latest.get("goals_blue", 0)),
            "provisional": not bool(latest.get("finished", False)),
            "telemetry_time": float(latest.get("time", 0.0)),
            "has_frames": bool(latest),
        }

    @staticmethod
    def ball_position(frame: dict[str, object]) -> tuple[float, float] | None:
        ball = frame.get("ball")
        if isinstance(ball, (list, tuple)) and len(ball) >= 2:
            return float(ball[0]), float(ball[1])
        if isinstance(ball, dict) and "x" in ball and "y" in ball:
            return float(ball["x"]), float(ball["y"])
        return None

    @classmethod
    def update_ball_stall_state(
        cls, state: dict[str, object], frame: dict[str, object], speed_limit: float,
    ) -> None:
        position = cls.ball_position(frame)
        timestamp = float(frame.get("time", frame.get("step", 0.0)))
        previous_position = state.get("position")
        previous_time = state.get("time")
        if position is None or previous_position is None or previous_time is None:
            state["position"], state["time"] = position, timestamp
            return
        dt = timestamp - float(previous_time)
        if dt <= 0.0:
            state["position"], state["time"] = position, timestamp
            return
        px, py = previous_position
        speed = math.hypot(position[0] - float(px), position[1] - float(py)) / dt
        current = float(state.get("current", 0.0)) + dt if speed <= speed_limit else 0.0
        if speed <= speed_limit:
            state["total"] = float(state.get("total", 0.0)) + dt
        state["current"] = current
        state["maximum"] = max(float(state.get("maximum", 0.0)), current)
        state["position"], state["time"] = position, timestamp

    def ball_stall_diagnostics(self, path: Path, live: bool) -> dict[str, object]:
        """Incrementally identify a sustained stationary ball in a replay."""
        config = self.read_json("config.json", {})
        speed_limit = float(config.get("ball_stall_speed", 0.02)) if isinstance(config, dict) else 0.02
        duration_limit = float(config.get("ball_stall_duration", 5.0)) if isinstance(config, dict) else 5.0
        key = f"{'live' if live else 'complete'}:{path}"
        try:
            stat = path.stat()
        except OSError:
            return {"ball_stalled": False, "ball_stall_seconds": 0.0, "ball_stationary_seconds": 0.0}
        with self.cache_lock:
            state = self.ball_stall_cache.get(key)
            if state is None or stat.st_size < int(state.get("offset", 0)):
                state = {"offset": 0, "position": None, "time": None, "current": 0.0, "maximum": 0.0, "total": 0.0}
            if not live and state.get("mtime_ns") == stat.st_mtime_ns and state.get("complete"):
                maximum = float(state.get("maximum", 0.0))
                return {"ball_stalled": maximum >= duration_limit, "ball_stall_seconds": maximum, "ball_stationary_seconds": float(state.get("total", 0.0))}
            try:
                if live:
                    with path.open("rb") as stream:
                        stream.seek(int(state.get("offset", 0)))
                        chunk = stream.read()
                        state["offset"] = int(state.get("offset", 0)) + len(chunk)
                    for raw_line in chunk.splitlines():
                        try:
                            frame = json.loads(raw_line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if isinstance(frame, dict) and frame.get("type") == "frame":
                            self.update_ball_stall_state(state, frame, speed_limit)
                else:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    for frame in payload.get("frames", []) if isinstance(payload, dict) else []:
                        if isinstance(frame, dict):
                            self.update_ball_stall_state(state, frame, speed_limit)
                    state["offset"] = stat.st_size
                    state["complete"] = True
                state["mtime_ns"] = stat.st_mtime_ns
                self.ball_stall_cache[key] = state
            except (OSError, json.JSONDecodeError):
                pass
        maximum = float(state.get("maximum", 0.0))
        return {"ball_stalled": maximum >= duration_limit, "ball_stall_seconds": maximum, "ball_stationary_seconds": float(state.get("total", 0.0))}

    def completed_recording_valid(self, path: Path, summary: dict[str, object]) -> bool:
        """Reject empty/crashed replays without parsing their complete frame arrays."""
        if "frame_count" in summary:
            if int(summary.get("frame_count", 0)) <= 0:
                return False
            return summary.get("backend") != "travesim" or bool(summary.get("finished"))
        try:
            with path.open("rb") as stream:
                head = stream.read(65536)
                stream.seek(max(0, path.stat().st_size - 65536))
                edge_text = (head + stream.read()).decode("utf-8", errors="ignore")
        except OSError:
            return False
        has_frames = bool(re.search(r'"frames"\s*:\s*\[\s*\{', edge_text))
        return has_frames and (summary.get("backend") != "travesim" or '"finished": true' in edge_text)

    def read_match_catalog(self) -> list[dict[str, object]]:
        """Build compact generation/pair/game metadata without loading replay frames."""
        matches_dir = self.run_dir / "matches"
        entries: dict[str, dict[str, object]] = {}
        for path in matches_dir.glob("*.json"):
            if ".live." in path.name or path.name.endswith(".summary.json"):
                continue
            summary = self.read_match_summary(path)
            if not self.completed_recording_valid(path, summary):
                continue
            match_id = str(summary.get("match_id") or path.stem)
            entries[match_id] = {
                **summary, **self.ball_stall_diagnostics(path, False),
                "match_id": match_id, "live": False,
            }
        for path in matches_dir.glob("*.live.jsonl"):
            match_id = path.name.removesuffix(".live.jsonl")
            if match_id in entries:
                continue
            summary = self.read_live_match_summary(path)
            if not summary.get("has_frames"):
                continue
            entries[match_id] = {
                **summary, **self.ball_stall_diagnostics(path, True),
                "match_id": match_id, "live": True,
            }
        return sorted(
            entries.values(),
            key=lambda item: (int(item.get("generation", 0)), str(item.get("match_id", ""))),
        )

    def read_live_status(self) -> dict[str, object]:
        payload = self.read_json("live.json", {})
        if not isinstance(payload, dict):
            return {}
        if payload.get("status") != "running":
            return payload
        config = self.read_json("config.json", {})
        if not isinstance(config, dict):
            return payload
        matches_dir = self.run_dir / "matches"
        generation = int(payload.get("generation", 0))
        main_pattern = re.compile(r"^g(\d{4})-m(\d+)-r(\d+)$")
        completed_main: list[tuple[int, int, int, Path]] = []
        for path in matches_dir.glob("*.json"):
            if ".live." in path.name or ".summary." in path.name:
                continue
            match = main_pattern.match(path.stem)
            if match:
                completed_main.append(tuple(map(int, match.groups())) + (path,))
        completed_overall = len(completed_main)
        current_completed = sum(item_generation == generation for item_generation, _, _, _ in completed_main)
        competitors = max(2, int(config.get("competitors", 0)))
        matches_per_pair = max(1, int(config.get("matches_per_pair", 1)))
        generations = max(1, int(config.get("generations", 1)))
        workers = max(1, int(config.get("workers", 1)))
        per_generation = competitors * (competitors - 1) // 2 * matches_per_pair
        planned = per_generation * generations
        match_duration = max(1.0, float(config.get("match_duration", 600.0)))
        raw_schedule = config.get("match_duration_schedule_seconds", [])
        schedule = [max(1.0, float(item)) for item in raw_schedule] if isinstance(raw_schedule, list) else []
        def scheduled_duration(index: int) -> float:
            return schedule[min(index, len(schedule) - 1)] if schedule else match_duration
        def task_global_index(item_generation: int, pair: int, repetition: int) -> int:
            return item_generation * per_generation + pair * matches_per_pair + repetition
        completed_indices = {
            task_global_index(item_generation, pair, repetition)
            for item_generation, pair, repetition, _ in completed_main
        }
        total_simulated = sum(scheduled_duration(index) for index in range(planned))
        completed_simulated = sum(scheduled_duration(index) for index in completed_indices)
        generation_indices = range(generation * per_generation, min(planned, (generation + 1) * per_generation))
        generation_total_simulated = sum(scheduled_duration(index) for index in generation_indices)
        generation_completed_simulated = sum(
            scheduled_duration(index) for index in generation_indices if index in completed_indices
        )
        active_progress: list[float] = []
        simulation_speeds: list[float] = []
        now = time.time()
        for path in matches_dir.glob(f"g{generation:04d}-m*.live.jsonl"):
            summary = self.read_live_match_summary(path)
            if not summary.get("has_frames"):
                continue
            metadata = matches_dir / f"{path.name.removesuffix('.live.jsonl')}.live.meta.json"
            metadata_payload = self.read_json(str(metadata.relative_to(self.run_dir)), {})
            active_duration = max(1.0, float(metadata_payload.get("match_duration", match_duration))) if isinstance(metadata_payload, dict) else match_duration
            simulated = max(0.0, min(active_duration, float(summary.get("telemetry_time", 0.0))))
            active_progress.append(simulated)
            try:
                wall_elapsed = max(0.1, now - metadata.stat().st_mtime)
            except OSError:
                continue
            if simulated > 0:
                simulation_speeds.append(simulated / wall_elapsed)
        simulation_speed = statistics.median(simulation_speeds) if simulation_speeds else 1.0
        parallelism = max(1, min(workers, per_generation - current_completed))
        generation_simulated_remaining = max(
            0.0,
            generation_total_simulated - generation_completed_simulated - sum(active_progress),
        )
        overall_simulated_remaining = max(
            0.0,
            total_simulated - completed_simulated - sum(active_progress),
        )
        payload["completed_matches_overall"] = completed_overall
        payload["planned_matches_overall"] = planned
        payload["completed_matches"] = current_completed
        payload["total_matches"] = per_generation
        payload["active_matches"] = len(active_progress)
        payload["generation_progress"] = (
            per_generation * (generation_completed_simulated + sum(active_progress)) / generation_total_simulated
            if generation_total_simulated else float(current_completed)
        )
        payload["generation_eta_seconds"] = generation_simulated_remaining / simulation_speed / parallelism
        payload["run_eta_seconds"] = overall_simulated_remaining / simulation_speed / parallelism
        payload["eta_source"] = (
            "curriculum_weighted_live_telemetry" if schedule else "live_telemetry_progress"
        )
        return payload

    def read_live_match(self, match_id: str) -> dict[str, object]:
        metadata = self.read_json(f"matches/{match_id}.live.meta.json", {})
        if not isinstance(metadata, dict):
            metadata = {}
        path = self.run_dir / "matches" / f"{match_id}.live.jsonl"
        cache_key = str(path)
        with self.cache_lock:
            cached = self.live_cache.setdefault(cache_key, {"offset": 0, "frames": [], "size": 0})
        try:
            size = path.stat().st_size
            if size < int(cached["offset"]):
                cached = {"offset": 0, "frames": [], "size": 0}
            new_frames: list[dict[str, object]] = []
            with path.open("rb") as stream:
                stream.seek(int(cached["offset"]))
                chunk = stream.read()
                cached["offset"] = int(cached["offset"]) + len(chunk)
                for raw_line in chunk.splitlines():
                    try:
                        frame = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if frame.get("type") == "frame":
                        duration = float(metadata.get("match_duration", 600.0))
                        frame["time_remaining"] = max(0.0, duration - float(frame.get("time", 0.0)))
                        new_frames.append(frame)
            cached["frames"] = self.sample_frames([*cached["frames"], *new_frames])
            cached["size"] = size
            with self.cache_lock:
                self.live_cache[cache_key] = cached
        except FileNotFoundError:
            pass
        return self.enrich_match({**metadata, "live": True, "frames": cached["frames"]})

    def read_ranking(self, requested_generation: int | None = None) -> list[dict[str, object]]:
        live = self.read_json("live.json", {})
        current_generation = int(live.get("generation", 0)) if isinstance(live, dict) else 0
        generation = current_generation if requested_generation is None else requested_generation
        historical = self.read_json(f"generations/generation-{generation:04d}.json", None)
        if isinstance(historical, list):
            return historical
        if (not isinstance(live, dict) or live.get("status") != "running") and generation == current_generation:
            ranking = self.read_json("ranking.json", [])
            return ranking if isinstance(ranking, list) else []
        candidates = self.read_json("candidates.json", [])
        if not isinstance(candidates, list):
            return []
        candidates = [candidate for candidate in candidates if int(candidate.get("generation", generation)) == generation]
        config = self.read_json("config.json", {})
        draw_penalty = float(config.get("draw_penalty", 1.0)) if isinstance(config, dict) else 1.0
        rows = {
            str(candidate["candidate_id"]): {
                **candidate,
                "score": 0.0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "goals_for": 0,
                "goals_against": 0,
                "provisional_matches": 0,
            }
            for candidate in candidates
            if isinstance(candidate, dict) and "candidate_id" in candidate
        }
        results: list[dict[str, object]] = []
        completed_ids: set[str] = set()
        for path in (self.run_dir / "matches").glob("*.json"):
            if ".live." in path.name or path.name.endswith(".summary.json"):
                continue
            result = self.read_match_summary(path)
            if int(result.get("generation", -1)) != generation:
                continue
            completed_ids.add(path.stem)
            results.append(result)
        for path in (self.run_dir / "matches").glob("*.live.jsonl"):
            if path.name.removesuffix(".live.jsonl") in completed_ids:
                continue
            result = self.read_live_match_summary(path)
            if result.get("has_frames") and int(result.get("generation", -1)) == generation:
                results.append(result)
        for result in results:
            for own, opponent, own_goals, opponent_goals in (
                (result.get("home"), result.get("away"), result.get("home_goals", 0), result.get("away_goals", 0)),
                (result.get("away"), result.get("home"), result.get("away_goals", 0), result.get("home_goals", 0)),
            ):
                row = rows.get(str(own))
                if row is None or str(opponent) not in rows:
                    continue
                own_goals, opponent_goals = int(own_goals), int(opponent_goals)
                row["goals_for"] += own_goals
                row["goals_against"] += opponent_goals
                row["score"] += own_goals - opponent_goals
                if result.get("provisional"):
                    row["provisional_matches"] += 1
                if own_goals > opponent_goals:
                    row["wins"] += 1
                    row["score"] += 3.0
                elif own_goals < opponent_goals:
                    row["losses"] += 1
                    row["score"] -= 3.0
                else:
                    row["draws"] += 1
                    row["score"] -= draw_penalty
        ordered = sorted(
            rows.values(),
            key=lambda row: (row["score"], row["wins"], row["goals_for"] - row["goals_against"], -row["draws"]),
            reverse=True,
        )
        return [{"rank": index, **row} for index, row in enumerate(ordered, start=1)]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            data = HTML.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/live":
            self.send_json(self.read_live_status())
        elif parsed.path == "/api/ranking":
            raw_generation = parse_qs(parsed.query).get("generation", [None])[0]
            generation = int(raw_generation) if raw_generation is not None else None
            self.send_json(self.read_ranking(generation))
        elif parsed.path == "/api/candidates":
            self.send_json(self.read_json("candidates.json", []))
        elif parsed.path == "/api/config":
            self.send_json(self.read_json("config.json", {}))
        elif parsed.path == "/api/distributed":
            self.send_json(self.read_json("distributed.json", {"counts": {}, "workers": []}))
        elif parsed.path == "/api/matches":
            matches_dir = self.run_dir / "matches"
            completed = {
                path.stem for path in matches_dir.glob("*.json")
                if ".live." not in path.name and not path.name.endswith(".summary.json")
            }
            live = {path.name.removesuffix(".live.jsonl") for path in matches_dir.glob("*.live.jsonl")}
            self.send_json(sorted(completed | live))
        elif parsed.path == "/api/match-catalog":
            self.send_json(self.read_match_catalog())
        elif parsed.path == "/api/match":
            match_id = parse_qs(parsed.query).get("id", [""])[0]
            safe_id = Path(match_id).name
            completed = self.read_completed_match(safe_id)
            self.send_json(completed if completed is not None else self.read_live_match(safe_id))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return


class DashboardServer(ThreadingHTTPServer):
    """HTTP server that avoids a blocking reverse-DNS lookup on startup."""

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def main() -> None:
    parser = argparse.ArgumentParser(description="VSSS Coach tournament dashboard")
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not args.run.is_dir():
        raise SystemExit(f"Run directory not found: {args.run}")
    handler = type("RunDashboardHandler", (DashboardHandler,), {"run_dir": args.run.resolve()})
    server = DashboardServer((args.host, args.port), handler)
    print(f"Dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
