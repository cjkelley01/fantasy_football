import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "./lib/api";
import { SeasonArchive } from "./SeasonArchive";
import type { CglHistory } from "./types/api";

const season = (year: number, name: string): CglHistory["seasons"][number] => ({
  season: year, champion_team_id: 1,
  teams: [{ team_id: 1, team_name: name, manager_ids: [], final_rank: 1,
    regular_season: { wins: 1, losses: 0, ties: 0, points_for: 12, points_against: 0 } }],
  matchups: [{ matchup_id: 1, scoring_periods: [15], stage: "championship_playoffs",
    playoff_round: 1, home_team_id: 1, away_team_id: null, home_score: null,
    away_score: null, winner: "UNDECIDED" }],
});

afterEach(() => vi.restoreAllMocks());

test("loads backed history and switches completed years without fabricating bye scores", async () => {
  vi.spyOn(api, "cglHistory").mockResolvedValue({
    league_id: 7, source: { revision: "a".repeat(40), sha256: "b".repeat(64) },
    seasons: [season(2024, "Harbor"), season(2025, "Coast")],
  });
  render(<SeasonArchive />);
  expect(await screen.findByText("Champion · Coast")).toBeInTheDocument();
  const bracket = within(screen.getByRole("table", { name: "Championship bracket" }));
  expect(bracket.getByText("Bye")).toBeInTheDocument();
  expect(bracket.getByText("Coast · Unavailable")).toBeInTheDocument();
  await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Season" }), "2024");
  expect(screen.getByText("Champion · Harbor")).toBeInTheDocument();
});

test("distinguishes no archive configuration", async () => {
  vi.spyOn(api, "cglHistory").mockResolvedValue(null);
  render(<SeasonArchive />);
  expect(await screen.findByText("CGL history is not configured.")).toBeInTheDocument();
});
