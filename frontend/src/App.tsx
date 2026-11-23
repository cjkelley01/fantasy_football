import {useProjectedSeason} from "./useProjectedSeason";
import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, BarChart3, Database, FlaskConical, LayoutGrid, Menu, Moon, RefreshCw, Settings, ShieldCheck, Sun, Trophy, Users, X } from "lucide-react";
import { api, ApiError } from "./lib/api";
import type { BackgroundRunRecord, HealthResponse, SnapshotSummary } from "./types/api";
import { LeagueOverview, PlayoffRace } from "./LeagueOverview";
import { LeagueMatchups } from "./LeagueMatchups";
import { OpportunityExplorer } from "./OpportunityExplorer";
import { TradeLab } from "./TradeLab";
import { SimulationCenter } from "./SimulationCenter";
import { LeagueRefresh } from "./LeagueRefresh";
import { SeasonArchive } from "./SeasonArchive";
import { TeamOverview } from "./TeamOverview";
import { HomeDashboard } from "./HomeDashboard";
import type { SnapshotRefreshResult } from "./types/api";

import { readWorkspaceRoute, workspacePath, type Destination, type WorkspaceRoute } from "./workspaceRoutes";
type Theme = "dark" | "light";
type NavigationItem = readonly [Destination, string, typeof Users, boolean];
const navigationGroups: readonly { label: string; items: readonly NavigationItem[] }[] = [
  { label: "Home", items: [
    ["home", "Dashboard", Users, false],
  ] },
  { label: "My Team", items: [
    ["team", "Team Overview", Users, false],
  ] },
  { label: "Players & Moves", items: [
    ["opportunities", "Opportunity Explorer", LayoutGrid, false],
    ["trades", "Trade Lab", FlaskConical, false],
  ] },
  { label: "League", items: [
    ["league", "League Overview", Users, false],
    ["playoffs", "Playoff Race", Trophy, false],
    ["matchups", "League Matchups", Activity, false],
    ["insights", "League Insights", BarChart3, true],
  ] },
  { label: "History", items: [
    ["history", "Season Archive", Trophy, false],
  ] },
  { label: "Tools", items: [
    ["simulation", "Simulation Center", Activity, false],
  ] },
];

