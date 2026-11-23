import {StrictMode} from "react";
import {act, renderHook, waitFor} from "@testing-library/react";
import {api, ApiError} from "./lib/api";
import {snapshot} from "./test/fixtures";
import type {ProjectedBaselineResult, SnapshotSummary} from "./types/api";
import {useProjectedSeason} from "./useProjectedSeason";

const projection = (source: SnapshotSummary): ProjectedBaselineResult => ({
  status:"complete", snapshot_id:source.snapshot_id, canonical_sha256:source.canonical_sha256,
  champion_team_id:null, teams:[], weeks:[], postseason:[], transactions:[],
  unresolved_capabilities:[], assumptions:[], observed_scores:[], missing_projection_count:0,
});
afterEach(()=>vi.restoreAllMocks());

test("reuses a prepared snapshot under Strict Mode and distinguishes changed canonical content", async () => {
  const fetch = vi.spyOn(api,"projectedBaseline").mockImplementation(async input=>projection(input));
  const changed = {...snapshot,canonical_sha256:"f".repeat(64)};
  const {result,rerender}=renderHook(useProjectedSeason,{initialProps:snapshot,wrapper:StrictMode});
  await waitFor(()=>expect(result.current.state.status).toBe("available"));
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender(changed);
  await waitFor(()=>expect(result.current.state).toEqual({status:"available",result:projection(changed)}));
  rerender(snapshot);
  expect(result.current.state).toEqual({status:"available",result:projection(snapshot)});
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("ignores a late result from an aborted snapshot request",async()=>{
  let resolve!: (value:ProjectedBaselineResult)=>void;
  const slow = new Promise<ProjectedBaselineResult>(done=>resolve=done);
  const fetch = vi.spyOn(api,"projectedBaseline").mockReturnValueOnce(slow).mockImplementation(async input=>projection(input));
  const other={...snapshot,snapshot_id:"other"};
  const {result,rerender}=renderHook(useProjectedSeason,{initialProps:snapshot});
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
  rerender(other);
  await waitFor(()=>expect(result.current.state).toEqual({status:"available",result:projection(other)}));
  expect(fetch.mock.calls[0][1]?.aborted).toBe(true);
  await act(async()=>resolve(projection(snapshot)));
  expect(result.current.state).toEqual({status:"available",result:projection(other)});
});

test("distinguishes unsupported evidence and allows retry after a failed calculation",async()=>{
  vi.spyOn(api,"projectedBaseline")
    .mockRejectedValueOnce(new ApiError("Scoring evidence unresolved.",422,"current_week_evidence_unresolved"))
    .mockRejectedValueOnce(new ApiError("Local request failed.",503))
    .mockResolvedValueOnce(projection(snapshot));
  const {result}=renderHook(useProjectedSeason,{initialProps:snapshot});
  await waitFor(()=>expect(result.current.state.status).toBe("unsupported"));
  act(()=>result.current.retry());
  await waitFor(()=>expect(result.current.state.status).toBe("failed"));
  act(()=>result.current.retry());
  await waitFor(()=>expect(result.current.state.status).toBe("available"));
});
