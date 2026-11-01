import {useEffect,useRef,useState} from "react";
import {CheckCircle2,Database,LoaderCircle,RefreshCw,ShieldAlert,X} from "lucide-react";
import {api,ApiError} from "./lib/api";
import type {LeagueRefreshProfile,SnapshotRefreshRecord,SnapshotRefreshResult} from "./types/api";

const terminal=new Set(["completed","cancelled","failed","interrupted"]);

export function LeagueRefresh({open,onClose,onPublished}:{open:boolean;onClose:()=>void;onPublished:(result:SnapshotRefreshResult)=>void}){
  const [profile,setProfile]=useState<LeagueRefreshProfile>();
  const [week,setWeek]=useState(1);
  const [record,setRecord]=useState<SnapshotRefreshRecord>();
  const [result,setResult]=useState<SnapshotRefreshResult>();
  const [error,setError]=useState<string>();
  const request=useRef(0);
  useEffect(()=>{if(!open)return;const controller=new AbortController(),version=++request.current;setError(undefined);void api.refreshProfile(controller.signal).then(value=>{if(version===request.current)setProfile(value)}).catch(cause=>{if(version===request.current&&!(cause instanceof DOMException&&cause.name==="AbortError"))setError(cause instanceof ApiError?cause.message:"Configured league profile could not be loaded.")});return()=>{request.current++;controller.abort()}},[open]);
  useEffect(()=>{if(!record||terminal.has(record.status))return;const controller=new AbortController();const timer=setInterval(()=>{void api.refresh(record.refresh_id,controller.signal).then(setRecord).catch(cause=>{if(!(cause instanceof DOMException&&cause.name==="AbortError"))setError(cause instanceof ApiError?cause.message:"Refresh status could not be loaded.")})},600);return()=>{clearInterval(timer);controller.abort()}},[record?.refresh_id,record?.status]);
  useEffect(()=>{if(record?.status!=="completed"||result?.refresh.refresh_id===record.refresh_id)return;const controller=new AbortController();void api.refreshResult(record.refresh_id,controller.signal).then(value=>{setResult(value);onPublished(value)}).catch(cause=>{if(!(cause instanceof DOMException&&cause.name==="AbortError"))setError(cause instanceof ApiError?cause.message:"Published snapshot result could not be loaded.")});return()=>controller.abort()},[record?.refresh_id,record?.status,result?.refresh.refresh_id,onPublished]);
  if(!open)return null;
  const submit=async()=>{if(!profile)return;setError(undefined);setResult(undefined);try{const submitted=await api.submitRefresh({api_schema_version:"1.0",profile_id:profile.profile_id,league_id:profile.league_id,season:profile.season,first_simulation_week:week});setRecord({api_schema_version:submitted.api_schema_version,refresh_id:submitted.refresh_id,status:submitted.status,created_at_utc:submitted.created_at_utc,stage:"queued",league_id:profile.league_id,season:profile.season,first_simulation_week:week,started_at_utc:null,finished_at_utc:null,snapshot_id:null,publication_committed:false,error_code:null,error_message:null})}catch(cause){setError(cause instanceof ApiError?cause.message:"Refresh could not be started.")}};
  const cancel=async()=>{if(!record)return;try{setRecord(await api.cancelRefresh(record.refresh_id))}catch(cause){setError(cause instanceof ApiError?cause.message:"Refresh could not be cancelled.")}};
  const active=record&&!terminal.has(record.status);
  return <div className="refresh-scrim" role="presentation"><section className="refresh-panel" role="dialog" aria-modal="true" aria-label="Refresh ESPN league"><header><div><span className="eyebrow">Source-backed publication</span><h2>Refresh ESPN league</h2></div><button className="icon-button" aria-label="Close refresh" onClick={onClose} disabled={!!active}><X/></button></header>
    {!profile&&!error?<p aria-busy="true"><LoaderCircle className="spin"/> Loading configured league profile…</p>:profile&&<><div className="refresh-identity"><Database/><div><strong>League {profile.league_id} · {profile.season}</strong><span>{profile.credential_mode==="private_credentials_configured"?"Private credentials configured on this server":"Public league access"}</span></div></div><p className="privacy-note">Credentials remain server-side and are never returned to this browser.</p><label>First unstarted scoring week<input type="number" min="1" max="25" step="1" value={week} disabled={!!active} onChange={event=>setWeek(Number(event.target.value))}/></label></>}
    {record&&<div className={`refresh-status status-${record.status}`} aria-live="polite">{active?<LoaderCircle className="spin"/>:record.status==="completed"?<CheckCircle2/>:<ShieldAlert/>}<div><strong>{record.status.replaceAll("_"," ")}</strong><span>{record.stage.replaceAll("_"," ")}</span>{record.error_message&&<small>{record.error_message}</small>}</div></div>}
    {result&&<div className="refresh-result"><strong>Published snapshot {result.snapshot.snapshot_id}</strong>{result.validation_findings.map(item=><span key={item}>{item}</span>)}{result.unresolved_capabilities.length>0&&<details><summary>Unresolved capabilities ({result.unresolved_capabilities.length})</summary>{result.unresolved_capabilities.map(item=><p key={item}>{item}</p>)}</details>}</div>}
    {error&&<p className="refresh-error" role="alert">{error}</p>}
    <footer>{active&&record.stage!=="publishing"&&record.stage!=="verifying"?<button onClick={()=>void cancel()}>Cancel before publication</button>:<button className="primary-button" disabled={!profile||!!active||!Number.isInteger(week)||week<1||week>25} onClick={()=>void submit()}><RefreshCw/>Start authorized refresh</button>}</footer>
  </section></div>
}