function initialTheme(): Theme {
  const stored = localStorage.getItem("war-room-theme");
  if (stored === "dark" || stored === "light") return stored;
  return matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [route, setRoute] = useState(() => readWorkspaceRoute(window.location.pathname));
  const [focusArchiveBracket, setFocusArchiveBracket] = useState(false);
  const { destination } = route;
  const archiveYear = route.destination === "history" ? route.year : undefined;
  const [health, setHealth] = useState<HealthResponse>();
  const [snapshots, setSnapshots] = useState<readonly SnapshotSummary[]>([]);
  const [selected, setSelected] = useState<SnapshotSummary>();
  const [runListStatus, setRunListStatus] = useState<"loading" | "loaded" | "unavailable">("loading");
  const [runs, setRuns] = useState<readonly BackgroundRunRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError>();
  const [contextOpen, setContextOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [refreshOpen,setRefreshOpen]=useState(false);
  const [automaticRefresh,setAutomaticRefresh]=useState(false);
  const [profileUnavailable,setProfileUnavailable]=useState(false);
  const [preferredRun,setPreferredRun]=useState<string|null>(null);
  const requestVersion=useRef(0);
  const runRequestVersion=useRef(0);
  const runController=useRef<AbortController | undefined>(undefined);
  const activeController=useRef<AbortController|undefined>(undefined);
  const contextTrigger=useRef<HTMLButtonElement>(null);
  const contextClose=useRef<HTMLButtonElement>(null);
  const menuTrigger=useRef<HTMLButtonElement>(null);
  const menuClose=useRef<HTMLButtonElement>(null);
  const contextPanel=useRef<HTMLElement>(null);
  const menuPanel=useRef<HTMLElement>(null);

  const readRuns = useCallback(async (signal?: AbortSignal) => {
    const version = ++runRequestVersion.current;
    try {
      const value = await api.runs(signal);
      if (!signal?.aborted && version === runRequestVersion.current) {
        setRuns(value);
        setRunListStatus("loaded");
      }
      return value;
    } catch (cause) {
      if (!signal?.aborted && version === runRequestVersion.current) setRunListStatus("unavailable");
      throw cause;
    }
  }, []);
  const retryRuns = useCallback(() => {
    runController.current?.abort();
    const controller = new AbortController();
    runController.current = controller;
    setRunListStatus("loading");
    void readRuns(controller.signal).catch(() => {
      // readRuns reports failures through run-list status.
    });
  }, [readRuns]);

  useEffect(() => {
    retryRuns();
    return () => {
      runRequestVersion.current++;
      runController.current?.abort();
    };
  }, [retryRuns]);

  const load = async (signal?: AbortSignal) => {
    const version=++requestVersion.current;
    setLoading(true); setError(undefined);
    try {
      const [nextHealth, nextSnapshots, profile] = await Promise.all([api.health(signal).then(value=>{if(!signal?.aborted&&version===requestVersion.current)setHealth(value);return value}), api.snapshots(signal), api.refreshProfile(signal).catch(()=>undefined)]);
      if(signal?.aborted||version!==requestVersion.current)return;
      setHealth(nextHealth); setSnapshots(nextSnapshots);
      setAutomaticRefresh(profile?.automatic_refresh_enabled===true);
      setProfileUnavailable(profile===undefined);
      const currentSnapshots=[...(profile?nextSnapshots.filter(item=>item.league_id===profile.league_id&&item.season===profile.season):nextSnapshots)].sort((a,b)=>Date.parse(b.retrieval_finished_at_utc)-Date.parse(a.retrieval_finished_at_utc)||Date.parse(b.published_at_utc)-Date.parse(a.published_at_utc));
      if (currentSnapshots.length) {
        const id = currentSnapshots[0].snapshot_id;
        const detail=await api.snapshot(id,signal);
        if(signal?.aborted||version!==requestVersion.current)return;
        setSelected(detail); localStorage.setItem("war-room-snapshot",id);
      } else setSelected(undefined);
    } catch (cause) {
      if (!(cause instanceof DOMException&&cause.name==="AbortError")&&version===requestVersion.current) {
        setError(cause instanceof ApiError?cause:new ApiError("Unexpected application error.",-1));
      }
    } finally { if(!signal?.aborted&&version===requestVersion.current)setLoading(false); }
  };

  useEffect(()=>{const controller=new AbortController();activeController.current=controller;void load(controller.signal);return()=>{requestVersion.current++;controller.abort();};},[]);
  useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem("war-room-theme",theme);},[theme]);
  useEffect(()=>drawerFocus(contextOpen,contextPanel,contextClose,contextTrigger,setContextOpen),[contextOpen]);
  useEffect(()=>drawerFocus(menuOpen,menuPanel,menuClose,menuTrigger,setMenuOpen),[menuOpen]);

  const chooseSnapshot=async(id:string)=>{
    activeController.current?.abort();const controller=new AbortController();activeController.current=controller;const version=++requestVersion.current;
    setLoading(true);setError(undefined);
    try{const detail=await api.snapshot(id,controller.signal);if(controller.signal.aborted||version!==requestVersion.current)return;setPreferredRun(null);setSelected(detail);localStorage.setItem("war-room-snapshot",id);}
    catch(cause){if(!(cause instanceof DOMException&&cause.name==="AbortError")&&version===requestVersion.current)setError(cause instanceof ApiError?cause:new ApiError("Snapshot could not be loaded.",-1));}
    finally{if(!controller.signal.aborted&&version===requestVersion.current)setLoading(false);}
  };
  useEffect(() => {
    function onPopState() {
      setRoute(readWorkspaceRoute(window.location.pathname));
      setFocusArchiveBracket(false);
      setPreferredRun(null);
      setMenuOpen(false);
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function go(next: WorkspaceRoute, focusBracket = false) {
    const path = workspacePath(next);
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
    setRoute(next);
    setFocusArchiveBracket(focusBracket);
    setMenuOpen(false);
  }
  const navigate=(id:Destination)=>{go({destination:id});if(id!=="playoffs"&&id!=="opportunities")setPreferredRun(null);};
  const openRun=(run:BackgroundRunRecord)=>{if(run.snapshot_id!==selected?.snapshot_id)return;setPreferredRun(run.run_id);go({destination:run.run_type==="baseline"?"playoffs":run.run_type==="action_search"?"opportunities":"trades"});};
  const retry=()=>{retryRuns();activeController.current?.abort();const controller=new AbortController();activeController.current=controller;void load(controller.signal);};
  const projection=useProjectedSeason(selected);
  const activeRuns=runs.filter(run=>["queued","running","cancel_requested"].includes(run.status));
  const acceptPublication=useCallback((publication:SnapshotRefreshResult)=>{const snapshot=publication.snapshot;requestVersion.current++;activeController.current?.abort();setLoading(false);setSnapshots(current=>[snapshot,...current.filter(item=>item.snapshot_id!==snapshot.snapshot_id)]);setSelected(snapshot);setError(undefined);setPreferredRun(null);localStorage.setItem("war-room-snapshot",snapshot.snapshot_id);},[]);

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to analytical content</a>
    <header className="app-header">
      <div className="brand"><div className="brand-mark"><Trophy/></div><div><strong>War Room</strong><span>Fantasy intelligence</span></div></div>
      <div className="header-tools">
        <div className="snapshot-control"><strong>{selected?.league_name??"Current league"}</strong>{selected&&<small>Retrieved {formatDate(selected.retrieval_finished_at_utc)}</small>}</div>
        <button className="icon-button" aria-label="Refresh ESPN league" onClick={()=>setRefreshOpen(true)}><RefreshCw/></button>
        <div className="connection-state" title={health?"Local analytical service connected":loading?"Connecting to local analytical service":"Local analytical service offline"}><span className={`status-dot ${health?"online":""}`}/><span>{health?"Connected":loading?"Connecting":"Offline"}</span></div>
        <div className="run-indicator" aria-live="polite"><Activity/><span>{runListStatus==="loading"?"Loading run list":runListStatus==="unavailable"?"Run list unavailable":activeRuns.length?`${activeRuns.length} active`:"No active runs"}</span></div>
        <button className="icon-button" aria-label={`Use ${theme==="dark"?"light":"dark"} theme`} onClick={()=>setTheme(theme==="dark"?"light":"dark")}>{theme==="dark"?<Sun/>:<Moon/>}</button>
        <button ref={contextTrigger} className="icon-button" aria-label="Open league context" onClick={()=>setContextOpen(true)}><Settings/></button>
        <button ref={menuTrigger} className="icon-button menu-trigger" aria-label="Open navigation" aria-expanded={menuOpen} onClick={()=>setMenuOpen(true)}><Menu/></button>
      </div>
    </header>
    <div className="workspace-layout">
      <nav className="workspace-sidebar" aria-label="Primary navigation">
        <NavButtons destination={destination} navigate={navigate}/>
      </nav>
      <div className="workspace-content">
    <LeagueRefresh open={refreshOpen} onClose={()=>setRefreshOpen(false)} onPublished={acceptPublication} automaticStart={automaticRefresh} leagueName={selected?.league_name} onOpen={()=>setRefreshOpen(true)}/>
    {profileUnavailable&&<p role="status" className="runtime-caveat">Configured ESPN connection is unavailable. Displayed saved data has not been verified as the current configured league; open Refresh details to check configuration.</p>}<main id="main-content" tabIndex={-1}>{runListStatus==="unavailable"&&<p role="status" className="runtime-caveat">The run list could not be loaded. <button onClick={retryRuns}>Retry run list</button></p>}{error&&selected&&<p role="alert" className="runtime-caveat">{error.message} <button onClick={retry}>Try again</button></p>}{destination==="history"?<SeasonArchive initialYear={archiveYear} focusBracket={focusArchiveBracket} onYearChange={year=>go({destination:"history",year})}/>:loading&&!selected?<LoadingState connected={Boolean(health)}/>:error&&!selected?<FailureState error={error} retry={retry} refresh={()=>setRefreshOpen(true)}/>:!selected?<EmptyState refresh={()=>setRefreshOpen(true)}/>:destination==="home"?<HomeDashboard key={selected.snapshot_id} snapshot={selected} projection={projection} runs={runs} runListStatus={runListStatus} openTrades={()=>navigate("trades")} openOpportunities={()=>navigate("opportunities")} openTeam={()=>navigate("team")} openMatchups={()=>navigate("matchups")} openLeague={()=>navigate("league")} openPlayoffRace={()=>navigate("playoffs")} openSimulation={()=>navigate("simulation")}/>:destination==="matchups"?<LeagueMatchups key={selected.snapshot_id} snapshot={selected}/>:destination==="team"?<TeamOverview key={selected.snapshot_id} snapshot={selected}/>:destination==="playoffs"?<PlayoffRace key={selected.snapshot_id} snapshot={selected} runs={runs} runListStatus={runListStatus} preferredRunId={preferredRun} openSimulation={()=>navigate("simulation")}/>:destination==="league"?<LeagueOverview snapshot={selected} projection={projection} openPlayoffRace={()=>navigate("playoffs")} openSimulation={()=>navigate("simulation")} openArchive={year=>{setPreferredRun(null);go({destination:"history",year},true)}}/>:destination==="opportunities"?<OpportunityExplorer snapshot={selected} runs={runs} runListStatus={runListStatus} preferredRunId={preferredRun}/>:destination==="trades"?<TradeLab snapshot={selected} preferredRunId={preferredRun}/>:destination==="simulation"?<SimulationCenter snapshot={selected} runs={runs} runListStatus={runListStatus} loadRuns={readRuns} openResult={openRun}/>:<FutureWorkspace activeRuns={runListStatus==="loaded"?activeRuns:undefined}/>}</main>
      </div>
    </div>
    {(menuOpen||contextOpen)&&<div className="scrim" aria-hidden="true" onClick={()=>{setMenuOpen(false);setContextOpen(false);}}/>}
    {menuOpen&&<aside ref={menuPanel} className="mobile-menu open" role="dialog" aria-modal="true" aria-label="Mobile primary navigation">
      <div className="drawer-head"><div className="brand"><div className="brand-mark"><Trophy/></div><div><strong>War Room</strong><span>Navigation</span></div></div><button ref={menuClose} disabled={!menuOpen} className="icon-button" aria-label="Close navigation" onClick={()=>{setMenuOpen(false);menuTrigger.current?.focus();}}><X/></button></div>
      <nav><NavButtons destination={destination} navigate={navigate}/></nav>
    </aside>}
    <aside ref={contextPanel} className={`context-panel ${contextOpen?"context-open":""}`} role="dialog" aria-modal={contextOpen||undefined} aria-label="League context" aria-hidden={!contextOpen}>
      <div className="context-head"><div><span className="eyebrow">Decision context</span><h2>Source & build</h2></div><button ref={contextClose} disabled={!contextOpen} className="icon-button" aria-label="Close league context" onClick={()=>{setContextOpen(false);contextTrigger.current?.focus();}}><X/></button></div>
      {snapshots.length > 0 && <details><summary>Saved snapshots</summary><label className="snapshot-control"><span>League</span><select aria-label="Active snapshot" aria-busy={loading} value={selected?.snapshot_id??""} disabled={!snapshots.length} onChange={event=>void chooseSnapshot(event.target.value)}>{!selected && <option value="" disabled>Choose a saved snapshot</option>}{snapshots.map(item=><option key={item.snapshot_id} value={item.snapshot_id}>{item.league_name??`League ${item.league_id}`} · {item.season}</option>)}</select></label></details>}
      {selected?<><Info label="Snapshot" value={selected.snapshot_id}/><Info label="Published" value={formatDate(selected.published_at_utc)}/><Info label="Canonical model" value={`Schema v${selected.canonical_schema_version}`}/><Info label="Evidence sources" value={`${selected.source_count} linked`}/><Info label="Simulation horizon" value={`Weeks ${selected.first_simulation_week}–${selected.final_simulation_week}`}/></>:<p>No snapshot selected.</p>}
      {health&&<div className="build-card"><ShieldCheck/><div><strong>API contract {health.api_schema_version}</strong><span>{health.package_version} · {health.build_revision??"revision unavailable"}</span></div></div>}
    </aside>
  </div>;
}

function NavButtons({
  destination, navigate,
}: { destination: Destination; navigate: (id: Destination) => void }) {
  return navigationGroups.filter(group => group.items.length > 0).map(group => (
    <div key={group.label} role="group" aria-label={group.label} className="workspace-nav-group">
      <p>{group.label}</p>
      {group.items.map(([id, label, Icon, future]) => (
        <button key={id} type="button" className={destination === id ? "active" : ""}
          aria-current={destination === id ? "page" : undefined} onClick={() => navigate(id)}>
          <Icon aria-hidden="true"/><span>{label}</span>{future && <small>Future</small>}
        </button>
      ))}
    </div>
  ));
}
function drawerFocus(open:boolean,panel:React.RefObject<HTMLElement|null>,close:React.RefObject<HTMLButtonElement|null>,trigger:React.RefObject<HTMLButtonElement|null>,setOpen:(value:boolean)=>void) {
  if(!open)return;
  const background=document.querySelectorAll<HTMLElement>(".app-header, .workspace-sidebar, #main-content");
  background.forEach(item=>item.inert=true);
  close.current?.focus();
  const handler=(event:KeyboardEvent)=>{
    if(event.key==="Escape"){event.preventDefault();setOpen(false);trigger.current?.focus();return;}
    if(event.key!=="Tab"||!panel.current)return;
    const focusable=[...panel.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')];
    if(!focusable.length){event.preventDefault();return;}
    const first=focusable[0],last=focusable.at(-1)!;
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  };
  document.addEventListener("keydown",handler);
  return()=>{background.forEach(item=>item.inert=false);document.removeEventListener("keydown",handler);if(panel.current?.contains(document.activeElement))trigger.current?.focus();};
}

function FutureWorkspace({activeRuns}:{activeRuns?:readonly BackgroundRunRecord[]}) { return <div className="surface"><section className="hero"><div><span className="eyebrow">Application shell · future workspace</span><h1>League Insights</h1><p>A future analytical workspace; no capability is implied yet.</p></div></section><article className="card placeholder"><div className="placeholder-orbit"><Activity/></div><h2>Foundation connected</h2><p>This destination is intentionally non-operational. No analytical capability is implied by this shell.</p><div className="placeholder-status">{activeRuns?<><span>{activeRuns.length}</span> active backend runs visible</>:"Backend run count unavailable"}</div></article></div>; }
function FailureState({error,retry,refresh}:{error:ApiError;retry:()=>void;refresh:()=>void}) { const disconnected=error.status===0;const storage=error.code==="snapshot_store_unavailable"||error.code==="snapshot_integrity_failure";return <div className="state-card" role="alert"><div className="state-icon">{disconnected?<Database/>:<ShieldCheck/>}</div><span className="eyebrow">{disconnected?"Local service disconnected":"Request failed"}</span><h1>{disconnected?"Reconnect the decision engine":"The War Room could not load"}</h1><p>{error.message}</p>{storage&&<p>Check that the configured snapshot database is accessible. Restore access to that file, then try again. If its location changed, restart War Room with the correct database path.</p>}<button className="primary-button" onClick={retry}><RefreshCw/>Try again</button>{storage&&<button onClick={refresh}>Open refresh</button>}</div>; }
function EmptyState({refresh}:{refresh:()=>void}){return <div className="state-card"><div className="state-icon"><Database/></div><span className="eyebrow">Your league</span><h1>No league data yet</h1><p>Refresh your configured ESPN league to get started.</p><button className="primary-button" onClick={refresh}><RefreshCw/>Refresh ESPN league</button></div>;}
function LoadingState({connected}:{connected:boolean}){return <div className="surface" aria-busy="true" aria-label="Loading War Room"><p role="status">{connected?"Loading saved league snapshot...":"Connecting to the local analytical service..."}</p><div className="skeleton hero-skeleton"/><div className="league-grid"><div className="skeleton card"/><div className="skeleton card"/></div></div>;}
function Info({label,value}:{label:string;value:string}){return <div className="info-row"><span>{label}</span><strong>{value}</strong></div>;}
function formatDate(value:string){return new Intl.DateTimeFormat(undefined,{dateStyle:"medium",timeStyle:"short"}).format(new Date(value));}
