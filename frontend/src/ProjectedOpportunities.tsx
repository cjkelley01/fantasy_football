import {useEffect, useRef, useState} from "react";
import {api} from "./lib/api";
import {ProjectedTradeResult} from "./ProjectedTradeResult";
import type {ProjectedOpportunityResult, SnapshotSummary} from "./types/api";

export function ProjectedOpportunities({snapshot}: {snapshot: SnapshotSummary}) {
  const [team, setTeam] = useState(snapshot.teams[0]?.team_id ?? 0);
  const [counterparty, setCounterparty] = useState(snapshot.teams[1]?.team_id ?? 0);
  const [result, setResult] = useState<ProjectedOpportunityResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const clear = () => {
    active.current?.abort();
    setResult(null);
    setError(null);
    setBusy(false);
  };
  async function search() {
    clear();
    const request = new AbortController();
    active.current = request;
    setBusy(true);
    try {
      const response = await api.projectedOpportunities(snapshot, team, counterparty, request.signal);
      if (request.signal.aborted) return;
      if (response.snapshot_id !== snapshot.snapshot_id || response.canonical_sha256 !== snapshot.canonical_sha256)
        throw new Error("The projected search belongs to a different snapshot.");
      setResult(response);
    } catch (reason) {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "Projected search unavailable.");
    } finally {
      if (!request.signal.aborted) setBusy(false);
    }
  }
  const player = (id: number) => snapshot.players.find(row => row.player_id === id)?.display_name ?? String(id);
  return <section className="analytics-panel" aria-labelledby="projected-opportunities-title">
    <h2 id="projected-opportunities-title">Projected trade opportunities</h2>
    <p>Compare up to six one-for-one exchanges against the unchanged season.</p>
    <label>Requesting team <select value={team} onChange={event => {clear(); setTeam(Number(event.target.value));}}>
      {snapshot.teams.map(row => <option key={row.team_id} value={row.team_id}>{row.display_name}</option>)}
    </select></label>
    <label>Counterparty <select value={counterparty} onChange={event => {clear(); setCounterparty(Number(event.target.value));}}>
      {snapshot.teams.map(row => <option key={row.team_id} value={row.team_id}>{row.display_name}</option>)}
    </select></label>
    <button className="primary-button" onClick={() => void search()} disabled={busy || !team || !counterparty || team === counterparty}>
      {busy ? "Evaluating exchanges..." : "Find projected trades"}
    </button>
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p role="status">{result.candidates.length} retained candidates from {result.generated_candidates} generated.
        {" "}{result.evaluations.length} distinct season evaluations.
        {!result.candidate_coverage_complete && ` Candidate coverage incomplete. ${result.truncation_reason ?? ""}`}
        {!result.evaluation_coverage_complete && " Evaluation coverage incomplete."}
      </p>
      <p>Each comparison shows one projected outcome. No probability ranking is calculated.</p>
      {result.candidates.map(candidate => {
        const season = candidate.evaluation_index === null ? null : result.evaluations[candidate.evaluation_index];
        return <details key={candidate.candidate_id}>
          <summary>{candidate.explicit_changes.map(move => player(move.player_id)).join(" / ")}
            {candidate.timeline.map(trade => ` / Week ${trade.scoring_week}`)}
          </summary>
          {candidate.limitation && <p>{candidate.limitation}</p>}
          {candidate.caveats.length > 0 && <ul>{candidate.caveats.map(text =>
            <li key={text}>{text.replaceAll("_", " ")}</li>)}</ul>}
          {result.baseline && season && <ProjectedTradeResult snapshot={snapshot} result={{
            ...candidate, scenario_id: candidate.candidate_id,
            baseline: result.baseline.season, hypothetical: season.season,
            baseline_final_roster: result.baseline.final_roster, hypothetical_final_roster: season.final_roster,
          }}/>}
        </details>;
      })}
    </>}
  </section>;
}
