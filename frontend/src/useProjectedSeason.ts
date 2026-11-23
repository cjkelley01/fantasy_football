import {useEffect, useRef, useState} from "react";
import {api, ApiError, projectedBaselinePolicy} from "./lib/api";
import type {ProjectedBaselineResult, SnapshotSummary} from "./types/api";

type ProjectionState =
  | {status: "not_requested" | "preparing"}
  | {status: "available"; result: ProjectedBaselineResult}
  | {status: "unsupported" | "failed"; message: string};

const idle: ProjectionState = {status: "not_requested"};

export function useProjectedSeason(snapshot: SnapshotSummary | undefined) {
  const key = snapshot ? JSON.stringify([
    snapshot.snapshot_id, snapshot.canonical_sha256, snapshot.league_id, snapshot.season, projectedBaselinePolicy,
  ]) : null;
  const completed = useRef(new Map<string, ProjectedBaselineResult>());
  const [progress, setProgress] = useState<{key: string; state: ProjectionState}>();
  const [attempt, setAttempt] = useState(0);
  const selected = useRef(snapshot);
  selected.current = snapshot;

  useEffect(() => {
    const input = selected.current;
    if (!key || !input || completed.current.has(key)) return;
    const controller = new AbortController();
    setProgress({key, state: {status: "preparing"}});
    const timeout = window.setTimeout(() => {
      controller.abort();
      setProgress({key, state: {status: "failed", message: "The projected season request timed out."}});
    }, 30_000);
    // A cancelled effect setup, including Strict Mode's initial replay, sends no request.
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try {
        const value = await api.projectedBaseline(input, controller.signal);
        if (controller.signal.aborted) return;
        if (value.snapshot_id !== input.snapshot_id || value.canonical_sha256 !== input.canonical_sha256) {
          throw new Error("The projected season belongs to a different snapshot.");
        }
        completed.current.set(key, value);
        setProgress({key, state: {status: "available", result: value}});
      } catch (reason) {
        if (controller.signal.aborted) return;
        const status = reason instanceof ApiError && reason.status === 422 &&
          ["current_week_evidence_unresolved", "engine_request_rejected"].includes(reason.code ?? "")
          ? "unsupported" : "failed";
        setProgress({key, state: {status, message: reason instanceof Error ? reason.message : "Projected season unavailable."}});
      } finally {
        window.clearTimeout(timeout);
      }
    });
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [key, attempt]);

  const saved = key ? completed.current.get(key) : undefined;
  const state: ProjectionState = saved ? {status: "available", result: saved}
    : key && progress?.key === key ? progress.state : idle;
  return {state, retry: () => setAttempt(value => value + 1)};
}

export type ProjectedSeasonState = ReturnType<typeof useProjectedSeason>;
