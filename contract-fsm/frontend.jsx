import { useState, useEffect, useRef, useCallback } from "react";

const FSM = {
  contract_id: "WesTex Community Credit Union VISA Credit Card Agreement",
  parties: ["WesTex Community Credit Union", "Cardholder"],
  states: [
    { id: "s_active",   description: "Account Active",             terminal: false, active_rule_ids: ["R01"] },
    { id: "s_grace",    description: "Grace Period",                terminal: false, active_rule_ids: ["R01","R03"] },
    { id: "s_accruing", description: "Interest Accruing",           terminal: false, active_rule_ids: ["R01","R02","R03"] },
    { id: "s_past_due", description: "Past Due",                    terminal: false, active_rule_ids: ["R01","R02","R03","R04"] },
    { id: "s_default",  description: "Default",                     terminal: false, active_rule_ids: [] },
    { id: "s_closed",   description: "Account Closed",              terminal: true,  active_rule_ids: [] },
  ],
  initial_state: "s_active",
  rules: [
    { id: "R01", type: "OBLIGATION", party: "Credit Union", action: "Extend credit up to limit", desc: "Credit extension on card use" },
    { id: "R02", type: "OBLIGATION", party: "Cardholder",   action: "Make minimum payment by due date", desc: "Minimum payment obligation" },
    { id: "R03", type: "OBLIGATION", party: "Credit Union", action: "Compute and apply periodic interest", desc: "Interest accrual (grace period exception)" },
    { id: "R04", type: "OBLIGATION", party: "Cardholder",   action: "Pay late fee on missed deadline", desc: "Penalty: $29 late fee" },
    { id: "R05", type: "OBLIGATION", party: "Cardholder",   action: "Pay over-limit fee", desc: "Penalty: $29 over-limit fee" },
  ],
  params: { amounts: { credit_limit: "5000", late_fee: "29", overlimit_fee: "29" }, rates: { apr: "0.1499" }, durations: { grace_period: 25, default_threshold: 60 } },
  transitions: [
    { id: "T01", from: ["s_active"],   to: "s_accruing", event_type: "purchase",                guard: "amt + bal ≤ limit", effects: ["ASSET_TRANSFER"], desc: "Purchase → accrual" },
    { id: "T03", from: ["s_accruing"], to: "s_accruing", event_type: "purchase",                guard: null, effects: ["ASSET_TRANSFER"], desc: "Additional purchase" },
    { id: "T05", from: ["s_accruing"], to: "s_accruing", event_type: "billing_cycle_close",     guard: "balance > 0", effects: ["PAYMENT","RULE_ACTIVATION"], desc: "Interest + min payment" },
    { id: "T06", from: ["s_accruing"], to: "s_grace",    event_type: "payment_received",        guard: "amt ≥ balance", effects: ["PAYMENT"], desc: "Full payoff → grace" },
    { id: "T07", from: ["s_accruing"], to: "s_accruing", event_type: "payment_received",        guard: "amt ≥ minimum", effects: ["PAYMENT"], desc: "Partial payment applied" },
    { id: "T08", from: ["s_accruing"], to: "s_past_due", event_type: "payment_deadline_passed", guard: "first missed",  effects: ["PAYMENT"], desc: "Late penalty assessed" },
    { id: "T09", from: ["s_past_due"], to: "s_accruing", event_type: "payment_received",        guard: "amt ≥ minimum", effects: ["PAYMENT"], desc: "Payment cures delinquency" },
    { id: "T10", from: ["s_past_due"], to: "s_default",  event_type: "payment_deadline_passed", guard: "dpd ≥ 60",      effects: ["ACCELERATION"], desc: "60+ days → default" },
    { id: "T12", from: ["s_default"],  to: "s_closed",   event_type: "account_closed",          guard: null, effects: ["TERMINATION"], desc: "Account closed" },
    { id: "T13", from: ["s_grace"],    to: "s_accruing", event_type: "purchase",                guard: null, effects: ["ASSET_TRANSFER"], desc: "Purchase ends grace" },
  ],
  unmappable: [
    { id: "U01", type: "POWER", desc: "Refuse illegal transactions — discretionary" },
    { id: "U02", type: "REPRESENTATION", desc: "Security interest grant — legal declaration" },
    { id: "U03", type: "COVENANT", desc: "Indemnification and waiver — procedural" },
  ],
};

