import requests
from dataclasses import dataclass, asdict
import csv
import pandas as pd
import json

serverUrl = "https://tplapp.onrender.com/"
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
            id = env['id'],
            leagueId = env['leagueId'],
            awayTeamId = env['awayTeamId'],
            homeTeamId = env['homeTeamId'],
            awayTeam = env['awayTeam'],
            homeTeam = env['homeTeam'],
            location = env['location'],
            time = env['time'],
            date = env['date'],
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
            id=env['_id'],
            gameId=env['gameId'],
            teamId=env['teamId'],
            playerId=env['player']['id'],
            playerName=env['player']['playerName'],
            playerGender=env['player']['gender'],
            eventType=env['eventType'],
            timestamp=env['timestamp'],
            sequence=env['sequence'],
        )
    
_MAPPING = {
    "Goal": "goals",
    "Assist": "assists",
    "2nd Assist": "second_assists",
    "D": "blocks",
    "TA": "throwaways",
    "Drop": "drops",
    "": "other_passes",
    "pickup": "pickup"
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


    def add(self, key,value):
        key = _MAPPING[key]
        if hasattr(self, key):
            setattr(self, key, getattr(self, key) + value)

def getAllPlayers():
    return requests.get(f"{serverUrl}players").json()

def getAllGames():
    return [Game.from_dict(g) for g in requests.get(f"{serverUrl}games").json()]

def getGameEvents(gameId, teamId):
    return [GameEvent.from_dict(ge) for ge in requests.get(f"{serverUrl}gameEvents/{gameId}/{teamId}").json()]

def getAllGameEvents():
    return [GameEvent.from_dict(ge) for ge in requests.get(f"{serverUrl}gameEvents").json()]

def getAllTeams():
    return {t['id']:t['leagueId'] for t in requests.get(f"{serverUrl}teams").json()}

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
        stats_summary[(e.gameId,e.playerId)].add(e.eventType, 1)

        if last_e.eventType in  {"Goal", "D", "TA", "Drop"}:
            stats_summary[(e.gameId,e.playerId)].add("pickup", 1)
    return stats_summary

def gameToRow(game:Game):
    for g in eventsToRows(getGameEvents(game.id, game.awayTeamId)).values():
        print(g)
        
    
if __name__ == "__main__":
    allEvents = getAllGameEvents()
    rows = eventsToRows(allEvents)
    allGames = getAllGames()
    # gameEvents = getGameEvents(allGames[0].id, allGames[0].awayTeamId)
    # rows = eventsToRows(gameEvents)
    # print(rows)

    with open("data/tpl_stats_summary.json", "w", encoding="utf-8") as file:
        json.dump([asdict(row) for row in rows.values()], file)

    with open("data/tpl_game_info.json", "w", encoding="utf-8") as file:
        json.dump([asdict(row) for row in allGames], file)

    # df = pd.DataFrame(rows.values())
    # df.to_csv("data/tpl_stats_summary.csv")
