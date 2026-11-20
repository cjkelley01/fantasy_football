import {useEffect,useRef,useState} from "react";
import {CheckCircle2,LoaderCircle,RefreshCw,ShieldAlert,X} from "lucide-react";
import {api,ApiError} from "./lib/api";
import type {LeagueRefreshProfile,SnapshotRefreshRecord,SnapshotRefreshResult,SnapshotRefreshSubmission} from "./types/api";

const terminal=new Set(["completed","cancelled","failed","interrupted"]);
const aborted=(cause:unknown)=>cause instanceof DOMException&&cause.name==="AbortError";
const message=(cause:unknown,fallback:string)=>cause instanceof ApiError?cause.message:fallback;
type Props={open:boolean;onClose:()=>void;onPublished:(result:SnapshotRefreshResult)=>void;automaticStart?:boolean;leagueName?:string|null;onOpen?:()=>void};
function queued(submitted:SnapshotRefreshSubmission,profile:LeagueRefreshProfile):SnapshotRefreshRecord{return {api_schema_version:submitted.api_schema_version,refresh_id:submitted.refresh_id,status:submitted.status,created_at_utc:submitted.created_at_utc,stage:submitted.status==="completed"?"completed":"queued",league_id:profile.league_id,season:profile.season,first_simulation_week:null,started_at_utc:null,finished_at_utc:null,snapshot_id:null,publication_committed:submitted.status==="completed",error_code:null,error_message:null}}