const LOG = [
  { step: 1,  from: "s_active",   ev: "purchase",                to: "s_accruing", tid: "T01", vars: { balance: 1200, interest: 0, fees: 0, dpd: 0 }, emit: "Purchase $1,200 → Balance: $1,200.00" },
  { step: 2,  from: "s_accruing", ev: "purchase",                to: "s_accruing", tid: "T03", vars: { balance: 1550, interest: 0, fees: 0, dpd: 0 }, emit: "Purchase $350 → Balance: $1,550.00" },
  { step: 3,  from: "s_accruing", ev: "billing_cycle_close",     to: "s_accruing", tid: "T05", vars: { balance: 1569.10, interest: 19.10, fees: 0, dpd: 0 }, emit: "Interest $19.10 · Min pmt $31.38 · Bal $1,569.10" },
  { step: 4,  from: "s_accruing", ev: "payment_received",        to: "s_accruing", tid: "T07", vars: { balance: 1519.10, interest: 19.10, fees: 0, dpd: 0 }, emit: "Payment $50.00 applied → Bal $1,519.10" },
  { step: 5,  from: "s_accruing", ev: "purchase",                to: "s_accruing", tid: "T03", vars: { balance: 1719.10, interest: 19.10, fees: 0, dpd: 0 }, emit: "Purchase $200 → Balance: $1,719.10" },
  { step: 6,  from: "s_accruing", ev: "billing_cycle_close",     to: "s_accruing", tid: "T05", vars: { balance: 1740.99, interest: 21.89, fees: 0, dpd: 0 }, emit: "Interest $21.89 · Min pmt $34.82 · Bal $1,740.99" },
  { step: 7,  from: "s_accruing", ev: "payment_deadline_passed", to: "s_past_due", tid: "T08", vars: { balance: 1769.99, interest: 21.89, fees: 29, dpd: 15 }, emit: "⚠ LATE — Penalty $29.00 · DPD 15 · Bal $1,769.99" },
  { step: 8,  from: "s_past_due", ev: "payment_received",        to: "s_accruing", tid: "T09", vars: { balance: 1669.99, interest: 21.89, fees: 29, dpd: 0 }, emit: "Payment $100 — cured · Bal $1,669.99" },
  { step: 9,  from: "s_accruing", ev: "billing_cycle_close",     to: "s_accruing", tid: "T05", vars: { balance: 1690.57, interest: 20.58, fees: 29, dpd: 0 }, emit: "Interest $20.58 · Min pmt $33.81 · Bal $1,690.57" },
  { step: 10, from: "s_accruing", ev: "payment_received",        to: "s_grace",    tid: "T06", vars: { balance: 0, interest: 20.58, fees: 29, dpd: 0 }, emit: "✓ Paid in full — grace period restored" },
];

const POS = {
  s_active:   { x: 110, y: 175 }, s_grace:    { x: 110, y: 390 },
  s_accruing: { x: 400, y: 280 }, s_past_due: { x: 650, y: 175 },
  s_default:  { x: 650, y: 390 }, s_closed:   { x: 860, y: 390 },
};

const EV_COL = {
  purchase: "#60a5fa", cash_advance: "#c084fc", billing_cycle_close: "#fb923c",
  payment_received: "#34d399", payment_deadline_passed: "#f87171", account_closed: "#9ca3af",
};

const RULE_COL = { OBLIGATION: "#60a5fa", PROHIBITION: "#f87171", PERMISSION: "#34d399", POWER: "#fb923c", REPRESENTATION: "#c084fc", WARRANTY: "#eab308", COVENANT: "#9ca3af" };

