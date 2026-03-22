import { useState, useEffect, useRef, useCallback } from "react";
import { AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

// ─────────────────────────────────────────────────────────────────────────────
// MOCK — matches exact shape of audited bot.py get_state_json()
// ─────────────────────────────────────────────────────────────────────────────
const makeMock = (tick) => {
  const qqq   = 479 + Math.sin(tick * 0.09) * 6;
  const qVwap = 479 + Math.sin(tick * 0.07) * 4;
  const tqqq  = 60.5 + Math.sin(tick * 0.09) * 2.5;
  const tVwap = 60.5 + Math.sin(tick * 0.07) * 1.8;
  const qA = qqq > qVwap, tA = tqqq > tVwap;
  return {
    account: {
      equity: 100000 + tick * 14.8, buying_power: 96000, cash: 48200,
      pnl_today: tick * 14.8, total_commission: tick * 0.31,
      total_trades: Math.floor(tick / 4),
    },
    symbols: {
      QQQ: {
        symbol:"QQQ", last_price:+(qqq+(Math.random()-.5)*.3).toFixed(2),
        vwap:+qVwap.toFixed(2), side:qA?"long":"short",
        position:qA?208:-208, entry_price:+(qqq-.6).toFixed(2),
        unrealized_pnl:+(Math.random()*300-80).toFixed(2),
        realized_pnl:+(tick*9.1).toFixed(2),
        commission_paid:+(tick*.18).toFixed(2),
        trade_count:Math.floor(tick/4), win_count:Math.floor(tick/24),
        loss_count:Math.max(0,Math.floor(tick/7)-Math.floor(tick/24)), trades:[],
      },
      TQQQ: {
        symbol:"TQQQ", last_price:+(tqqq+(Math.random()-.5)*.2).toFixed(2),
        vwap:+tVwap.toFixed(2), side:tA?"long":"short",
        position:tA?820:-820, entry_price:+(tqqq-.35).toFixed(2),
        unrealized_pnl:+(Math.random()*250-60).toFixed(2),
        realized_pnl:+(tick*5.7).toFixed(2),
        commission_paid:+(tick*.13).toFixed(2),
        trade_count:Math.floor(tick/4), win_count:Math.floor(tick/22),
        loss_count:Math.max(0,Math.floor(tick/6)-Math.floor(tick/22)), trades:[],
      },
    },
    config: {
      symbols:["QQQ","TQQQ"], session:"09:30–16:00 ET",
      first_entry:"09:31:00", skip_midday:false, commission:0.0005,
    },
    timestamp: new Date().toISOString(),
  };
};

const DEMO_TRADES = [
  {symbol:"QQQ",  action:"ENTER LONG",   price:479.34,qty:208,vwap:478.91,time:new Date(Date.now()-7200000).toISOString()},
  {symbol:"TQQQ", action:"ENTER LONG",   price:60.12, qty:820,vwap:59.88, time:new Date(Date.now()-7200000).toISOString()},
  {symbol:"QQQ",  action:"FLIP → SHORT", price:477.88,qty:209,vwap:478.10,realized_pnl:-301.28,time:new Date(Date.now()-5400000).toISOString()},
  {symbol:"TQQQ", action:"FLIP → SHORT", price:59.44, qty:825,vwap:59.60, realized_pnl:-142.08,time:new Date(Date.now()-5400000).toISOString()},
  {symbol:"QQQ",  action:"FLIP → LONG",  price:480.22,qty:207,vwap:479.80,realized_pnl:196.04, time:new Date(Date.now()-3600000).toISOString()},
  {symbol:"TQQQ", action:"FLIP → LONG",  price:60.55, qty:818,vwap:60.30, realized_pnl:93.51,  time:new Date(Date.now()-3600000).toISOString()},
  {symbol:"QQQ",  action:"FLIP → SHORT", price:478.90,qty:208,vwap:479.10,realized_pnl:272.16, time:new Date(Date.now()-1800000).toISOString()},
  {symbol:"TQQQ", action:"FLIP → SHORT", price:59.92, qty:821,vwap:60.05, realized_pnl:51.67,  time:new Date(Date.now()-1800000).toISOString()},
];

// ─────────────────────────────────────────────────────────────────────────────
// HOOK
// ─────────────────────────────────────────────────────────────────────────────
function useBot() {
  const [state,setState]         = useState(null);
  const [chart,setChart]         = useState({QQQ:[],TQQQ:[]});
  const [connected,setConnected] = useState(false);
  const [trades,setTrades]       = useState(DEMO_TRADES);
  const tick = useRef(0);

  const push = useCallback((s) => {
    setState(s);
    const ts = new Date().toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
    setChart(prev => {
      const n={...prev};
      for(const sym of ["QQQ","TQQQ"]){
        const d=s.symbols[sym];
        n[sym]=[...(prev[sym]||[]),{t:ts,price:d.last_price,vwap:d.vwap}].slice(-80);
      }
      return n;
    });
  },[]);

  useEffect(()=>{
    let ws;
    try {
      ws=new WebSocket("ws://localhost:5050/ws");
      ws.onopen=()=>setConnected(true);
      ws.onclose=()=>setConnected(false);
      ws.onerror=()=>{};
      ws.onmessage=(e)=>{
        try{
          const d=JSON.parse(e.data); push(d);
          const all=[];
          for(const sym of ["QQQ","TQQQ"])
            (d.symbols?.[sym]?.trades||[]).forEach(t=>all.push({...t,symbol:sym}));
          if(all.length) setTrades(all.sort((a,b)=>new Date(b.time)-new Date(a.time)));
        }catch(_){}
      };
    }catch(_){}
    const iv=setInterval(()=>{tick.current++;push(makeMock(tick.current));setConnected(true);},1500);
    return()=>{clearInterval(iv);ws?.close();};
  },[push]);

  return {state,chart,connected,trades};
}

// ─────────────────────────────────────────────────────────────────────────────
// UTILS & THEME
// ─────────────────────────────────────────────────────────────────────────────
const G="#00ff88", R="#ff4466", Y="#f7c948", P="#8b8bff", T="#e6edf3", D="#8b949e";
const f2=(n)=>n==null?"—":(+n).toFixed(2);
const fM=(n,s=false)=>{
  if(n==null)return"—";
  const a=Math.abs(+n).toLocaleString("en-US",{minimumFractionDigits:2});
  return s?(n<0?"-$":"+$")+a:"$"+a;
};
const fN=(n)=>n==null?"—":(+n).toLocaleString();
const MONO={fontFamily:"'JetBrains Mono',monospace"};
const CARD={background:"#0d1117",border:"1px solid #21262d",borderRadius:8};
const TTS={background:"#0d1117",border:"1px solid #30363d",borderRadius:6,color:T,fontSize:11,...MONO};
const ACT={
  "ENTER LONG":G,"ENTER SHORT":R,
  "FLIP → LONG":Y,"FLIP → SHORT":Y,
  "EOD CLOSE":P,
};

// ─────────────────────────────────────────────────────────────────────────────
// COMPONENTS
// ─────────────────────────────────────────────────────────────────────────────
function LiveDot({on}){
  return(
    <span style={{display:"inline-flex",alignItems:"center",gap:6}}>
      <span style={{width:8,height:8,borderRadius:"50%",display:"inline-block",
        background:on?G:R,boxShadow:`0 0 8px ${on?G:R}`,
        animation:on?"pulse 2s infinite":"none"}}/>
      <span style={{fontSize:10,letterSpacing:2,color:on?G:R,...MONO}}>
        {on?"LIVE":"OFFLINE"}
      </span>
    </span>
  );
}

function Pill({side}){
  if(!side)return<span style={{color:"#555",fontSize:11,...MONO}}>FLAT</span>;
  const c=side==="long"?G:R;
  return<span style={{background:`${c}22`,color:c,border:`1px solid ${c}44`,
    padding:"2px 10px",borderRadius:3,fontSize:11,fontWeight:700,letterSpacing:2,...MONO}}>
    {side.toUpperCase()}</span>;
}

function PnLVal({v,size=14}){
  return<span style={{color:(+v)>=0?G:R,fontWeight:700,fontSize:size,...MONO}}>{fM(v,true)}</span>;
}

function Box({label,children}){
  return<div style={{background:"#161b22",borderRadius:6,padding:"10px 14px"}}>
    <div style={{fontSize:9,color:D,letterSpacing:2,marginBottom:5}}>{label}</div>
    {children}
  </div>;
}

function Ring({wins,total,sz=54}){
  const p=total>0?wins/total:0,r=20,c=2*Math.PI*r;
  return<div style={{position:"relative",width:sz,height:sz}}>
    <svg width={sz} height={sz} style={{transform:"rotate(-90deg)"}}>
      <circle cx={sz/2} cy={sz/2} r={r} fill="none" stroke="#21262d" strokeWidth={4}/>
      <circle cx={sz/2} cy={sz/2} r={r} fill="none" stroke={G} strokeWidth={4}
        strokeDasharray={`${c*p} ${c}`} strokeLinecap="round"/>
    </svg>
    <div style={{position:"absolute",inset:0,display:"flex",alignItems:"center",
      justifyContent:"center",fontSize:11,fontWeight:700,color:G,...MONO}}>
      {(p*100).toFixed(0)}%
    </div>
  </div>;
}

function MiniChart({data,sym}){
  if(!data||data.length<3)return<div style={{height:100,display:"flex",alignItems:"center",
    justifyContent:"center",color:"#444",fontSize:11}}>Collecting data…</div>;
  return<ResponsiveContainer width="100%" height={108}>
    <AreaChart data={data} margin={{top:4,right:4,left:0,bottom:0}}>
      <defs>
        <linearGradient id={`g${sym}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="5%"  stopColor={G} stopOpacity={0.15}/>
          <stop offset="95%" stopColor={G} stopOpacity={0}/>
        </linearGradient>
      </defs>
      <XAxis dataKey="t" hide/><YAxis domain={["auto","auto"]} hide/>
      <Tooltip contentStyle={TTS} formatter={(v)=>[`$${v}`]}/>
      <Area type="monotone" dataKey="price" stroke={G} strokeWidth={1.5}
        fill={`url(#g${sym})`} dot={false}/>
      <Line type="monotone" dataKey="vwap" stroke={Y} strokeWidth={1.2}
        dot={false} strokeDasharray="4 2"/>
    </AreaChart>
  </ResponsiveContainer>;
}

function VWAPBar({price,vwap}){
  if(!price||!vwap)return null;
  const diff=price-vwap, pct=Math.min(Math.abs(diff)/vwap*100*25,100), up=diff>0, col=up?G:R;
  return<div style={{marginTop:8}}>
    <div style={{display:"flex",justifyContent:"space-between",fontSize:10,color:D,marginBottom:4}}>
      <span>PRICE vs VWAP</span>
      <span style={{color:col,...MONO}}>{up?"+":""}{f2(diff)} ({up?"+":""}{f2(diff/vwap*100)}%)</span>
    </div>
    <div style={{height:3,background:"#21262d",borderRadius:2}}>
      <div style={{height:"100%",width:`${pct}%`,background:col,
        marginLeft:up?"50%":`${50-pct/2}%`,borderRadius:2,transition:"all .6s ease"}}/>
    </div>
  </div>;
}

function SymbolCard({sym,s,chartData}){
  if(!s)return null;
  const ac=s.side==="long"?G:s.side==="short"?R:"#555";
  return<div style={{flex:1,minWidth:0}}>
    <div style={{...CARD,borderTop:`2px solid ${ac}`,padding:"20px 22px"}}>
      {/* Header */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:16}}>
        <div>
          <div style={{fontSize:22,fontWeight:900,color:T,letterSpacing:1,...MONO}}>{sym}</div>
          <div style={{fontSize:10,color:D,letterSpacing:2,marginTop:2}}>
            {sym==="QQQ"?"INVESCO QQQ TRUST":"PROSHARES ULTRA QQQ  3×"}
          </div>
        </div>
        <Pill side={s.side}/>
      </div>

      {/* Prices */}
      <div style={{display:"flex",gap:28,marginBottom:14}}>
        <div>
          <div style={{fontSize:10,color:D,letterSpacing:1,marginBottom:3}}>PRICE</div>
          <div style={{fontSize:28,fontWeight:700,color:T,...MONO}}>${f2(s.last_price)}</div>
        </div>
        <div>
          <div style={{fontSize:10,color:"#f7c94888",letterSpacing:1,marginBottom:3}}>VWAP</div>
          <div style={{fontSize:28,fontWeight:700,color:Y,...MONO}}>${f2(s.vwap)}</div>
        </div>
      </div>

      <MiniChart data={chartData} sym={sym}/>
      <VWAPBar price={s.last_price} vwap={s.vwap}/>

      {/* Stats */}
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,marginTop:14}}>
        <Box label="POSITION">
          <div style={{fontSize:14,fontWeight:700,color:T,...MONO}}>
            {s.position>0?`+${fN(s.position)}`:fN(s.position)} sh
          </div>
        </Box>
        <Box label="ENTRY">
          <div style={{fontSize:14,fontWeight:700,color:T,...MONO}}>
            {s.entry_price?`$${f2(s.entry_price)}`:"—"}
          </div>
        </Box>
        <Box label="UNREALIZED"><PnLVal v={s.unrealized_pnl}/></Box>
        <Box label="REALIZED">  <PnLVal v={s.realized_pnl}/></Box>
      </div>

      {/* Trade stats */}
      <div style={{display:"flex",gap:10,marginTop:10,alignItems:"center"}}>
        <Ring wins={s.win_count||0} total={s.trade_count||0}/>
        <div style={{flex:1,display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
          <Box label="TRADES">
            <div style={{fontSize:14,fontWeight:700,color:T,...MONO}}>{s.trade_count||0}</div>
          </Box>
          <Box label="COMMISSION">
            <div style={{fontSize:13,fontWeight:700,color:R,...MONO}}>-${f2(s.commission_paid||0)}</div>
          </Box>
        </div>
      </div>
    </div>
  </div>;
}

function AccountBar({a}){
  if(!a)return null;
  const pp=a.pnl_today>=0;
  return<div style={{display:"flex",...CARD,overflow:"hidden",marginBottom:20}}>
    {[
      {l:"EQUITY",          v:fM(a.equity),              c:T},
      {l:"BUYING POWER",    v:fM(a.buying_power),        c:T},
      {l:"P&L TODAY",       v:fM(a.pnl_today,true),      c:pp?G:R},
      {l:"COMMISSION PAID", v:`-$${f2(a.total_commission)}`, c:R},
      {l:"TOTAL TRADES",    v:fN(a.total_trades),        c:T},
    ].map(({l,v,c},i,arr)=>(
      <div key={i} style={{flex:1,padding:"16px 18px",
        borderRight:i<arr.length-1?"1px solid #21262d":"none"}}>
        <div style={{fontSize:9,color:D,letterSpacing:3,marginBottom:6}}>{l}</div>
        <div style={{fontSize:17,fontWeight:700,color:c,...MONO}}>{v}</div>
      </div>
    ))}
  </div>;
}

function DualChart({chart}){
  const q=chart.QQQ||[], t=chart.TQQQ||[];
  const len=Math.max(q.length,t.length);
  const data=Array.from({length:len},(_,i)=>({
    t:(q[i]||t[i])?.t||i,
    QQQ:q[i]?.price, qVwap:q[i]?.vwap,
    TQQQ:t[i]?.price, tVwap:t[i]?.vwap,
  }));
  return<div style={{...CARD,padding:"16px 20px",marginBottom:16}}>
    <div style={{fontSize:10,color:D,letterSpacing:3,marginBottom:10}}>LIVE PRICE vs VWAP (solid=price, dashed=VWAP)</div>
    <ResponsiveContainer width="100%" height={120}>
      <LineChart data={data} margin={{top:4,right:4,left:0,bottom:0}}>
        <XAxis dataKey="t" hide/><YAxis yAxisId="q" domain={["auto","auto"]} hide/>
        <YAxis yAxisId="t" domain={["auto","auto"]} hide orientation="right"/>
        <Tooltip contentStyle={TTS} formatter={(v,n)=>[`$${f2(v)}`,n]}/>
        <Line yAxisId="q" type="monotone" dataKey="QQQ"   stroke={G} strokeWidth={1.5} dot={false} name="QQQ"/>
        <Line yAxisId="q" type="monotone" dataKey="qVwap" stroke={G} strokeWidth={1}   dot={false} strokeDasharray="4 2" name="VWAP·QQQ"/>
        <Line yAxisId="t" type="monotone" dataKey="TQQQ"  stroke={P} strokeWidth={1.5} dot={false} name="TQQQ"/>
        <Line yAxisId="t" type="monotone" dataKey="tVwap" stroke={P} strokeWidth={1}   dot={false} strokeDasharray="4 2" name="VWAP·TQQQ"/>
      </LineChart>
    </ResponsiveContainer>
    <div style={{display:"flex",gap:20,marginTop:6,fontSize:10}}>
      {[[G,"── QQQ"],[G,"- - VWAP"],[P,"── TQQQ"],[P,"- - VWAP"]].map(([c,l],i)=>(
        <span key={i} style={{color:c,opacity:l.includes("-")?0.65:1}}>{l}</span>
      ))}
    </div>
  </div>;
}

function Rules({cfg}){
  const rules=[
    ["VWAP",       "Σ(HLC3×Vol) / Σ(Vol) — resets each day at 09:30 ET"],
    ["LONG",       "1-min candle closes ABOVE VWAP → BUY"],
    ["SHORT",      "1-min candle closes BELOW VWAP → SELL SHORT"],
    ["FLIP",       "Candle close crosses VWAP → flatten + reverse"],
    ["INTRACANDLE","Mid-candle VWAP touches are IGNORED"],
    ["ENTRY",      `First signal after ${cfg?.first_entry||"09:31"} ET`],
    ["EOD EXIT",   "All positions force-closed at 16:00 ET"],
    ["SIZE",       "100% available equity — no fixed lots"],
    ["COMMISSION", `$${cfg?.commission||0.0005}/share (tracked)`],
  ];
  return<div style={{...CARD,padding:"14px 20px",marginBottom:16}}>
    <div style={{fontSize:10,color:D,letterSpacing:3,marginBottom:12}}>
      STRATEGY RULES — ZARATTINI &amp; AZIZ 2023 · SSRN-4631351
    </div>
    <div style={{display:"flex",flexWrap:"wrap",gap:"8px 28px"}}>
      {rules.map(([k,v])=>(
        <div key={k} style={{minWidth:240}}>
          <span style={{color:G,fontSize:10,fontWeight:700,...MONO,marginRight:8}}>{k}</span>
          <span style={{color:D,fontSize:11}}>{v}</span>
        </div>
      ))}
    </div>
  </div>;
}

function TradeLog({trades}){
  const sorted=[...trades].sort((a,b)=>new Date(b.time)-new Date(a.time)).slice(0,40);
  return<div style={{...CARD,padding:"20px 22px"}}>
    <div style={{fontSize:10,color:D,letterSpacing:3,marginBottom:14}}>
      TRADE LOG — {sorted.length} entries
    </div>
    <div style={{overflowY:"auto",maxHeight:220}}>
      {sorted.length===0&&<div style={{color:"#444",fontSize:12,textAlign:"center",padding:20}}>
        No trades — waiting for 09:31 ET
      </div>}
      {sorted.map((t,i)=>{
        const col=ACT[t.action]||D;
        const ts=new Date(t.time).toLocaleTimeString("en-US",
          {hour:"2-digit",minute:"2-digit",second:"2-digit"});
        return<div key={i} style={{display:"flex",alignItems:"center",gap:10,
          padding:"7px 0",borderBottom:"1px solid #161b22",fontSize:12,...MONO}}>
          <span style={{color:"#444",fontSize:10,minWidth:70}}>{ts}</span>
          <span style={{background:`${col}22`,color:col,border:`1px solid ${col}33`,
            padding:"1px 8px",borderRadius:3,fontSize:10,letterSpacing:1,
            minWidth:52,textAlign:"center"}}>{t.symbol}</span>
          <span style={{color:col,flex:1,fontSize:11}}>{t.action}</span>
          <span style={{color:D}}>${f2(t.price)}</span>
          <span style={{color:"#555"}}>×{fN(t.qty)}</span>
          {t.realized_pnl!=null&&<span style={{
            color:(+t.realized_pnl)>=0?G:R,minWidth:80,textAlign:"right",fontWeight:700,
          }}>{fM(t.realized_pnl,true)}</span>}
        </div>;
      })}
    </div>
  </div>;
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────────────────────
export default function App(){
  const {state,chart,connected,trades}=useBot();
  const [now,setNow]=useState(new Date());
  useEffect(()=>{const t=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(t);},[]);

  const etTime=now.toLocaleTimeString("en-US",
    {timeZone:"America/New_York",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
  const etDate=now.toLocaleDateString("en-US",
    {timeZone:"America/New_York",weekday:"short",month:"short",day:"numeric"});

  const mkt=(()=>{
    const s=now.toLocaleString("en-US",{timeZone:"America/New_York",
      hour:"numeric",minute:"numeric",hour12:false});
    const [h,m]=s.split(":").map(Number), mins=h*60+m;
    if(mins<9*60+30)  return{label:"PRE-MARKET",  col:Y};
    if(mins>=16*60)   return{label:"AFTER-HOURS", col:D};
    if(mins<9*60+31)  return{label:"WAITING 09:31",col:Y};
    return{label:"MARKET OPEN",col:G};
  })();

  return<div style={{minHeight:"100vh",background:"#010409",color:T,
    fontFamily:"'JetBrains Mono','Fira Code','Courier New',monospace",
    padding:"22px 26px",boxSizing:"border-box"}}>
    <style>{`
      @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800;900&display=swap');
      *{box-sizing:border-box;margin:0;padding:0}
      ::-webkit-scrollbar{width:4px}
      ::-webkit-scrollbar-track{background:#0d1117}
      ::-webkit-scrollbar-thumb{background:#30363d;border-radius:2px}
      @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
    `}</style>

    {/* TOP BAR */}
    <div style={{display:"flex",justifyContent:"space-between",
      alignItems:"flex-start",marginBottom:22}}>
      <div style={{display:"flex",alignItems:"center",gap:14}}>
        <div style={{width:40,height:40,borderRadius:8,
          background:"linear-gradient(135deg,#00ff8820,#00ff8808)",
          border:"1px solid #00ff8830",display:"flex",alignItems:"center",
          justifyContent:"center",fontSize:20}}>⚡</div>
        <div>
          <div style={{fontSize:20,fontWeight:900,letterSpacing:2,color:T}}>VWAP BOT</div>
          <div style={{fontSize:10,color:D,letterSpacing:2,marginTop:1}}>
            QQQ · TQQQ · ZARATTINI &amp; AZIZ 2023 · ALPACA PAPER
          </div>
        </div>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:6,alignItems:"flex-end"}}>
        <LiveDot on={connected}/>
        <div style={{fontSize:13,color:T,...MONO}}>{etTime} ET</div>
        <div style={{fontSize:10,color:D}}>{etDate}</div>
        <div style={{fontSize:10,letterSpacing:2,color:mkt.col,
          padding:"2px 10px",background:`${mkt.col}11`,
          border:`1px solid ${mkt.col}33`,borderRadius:3}}>{mkt.label}</div>
      </div>
    </div>

    <AccountBar a={state?.account}/>
    <DualChart chart={chart}/>

    <div style={{display:"flex",gap:16,marginBottom:16}}>
      {["QQQ","TQQQ"].map(sym=>(
        <SymbolCard key={sym} sym={sym}
          s={state?.symbols?.[sym]} chartData={chart[sym]||[]}/>
      ))}
    </div>

    <Rules cfg={state?.config}/>
    <TradeLog trades={trades}/>

    <div style={{marginTop:14,display:"flex",justifyContent:"space-between",
      fontSize:10,color:"#333"}}>
      <span>PAPER TRADING ONLY · FOR RESEARCH PURPOSES · NOT FINANCIAL ADVICE</span>
      <span>
        {state?`Updated: ${new Date(state.timestamp).toLocaleTimeString()}`:"—"}
        {state?.config?.skip_midday?" · MIDDAY FILTER ON":""}
      </span>
    </div>
  </div>;
}
