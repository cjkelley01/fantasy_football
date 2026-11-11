import {useEffect,useRef,useState} from "react";
import {CheckCircle2,Database,LoaderCircle,RefreshCw,ShieldAlert,X} from "lucide-react";
import {api,ApiError} from "./lib/api";
import type {LeagueRefreshProfile,SnapshotRefreshRecord,SnapshotRefreshResult} from "./types/api";

const terminal=new Set(["completed","cancelled","failed","interrupted"]);
const aborted=(cause:unknown)=>cause instanceof DOMException&&cause.name==="AbortError";
const message=(cause:unknown,fallback:string)=>cause instanceof ApiError?cause.message:fallback;

export function LeagueRefresh({open,onClose,onPublished}:{open:boolean;onClose:()=>void;onPublished:(result:SnapshotRefreshResult)=>void}){
  const [profile,setProfile]=useState<LeagueRefreshProfile>();
  const [week,setWeek]=useState<number|"">("");
  const [manualOverride,setManualOverride]=useState(false);
  const [record,setRecord]=useState<SnapshotRefreshRecord>();
  const [result,setResult]=useState<SnapshotRefreshResult>();
  const [error,setError]=useState<string>();
  const [submitting,setSubmitting]=useState(false);
  const discoveryVersion=useRef(0),pollVersion=useRef(0),resultVersion=useRef(0),submitVersion=useRef(0);

  useEffect(()=>{
    if(!open)return;
    const controller=new AbortController(),version=++discoveryVersion.current;
    setError(undefined);
    void Promise.all([api.refreshProfile(controller.signal),api.currentRefresh(controller.signal)]).then(([nextProfile,current])=>{
      if(controller.signal.aborted||version!==discoveryVersion.current)return;
      setProfile(nextProfile);setRecord(current??undefined);
      if(current&&current.first_simulation_week!==null)setWeek(current.first_simulation_week);
    }).catch(cause=>{if(!aborted(cause)&&version===discoveryVersion.current)setError(message(cause,"Configured league profile could not be loaded."));});
    return()=>{discoveryVersion.current++;controller.abort()};
  },[open]);

  useEffect(()=>{
    if(!open||!record||terminal.has(record.status))return;
    let timer:number|undefined;
    const controller=new AbortController(),refreshId=record.refresh_id,version=++pollVersion.current;
    const poll=async()=>{
      try{
        const next=await api.refresh(refreshId,controller.signal);
        if(controller.signal.aborted||version!==pollVersion.current||next.refresh_id!==refreshId)return;
        setRecord(next);
        if(!terminal.has(next.status))timer=window.setTimeout(()=>void poll(),600);
      }catch(cause){if(!aborted(cause)&&version===pollVersion.current)setError(message(cause,"Refresh status could not be loaded."));}
    };
    timer=window.setTimeout(()=>void poll(),0);
    return()=>{if(timer!==undefined)clearTimeout(timer);controller.abort()};
  },[open,record?.refresh_id,record?.status]);

  useEffect(()=>{
    if(!open||record?.status!=="completed"||result?.refresh.refresh_id===record.refresh_id)return;
    const controller=new AbortController(),refreshId=record.refresh_id,version=++resultVersion.current;
    void api.refreshResult(refreshId,controller.signal).then(value=>{
      if(controller.signal.aborted||version!==resultVersion.current||value.refresh.refresh_id!==refreshId)return;
      setResult(value);onPublished(value);
    }).catch(cause=>{if(!aborted(cause)&&version===resultVersion.current)setError(message(cause,"Published snapshot result could not be loaded."));});
    return()=>{resultVersion.current++;controller.abort()};
  },[open,record?.refresh_id,record?.status,result?.refresh.refresh_id,onPublished]);

  if(!open)return null;
  const submit=async()=>{
    if(!profile||submitting||manualOverride&&week==="")return;
    const controller=new AbortController(),version=++submitVersion.current;
    setSubmitting(true);setError(undefined);setResult(undefined);
    try{
      const selectedWeek=manualOverride?week as number:null;
      const submitted=await api.submitRefresh({api_schema_version:"1.0",profile_id:profile.profile_id,league_id:profile.league_id,season:profile.season,first_simulation_week:selectedWeek},controller.signal);
      if(controller.signal.aborted||version!==submitVersion.current)return;
      setRecord({api_schema_version:submitted.api_schema_version,refresh_id:submitted.refresh_id,status:submitted.status,created_at_utc:submitted.created_at_utc,stage:"queued",league_id:profile.league_id,season:profile.season,first_simulation_week:selectedWeek,started_at_utc:null,finished_at_utc:null,snapshot_id:null,publication_committed:false,error_code:null,error_message:null});
    }catch(cause){if(!aborted(cause)&&version===submitVersion.current)setError(message(cause,"Refresh could not be started."));}
    finally{if(version===submitVersion.current)setSubmitting(false);}
  };
  const cancel=async()=>{if(!record)return;try{setRecord(await api.cancelRefresh(record.refresh_id))}catch(cause){setError(message(cause,"Refresh could not be cancelled."));}};
  const active=record&&!terminal.has(record.status);
  return <div className="refresh-scrim" role="presentation"><section className="refresh-panel" role="dialog" aria-modal="true" aria-label="Refresh ESPN league"><header><div><span className="eyebrow">Source-backed publication</span><h2>Refresh ESPN league</h2></div><button className="icon-button" aria-label="Close refresh" onClick={onClose}><X/></button></header>
    {!profile&&!error?<p aria-busy="true"><LoaderCircle className="spin"/> Loading configured league profile…</p>:profile&&<><div className="refresh-identity"><Database/><div><strong>League {profile.league_id} · {profile.season}</strong><span>{profile.credential_mode==="private_credentials_configured"?"Private credentials configured on this server":"Public league access"}</span></div></div><p className="privacy-note">Credentials remain server-side and are never returned to this browser.</p><p><strong>Automatic simulation boundary</strong><br/><small>The server selects the first fully unplayed week only from explicit ESPN game-status evidence and fails closed when that evidence is incomplete.</small></p><label><input type="checkbox" checked={manualOverride} disabled={!!active||submitting} onChange={event=>setManualOverride(event.target.checked)}/> Use advanced manual week override</label>{manualOverride&&<><label>Manual first unstarted scoring week<input type="number" min="1" max="25" step="1" value={week} disabled={!!active||submitting} onChange={event=>setWeek(event.target.value===""?"":Number(event.target.value))}/></label><small>The snapshot labels this as a manual override, not automatically detected ESPN evidence.</small></>}</>}
    {record&&<div className={`refresh-status status-${record.status}`} aria-live="polite">{active?<LoaderCircle className="spin"/>:record.status==="completed"?<CheckCircle2/>:<ShieldAlert/>}<div><strong>{record.status.replaceAll("_"," ")}</strong><span>{record.stage.replaceAll("_"," ")}</span>{record.error_message&&<small>{record.error_message}</small>}</div></div>}
    {active&&<p className="privacy-note">You may close this view while retrieval continues. Reopen it to rediscover this server-side refresh.</p>}
    {result&&<div className="refresh-result"><strong>Published snapshot {result.snapshot.snapshot_id}</strong>{result.snapshot.live_scoring&&<><span>Current scoring period: {result.snapshot.live_scoring.current_scoring_period}</span><span>Last completed: {result.snapshot.live_scoring.last_fully_completed_period??"unresolved"}</span><span>Earliest unresolved: {result.snapshot.live_scoring.earliest_unresolved_period??"none"}</span><span>Next fully unplayed: {result.snapshot.live_scoring.next_fully_unplayed_period??"unresolved"}</span><span>Supported V1 boundary: {result.snapshot.live_scoring.supported_simulation_week??"unresolved"} ({result.snapshot.live_scoring.boundary_method.replaceAll("_"," ")})</span><small>{result.snapshot.live_scoring.boundary_rationale}</small>{result.snapshot.live_scoring.partial_week_simulation_required&&<small>Current-week outcomes are partially resolved. LW1 preserves the later V1 boundary; partial-week Monte Carlo remains deferred to LW2.</small>}</>}{result.validation_findings.map(item=><span key={item}>{item}</span>)}{result.unresolved_capabilities.length>0&&<details><summary>Unresolved capabilities ({result.unresolved_capabilities.length})</summary>{result.unresolved_capabilities.map(item=><p key={item}>{item}</p>)}</details>}</div>}
    {error&&<p className="refresh-error" role="alert">{error}</p>}
    <footer>{active&&record.stage!=="publishing"&&record.stage!=="verifying"?<button onClick={()=>void cancel()}>Cancel before publication</button>:<button className="primary-button" disabled={!profile||submitting||!!active||manualOverride&&(week===""||!Number.isInteger(week)||week<1||week>25)} onClick={()=>void submit()}><RefreshCw/>{submitting?"Submitting…":"Start authorized refresh"}</button>}</footer>
  </section></div>;
}
