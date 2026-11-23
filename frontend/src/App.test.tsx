import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { App } from "./App";
import { activeRun, health, snapshot } from "./test/fixtures";

function response(body: unknown, status=200) { return Promise.resolve(new Response(JSON.stringify(body), { status, headers:{"Content-Type":"application/json"} })); }
function mockApi(options: { snapshots?: unknown; detail?: unknown; runs?: unknown; history?: unknown; managers?: unknown; recentResults?: unknown; leagueResults?: unknown } = {}) {
  vi.spyOn(globalThis,"fetch").mockImplementation((input) => {
    const url=String(input);
    if(url.includes("/health")) return response(health);
    if(url.endsWith("/analyses/projected-baseline")) return response(outlook(snapshot));
    if(url.endsWith("/managers")) return response(options.managers ?? null);
    if(url.endsWith("/recent-results")) return response(options.recentResults ?? null);
    if(url.endsWith("/league-results")) return response(options.leagueResults ?? null);
    if(url.includes(`/snapshots/${snapshot.snapshot_id}`)) return response(options.detail ?? snapshot);
    if(url.endsWith("/snapshots")) return response(options.snapshots ?? [snapshot]);
    if(url.includes("/runs")) return response(options.runs ?? [activeRun]);
    if(url.endsWith("/history/cgl")) return response(options.history ?? null);
    throw new Error(`Unexpected URL ${url}`);
  });
}
function outlook(value: typeof snapshot, champion = value.teams[0].team_id) {
  return {snapshot_id: value.snapshot_id, canonical_sha256: value.canonical_sha256,
    champion_team_id: champion, status: "complete", teams: [], weeks: [], postseason: [], transactions: [],
    observed_scores: [], assumptions: [], unresolved_capabilities: [], missing_projection_count: 0};
}
async function openSnapshots(user:ReturnType<typeof userEvent.setup>){await user.click(screen.getByRole("button",{name:"Open league context"}));await user.click(screen.getByText("Saved snapshots"));}

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

test("opens a bookmarked archive year and restores it with browser Back", async () => {
  const team = { team_id: 1, team_name: "Archived Harbor", final_rank: 1,
    regular_season: { wins: 1, losses: 0, ties: 0, points_for: 12, points_against: 0 } };
  mockApi({ snapshots: [], runs: [], history: {
    league_id: 42, source: { revision: "a".repeat(40), sha256: "b".repeat(64) },
    seasons: [2024, 2025].map(season => ({ season, champion_team_id: 1, teams: [team], matchups: [] })),
  } });
  window.history.replaceState(null, "", "/history/seasons/2024");
  const user = userEvent.setup();
  const app = render(<App />);
  expect(await screen.findByRole("heading", { name: "2024 final standings" })).toBeInTheDocument();
  await user.selectOptions(screen.getByRole("combobox", { name: "Season" }), "2025");
  expect(window.location.pathname).toBe("/history/seasons/2025");
  window.history.back();
  expect(await screen.findByRole("heading", { name: "2024 final standings" })).toBeInTheDocument();
  window.history.forward();
  expect(await screen.findByRole("heading", { name: "2025 final standings" })).toBeInTheDocument();
  app.unmount();
  render(<App />);
  expect(await screen.findByRole("heading", { name: "2025 final standings" })).toBeInTheDocument();
});

test("browses verified results and scheduled pairings without mixing their scores", async () => {
  mockApi({
    detail: { ...snapshot, live_scoring: { current_scoring_period: 8 } },
    leagueResults: [{
      snapshot_id: snapshot.snapshot_id, source_id: "fixture", matchup_period_id: 7, scoring_weeks: [7],
      results: [{ matchup_id: 17, home_team_id: 1, away_team_id: 2, home_score: 0, away_score: 20.25, winner: "AWAY" }],
    }],
  });
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "Home" });
  await user.click(screen.getByRole("button", { name: "League Matchups" }));
  const picker = await screen.findByRole("combobox", { name: "Matchup period" });
  expect(picker).toHaveValue("8");
  expect(screen.getByRole("table", { name: "Scheduled pairings" })).toBeInTheDocument();
  await user.selectOptions(picker, await screen.findByRole("option", { name: "Week 7" }));
  expect(within(screen.getByRole("table", { name: "Reconciled scores" })).getByText("North Harbor · 0")).toBeInTheDocument();
  await user.selectOptions(picker, "8");
  expect(screen.queryByText("Sunday Union · 20.25")).not.toBeInTheDocument();
});