function Diagram({ cur, prev, firedT }) {
  const edges = {};
  FSM.transitions.forEach(t => { t.from.forEach(f => { const k = `${f}→${t.to}`; (edges[k] = edges[k] || []).push(t); }); });
  return (
    <svg viewBox="0 0 980 510" style={{ width: "100%", height: "auto" }}>
      <defs>
        <marker id="a" viewBox="0 0 10 7" refX="10" refY="3.5" markerWidth="7" markerHeight="5" orient="auto"><path d="M0 0L10 3.5L0 7z" fill="#3f3f46"/></marker>
        <marker id="al" viewBox="0 0 10 7" refX="10" refY="3.5" markerWidth="7" markerHeight="5" orient="auto"><path d="M0 0L10 3.5L0 7z" fill="#a78bfa"/></marker>
        <filter id="g"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      </defs>
      {Object.entries(edges).map(([k, ts]) => {
        const [fk, tk] = k.split("→");
        const f = POS[fk], t2 = POS[tk], lit = firedT && ts.some(t => t.id === firedT);
        const col = lit ? "#a78bfa" : "#3f3f46", sw = lit ? 2.5 : 1, mk = lit ? "url(#al)" : "url(#a)";
        if (fk === tk) return <path key={k} d={`M${f.x-20} ${f.y-28}C${f.x-50} ${f.y-85},${f.x+50} ${f.y-85},${f.x+20} ${f.y-28}`} fill="none" stroke={col} strokeWidth={sw} markerEnd={mk} style={lit?{filter:"url(#g)"}:{}}/>;
        const dx=t2.x-f.x,dy=t2.y-f.y,len=Math.sqrt(dx*dx+dy*dy),nx=dx/len,ny=dy/len;
        const x1=f.x+nx*62,y1=f.y+ny*28,x2=t2.x-nx*62,y2=t2.y-ny*28;
        const mx=(x1+x2)/2-ny*24,my=(y1+y2)/2+nx*24;
        return <path key={k} d={`M${x1} ${y1}Q${mx} ${my} ${x2} ${y2}`} fill="none" stroke={col} strokeWidth={sw} markerEnd={mk} style={lit?{filter:"url(#g)"}:{}}/>;
      })}
      {FSM.states.map(s => {
        const p = POS[s.id], isCur = s.id === cur, wasPrev = s.id === prev && prev !== cur;
        let fill="#18181b",stroke="#3f3f46",text="#a1a1aa";
        if (s.id === FSM.initial_state && !isCur && !wasPrev) { stroke="#60a5fa"; text="#60a5fa"; }
        if (s.terminal && !isCur) { stroke="#ef4444"; text="#ef4444"; }
        if (wasPrev) { stroke="#a78bfa"; text="#a78bfa"; fill="#1a1625"; }
        if (isCur) { stroke="#34d399"; text="#34d399"; fill="#052e16"; }
        const ruleIds = s.active_rule_ids || [];
        return (<g key={s.id}>
          <rect x={p.x-58} y={p.y-26} width={116} height={52} rx={8} fill={fill} stroke={stroke} strokeWidth={isCur?2.5:1.5} style={isCur?{filter:"url(#g)"}:{}}/>
          {s.id===FSM.initial_state && <polygon points={`${p.x-74},${p.y} ${p.x-64},${p.y-5} ${p.x-64},${p.y+5}`} fill={stroke}/>}
          <text x={p.x} y={p.y-4} textAnchor="middle" dominantBaseline="middle" fill={text} fontSize="9.5" fontFamily="'IBM Plex Mono',monospace" fontWeight={isCur?700:500}>{s.description}</text>
          {ruleIds.length > 0 && <text x={p.x} y={p.y+14} textAnchor="middle" fill="#52525b" fontSize="7.5" fontFamily="'IBM Plex Mono',monospace">{ruleIds.join(" · ")}</text>}
        </g>);
      })}
    </svg>
  );
}

