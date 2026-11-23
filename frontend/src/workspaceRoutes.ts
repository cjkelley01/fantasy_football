const paths = {
  home: "/home",
  team: "/my-team/overview",
  opportunities: "/players-moves/opportunities",
  trades: "/players-moves/trade-lab",
  league: "/league/overview",
  matchups: "/league/matchups",
  insights: "/league/insights",
  history: "/history/seasons",
  simulation: "/tools/simulation",
} as const;

export type Destination = keyof typeof paths;
export type WorkspaceRoute =
  | { destination: Exclude<Destination, "history"> }
  | { destination: "history"; year?: number };

export function readWorkspaceRoute(pathname: string): WorkspaceRoute {
  const path = pathname.replace(/\/$/, "");
  const archive = /^\/history\/seasons\/(\d{4})$/.exec(path);
  if (archive && Number(archive[1]) >= 2000) {
    return { destination: "history", year: Number(archive[1]) };
  }
  const destination = (Object.keys(paths) as Destination[]).find(key => paths[key] === path);
  return { destination: destination ?? "home" };
}

export function workspacePath(route: WorkspaceRoute): string {
  return route.destination === "history" && route.year !== undefined
    ? `${paths.history}/${route.year}` : paths[route.destination];
}