test("remembers a manager perspective without changing the active snapshot", async () => {
  const key = `war-room-manager:${snapshot.league_id}:${snapshot.season}`;
  localStorage.removeItem(key);
  mockApi({ detail: { ...snapshot, live_scoring: { current_scoring_period: 8 } },
    managers: { snapshot_id: snapshot.snapshot_id, unresolved_owner_count: 1,
    choices: [{ manager_id: "manager-a", display_name: "Avery", team_id: 1, is_primary: true }] } });
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "Home" });
  const selector = await screen.findByRole("combobox", { name: "Manager perspective" });
  const value = JSON.stringify(["manager-a", 1]);
  expect(selector).toHaveValue("");
  await user.selectOptions(selector, value);
  expect(screen.getByRole("heading", { name: "North Harbor" })).toBeInTheDocument();
  expect(screen.getByText("Home vs. Red Zone Club")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "View team roster" }));
  expect(await screen.findByText("Sanitized Player")).toBeInTheDocument();
  expect(await screen.findByRole("combobox", { name: "Manager perspective" })).toHaveValue(value);
  await user.click(screen.getByRole("button", { name: "Dashboard" }));
  expect(await screen.findByRole("combobox", { name: "Manager perspective" })).toHaveValue(value);
  await user.click(screen.getByRole("button", { name: "View league matchups" }));
  expect(screen.getByRole("heading", { name: "League Matchups" })).toBeInTheDocument();
  expect(localStorage.getItem("war-room-snapshot")).toBe(snapshot.snapshot_id);
  localStorage.removeItem(key);
});

test("opens the matching historical bracket without changing the active snapshot", async () => {
  const records = {
    league_id: snapshot.league_id,
    source: { revision: "a".repeat(40), sha256: "b".repeat(64) },
    seasons: [2025, 2026].map(season => ({
      season, champion_team_id: 1, matchups: [],
      teams: [{ team_id: 1, team_name: "Archived Harbor", final_rank: 1,
        regular_season: { wins: 1, losses: 0, ties: 0, points_for: 12, points_against: 0 } }],
    })),
  };
  mockApi({
    history: records, detail: { ...snapshot, live_scoring: { current_scoring_period: 8 } },
    recentResults: {
      snapshot_id: snapshot.snapshot_id, source_id: "fixture", matchup_period_id: 7, scoring_weeks: [7],
      results: [{ matchup_id: 17, home_team_id: 1, away_team_id: 2, home_score: 0, away_score: 20.25, winner: "AWAY" }],
    },
  });
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", {name:"Home"});
  await user.click(screen.getByRole("button", {name:"League Overview"}));
  expect(await screen.findByText("Red Zone Club at North Harbor")).toBeInTheDocument();
  const resultsTable = within(await screen.findByRole("table", { name: "Recent results" }));
  expect(resultsTable.getByText("North Harbor · 0")).toBeInTheDocument();
  expect(resultsTable.getByText("Sunday Union · 20.25")).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "View 2025 championship bracket" }));
  expect(await screen.findByRole("heading", { name: "Championship bracket" })).toHaveFocus();
  expect(screen.getByRole("combobox", { name: "Season" })).toHaveValue("2025");
  expect(localStorage.getItem("war-room-snapshot")).toBe(snapshot.snapshot_id);
});

test("keeps history for a different league out of the current league overview", async () => {
  mockApi({ history: { league_id: 99, seasons: [] } });
  render(<App />);
  await screen.findByRole("heading", {name:"Home"});
  await userEvent.setup().click(screen.getByRole("button", {name:"League Overview"}));
  expect(await screen.findByText("No earlier completed season is available for this league.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /championship bracket/i })).not.toBeInTheDocument();
});

test("groups existing workspaces and keeps the archive reachable without snapshots", async () => {
  mockApi({ snapshots: [], runs: [] });
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "No league data yet" });
  const nav = within(screen.getByRole("navigation", { name: "Primary navigation" }));
  const history = within(nav.getByRole("group", { name: "History" }));
  const tools = within(nav.getByRole("group", { name: "Tools" }));
  const moves = within(nav.getByRole("group", { name: "Players & Moves" }));
  expect(tools.getByRole("button", { name: "Simulation Center" })).toBeInTheDocument();
  expect(moves.getByRole("button", { name: "Trade Lab" })).toBeInTheDocument();
  const archive = history.getByRole("button", { name: "Season Archive" });
  await user.click(archive);
  expect(await screen.findByText("CGL history is not configured.")).toBeInTheDocument();
  expect(archive).toHaveAttribute("aria-current", "page");
});