function Bar({label,value,max,color,money}) {
  const pct = max > 0 ? Math.min(100,(Math.abs(value)/max)*100) : 0;
  const fmt = money ? `$${value.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}` : value;
  return (<div style={{marginBottom:8}}>
    <div style={{display:"flex",justifyContent:"space-between",fontSize:11,fontFamily:"'IBM Plex Mono',monospace",marginBottom:3}}>
      <span style={{color:"#71717a"}}>{label}</span><span style={{color,fontWeight:600}}>{fmt}</span>
    </div>
    <div style={{height:5,background:"#27272a",borderRadius:3,overflow:"hidden"}}><div style={{height:"100%",width:`${pct}%`,background:color,borderRadius:3,transition:"width 0.4s ease"}}/></div>
  </div>);
}

function Badge({trigger}) {
  const c = EV_COL[trigger]||"#9ca3af";
  return <span style={{display:"inline-block",padding:"2px 8px",borderRadius:4,fontSize:10,fontFamily:"'IBM Plex Mono',monospace",fontWeight:600,color:c,border:`1px solid ${c}25`,background:`${c}10`}}>{trigger.replace(/_/g," ")}</span>;
}

function RuleBadge({rule}) {
  const c = RULE_COL[rule.type] || "#9ca3af";
  return <span style={{display:"inline-block",padding:"2px 8px",borderRadius:4,fontSize:10,fontFamily:"'IBM Plex Mono',monospace",fontWeight:600,color:c,border:`1px solid ${c}25`,background:`${c}10`}}>{rule.id}</span>;
}

function RulesTab() {
  return (<div style={{background:"#18181b",borderRadius:10,border:"1px solid #27272a",padding:24}}>
    <div style={{fontSize:9,color:"#71717a",textTransform:"uppercase",letterSpacing:1.5,marginBottom:16,fontFamily:"'IBM Plex Mono',monospace"}}>Extracted Rules (Mappable)</div>
    {FSM.rules.map(r => (<div key={r.id} style={{padding:"12px 16px",background:"#09090b",borderRadius:8,border:`1px solid ${(RULE_COL[r.type]||"#3f3f46")}20`,marginBottom:10}}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
        <RuleBadge rule={r}/>
        <span style={{fontSize:12,fontWeight:600,color:"#d4d4d8"}}>{r.action}</span>
      </div>
      <div style={{fontSize:11,color:"#71717a"}}>{r.party} — {r.desc}</div>
    </div>))}
    <div style={{fontSize:9,color:"#71717a",textTransform:"uppercase",letterSpacing:1.5,margin:"20px 0 12px",fontFamily:"'IBM Plex Mono',monospace"}}>Unmappable Rules (flagged)</div>
    {FSM.unmappable.map(u => (<div key={u.id} style={{padding:"10px 16px",background:"#09090b",borderRadius:8,border:"1px solid #27272a",marginBottom:8}}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:2}}>
        <span style={{fontSize:10,fontFamily:"'IBM Plex Mono',monospace",color:"#eab308",fontWeight:600}}>{u.id}</span>
        <span style={{fontSize:10,fontFamily:"'IBM Plex Mono',monospace",color:"#52525b"}}>[{u.type}]</span>
      </div>
      <div style={{fontSize:11,color:"#71717a"}}>{u.desc}</div>
    </div>))}
  </div>);
}

