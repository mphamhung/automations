import requests
from dataclasses import dataclass, asdict
import pandas as pd
import json
import time
import sys
from typing import Iterable

serverUrl = "https://tplapp.onrender.com/"

REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 4
MAX_FAILED_REQUEST_RETRIES = 4


def _rate_limit_delay_seconds(response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass

    # Exponential backoff when Retry-After is missing or invalid.
    return min(2 ** (attempt - 1), 30)


def _warning_failed_requests(failed_requests):
    if failed_requests:
        print(
            (
                f"Warning: {len(failed_requests)} per-game event requests "
                f"failed after {MAX_FAILED_REQUEST_RETRIES} retries."
            ),
            file=sys.stderr,
        )


def request_json(path: str):
    url = f"{serverUrl}{path}"
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"Network error: {exc}"
            if attempt < MAX_RETRIES:
                time.sleep(attempt)
                continue
            raise RuntimeError(f"Failed to fetch {url}. {last_error}") from exc

        if response.status_code >= 500:
            last_error = (
                f"HTTP {response.status_code} with empty/transient "
                "server response"
            )
            if attempt < MAX_RETRIES:
                time.sleep(attempt)
                continue

        if response.status_code == 429:
            delay = _rate_limit_delay_seconds(response, attempt)
            last_error = f"HTTP 429 rate limited; retrying in {delay:.1f}s"
            if attempt < MAX_RETRIES:
                time.sleep(delay)
                continue

        content_type = response.headers.get("Content-Type", "")
        if not response.ok:
            preview = response.text[:300].strip()
            raise RuntimeError(
                f"Failed to fetch {url}: HTTP {response.status_code}, "
                f"content-type={content_type or 'unknown'}, body={preview!r}"
            )

        if not response.text.strip():
            raise RuntimeError(
                "Failed to parse JSON from "
                f"{url}: empty response body (HTTP {response.status_code})."
            )

        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as exc:
            preview = response.text[:300].strip()
            raise RuntimeError(
                "Failed to parse JSON from "
                f"{url}: content-type={content_type or 'unknown'}, "
                f"body={preview!r}"
            ) from exc

    raise RuntimeError(
        f"Failed to fetch {url} after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )


@dataclass
class Game:
    id: int
    leagueId: int
    awayTeamId: int
    homeTeamId: int
    homeTeam: str
    awayTeam: str
    location: str
    time: str
    date: str

    @classmethod
    def from_dict(cls, env):
        return cls(
            id=env["id"],
            leagueId=env["leagueId"],
            awayTeamId=env["awayTeamId"],
            homeTeamId=env["homeTeamId"],
            awayTeam=env["awayTeam"],
            homeTeam=env["homeTeam"],
            location=env["location"],
            time=env["time"],
            date=env["date"],
        )


@dataclass
class GameEvent:
    id: int
    gameId: int
    teamId: int
    playerId: int
    playerName: str
    playerGender: str
    eventType: str
    timestamp: str
    sequence: int

    @classmethod
    def from_dict(cls, env):
        return cls(
            id=env["_id"],
            gameId=env["gameId"],
            teamId=env["teamId"],
            playerId=env["player"]["id"],
            playerName=env["player"]["playerName"],
            playerGender=env["player"]["gender"],
            eventType=env["eventType"],
            timestamp=env["timestamp"],
            sequence=env["sequence"],
        )


_MAPPING = {
    "Goal": "goals",
    "Assist": "assists",
    "2nd Assist": "second_assists",
    "D": "blocks",
    "TA": "throwaways",
    "Drop": "drops",
    "": "other_passes",
    "pickup": "pickup",
}


@dataclass
class Row:
    name: str
    gameId: int
    playerId: int
    gender: str
    teamId: int
    leagueId: int
    goals: int = 0
    assists: int = 0
    second_assists: int = 0
    blocks: int = 0
    throwaways: int = 0
    drops: int = 0
    other_passes: int = 0
    pickup: int = 0

    def add(self, key, value):
        key = _MAPPING[key]
        if hasattr(self, key):
            setattr(self, key, getattr(self, key) + value)


def getAllPlayers():
    return request_json("players")


def getAllGames():
    return [Game.from_dict(g) for g in request_json("games")]


def getGameEvents(gameId, teamId):
    return [
        GameEvent.from_dict(ge)
        for ge in request_json(f"gameEvents/{gameId}/{teamId}")
    ]


def _game_team_pairs(games: Iterable[Game]):
    return [
        (game.id, team_id)
        for game in games
        for team_id in (game.awayTeamId, game.homeTeamId)
    ]


def _fetch_events_for_pairs(game_team_pairs):
    all_events = []
    failed_requests = []

    for game_id, team_id in game_team_pairs:
        try:
            all_events.extend(getGameEvents(game_id, team_id))
        except RuntimeError as team_exc:
            failed_requests.append((game_id, team_id, str(team_exc)))

    retry_round = 0
    while failed_requests and retry_round < MAX_FAILED_REQUEST_RETRIES:
        retry_round += 1
        next_failures = []
        for game_id, team_id, _ in failed_requests:
            try:
                all_events.extend(getGameEvents(game_id, team_id))
            except RuntimeError as retry_exc:
                next_failures.append((game_id, team_id, str(retry_exc)))
        failed_requests = next_failures

    return all_events, failed_requests


def getAllTeams():
    return {t["id"]: t["leagueId"] for t in request_json("teams")}


def eventsToRows(events):
    team_to_league = getAllTeams()
    stats_summary = {}
    last_e = None
    for e in events:
        if (e.gameId, e.playerId) not in stats_summary:
            stats_summary[(e.gameId, e.playerId)] = Row(
                name=e.playerName,
                gameId=e.gameId,
                playerId=e.playerId,
                teamId=e.teamId,
                gender=e.playerGender,
                leagueId=team_to_league[e.teamId],
            )
        stats_summary[(e.gameId, e.playerId)].add(e.eventType, 1)

        if last_e and last_e.eventType in {"Goal", "D", "TA", "Drop"}:
            stats_summary[(e.gameId, e.playerId)].add("pickup", 1)
        last_e = e
    return stats_summary


def _load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as json_file:
            return json.load(json_file)
    except FileNotFoundError:
        return []


def _fetch_game_events_for_games(games):
    all_events, failed_requests = _fetch_events_for_pairs(
        _game_team_pairs(games)
    )
    _warning_failed_requests(failed_requests)
    return all_events


def _missing_game_team_pairs(games, existing_rows_list):
    existing_pairs = {
        (row.gameId, row.teamId)
        for row in existing_rows_list
    }
    return [
        pair for pair in _game_team_pairs(games) if pair not in existing_pairs
    ]


def _merge_rows(existing_rows_list, new_rows_list):
    return {
        (row.gameId, row.playerId): row
        for row in [*existing_rows_list, *new_rows_list]
    }


if __name__ == "__main__":
    try:
        all_games = getAllGames()
        existing_game_data = _load_json_file("data/tpl_game_info.json")
        existing_row_data = _load_json_file("data/tpl_stats_summary.json")

        existing_games = [Game.from_dict(g) for g in existing_game_data]
        existing_rows = [Row(**row) for row in existing_row_data]
        existing_game_ids = {game.id for game in existing_games}

        new_games = [
            game for game in all_games if game.id not in existing_game_ids
        ]
        missing_game_team_pairs = _missing_game_team_pairs(
            all_games, existing_rows
        )
        new_rows = []

        if missing_game_team_pairs:
            print(
                (
                    "Found "
                    f"{len(missing_game_team_pairs)} missing game/team "
                    "pairs. Fetching events..."
                ),
                file=sys.stderr,
            )
            fetched_events, failed_pair_requests = _fetch_events_for_pairs(
                missing_game_team_pairs
            )
            _warning_failed_requests(failed_pair_requests)
            new_rows = list(
                eventsToRows(fetched_events).values()
            )
        else:
            print(
                (
                    "No missing game/team pairs found. "
                    "Existing data is up to date."
                ),
                file=sys.stderr,
            )

        merged_rows = _merge_rows(existing_rows, new_rows)
        merged_games = existing_games + new_games

        with open(
            "data/tpl_stats_summary.json", "w", encoding="utf-8"
        ) as file:
            json.dump([asdict(row) for row in merged_rows.values()], file)

        with open("data/tpl_game_info.json", "w", encoding="utf-8") as file:
            json.dump([asdict(row) for row in merged_games], file)

        df = pd.DataFrame(merged_rows.values())
        df.to_csv("data/tpl_stats_summary.csv")
    except RuntimeError as exc:
        print(f"Data pull failed: {exc}", file=sys.stderr)
        sys.exit(1)