test("loads the source-backed overview without fabricating analytics", async()=>{ mockApi(); render(<App/>); await screen.findByRole("heading",{name:"Home"}); await userEvent.setup().click(screen.getByRole("button",{name:"League Overview"})); expect(await screen.findByRole("heading",{name:"Sanitized Gridiron League"})).toBeInTheDocument(); expect(screen.getByRole("button",{name:"Open Playoff Race"})).toBeInTheDocument(); expect(screen.getByRole("heading",{name:"Current league standings"})).toBeInTheDocument(); expect(screen.queryByText("Championship probability")).not.toBeInTheDocument(); expect(screen.getByText("1 active")).toBeInTheDocument(); });
test("selects and persists another snapshot detail", async()=>{ const user=userEvent.setup(); const second={...snapshot,snapshot_id:"snapshot-beta",league_name:"Second Sanitized League",teams:[]}; vi.spyOn(globalThis,"fetch").mockImplementation((input)=>{ const url=String(input); if(url.includes("/health"))return response(health); if(url.endsWith("/snapshots"))return response([snapshot,second]); if(url.includes("snapshot-beta"))return response(second); if(url.includes("snapshot-alpha"))return response(snapshot); if(url.includes("/runs"))return response([]); throw new Error(url); }); render(<App/>); await screen.findByRole("heading",{name:"Home"}); await openSnapshots(user);await user.selectOptions(screen.getByRole("combobox",{name:"Active snapshot"}),"snapshot-beta");await user.keyboard("{Escape}"); expect(await screen.findByText("Second Sanitized League", {selector: "header strong"})).toBeInTheDocument(); expect(localStorage.getItem("war-room-snapshot")).toBe("snapshot-beta"); });
test("latest snapshot response wins a rapid selection race", async()=>{ const beta={...snapshot,snapshot_id:"snapshot-beta",league_name:"Slow Beta"}; const gamma={...snapshot,snapshot_id:"snapshot-gamma",league_name:"Fast Gamma"}; let resolveBeta:(value:Response)=>void=()=>{}; const betaResponse=new Promise<Response>(resolve=>{resolveBeta=resolve;}); vi.spyOn(globalThis,"fetch").mockImplementation((input)=>{ const url=String(input); if(url.includes("/health"))return response(health); if(url.endsWith("/snapshots"))return response([snapshot,beta,gamma]); if(url.includes("snapshot-alpha"))return response(snapshot); if(url.includes("snapshot-beta"))return betaResponse; if(url.includes("snapshot-gamma"))return response(gamma); if(url.includes("/runs"))return response([]); throw new Error(url); }); render(<App/>); await screen.findByRole("heading",{name:"Home"}); await openSnapshots(userEvent.setup());const selector=screen.getByRole("combobox",{name:"Active snapshot"}); fireEvent.change(selector,{target:{value:"snapshot-beta"}}); fireEvent.change(selector,{target:{value:"snapshot-gamma"}}); expect(await screen.findByText("Fast Gamma", {selector: "header strong"})).toBeInTheDocument(); resolveBeta(new Response(JSON.stringify(beta),{headers:{"Content-Type":"application/json"}})); await waitFor(()=>expect(screen.queryByRole("heading",{name:"Slow Beta"})).not.toBeInTheDocument()); expect(localStorage.getItem("war-room-snapshot")).toBe("snapshot-gamma"); });
test("unmount aborts effect-owned requests", ()=>{ const signals:AbortSignal[]=[]; vi.spyOn(globalThis,"fetch").mockImplementation((_input,init)=>{ if(init?.signal)signals.push(init.signal); return new Promise(()=>{}); }); const {unmount}=render(<App/>); unmount(); expect(signals.length).toBeGreaterThan(0); expect(signals.every(signal=>signal.aborted)).toBe(true); });
test("navigates by keyboard to the final-state Trade Lab", async()=>{ mockApi(); const user=userEvent.setup(); render(<App/>); await screen.findByRole("heading",{name:"Home"}); const trade=screen.getAllByRole("button",{name:"Trade Lab"})[0]; trade.focus(); await user.keyboard("{Enter}"); expect(screen.getByRole("heading",{name:/Build the exchange/})).toBeInTheDocument(); expect(screen.getByText(/No ESPN execution or trade acceptance/)).toBeInTheDocument(); });
test("navigates to the functional Simulation Center",async()=>{mockApi({runs:[]});const user=userEvent.setup();render(<App/>);await screen.findByRole("heading",{name:"Home"});await user.click(screen.getAllByRole("button",{name:"Simulation Center"})[0]);expect(screen.getByRole("heading",{name:"Simulation Center"})).toBeInTheDocument();expect(screen.getByRole("button",{name:"Submit baseline"})).toBeInTheDocument();expect(screen.queryByText(/intentionally non-operational/)).not.toBeInTheDocument()});
test("persists theme preference",async()=>{ mockApi(); const user=userEvent.setup(); render(<App/>); await screen.findByRole("heading",{name:"Home"}); await user.click(screen.getByRole("button",{name:"Use light theme"})); expect(document.documentElement).toHaveAttribute("data-theme","light"); expect(localStorage.getItem("war-room-theme")).toBe("light"); });
test("renders empty snapshot state",async()=>{ mockApi({snapshots:[]}); render(<App/>); expect(await screen.findByRole("heading",{name:"No league data yet"})).toBeInTheDocument(); });
test("publishes a configured ESPN profile without exposing credentials",async()=>{const user=userEvent.setup();let statusCalls=0;vi.spyOn(globalThis,"fetch").mockImplementation((input,init)=>{const url=String(input);if(url.includes("/health"))return response(health);if(url.endsWith("/snapshots"))return response([]);if(url.includes("/runs"))return response([]);if(url.endsWith("/refresh/profile"))return response({profile_id:"configured_local_league",league_id:42,season:2026,credential_mode:"private_credentials_configured",manual_rule_configuration_available:false});if(url.endsWith("/refreshes/current"))return response(null);if(url.endsWith("/refreshes")&&init?.method==="POST")return response({api_schema_version:"1.0",refresh_id:"refresh-1",status:"queued",created_at_utc:"2026-10-31T12:00:00Z"},202);if(url.endsWith("/refreshes/refresh-1")){statusCalls++;return response({api_schema_version:"1.0",refresh_id:"refresh-1",status:"completed",stage:"completed",league_id:42,season:2026,first_simulation_week:8,created_at_utc:"2026-10-31T12:00:00Z",started_at_utc:"2026-10-31T12:00:01Z",finished_at_utc:"2026-10-31T12:00:02Z",snapshot_id:snapshot.snapshot_id,publication_committed:true,error_code:null,error_message:null})}if(url.endsWith("/refreshes/refresh-1/result"))return response({api_schema_version:"1.0",refresh:{api_schema_version:"1.0",refresh_id:"refresh-1",status:"completed",stage:"completed",league_id:42,season:2026,first_simulation_week:8,created_at_utc:"2026-10-31T12:00:00Z",started_at_utc:"2026-10-31T12:00:01Z",finished_at_utc:"2026-10-31T12:00:02Z",snapshot_id:snapshot.snapshot_id,publication_committed:true,error_code:null,error_message:null},snapshot,validation_findings:["Teams reconciled: 8"],unresolved_capabilities:[]});throw new Error(url)});render(<App/>);await screen.findByRole("heading",{name:"No league data yet"});await user.click(screen.getAllByRole("button",{name:"Refresh ESPN league"})[0]);expect(await screen.findByText("War Room retrieves your latest ESPN league information and automatically determines the current simulation period.")).toBeInTheDocument();expect(screen.queryByText(/cookie|token/i)).not.toBeInTheDocument();await user.click(screen.getByRole("button",{name:"Refresh now"}));expect(await screen.findByRole("heading",{name:"Home"},{timeout:2500})).toBeInTheDocument();expect(statusCalls).toBeGreaterThan(0);expect(localStorage.getItem("war-room-snapshot")).toBe(snapshot.snapshot_id)});
test("requests automatic boundary detection unless the advanced override is enabled",async()=>{const user=userEvent.setup();let submitted:unknown;vi.spyOn(globalThis,"fetch").mockImplementation((input,init)=>{const url=String(input);if(url.includes("/health"))return response(health);if(url.endsWith("/snapshots"))return response([]);if(url.includes("/runs"))return response([]);if(url.endsWith("/refresh/profile"))return response({profile_id:"configured_local_league",league_id:42,season:2026,credential_mode:"public",manual_rule_configuration_available:false});if(url.endsWith("/refreshes/current"))return response(null);if(url.endsWith("/refreshes")&&init?.method==="POST"){submitted=JSON.parse(String(init.body));return response({api_schema_version:"1.0",refresh_id:"refresh-auto",status:"queued",created_at_utc:"2026-10-31T12:00:00Z"},202)}if(url.endsWith("/refreshes/refresh-auto"))return new Promise(()=>{});throw new Error(url)});render(<App/>);await screen.findByRole("heading",{name:"No league data yet"});await user.click(screen.getAllByRole("button",{name:"Refresh ESPN league"})[0]);await screen.findByText("War Room retrieves your latest ESPN league information and automatically determines the current simulation period.");expect(screen.queryByRole("spinbutton",{name:"Manual first unstarted scoring week"})).not.toBeInTheDocument();await user.click(screen.getByRole("button",{name:"Refresh now"}));await waitFor(()=>expect(submitted).toMatchObject({first_simulation_week:null}));});
test("closes and rediscovers an active server refresh without cancelling it",async()=>{const user=userEvent.setup();const active={api_schema_version:"1.0",refresh_id:"active-refresh",status:"running",stage:"retrieving_and_validating",league_id:42,season:2026,first_simulation_week:8,created_at_utc:"2026-10-31T12:00:00Z",started_at_utc:"2026-10-31T12:00:01Z",finished_at_utc:null,snapshot_id:null,publication_committed:false,error_code:null,error_message:null};let discovery=0,cancelled=false;vi.spyOn(globalThis,"fetch").mockImplementation((input,init)=>{const url=String(input);if(url.includes("/health"))return response(health);if(url.endsWith("/snapshots"))return response([]);if(url.includes("/runs"))return response([]);if(url.endsWith("/refresh/profile"))return response({profile_id:"configured_local_league",league_id:42,season:2026,credential_mode:"public",manual_rule_configuration_available:false});if(url.endsWith("/refreshes/current")){discovery++;return response(active)}if(url.endsWith("/refreshes/active-refresh/cancel")&&init?.method==="POST"){cancelled=true;return response({...active,status:"cancel_requested"})}if(url.endsWith("/refreshes/active-refresh"))return new Promise(()=>{});throw new Error(url)});render(<App/>);await screen.findByRole("heading",{name:"No league data yet"});await user.click(screen.getAllByRole("button",{name:"Refresh ESPN league"})[0]);expect(await screen.findByText("running")).toBeInTheDocument();await user.click(screen.getByRole("button",{name:"Close refresh"}));expect(screen.queryByRole("dialog",{name:"Refresh ESPN league"})).not.toBeInTheDocument();expect(cancelled).toBe(false);await user.click(screen.getAllByRole("button",{name:"Refresh ESPN league"})[0]);expect(await screen.findByText("running")).toBeInTheDocument();expect(discovery).toBeGreaterThanOrEqual(2)});
test("renders a disconnected state and retries",async()=>{ vi.spyOn(globalThis,"fetch").mockRejectedValue(new TypeError("offline")); render(<App/>); expect(await screen.findByRole("heading",{name:"Reconnect the decision engine"})).toBeInTheDocument(); expect(screen.getByRole("button",{name:/try again/i})).toBeInTheDocument(); });
test("renders typed API errors without fabricated data",async()=>{ vi.spyOn(globalThis,"fetch").mockImplementation((input)=>String(input).includes("health") ? response(health) : response({api_schema_version:"1.0",error:{code:"snapshot_store_error",message:"Snapshot store unavailable.",category:"storage",context:{}}},503)); render(<App/>); expect(await screen.findByText("Snapshot store unavailable.")).toBeInTheDocument(); expect(screen.queryByText("North Harbor")).not.toBeInTheDocument(); });
test("falls back from a deleted persisted snapshot",async()=>{ localStorage.setItem("war-room-snapshot","deleted"); mockApi(); render(<App/>); await screen.findByRole("heading",{name:"Home"}); expect(localStorage.getItem("war-room-snapshot")).toBe(snapshot.snapshot_id); });
test("core shell has no detectable accessibility violations",async()=>{ mockApi(); const {container}=render(<App/>); await screen.findByRole("heading",{name:"Home"}); const result=await axe.run(container); expect(result.violations).toEqual([]); });
test("context drawer receives focus and restores it on escape",async()=>{ mockApi(); const user=userEvent.setup(); render(<App/>); await screen.findByRole("heading",{name:"Home"}); const trigger=screen.getByRole("button",{name:"Open league context"}); await user.click(trigger); expect(screen.getByRole("button",{name:"Close league context"})).toHaveFocus(); await user.keyboard("{Escape}"); expect(trigger).toHaveFocus(); });
test("context drawer traps focus, makes the background inert, and restores focus",async()=>{mockApi();const user=userEvent.setup();const{container}=render(<App/>);await screen.findByRole("heading",{name:"Home"});const trigger=screen.getByRole("button",{name:"Open league context"});await user.click(trigger);const close=screen.getByRole("button",{name:"Close league context"});expect(document.querySelector("#main-content")).toHaveProperty("inert",true);await user.tab();expect(screen.getByText("Saved snapshots")).toHaveFocus();await user.keyboard("{Shift>}{Tab}{/Shift}");expect(close).toHaveFocus();fireEvent.click(container.querySelector(".scrim")!);expect(document.querySelector("#main-content")).toHaveProperty("inert",false);expect(trigger).toHaveFocus()});
test("responsive menu exposes reconciled navigation and restores focus",async()=>{ mockApi(); const user=userEvent.setup(); render(<App/>); await screen.findByRole("heading",{name:"Home"}); const trigger=screen.getByRole("button",{name:"Open navigation"}); await user.click(trigger); expect(screen.getByLabelText("Mobile primary navigation")).toBeInTheDocument(); expect(screen.getAllByRole("button",{name:"League Overview"})).toHaveLength(2); expect(screen.getAllByRole("button",{name:"Simulation Center"})).toHaveLength(2); expect(screen.getByRole("button",{name:"Close navigation"})).toHaveFocus(); await user.keyboard("{Escape}"); expect(trigger).toHaveFocus(); });
test("snapshot changes isolate observed league records",async()=>{ const user=userEvent.setup(); const other={...snapshot,snapshot_id:"snapshot-other",league_id:99,league_name:"Other League",teams:[{...snapshot.teams[2],team_id:3,display_name:"Other Default",standing:1},{...snapshot.teams[1],team_id:2,display_name:"Unrelated Two",standing:2}]}; vi.spyOn(globalThis,"fetch").mockImplementation((input)=>{const url=String(input);if(url.includes("/health"))return response(health);if(url.endsWith("/snapshots"))return response([snapshot,other]);if(url.includes("snapshot-other"))return response(other);if(url.includes("snapshot-alpha"))return response(snapshot);if(url.includes("/runs"))return response([]);throw new Error(url);});render(<App/>);await screen.findByRole("heading",{name:"Home"});await openSnapshots(user);await user.selectOptions(screen.getByRole("combobox",{name:"Active snapshot"}),"snapshot-other");await user.keyboard("{Escape}");expect(await screen.findByText("Other League", {selector: "header strong"})).toBeInTheDocument();await user.click(screen.getByRole("button",{name:"League Overview"}));expect(await screen.findByText("Other Default")).toBeInTheDocument();expect(screen.queryByText("North Harbor")).not.toBeInTheDocument();});


