from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .goals import Goal, load_goals


SUITS = ("Rot", "Grün", "Blau", "Gelb")
RANKS = ("1", "2", "3", "4", "5")
PLAYER_NAMES = ("Du", "Anna", "Ben", "Clara")
MAX_ROUNDS = 4


class Phase(str, Enum):
    PLAYING = "playing"
    FINISHED = "finished"


@dataclass(frozen=True)
class Card:
    id: str
    suit: str
    rank: str

    @property
    def label(self) -> str:
        return self.rank

    @property
    def points(self) -> int:
        if self.suit == "Schwarz":
            return 0
        return {"1": 3, "2": 2, "3": 1, "4": 0, "5": 0}[self.rank]


@dataclass
class TrickCard:
    player: int
    card: Card


def make_deck() -> list[Card]:
    return [
        Card(f"{suit[:2]}-{rank}-{copy}", suit, rank)
        for suit in SUITS
        for rank in RANKS
        for copy in (1, 2)
    ] + [
        Card(f"sc-{rank}-{copy}", "Schwarz", rank)
        for rank in ("6", "7")
        for copy in (1, 2)
    ]


def is_trump(card: Card) -> bool:
    return card.suit in ("Schwarz", "Rot")


def card_category(card: Card, lead: Card | None) -> str:
    if is_trump(card):
        return "trump"
    if lead and not is_trump(lead) and card.suit == lead.suit:
        return "suit"
    return "off"


def beats(candidate: Card, current: Card, lead: Card) -> bool:
    candidate_category = card_category(candidate, lead)
    current_category = card_category(current, lead)
    if candidate_category == "trump" and current_category != "trump":
        return True
    if candidate_category != "trump" and current_category == "trump":
        return False
    if candidate_category == "suit" and current_category != "suit":
        return True
    if candidate_category != "suit" and current_category == "suit":
        return False
    if candidate_category in ("trump", "suit") and current_category in ("trump", "suit"):
        order = {str(value): value for value in range(1, 8)}
        return order[candidate.rank] > order[current.rank]
    return False


