import type {
  ApiErrorEnvelope,
  BackgroundRunRecord,
  BackgroundRunSubmission, BackgroundManualScenarioResultSummary, ManualScenarioRequest,
  BackgroundBaselineResultSummary,
  BackgroundActionSearchResultSummary, CandidateDetail, CandidateQueryPage, CompletedStateDetail,
  HealthResponse,
  SnapshotSummary,
  LeagueRefreshProfile, SnapshotRefreshRequest, SnapshotRefreshSubmission,
  SnapshotRefreshRecord, SnapshotRefreshResult,
  BackgroundProgress, BaselineAnalysisRequest, ActionSearchRequest,
} from "../types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("The local decision engine is not reachable.", 0);
  }
  if (!response.ok) {
    let body: ApiErrorEnvelope | undefined;
    try {
      body = (await response.json()) as ApiErrorEnvelope;
    } catch {
      // The backend contract normally supplies JSON; retain a safe generic fallback.
    }
    throw new ApiError(body?.error.message ?? `Request failed (${response.status}).`, response.status, body?.error.code);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path:string,body:unknown,signal?:AbortSignal):Promise<T>{
  let response:Response;try{response=await fetch(path,{method:"POST",headers:{Accept:"application/json","Content-Type":"application/json"},body:JSON.stringify(body),signal})}catch(error){if(error instanceof DOMException&&error.name==="AbortError")throw error;throw new ApiError("The local decision engine is not reachable.",0)}
  if(!response.ok){let payload:ApiErrorEnvelope|undefined;try{payload=await response.json() as ApiErrorEnvelope}catch{/* typed fallback */}throw new ApiError(payload?.error.message??`Request failed (${response.status}).`,response.status,payload?.error.code)}return await response.json() as T;
}
async function deleteRequest(path:string,signal?:AbortSignal):Promise<void>{const response=await fetch(path,{method:"DELETE",signal});if(!response.ok){let payload:ApiErrorEnvelope|undefined;try{payload=await response.json() as ApiErrorEnvelope}catch{/* typed fallback */}throw new ApiError(payload?.error.message??`Request failed (${response.status}).`,response.status,payload?.error.code)}}

export const api = {
  health: (signal?: AbortSignal) => getJson<HealthResponse>("/api/v1/health", signal),
  snapshots: (signal?: AbortSignal) => getJson<readonly SnapshotSummary[]>("/api/v1/snapshots", signal),
  snapshot: (snapshotId: string, signal?: AbortSignal) =>
    getJson<SnapshotSummary>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}`, signal),
  refreshProfile:(signal?:AbortSignal)=>getJson<LeagueRefreshProfile>("/api/v1/refresh/profile",signal),
  submitRefresh:(request:SnapshotRefreshRequest,signal?:AbortSignal)=>postJson<SnapshotRefreshSubmission>("/api/v1/refreshes",request,signal),
  currentRefresh:(signal?:AbortSignal)=>getJson<SnapshotRefreshRecord|null>("/api/v1/refreshes/current",signal),
  refresh:(refreshId:string,signal?:AbortSignal)=>getJson<SnapshotRefreshRecord>(`/api/v1/refreshes/${encodeURIComponent(refreshId)}`,signal),
  cancelRefresh:(refreshId:string,signal?:AbortSignal)=>postJson<SnapshotRefreshRecord>(`/api/v1/refreshes/${encodeURIComponent(refreshId)}/cancel`,{},signal),
  refreshResult:(refreshId:string,signal?:AbortSignal)=>getJson<SnapshotRefreshResult>(`/api/v1/refreshes/${encodeURIComponent(refreshId)}/result`,signal),
  runs: (signal?: AbortSignal) => getJson<readonly BackgroundRunRecord[]>("/api/v1/runs?limit=50", signal),
  submitBaseline:(request:BaselineAnalysisRequest,signal?:AbortSignal)=>postJson<BackgroundRunSubmission>("/api/v1/runs/baseline",request,signal),
  submitActionSearch:(request:ActionSearchRequest,signal?:AbortSignal)=>postJson<BackgroundRunSubmission>("/api/v1/runs/action-search",request,signal),
  progress:(runId:string,signal?:AbortSignal)=>getJson<BackgroundProgress>(`/api/v1/runs/${encodeURIComponent(runId)}/progress`,signal),
  cancelRun:(runId:string,signal?:AbortSignal)=>postJson<BackgroundRunRecord>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`,{},signal),
  deleteRun:(runId:string,signal?:AbortSignal)=>deleteRequest(`/api/v1/runs/${encodeURIComponent(runId)}`,signal),
  runResult: (runId: string, signal?: AbortSignal) =>
    getJson<BackgroundBaselineResultSummary>(`/api/v1/runs/${encodeURIComponent(runId)}/result`, signal),
  actionResult: (runId:string,signal?:AbortSignal)=>getJson<BackgroundActionSearchResultSummary>(`/api/v1/runs/${encodeURIComponent(runId)}/result`,signal),
  candidates: (runId:string,query:string,signal?:AbortSignal)=>getJson<CandidateQueryPage>(`/api/v1/runs/${encodeURIComponent(runId)}/candidates?${query}`,signal),
  candidate: (runId:string,candidateId:string,signal?:AbortSignal)=>getJson<CandidateDetail>(`/api/v1/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}`,signal),
  completedState: (runId:string,stateId:string,signal?:AbortSignal)=>getJson<CompletedStateDetail>(`/api/v1/runs/${encodeURIComponent(runId)}/states/${encodeURIComponent(stateId)}`,signal),
  submitManualScenario:(request:ManualScenarioRequest,signal?:AbortSignal)=>postJson<BackgroundRunSubmission>("/api/v1/runs/manual-scenario",request,signal),
  run:(runId:string,signal?:AbortSignal)=>getJson<BackgroundRunRecord>(`/api/v1/runs/${encodeURIComponent(runId)}`,signal),
  manualScenarioResult:(runId:string,signal?:AbortSignal)=>getJson<BackgroundManualScenarioResultSummary>(`/api/v1/runs/${encodeURIComponent(runId)}/result`,signal),
};