test("startup prefers latest configured-league evidence over saved selection and publication order",async()=>{
 const latest={...snapshot,snapshot_id:"latest",retrieval_finished_at_utc:"2026-11-20T12:00:00Z",published_at_utc:"2026-11-20T12:01:00Z",league_name:"Latest configured league"};
 const old={...snapshot,published_at_utc:"2026-11-20T13:00:00Z"};
 localStorage.setItem("war-room-snapshot",old.snapshot_id);
 vi.spyOn(globalThis,"fetch").mockImplementation(input=>{
  const url=String(input);
  if(url.includes("/health"))return response(health);
  if(url.endsWith("/snapshots"))return response([old,latest]);
  if(url.endsWith("/snapshots/latest"))return response(latest);
  if(url.includes("/runs"))return response([]);
  if(url.endsWith("/refresh/profile"))return response({profile_id:"configured_local_league",league_id:snapshot.league_id,season:snapshot.season,credential_mode:"public",manual_rule_configuration_available:false,automatic_refresh_enabled:false});
  throw new Error(url);
 });
 render(<App/>);
 expect(await screen.findByText("Latest configured league", {selector: "header strong"})).toBeInTheDocument();
 expect(localStorage.getItem("war-room-snapshot")).toBe("latest");
});

test("saved snapshots remain selectable when the configured season has no match", async () => {
  const user = userEvent.setup();
  vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const url = String(input);
    if (url.endsWith("/health")) return response(health);
    if (url.endsWith("/snapshots")) return response([snapshot]);
    if (url.endsWith("/snapshots/" + snapshot.snapshot_id)) return response(snapshot);
    if (url.includes("/runs?")) return response([]);
    if (url.endsWith("/refresh/profile")) return response({
      profile_id: "configured_local_league",
      league_id: snapshot.league_id,
      season: snapshot.season + 1,
      credential_mode: "public",
      manual_rule_configuration_available: false,
      automatic_refresh_enabled: false,
    });
    throw new Error(url);
  });
  render(<App/>);
  await screen.findByRole("heading", {name: "No league data yet"});
  await openSnapshots(user);
  const selector = screen.getByRole("combobox", {name: "Active snapshot"});
  expect(selector).toHaveValue("");
  await user.selectOptions(selector, snapshot.snapshot_id);
  await user.keyboard("{Escape}");
  expect(await screen.findByText(snapshot.league_name!, {selector: "header strong"})).toBeInTheDocument();
});