@dataclass
class Game:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    round_number: int = 1
    start_player: int = 0
    player_names: tuple[str, str, str, str] = PLAYER_NAMES
    match_scores: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    hands: list[list[Card]] = field(default_factory=lambda: [[] for _ in range(4)])
    current_player: int = 0
    trick: list[TrickCard] = field(default_factory=list)
    trick_number: int = 1
    scores: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    phase: Phase = Phase.PLAYING
    last_message: str = ""
    history: list[str] = field(default_factory=list)
    teams: list[int] = field(default_factory=lambda: [0, 1, 0, 1])
    revealed_black_players: set[int] = field(default_factory=set)
    last_trick: list[TrickCard] = field(default_factory=list)
    last_trick_winner: int | None = None
    goals: list[Goal] = field(default_factory=list)
    goal_options: list[list[Goal]] = field(default_factory=lambda: [[] for _ in range(4)])
    selected_goals: list[Goal | None] = field(default_factory=lambda: [None, None, None, None])
    completed_goals: list[Goal | None] = field(default_factory=lambda: [None, None, None, None])
    revealed_goals: list[dict[str, Any]] = field(default_factory=list)
    captured_cards: list[list[Card]] = field(default_factory=lambda: [[] for _ in range(4)])
    won_tricks: list[list[list[Card]]] = field(default_factory=lambda: [[] for _ in range(4)])
    trick_winners: list[int] = field(default_factory=list)
    round_result: dict[str, Any] | None = None
    match_finished: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def start(
        self,
        round_number: int = 1,
        match_scores: list[int] | None = None,
        start_player: int | None = None,
        player_names: tuple[str, str, str, str] | None = None,
        automated_players: set[int] | None = None,
    ) -> None:
        self.round_number = round_number
        if start_player is not None:
            self.start_player = start_player
        if player_names is not None:
            self.player_names = player_names
        automated_players = automated_players or set()
        if match_scores is not None:
            self.match_scores = match_scores[:]
        self.goals = load_goals()
        self.goal_options = [random.sample(self.goals, 2) for _ in range(4)]
        self.selected_goals = [
            random.choice(options) if player in automated_players else None
            for player, options in enumerate(self.goal_options)
        ]
        self.completed_goals = [None, None, None, None]
        self.revealed_goals = []
        self.captured_cards = [[] for _ in range(4)]
        self.won_tricks = [[] for _ in range(4)]
        self.trick_winners = []
        self.round_result = None
        self.match_finished = False
        # Die beiden schwarzen 6er definieren Team Schwarz.
        # Neu mischen, falls beide 6er beim gleichen Spieler landen.
        while True:
            deck = make_deck()
            random.shuffle(deck)
            hands = [deck[i * 11 : (i + 1) * 11] for i in range(4)]
            black_six_holders = {
                player
                for player, hand in enumerate(hands)
                if any(card.suit == "Schwarz" and card.rank == "6" for card in hand)
            }
            if len(black_six_holders) == 2:
                break
        self.hands = [
            sorted(hand, key=lambda c: (c.suit != "Schwarz", c.suit, int(c.rank)))
            for hand in hands
        ]
        self.teams = [0 if player in black_six_holders else 1 for player in range(4)]
        self.current_player = self.start_player
        self.trick = []
        self.last_trick = []
        self.last_trick_winner = None
        self.revealed_black_players = set()
        self.trick_number = 1
        self.scores = [0, 0, 0, 0]
        self.phase = Phase.PLAYING
        self.last_message = "Wähle einen Auftrag."
        self.history = [
            f"Partie {self.round_number} gestartet.",
            f"Startspieler: {self.player_names[self.start_player]}.",
            "Wähle einen Auftrag.",
        ]

    @staticmethod
    def _goal_view(goal: Goal | None) -> dict[str, Any] | None:
        if goal is None:
            return None
        return {"id": goal.id, "condition": goal.condition, "reward": goal.reward}

    def choose_goal(self, player: int, goal_id: str) -> None:
        if self.phase != Phase.PLAYING:
            raise ValueError("Die Partie ist bereits beendet.")
        if self.selected_goals[player] is not None:
            raise ValueError("Du hast bereits einen Auftrag gewählt.")
        selected = next((goal for goal in self.goal_options[player] if goal.id == goal_id), None)
        if selected is None:
            raise ValueError("Dieser Auftrag wurde dir nicht angeboten.")
        self.selected_goals[player] = selected
        if not self._goal_selection_complete():
            self.last_message = "Warte auf die Auftragsauswahl der anderen Spieler."
        else:
            self.last_message = (
                "Du bist am Zug."
                if self.current_player == player
                else f"{self.player_names[self.current_player]} ist am Zug."
            )
        self.history.append(f"{self.player_names[player]} hat einen Auftrag gewählt.")

    def _complete_goal(self, player: int, goal: Goal) -> None:
        if self.completed_goals[player] is not None:
            return
        self.completed_goals[player] = goal
        self.match_scores[player] += goal.reward
        self.revealed_goals.append({
            "player": player,
            "player_name": self.player_names[player],
            "id": goal.id,
            "condition": goal.condition,
            "reward": goal.reward,
        })
        self.history.append(
            f"{self.player_names[player]} erfüllt den Auftrag und erhält {goal.reward} Siegpunkte."
        )

    def _check_goal_after_trick(self, player: int, trick: list[TrickCard]) -> None:
        goal = self.selected_goals[player]
        if goal is None or self.completed_goals[player] is not None:
            return
        cards = [played.card for played in trick]
        if goal.id == "win_non_five_color_trick":
            won_card = next(item.card for item in trick if item.player == player)
            fulfilled = not is_trump(won_card) and won_card.rank != "5"
        elif goal.id == "win_trick_two_colors":
            fulfilled = len({card.suit for card in cards if not is_trump(card)}) >= 2
        elif goal.id == "win_trick_six_points":
            fulfilled = sum(card.points for card in cards) >= 6
        elif goal.id in {"win_blue_3", "win_red_3", "win_green_3", "win_yellow_3"}:
            color = {
                "win_blue_3": "Blau",
                "win_red_3": "Rot",
                "win_green_3": "Grün",
                "win_yellow_3": "Gelb",
            }[goal.id]
            fulfilled = any(card.suit == color and card.rank == "3" for card in cards)
        elif goal.id == "win_two_tricks_in_row":
            fulfilled = len(self.trick_winners) >= 2 and self.trick_winners[-2:] == [player, player]
        else:
            fulfilled = False
        if fulfilled:
            self._complete_goal(player, goal)

    def _check_end_goals(self) -> None:
        for player, goal in enumerate(self.selected_goals):
            if goal is None or self.completed_goals[player] is not None:
                continue
            captured = self.captured_cards[player]
            won_count = len(self.won_tricks[player])
            color_counts = {suit: sum(card.suit == suit for card in captured) for suit in SUITS}
            if goal.id == "win_last_trick":
                fulfilled = self.trick_winners[-1:] == [player]
            elif goal.id == "win_exactly_three_tricks":
                fulfilled = won_count == 3
            elif goal.id == "capture_no_five":
                fulfilled = all(card.rank != "5" for card in captured)
            elif goal.id.startswith("capture_four_"):
                color = {
                    "capture_four_blue": "Blau",
                    "capture_four_green": "Grün",
                    "capture_four_yellow": "Gelb",
                    "capture_four_red": "Rot",
                }[goal.id]
                fulfilled = color_counts[color] >= 4
            else:
                fulfilled = False
            if fulfilled:
                self._complete_goal(player, goal)

    def _goal_selection_complete(self) -> bool:
        return all(goal is not None for goal in self.selected_goals)

    def legal_cards(self, player: int) -> list[Card]:
        hand = self.hands[player]
        if not self.trick:
            return hand[:]
        lead = self.trick[0].card
        lead_category = "trump" if is_trump(lead) else lead.suit
        matching = [
            c for c in hand
            if ("trump" if is_trump(c) else c.suit) == lead_category
        ]
        return matching or hand[:]

    def play(self, player: int, card_id: str) -> None:
        if self.phase != Phase.PLAYING:
            raise ValueError("Das Spiel ist bereits beendet.")
        if not self._goal_selection_complete():
            raise ValueError("Alle Spieler müssen zuerst einen Auftrag wählen.")
        if player != self.current_player:
            raise ValueError("Du bist nicht am Zug.")
        card = next((c for c in self.hands[player] if c.id == card_id), None)
        if card is None:
            raise ValueError("Diese Karte ist nicht auf deiner Hand.")
        if card not in self.legal_cards(player):
            raise ValueError("Du musst Farbe oder Trumpf bedienen.")
        self.hands[player].remove(card)
        self.trick.append(TrickCard(player, card))
        self.history.append(f"{self.player_names[player]} spielt {card.rank} {card.suit}.")
        if card.suit == "Schwarz" and card.rank == "6":
            self.revealed_black_players.add(player)
            self.history.append(f"{self.player_names[player]} ist Team Schwarz.")

        if len(self.trick) < 4:
            self.current_player = (player + 1) % 4
            self.last_message = f"{self.player_names[self.current_player]} ist am Zug."
            return

        winner = self.trick[0].player
        lead = self.trick[0].card
        for played in self.trick[1:]:
            if beats(played.card, next(x.card for x in self.trick if x.player == winner), lead):
                winner = played.player
        points = sum(x.card.points for x in self.trick)
        self.scores[winner] += points
        self.history.append(f"{self.player_names[winner]} gewinnt Stich {self.trick_number} ({points} Augen).")
        won_cards = self.trick[:]
        self.captured_cards[winner].extend(item.card for item in won_cards)
        self.won_tricks[winner].append([item.card for item in won_cards])
        self.trick_winners.append(winner)
        self._check_goal_after_trick(winner, won_cards)
        self.current_player = winner
        self.last_trick = self.trick[:]
        self.last_trick_winner = winner
        self.trick = []
        self.trick_number += 1
        if not any(self.hands):
            self._check_end_goals()
            self._finish_round()
            self.phase = Phase.FINISHED
            self.last_message = "Spiel beendet."
        else:
            self.last_message = f"{self.player_names[winner]} gewinnt den Stich und ist am Zug."

    def _finish_round(self) -> None:
        team_scores = [
            sum(self.scores[seat] for seat, team in enumerate(self.teams) if team == team_id)
            for team_id in (0, 1)
        ]
        winning_team = 0 if team_scores[0] > team_scores[1] else 1
        for player, team in enumerate(self.teams):
            if team == winning_team:
                self.match_scores[player] += 2
        self.match_finished = self.round_number >= MAX_ROUNDS
        winner = max(range(4), key=lambda player: self.match_scores[player])
        self.round_result = {
            "round_number": self.round_number,
            "team_scores": team_scores,
            "winning_team": winning_team,
            "player_scores": self.scores[:],
            "match_scores": self.match_scores[:],
            "match_finished": self.match_finished,
            "match_winner": winner if self.match_finished else None,
            "revealed_goals": self.revealed_goals[:],
        }

    def choose_ai_card(self, player: int) -> Card:
        base_card = self._choose_base_ai_card(player)
        return self._adjust_ai_card_for_goal(player, base_card)

    def _adjust_ai_card_for_goal(self, player: int, base_card: Card) -> Card:
        goal = self.selected_goals[player]
        if goal is None or self.completed_goals[player] is not None:
            return base_card

        legal = self.legal_cards(player)
        if not legal:
            return base_card

        partner = self.known_teammate(player)
        pool = legal
        if partner is not None and self.trick:
            best_player, best_card = self._current_trick_winner()
            if best_player == partner:
                safe = [card for card in legal if not beats(card, best_card, self.trick[0].card)]
                pool = safe or [base_card]

        def would_win(card: Card) -> bool:
            if not self.trick:
                return False
            _, best_card = self._current_trick_winner()
            return beats(card, best_card, self.trick[0].card)

        trick_cards = [item.card for item in self.trick]
        trick_points = sum(card.points for card in trick_cards)
        target_suit = {
            "capture_four_blue": "Blau",
            "capture_four_green": "Grün",
            "capture_four_yellow": "Gelb",
            "capture_four_red": "Rot",
        }.get(goal.id)
        target_card = {
            "win_blue_3": ("Blau", "3"),
            "win_red_3": ("Rot", "3"),
            "win_green_3": ("Grün", "3"),
            "win_yellow_3": ("Gelb", "3"),
        }.get(goal.id)

        def goal_bonus(card: Card) -> int:
            wins = would_win(card)
            bonus = 0
            if goal.id == "capture_no_five":
                if wins and (card.rank == "5" or any(item.rank == "5" for item in trick_cards)):
                    bonus -= 60
                elif card.rank == "5":
                    bonus -= 8
            elif goal.id in {"capture_four_blue", "capture_four_green", "capture_four_yellow", "capture_four_red"}:
                captured_count = sum(item.suit == target_suit for item in self.captured_cards[player])
                if captured_count < 4 and card.suit == target_suit:
                    bonus += 8
                if wins and any(item.suit == target_suit for item in trick_cards + [card]):
                    bonus += 25
            elif goal.id == "win_non_five_color_trick":
                if wins and not is_trump(card) and card.rank != "5":
                    bonus += 35
            elif goal.id == "win_trick_two_colors":
                colors = {item.suit for item in trick_cards + [card] if not is_trump(item)}
                if wins and len(colors) >= 2:
                    bonus += 35
            elif goal.id == "win_trick_six_points":
                if wins and trick_points + card.points >= 6:
                    bonus += 35
            elif goal.id in {"win_blue_3", "win_red_3", "win_green_3", "win_yellow_3"}:
                if card.suit == target_card[0] and card.rank == target_card[1]:
                    bonus += 12
                    if wins or (not self.trick and len(self.hands[player]) <= 1):
                        bonus += 45
            elif goal.id == "win_two_tricks_in_row":
                if wins and self.trick_winners[-1:] == [player]:
                    bonus += 35
            elif goal.id == "win_exactly_three_tricks":
                if wins and len(self.won_tricks[player]) < 3:
                    bonus += 30
                elif wins and len(self.won_tricks[player]) >= 3:
                    bonus -= 50
            elif goal.id == "win_last_trick":
                if len(self.hands[player]) <= 1 and wins:
                    bonus += 50
            return bonus

        return max(
            pool,
            key=lambda card: (
                goal_bonus(card),
                1 if card.id == base_card.id else 0,
                -card.points,
                -int(card.rank),
            ),
        )

    def _choose_base_ai_card(self, player: int) -> Card:
        legal = self.legal_cards(player)
        if not legal:
            raise ValueError("Der Computer hat keine erlaubte Karte.")

        # Beim Anspiel wird bevorzugt eine wertlose Farbkarte abgelegt.
        # Gibt es keine 5, wird ein möglichst billiger roter Trumpf gespielt.
        if not self.trick:
            color_fives = [card for card in legal if not is_trump(card) and card.rank == "5"]
            if color_fives:
                color_counts = {
                    suit: sum(
                        not is_trump(hand_card) and hand_card.suit == suit
                        for hand_card in self.hands[player]
                    )
                    for suit in SUITS
                }
                color_points = {
                    suit: sum(
                        hand_card.points
                        for hand_card in self.hands[player]
                        if not is_trump(hand_card) and hand_card.suit == suit
                    )
                    for suit in SUITS
                }
                # Eine 5 in der kürzesten Farbkarte entlastet diese Farbe
                # und lässt längere Farben als spätere Bedienoption offen.
                # Bei gleicher Länge wird die Farbe mit dem höheren
                # Punktepotenzial zuerst eröffnet.
                return min(
                    color_fives,
                    key=lambda card: (
                        color_counts[card.suit],
                        -color_points[card.suit],
                        self._card_value(card),
                    ),
                )

            red_trumps = [card for card in legal if card.suit == "Rot"]
            if red_trumps:
                return min(red_trumps, key=lambda card: self._card_value(card))

            non_black = [card for card in legal if card.suit != "Schwarz"]
            return min(non_black or legal, key=lambda card: self._card_value(card))

        lead = self.trick[0].card
        best_player, best_card = self._current_trick_winner()
        teammate = self.known_teammate(player)

        # Wenn der Partner bereits gewinnt, wird der Stich nicht überboten.
        # Stattdessen wird möglichst eine wertvolle Karte abgegeben.
        if teammate == best_player:
            safe_cards = [card for card in legal if not beats(card, best_card, lead)]
            safe_color_cards = [card for card in safe_cards if not is_trump(card)]
            return max(safe_color_cards or safe_cards or legal, key=lambda card: (card.points, int(card.rank)))

        if is_trump(lead):
            # Trumpfstich: gerade so hoch über den aktuellen Gewinner gehen.
            winning_trumps = [card for card in legal if beats(card, best_card, lead)]
            if winning_trumps:
                return min(winning_trumps, key=lambda card: (int(card.rank), card.points))
        else:
            # Farbspiel: möglichst einen wertvollen Farbpunkt stechen.
            winning_color_cards = [
                card for card in legal
                if not is_trump(card) and card.suit == lead.suit and beats(card, best_card, lead)
            ]
            if winning_color_cards:
                return max(winning_color_cards, key=lambda card: (card.points, int(card.rank)))

            # Wenn die Farbe nicht bedient werden kann, mit dem kleinsten
            # ausreichenden Trumpf übernehmen.
            winning_trumps = [card for card in legal if is_trump(card) and beats(card, best_card, lead)]
            if winning_trumps:
                return min(winning_trumps, key=lambda card: (int(card.rank), card.points))

        # Kein sinnvoller Stichgewinn: möglichst wenige Punkte wegwerfen.
        return min(legal, key=lambda card: self._card_value(card))

    @staticmethod
    def _card_value(card: Card) -> tuple[int, int, bool]:
        """Sortierschlüssel für billige Abwurfkarten."""
        return card.points, int(card.rank), is_trump(card)

    def _current_trick_winner(self) -> tuple[int, Card]:
        winner = self.trick[0].player
        lead = self.trick[0].card
        for played in self.trick[1:]:
            current = next(item.card for item in self.trick if item.player == winner)
            if beats(played.card, current, lead):
                winner = played.player
        winning_card = next(item.card for item in self.trick if item.player == winner)
        return winner, winning_card

    def known_teammate(self, player: int) -> int | None:
        """Gibt nur einen Partner zurück, den der Spieler regelkonform kennt."""
        has_black_six = any(
            card.suit == "Schwarz" and card.rank == "6"
            for card in self.hands[player]
        ) or player in self.revealed_black_players

        if has_black_six:
            known_black_partners = self.revealed_black_players - {player}
            return min(known_black_partners) if known_black_partners else None

        # Erst wenn beide schwarzen 6er offen liegen, ist für Team Hell
        # der andere Nicht-Schwarz-Spieler eindeutig bekannt.
        if len(self.revealed_black_players) == 2:
            known_black = set(self.revealed_black_players)
            possible_partners = set(range(4)) - known_black - {player}
            if len(possible_partners) == 1:
                return next(iter(possible_partners))
        return None

    def view_for(self, player: int = 0, include_result: bool = True) -> dict[str, Any]:
        result = None
        if include_result and self.phase == Phase.FINISHED and self.round_result is not None:
            result = dict(self.round_result)
            result["players"] = [
                {"player": seat, "team": self.teams[seat], "score": self.scores[seat]}
                for seat in range(4)
            ]
        if self.selected_goals[player] is None:
            viewer_message = "Wähle einen Auftrag."
        elif not self._goal_selection_complete():
            viewer_message = "Warte auf die Auftragsauswahl der anderen Spieler."
        elif self.phase == Phase.FINISHED:
            viewer_message = self.last_message
        elif self.current_player == player:
            viewer_message = "Du bist am Zug."
        else:
            viewer_message = f"{self.player_names[self.current_player]} ist am Zug."
        displayed_trick = self.trick if self.trick else self.last_trick
        team_status = [
            "black" if player in self.revealed_black_players
            else "white" if len(self.revealed_black_players) == 2
            else "unknown"
            for player in range(4)
        ]
        return {
            "game_id": self.id,
            "phase": self.phase.value,
            "current_player": self.current_player,
            "trick_number": self.trick_number,
            "scores": self.scores,
            "match_scores": self.match_scores,
            "round_number": self.round_number,
            "max_rounds": MAX_ROUNDS,
            "start_player": self.start_player,
            "start_player_name": self.player_names[self.start_player],
            "player_names": self.player_names,
            "revealed_black_players": sorted(self.revealed_black_players),
            "team_status": team_status,
            "result": result,
            "goal_selection_required": self.selected_goals[player] is None,
            "waiting_for_goals": not self._goal_selection_complete(),
            "goal_options": [
                self._goal_view(goal) for goal in self.goal_options[player]
            ] if self.selected_goals[player] is None else [],
            "selected_goal": self._goal_view(self.selected_goals[player]),
            "completed_goal": self._goal_view(self.completed_goals[player]),
            "revealed_goals": self.revealed_goals,
            "hand": [
                {"id": c.id, "label": c.label, "suit": c.suit, "rank": c.rank, "points": c.points, "trump": is_trump(c)}
                for c in self.hands[player]
            ],
            "trick": [
                {
                    "player": x.player,
                    "label": x.card.label,
                    "suit": x.card.suit,
                    "rank": x.card.rank,
                    "points": x.card.points,
                    "trump": is_trump(x.card),
                    "winner": self.last_trick_winner == x.player if not self.trick else False,
                }
                for x in displayed_trick
            ],
            "hand_counts": [len(hand) for hand in self.hands],
            "legal_ids": [
                c.id for c in self.legal_cards(player)
            ] if (
                self.phase == Phase.PLAYING
                and self.current_player == player
                and self._goal_selection_complete()
                and self.selected_goals[player] is not None
            ) else [],
            "message": viewer_message,
            "history": self.history[-8:],
        }


class GameManager:
    def __init__(self) -> None:
        self.games: dict[str, Game] = {}

    def new_game(self) -> Game:
        game = Game()
        game.start(
            round_number=1,
            match_scores=[0, 0, 0, 0],
            start_player=random.randrange(4),
            automated_players={1, 2, 3},
        )
        self.games[game.id] = game
        return game

    def next_round(self, game_id: str) -> Game:
        game = self.get(game_id)
        if game.phase != Phase.FINISHED:
            raise ValueError("Die aktuelle Partie ist noch nicht beendet.")
        if game.match_finished:
            raise ValueError("Das Gesamtspiel ist bereits beendet.")
        game.start(
            round_number=game.round_number + 1,
            match_scores=game.match_scores,
            start_player=(game.start_player + 1) % 4,
            automated_players={1, 2, 3},
        )
        return game

    def get(self, game_id: str) -> Game:
        if game_id not in self.games:
            raise KeyError(game_id)
        return self.games[game_id]