function EnglishTab() {
  return (<div style={{background:"#18181b",borderRadius:10,border:"1px solid #27272a",padding:24}}>
    <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:16}}>
      <span style={{fontSize:10,fontFamily:"'IBM Plex Mono',monospace",color:"#34d399",padding:"2px 8px",border:"1px solid #34d39930",borderRadius:4,background:"#34d39910"}}>DETERMINISTIC</span>
      <span style={{fontSize:10,fontFamily:"'IBM Plex Mono',monospace",color:"#71717a"}}>No LLMs · Template-generated · Diff-safe</span>
    </div>
    <pre style={{fontSize:11,lineHeight:1.7,color:"#d4d4d8",whiteSpace:"pre-wrap",fontFamily:"'IBM Plex Mono',monospace",margin:0}}>{`PARTIES
  1. Cardholder
  2. WesTex Community Credit Union

CONTRACT PARAMETERS
  Amounts: Credit Limit $5,000 · Late Fee $29 · Overlimit Fee $29
  Rates: APR 14.99% · Min Payment Rate 2%
  Durations: Grace Period 25d · Default Threshold 60d · Billing Cycle 30d

RULES AND OBLIGATIONS
  R01 [OBLIGATION] Credit Union → Cardholder
    Extend credit up to limit upon card use
    Constraint: CAP — balance ≤ $5,000

  R02 [OBLIGATION] Cardholder → Credit Union
    Make at least minimum payment each billing cycle
    Trigger: DATE — billing cycle closes (30 days)
    Constraint: FLOOR — minimum payment ≥ $25

  R03 [OBLIGATION] Credit Union → Cardholder
    Compute periodic interest on outstanding balance
    Trigger: TEMPORAL — billing cycle close with balance > 0
    Formula: balance × (APR / 365) × days_in_cycle
    Exception: Grace period (25 days, no interest if prior balance paid in full)

  R04 [OBLIGATION] Cardholder → Credit Union
    Penalty: $29 late fee on missed payment deadline
    Trigger: ABSENCE — minimum payment not received by due date
    Constraint: CAP — $29 per occurrence

  R05 [OBLIGATION] Cardholder → Credit Union
    Penalty: $29 over-limit fee when balance exceeds credit limit
    Trigger: THRESHOLD — balance > $5,000

STATE TRANSITIONS
  When in "Account Active":
    On "purchase": Guard: amt + bal ≤ limit → "Interest Accruing"

  When in "Interest Accruing":
    On "billing cycle close": Guard: balance > 0
      → PAYMENT: periodic interest · RULE_ACTIVATION: compute min payment
    On "payment received": Guard: amt ≥ balance → "Grace Period"
    On "payment received": Guard: amt ≥ minimum → remains
    On "payment deadline passed": Guard: first missed
      → PAYMENT: late penalty $29 → "Past Due"

  When in "Past Due":
    On "payment received": Guard: amt ≥ minimum → "Interest Accruing" (cured)
    On "payment deadline passed": Guard: dpd ≥ 60 → "Default"

  When in "Default":
    On "account closed": → "Account Closed" [terminal]

UNMODELED RULES
  U01 [POWER] Refuse illegal transactions — discretionary
  U02 [REPRESENTATION] Security interest grant — legal declaration
  U03 [COVENANT] Indemnification/waiver — procedural`}</pre>
  </div>);
}