test("startup storage failure offers refresh and retry against the same store", async () => {
  const user = userEvent.setup();
  let restored = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const url = String(input);
    if (url.endsWith("/health")) return response(health);
    if (url.endsWith("/snapshots")) return restored ? response([snapshot]) : response({
      api_schema_version: "1.0",
      error: {code: "snapshot_store_unavailable", message: "The configured snapshot store is unavailable.",
        category: "invalid_request", context: {}},
    }, 422);
    if (url.endsWith("/snapshots/" + snapshot.snapshot_id)) return response(snapshot);
    if (url.includes("/runs?")) return response([]);
    if (url.endsWith("/refresh/profile")) return response({
      profile_id: "configured_local_league", league_id: snapshot.league_id, season: snapshot.season,
      credential_mode: "public", manual_rule_configuration_available: false,
    });
    if (url.endsWith("/refreshes/current")) return response(null);
    throw new Error(url);
  });
  render(<App/>);
  await screen.findByText("The configured snapshot store is unavailable.");
  expect(screen.getByText("Connected")).toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "Open refresh"}));
  expect(await screen.findByRole("dialog", {name: "Refresh ESPN league"})).toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "Close refresh"}));
  restored = true;
  await user.click(screen.getByRole("button", {name: "Try again"}));
  expect(await screen.findByText(snapshot.league_name!, {selector: "header strong"})).toBeInTheDocument();
});