export function LeagueRefresh({open,onClose,onPublished,automaticStart=false,leagueName,onOpen}:Props){
  const [profile,setProfile]=useState<LeagueRefreshProfile>();
  const [record,setRecord]=useState<SnapshotRefreshRecord>();
  const [result,setResult]=useState<SnapshotRefreshResult>();
  const [error,setError]=useState<string>();
  const [submitting,setSubmitting]=useState(false);
  const automaticAttempted=useRef(false);
  const discoveryVersion=useRef(0),pollVersion=useRef(0),resultVersion=useRef(0),submitVersion=useRef(0);

  useEffect(()=>{
    if(!open&&!automaticStart)return;
    const controller=new AbortController(),version=++discoveryVersion.current;
    setError(undefined);
    void Promise.all([api.refreshProfile(controller.signal),api.currentRefresh(controller.signal)]).then(async([nextProfile,current])=>{
      if(controller.signal.aborted||version!==discoveryVersion.current)return;
      setProfile(nextProfile);setRecord(current??undefined);
      if(automaticStart&&nextProfile.automatic_refresh_enabled&&!automaticAttempted.current){
        automaticAttempted.current=true;
        if(current&&!terminal.has(current.status))return;
        const submitted=await api.submitRefresh({api_schema_version:"1.0",profile_id:nextProfile.profile_id,league_id:nextProfile.league_id,season:nextProfile.season,first_simulation_week:null,startup_refresh:true},controller.signal);
        const next=terminal.has(submitted.status)&&submitted.status!=="completed"?await api.refresh(submitted.refresh_id,controller.signal):queued(submitted,nextProfile);
        if(!controller.signal.aborted&&version===discoveryVersion.current)setRecord(next);
      }
    }).catch(cause=>{if(!aborted(cause)&&version===discoveryVersion.current)setError(message(cause,"Configured league could not be loaded."));});
    return()=>{discoveryVersion.current++;controller.abort()};
  },[open,automaticStart]);

  useEffect(()=>{
    if(!record||terminal.has(record.status))return;
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
  },[record?.refresh_id,record?.status]);

  useEffect(()=>{
    if(record?.status!=="completed"||result?.refresh.refresh_id===record.refresh_id)return;
    const controller=new AbortController(),refreshId=record.refresh_id,version=++resultVersion.current;
    void api.refreshResult(refreshId,controller.signal).then(value=>{
      if(controller.signal.aborted||version!==resultVersion.current||value.refresh.refresh_id!==refreshId)return;
      setResult(value);onPublished(value);
    }).catch(cause=>{if(!aborted(cause)&&version===resultVersion.current)setError(message(cause,"Updated league could not be loaded."));});
    return()=>{resultVersion.current++;controller.abort()};
  },[record?.refresh_id,record?.status,result?.refresh.refresh_id,onPublished]);

  const submit=async()=>{
    if(!profile||submitting)return;
    const controller=new AbortController(),version=++submitVersion.current;
    setSubmitting(true);setError(undefined);setResult(undefined);
    try{
      const submitted=await api.submitRefresh({api_schema_version:"1.0",profile_id:profile.profile_id,league_id:profile.league_id,season:profile.season,first_simulation_week:null},controller.signal);
      if(!controller.signal.aborted&&version===submitVersion.current)setRecord(queued(submitted,profile));
    }catch(cause){if(!aborted(cause)&&version===submitVersion.current)setError(message(cause,"Refresh could not be started."));}
    finally{if(version===submitVersion.current)setSubmitting(false);}
  };
  const cancel=async()=>{if(!record)return;try{setRecord(await api.cancelRefresh(record.refresh_id))}catch(cause){setError(message(cause,"Refresh could not be cancelled."));}};
  const active=record&&!terminal.has(record.status);
  if(!open)return automaticStart&&(active||error||record?.status==="failed")?<aside className="runtime-caveat" role="status">{active?"Updating ESPN league information...":error??record?.error_message??"Refresh failed; showing previously validated data."}{onOpen&&<button onClick={onOpen}>Refresh details</button>}</aside>:null;
  const snapshot=result?.snapshot;
  const name=snapshot?.league_name||leagueName||"Configured ESPN league";
  return <div className="refresh-scrim" role="presentation"><section className="refresh-panel" role="dialog" aria-modal="true" aria-label="Refresh ESPN league"><header><div><span className="eyebrow">ESPN league</span><h2>Refresh league</h2></div><button className="icon-button" aria-label="Close refresh" onClick={onClose}><X/></button></header>
    <strong>{name}</strong><p>War Room retrieves your latest ESPN league information and automatically determines the current simulation period.</p>
    {!profile&&!error&&<p aria-busy="true"><LoaderCircle className="spin"/> Loading configured league...</p>}
    {record&&<div className={`refresh-status status-${record.status}`} aria-live="polite">{active?<LoaderCircle className="spin"/>:record.status==="completed"?<CheckCircle2/>:<ShieldAlert/>}<div><strong>{record.status.replaceAll("_"," ")}</strong>{record.error_message&&<small>{record.error_message}</small>}</div></div>}
    {active&&<p>You can close this view while the update continues.</p>}
    {snapshot&&<div className="refresh-result"><span>Last updated: {new Date(snapshot.retrieval_finished_at_utc).toLocaleString()}</span><span>Current scoring week: {snapshot.live_scoring?.current_scoring_period??"Unavailable"}</span><span>Simulation horizon: weeks {snapshot.first_simulation_week} to {snapshot.final_simulation_week}</span>{snapshot.live_scoring?.current_week_limitations?.map(item=><p key={item}>{item}</p>)}<details><summary>Advanced details</summary><p>Snapshot: {snapshot.snapshot_id}</p>{result?.validation_findings.map(item=><p key={item}>{item}</p>)}{result?.unresolved_capabilities.map(item=><p key={item}>{item}</p>)}</details></div>}
    {profile&&<details><summary>Connection details</summary><p>League ID: {profile.league_id}; season: {profile.season}</p><p>Updates are read-only. Private credentials remain on this local server.</p></details>}
    {error&&<p className="refresh-error" role="alert">{error}</p>}
    <footer>{active&&record.stage!=="publishing"&&record.stage!=="verifying"?<button onClick={()=>void cancel()}>Cancel update</button>:<button className="primary-button" disabled={!profile||submitting||!!active} onClick={()=>void submit()}><RefreshCw/>{submitting?"Starting...":"Refresh now"}</button>}</footer>
  </section></div>;
}