export default function App() {
  const [step, setStep] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState("diagram");
  const ref = useRef(null);

  const entry = step >= 0 ? LOG[step] : null;
  const cur = entry ? entry.to : FSM.initial_state;
  const prev = entry ? entry.from : null;
  const firedT = entry ? entry.tid : null;
  const vars = entry ? entry.vars : {balance:0,interest:0,fees:0,dpd:0};

  const next = useCallback(() => setStep(s => { if (s >= LOG.length-1) { setPlaying(false); return s; } return s+1; }), []);
  useEffect(() => { if (playing) ref.current = setInterval(next,1100); else clearInterval(ref.current); return () => clearInterval(ref.current); }, [playing,next]);

  const desc = FSM.states.find(s => s.id === cur)?.description || cur;
  const activeRules = FSM.states.find(s => s.id === cur)?.active_rule_ids || [];

  const B = (p) => <button {...p} style={{padding:"7px 16px",fontSize:12,fontWeight:600,cursor:p.disabled?"default":"pointer",background:"#27272a",border:"1px solid #3f3f46",borderRadius:8,color:p.disabled?"#52525b":"#d4d4d8",fontFamily:"'IBM Plex Mono',monospace",opacity:p.disabled?0.5:1,...p.style}}>{p.children}</button>;

  const tabs = [{id:"diagram",l:"State Machine"},{id:"rules",l:"Rules"},{id:"english",l:"Decompiled English"},{id:"log",l:"Execution Log"}];

  return (
    <div style={{fontFamily:"'Söhne','Helvetica Neue',sans-serif",background:"#09090b",color:"#d4d4d8",minHeight:"100vh",maxWidth:1020,margin:"0 auto"}}>
      <div style={{padding:"28px 28px 0",borderBottom:"1px solid #27272a"}}>
        <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:6}}>
          <span style={{fontSize:9,fontFamily:"'IBM Plex Mono',monospace",color:"#34d399",letterSpacing:2.5,textTransform:"uppercase",fontWeight:700}}>Executable Contract</span>
          <span style={{width:4,height:4,borderRadius:"50%",background:"#3f3f46"}}/>
          <span style={{fontSize:9,fontFamily:"'IBM Plex Mono',monospace",color:"#52525b",letterSpacing:1}}>OBLIGATIONS · PENALTIES · DEADLINES · CONDITIONS</span>
        </div>
        <h1 style={{fontSize:21,fontWeight:700,color:"#fafafa",margin:"0 0 18px",letterSpacing:"-0.03em"}}>{FSM.contract_id}</h1>
        <div style={{display:"flex",gap:0}}>{tabs.map(t => <button key={t.id} onClick={()=>setTab(t.id)} style={{padding:"9px 18px",fontSize:11.5,fontWeight:600,cursor:"pointer",background:"none",border:"none",borderBottom:tab===t.id?"2px solid #fafafa":"2px solid transparent",color:tab===t.id?"#fafafa":"#71717a",fontFamily:"'IBM Plex Mono',monospace"}}>{t.l}</button>)}</div>
      </div>

      <div style={{padding:28}}>
        {tab === "diagram" && (<>
          <div style={{background:"#18181b",borderRadius:10,border:"1px solid #27272a",padding:16,marginBottom:20}}><Diagram cur={cur} prev={prev} firedT={firedT}/></div>

          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:20}}>
            <B onClick={()=>{setStep(-1);setPlaying(false);}}>↺</B>
            <B onClick={()=>setStep(s=>Math.max(-1,s-1))} disabled={step<0}>◂</B>
            <B onClick={()=>setPlaying(!playing)} style={{background:playing?"#7f1d1d20":"#05291020",color:playing?"#f87171":"#34d399",borderColor:playing?"#f8717130":"#34d39930"}}>{playing?"⏸":"▸ Play"}</B>
            <B onClick={next} disabled={step>=LOG.length-1}>▸</B>
            <span style={{marginLeft:"auto",fontSize:11,fontFamily:"'IBM Plex Mono',monospace",color:"#52525b"}}>{step+1} / {LOG.length}</span>
          </div>

          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
            <div style={{background:"#18181b",borderRadius:10,border:"1px solid #27272a",padding:20}}>
              <div style={{fontSize:9,color:"#71717a",textTransform:"uppercase",letterSpacing:1.5,marginBottom:8,fontFamily:"'IBM Plex Mono',monospace"}}>Current State</div>
              <div style={{fontSize:18,fontWeight:700,color:"#34d399",marginBottom:6}}>{desc}</div>
              {activeRules.length > 0 && <div style={{marginBottom:14,display:"flex",gap:4,flexWrap:"wrap"}}>{activeRules.map(rid => { const r = FSM.rules.find(x=>x.id===rid); return r ? <RuleBadge key={rid} rule={r}/> : null; })}</div>}
              {entry ? (<>
                <div style={{fontSize:9,color:"#71717a",textTransform:"uppercase",letterSpacing:1.5,marginBottom:6,fontFamily:"'IBM Plex Mono',monospace"}}>Event</div>
                <div style={{marginBottom:14}}><Badge trigger={entry.ev}/><span style={{fontSize:10,color:"#3f3f46",marginLeft:8,fontFamily:"'IBM Plex Mono',monospace"}}>{entry.tid}</span></div>
                <div style={{fontSize:9,color:"#71717a",textTransform:"uppercase",letterSpacing:1.5,marginBottom:6,fontFamily:"'IBM Plex Mono',monospace"}}>Output</div>
                <div style={{fontSize:12,lineHeight:1.5,color:"#d4d4d8",padding:"10px 14px",background:"#09090b",borderRadius:6,border:"1px solid #27272a",fontFamily:"'IBM Plex Mono',monospace"}}>{entry.emit}</div>
              </>) : <div style={{fontSize:12,color:"#52525b",fontStyle:"italic"}}>Press Play or ▸ to begin.</div>}
            </div>
            <div style={{background:"#18181b",borderRadius:10,border:"1px solid #27272a",padding:20}}>
              <div style={{fontSize:9,color:"#71717a",textTransform:"uppercase",letterSpacing:1.5,marginBottom:14,fontFamily:"'IBM Plex Mono',monospace"}}>Contract Variables</div>
              <Bar label="Balance" value={vars.balance} max={5000} color="#60a5fa" money/>
              <Bar label="Accrued Interest" value={vars.interest} max={100} color="#fb923c" money/>
              <Bar label="Total Fees (Penalties)" value={vars.fees} max={100} color="#f87171" money/>
              <Bar label="Days Past Due" value={vars.dpd} max={60} color="#eab308"/>
            </div>
          </div>
        </>)}

        {tab === "rules" && <RulesTab/>}
        {tab === "english" && <EnglishTab/>}

        {tab === "log" && (
          <div style={{background:"#18181b",borderRadius:10,border:"1px solid #27272a",overflow:"hidden"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:11,fontFamily:"'IBM Plex Mono',monospace"}}>
              <thead><tr style={{borderBottom:"1px solid #27272a"}}>
                {["#","From","Event","T","To","Balance","Fees","Output"].map(h=><th key={h} style={{padding:"10px",textAlign:"left",color:"#71717a",fontWeight:600,fontSize:9,textTransform:"uppercase",letterSpacing:1}}>{h}</th>)}
              </tr></thead>
              <tbody>{LOG.map((e,i)=>{
                const fd=FSM.states.find(s=>s.id===e.from)?.description, td=FSM.states.find(s=>s.id===e.to)?.description;
                return <tr key={i} onClick={()=>{setStep(i);setTab("diagram");}} style={{borderBottom:"1px solid #27272a15",cursor:"pointer",background:i===step?"#34d39910":"transparent"}}>
                  <td style={{padding:"7px 10px",color:"#71717a"}}>{e.step}</td>
                  <td style={{padding:"7px 10px",color:"#a1a1aa",maxWidth:80,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{fd}</td>
                  <td style={{padding:"7px 10px"}}><Badge trigger={e.ev}/></td>
                  <td style={{padding:"7px 10px",color:"#71717a"}}>{e.tid}</td>
                  <td style={{padding:"7px 10px",color:"#a1a1aa",maxWidth:80,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{td}</td>
                  <td style={{padding:"7px 10px",color:"#60a5fa"}}>${e.vars.balance.toFixed(2)}</td>
                  <td style={{padding:"7px 10px",color:"#f87171"}}>${e.vars.fees.toFixed(2)}</td>
                  <td style={{padding:"7px 10px",color:"#71717a",maxWidth:180,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{e.emit}</td>
                </tr>;
              })}</tbody>
            </table>
          </div>
        )}

        <div style={{marginTop:24,padding:"14px 18px",background:"#18181b",borderRadius:10,border:"1px solid #27272a",display:"flex",gap:20,fontSize:10,fontFamily:"'IBM Plex Mono',monospace",color:"#52525b",flexWrap:"wrap"}}>
          <span>Prose →<span style={{color:"#60a5fa"}}> LLM </span>→ Rules</span>
          <span>Rules →<span style={{color:"#c084fc"}}> DET </span>→ FSM</span>
          <span>FSM →<span style={{color:"#c084fc"}}> DET </span>→ Python</span>
          <span>FSM →<span style={{color:"#c084fc"}}> DET </span>→ English</span>
          <span style={{marginLeft:"auto",color:"#34d399"}}>LLM-free round-trip ✓</span>
        </div>
      </div>
    </div>
  );
}