test("Home automatically prepares one outlook and reuses it in League without starting jobs", async () => {
  const user = userEvent.setup();
  mockApi({runs: []});
  const fetch = vi.mocked(globalThis.fetch);
  render(<App/>);
  expect(await screen.findByRole("heading", {name:"Home"})).toBeInTheDocument();
  expect(await screen.findByText("Projected champion")).toBeInTheDocument();
  expect(screen.getByRole("button", {name:"Dashboard"})).toHaveAttribute("aria-current", "page");
  await user.click(screen.getByRole("button", {name:"View projected season details"}));
  expect(await screen.findByText(/Champion in this projected season/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", {name:"Dashboard"}));
  expect(screen.getByText("Projected champion")).toBeInTheDocument();
  const submissions = fetch.mock.calls.filter(([,init]) => init?.method === "POST");
  expect(submissions.map(([url])=>String(url))).toEqual(["/api/v1/analyses/projected-baseline"]);
});

test.each(["completed", "failed"] as const)("startup observes existing ESPN refresh and preserves saved results until publication (%s)", async (status) => {
  const published = {...snapshot, snapshot_id:"published-beta", canonical_sha256:"b".repeat(64), league_name:"Published league"};
  const record = {refresh_id:"startup-existing", league_id:snapshot.league_id, season:snapshot.season,
    status:"running", stage:"retrieving_and_validating", publication_committed:false};
  let finish!: (value:Response)=>void;
  const pending = new Promise<Response>(resolve=>finish=resolve);
  const fetch = vi.spyOn(globalThis,"fetch").mockImplementation((input,init)=>{
    const url=String(input);
    if(url.endsWith("/health"))return response(health);
    if(url.endsWith("/snapshots"))return response([snapshot]);
    if(url.endsWith("/snapshots/"+snapshot.snapshot_id))return response(snapshot);
    if(url.endsWith("/managers"))return response(null);
    if(url.endsWith("/runs"))return response([]);
    if(url.endsWith("/refresh/profile"))return response({profile_id:"configured_local_league",
      league_id:snapshot.league_id,season:snapshot.season,automatic_refresh_enabled:true});
    if(url.endsWith("/refreshes/current"))return response(record);
    if(url.endsWith("/refreshes/startup-existing"))return pending;
    if(url.endsWith("/refreshes/startup-existing/result"))return response({
      refresh:{...record,status:"completed",snapshot_id:published.snapshot_id,publication_committed:true},
      snapshot:published,validation_findings:[],unresolved_capabilities:[],
    });
    if(url.endsWith("/analyses/projected-baseline")) {
      const request = JSON.parse(String(init?.body));
      return response(outlook(request.snapshot.snapshot_id===published.snapshot_id?published:snapshot));
    }
    throw new Error(url);
  });
  render(<App/>);
  expect(await screen.findByText("Sanitized Gridiron League", {selector: "header strong"})).toBeInTheDocument();
  expect(await screen.findByText("Projected champion")).toBeInTheDocument();
  expect(await screen.findByText(/Retrieving and validating ESPN league data/)).toBeInTheDocument();
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).endsWith("/refreshes/startup-existing"))).toBe(true));
  finish(await response({...record,status,stage:status,publication_committed:status==="completed",
    error_message:status==="failed"?"ESPN retrieval failed.":null}));
  if(status==="completed"){
    expect(await screen.findByText("Published league", {selector: "header strong"})).toBeInTheDocument();
    expect(await screen.findByText(/New league snapshot published/)).toBeInTheDocument();
    expect(localStorage.getItem("war-room-snapshot")).toBe(published.snapshot_id);
  }else{
    expect(await screen.findByText(/ESPN retrieval failed/)).toBeInTheDocument();
    expect(screen.getByText("Sanitized Gridiron League", {selector: "header strong"})).toBeInTheDocument();
    expect(screen.getByText("Projected champion")).toBeInTheDocument();
  }
  expect(fetch.mock.calls.filter(([url,init])=>String(url).endsWith("/refreshes")&&init?.method==="POST")).toHaveLength(0);
});

test("unsupported projected scoring leaves saved Home information available", async () => {
  mockApi({runs: []});
  const fetch = vi.mocked(globalThis.fetch);
  const fallback = fetch.getMockImplementation()!;
  fetch.mockImplementation((input, init) => {
    if (String(input).endsWith("/analyses/projected-baseline")) return response({
      error: {code:"current_week_evidence_unresolved", message:"Current scoring evidence is unresolved."},
    }, 422);
    return fallback(input, init);
  });
  render(<App/>);
  expect(await screen.findByText(/Projected season unsupported.*Current scoring evidence is unresolved/)).toBeInTheDocument();
  expect(screen.getByRole("heading", {name:"Home"})).toBeInTheDocument();
  expect(screen.getByText(snapshot.league_name!, {selector:"header strong"})).toBeInTheDocument();
  expect(screen.getByRole("button", {name:"Refresh ESPN league"})).toBeEnabled();
});

test("run list failure and retry are independent of the saved snapshot and projected season", async () => {
  const user = userEvent.setup();
  mockApi();
  const fetch = vi.mocked(globalThis.fetch);
  const fallback = fetch.getMockImplementation()!;
  let nextList = () => response({error:{message:"Run store unavailable."}}, 503);
  fetch.mockImplementation((input, init) =>
    String(input).includes("/runs?") ? nextList() : fallback(input, init));

  render(<App/>);
  await screen.findByText("Run list unavailable");
  expect(screen.queryByText("No active runs")).not.toBeInTheDocument();
  expect(await screen.findByText("Projected champion")).toBeInTheDocument();
  expect(screen.getByText("Retained Monte Carlo results are unavailable.")).toBeInTheDocument();

  await user.click(screen.getByRole("button", {name:"View projected season details"}));
  expect(screen.getByText(/Champion in this projected season/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", {name:"Open Playoff Race"}));
  expect(window.location.pathname).toBe("/league/playoff-race");
  expect(screen.getByRole("heading", {name:"Playoff Race"})).toBeInTheDocument();
  expect(screen.getByText("Retained baseline results unavailable")).toBeInTheDocument();
  expect(screen.queryByText("No completed baseline analysis")).not.toBeInTheDocument();
  let complete!: (value:Response)=>void;
  nextList = () => new Promise(resolve=>{complete=resolve;});
  await user.click(screen.getByRole("button", {name:"Retry run list"}));
  expect(screen.getByText("Loading run list")).toBeInTheDocument();
  complete(await response([]));
  expect(await screen.findByText("No completed baseline analysis")).toBeInTheDocument();
  expect(screen.getByText("No active runs")).toBeInTheDocument();
  expect(fetch.mock.calls.filter(([url])=>String(url).endsWith("/analyses/projected-baseline"))).toHaveLength(1);
});

test("a failed run-history reload withdraws previously loaded run information", async () => {
  const user = userEvent.setup();
  mockApi({runs:[]});
  const fetch = vi.mocked(globalThis.fetch);
  const fallback = fetch.getMockImplementation()!;
  let fail = false;
  const completed = {...activeRun,run_type:"baseline",status:"completed",result_available:true};
  fetch.mockImplementation((input,init) =>
    String(input).includes("/runs?") ? (fail ? response({error:{message:"Run store unavailable."}},503) :
      response([completed])) : fallback(input,init));
  render(<App/>);
  expect(await screen.findByText(/A retained run is listed/)).toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Open Simulation Center"}));
  fail=true;
  await user.click(screen.getByRole("button",{name:"Refresh"}));
  await screen.findByText("Run list unavailable");
  await user.click(screen.getByRole("button",{name:"Dashboard"}));
  expect(screen.queryByText(/A retained run is listed/)).not.toBeInTheDocument();
  expect(screen.getByText("Retained Monte Carlo results are unavailable.")).toBeInTheDocument();
  expect(screen.getByText("Projected champion")).toBeInTheDocument();
});

test("a late startup run list cannot replace a newer run-history response", async () => {
  const user = userEvent.setup();
  mockApi();
  const fetch = vi.mocked(globalThis.fetch);
  const fallback = fetch.getMockImplementation()!;
  let finishStartup!: (value:Response)=>void;
  const startup = new Promise<Response>(resolve=>{finishStartup=resolve;});
  let requests = 0;
  fetch.mockImplementation((input,init) =>
    String(input).includes("/runs?") ? (++requests === 1 ? startup : response([])) :
      fallback(input,init));

  render(<App/>);
  await screen.findByText("Projected champion");
  await user.click(screen.getByRole("button",{name:"Open Simulation Center"}));
  await user.click(screen.getByRole("button",{name:"Refresh"}));
  await screen.findByText("No active runs");
  await act(async()=>finishStartup(await response([activeRun])));
  expect(screen.getByText("No active runs")).toBeInTheDocument();
});
