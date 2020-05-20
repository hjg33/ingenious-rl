from copy import deepcopy

import pygame as pg
from pygame.locals import *

from ui.display import Display
from game.gameplay import get_gameplay
from game.misc import game_to_display_coords, display_to_game_coords
from game.misc import display_to_game_move, game_to_display_move, flip_tile
from game.board import Move

LOGO_PATH = '../imgs/logo.png'

class EventHandler:
    def __init__(self, display, controller):
        self.display = display
        self.controller = controller

    def await_click(self):
        """Awaits click and returns click coords"""
        while self.controller.running:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    self.controller.running = False
                    return 0, 0
                elif event.type == pg.MOUSEBUTTONDOWN:
                    x, y = event.pos
                    return x, y

    def request_tile_selection(self):
        while self.controller.running:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    self.controller.running = False
            x, y = self.await_click()
            for ref, hex in self.display.eg_rects.items():
                if hex.collidepoint(x, y):
                    return self.display.choice_to_col_map[ref]

    def request_hex_selection(self):
        while self.controller.running:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    self.controller.running = False
            x, y = self.await_click()
            for ref, hex in self.display.hex_rects.items():
                if hex.collidepoint(x, y):
                    return ref

    def confirm(self):
        while self.controller.running:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    self.controller.running = False
            x, y = self.await_click()
            if self.display.confirm_rect.collidepoint(x, y):
                return True
            if self.display.cancel_rect.collidepoint(x, y):
                return False

class Controller:
    def __init__(self, params):
        pg.init()
        pg.display.set_icon(pg.image.load(LOGO_PATH))
        pg.display.set_caption("ingenious")

        self.running = True
        self.size = (985, 650)
        self.screen = pg.display.set_mode(self.size)
        self.clock = pg.time.Clock()

        self.last_move = None

        self.params = params
        self.display = Display(self.screen, params)
        self.event_handler = EventHandler(self.display, self)
        self.gameplay = get_gameplay(params)

        self.request = []
        self.reponse = []

    def request_pick_up_tile(self, player):
        while self.running:
            self.display.display_messages(
                line1="Player {} to pick up:".format(player),
                line2="Select the first colour")
            pg.display.flip()
            colour1 = self.event_handler.request_tile_selection()
            self.display.draw_eg_1(colour1)
            self.display.display_messages(
                line1="Player {} to pick up:".format(player),
                line2="Select the second colour")
            pg.display.flip()
            colour2 = self.event_handler.request_tile_selection()
            self.display.draw_eg_2(colour2)
            self.display.display_messages(
                line1="Player {}:".format(player),
                line2="Confirm tile selection.")
            self.display.draw_confirm_cancel()
            pg.display.flip()
            if self.event_handler.confirm():
                tile = (colour1, colour2)
                self.display.reset_eg()
                self.display.add_tile_to_deck(player, tile)
                return tile
            self.display.reset_eg()
            self.display.hide_confirm_cancel()
            pg.display.flip()

    def display_message(self, message_1=None, message_2=None):
        self.display.draw_confirm_cancel()
        self.display.display_messages(
            line1=message_1,
            line2=message_2,
            line3="Click 'Confirm' to confirm")
        pg.display.flip()
        while not self.event_handler.confirm() and self.running:
            pass
        self.display.hide_confirm_cancel()
        pg.display.flip()

    def update_deck(self, player):
        self.display_message(message_1="Player {} picking up.".format(player))
        player_deck = deepcopy(self.display.deck[player])
        for tile in self.gameplay.players[player].deck.iterator():
            if tile not in player_deck and flip_tile(tile) not in player_deck:
                self.display.add_tile_to_deck(player, tile)
            if tile in player_deck:
                player_deck.remove(tile)
            elif flip_tile(tile) in player_deck:
                player_deck.remove(flip_tile(tile))

    def draw_move(self, player, move):
        self.display_message(message_1="Player {} to move:".format(player))
        self.display.clear_last_move()
        tile = []
        for hex in move.iterator():
            x, y = game_to_display_coords(hex)
            colour = hex[2]
            tile.append(colour)
            self.display.draw_hex_select_blue_dark((x, y))
            self.display.draw_hex_tile((x, y), colour)
        move_display = game_to_display_move(move)
        self.display.set_last_move(move_display)
        self.display.remove_tile_from_deck(player, tuple(tile))
        pg.display.flip()
        self.display_message(message_1="Player {} has moved".format(player))

    def request_make_move(self, player):
        while self.running:
            self.display.hide_confirm_cancel()
            self.display.display_messages(
                line1="Player {} to move:".format(player),
                line2="Select the first colour")
            colour1 = self.event_handler.request_tile_selection()
            self.display.highlight_choice_colour(colour1)
            pg.display.flip()
            self.display.display_messages(
                line1="Player {} to move:".format(player),
                line2="Place colour on board")
            coords1 = self.event_handler.request_hex_selection()
            self.display.draw_new_choice_map()
            self.display.draw_hex_select_red_dark(coords1)
            self.display.draw_hex_tile(coords1, colour1)
            self.display.draw_new_choice_map()
            pg.display.flip()

            self.display.display_messages(
                line1="Player {} to move:".format(player),
                line2="Select the second colour")
            colour2 = self.event_handler.request_tile_selection()
            self.display.draw_new_choice_map()
            self.display.highlight_choice_colour(colour2)
            pg.display.flip()
            self.display.display_messages(
                line1="Player {} to move:".format(player),
                line2="Place colour on board")
            coords2 = self.event_handler.request_hex_selection()
            self.display.draw_new_choice_map()
            self.display.draw_hex_select_red_dark(coords2)
            self.display.draw_hex_tile(coords2, colour2)
            self.display.draw_new_choice_map()
            self.display.draw_confirm_cancel()
            pg.display.flip()

            self.display.display_messages(
                line1="Player {} to move:".format(player),
                line2="Please confirm move")
            if self.event_handler.confirm():
                move_display = Move(coords1, coords2, colour1, colour2)
                move_game = display_to_game_move(move_display)
                tile_display = (move_display.colour1, move_display.colour2)
                if self.gameplay.board.check_move_is_legal(move_game) and \
                        self.display.tile_is_in_deck(player, tile_display):
                    self.display.clear_last_move()
                    self.display.set_last_move(move_display)
                    self.display.remove_tile_from_deck(player, tuple(tile_display))
                    pg.display.flip()
                    return move_game
                else:
                    self.display.display_messages(
                        line1="Player {}: move was illegal:".format(player),
                        line2="Choose a different move",
                        line3="Press confirm to continue")
                    self.display.draw_confirm_cancel()
                    pg.display.flip()
                    while not self.event_handler.confirm() and self.running:
                        pass
                    self.display.clear_move(move_display)

            self.display.draw_hex_light(coords1)
            self.display.draw_hex_light(coords2)
            self.display.hide_confirm_cancel()
            pg.display.flip()

    def loop(self):
        self.response = Response()

        for action in self.request.action_iterator():
            if action["type"] == "display_message":
                self.display_message(message_1=action["body"])
            elif action["type"] == "make_move":
                self.draw_move(action["player"], action["body"])
            elif action["type"] == "update_score":
                self.display.draw_score(action["player"], action["body"])
            elif action["type"] == "update_deck":
                self.update_deck(action["player"])
            elif action["type"] == "request_pickup":
                tiles = []
                for _ in range(action["body"]):
                    tiles.append(self.request_pick_up_tile(action["player"]))
                self.response.add_tiles_picked_up(action["player"], tiles)
            elif action["type"] == "request_exchange":
                tiles = []
                self.display_message(
                    message_1="Player {} exchanges".format(action["player"]),
                    message_2="all their tiles.")
                for tile in self.display.deck[action["player"]]:
                    if tile is not None:
                        self.display.remove_tile_from_deck(action["player"], tile)
                for _ in range(6):
                    tiles.append(self.request_pick_up_tile(action["player"]))
                self.response.add_tiles_picked_up(action["player"], tiles)
            elif action["type"] == "computer_exchange_tiles":
                self.display_message(
                    message_1="Player {} chooses".format(action["player"]),
                    message_2="to exchange tiles")
                for tile in self.display.deck[action["player"]]:
                    if tile is not None:
                        self.display.remove_tile_from_deck(action["player"], tile)
                self.display_message(
                    message_1="Player {}:".format(action["player"]),
                    message_2="picking new tiles")
                self.update_deck(action["player"])
            elif action["type"] == "possible_exchange":
                tiles = []
                self.display.display_messages(
                    line1="Player {}:".format(action["player"]),
                    line2="Exchange tiles?")
                if self.event_handler.confirm():
                    self.gameplay.players[action["player"]].exchange_tiles()
                    self.display_message(
                        message_1="Player {}:".format(action["player"]),
                        message_2="Discarding tiles")
                    for tile in self.display.deck[action["player"]]:
                        if tile is not None:
                            self.display.remove_tile_from_deck(action["player"], tile)
                else:
                    self.display_message(
                        message_1="Player {}:".format(action["player"]),
                        message_2="Picking up")
                    self.gameplay.players[action["player"]].pick_up()
                self.update_deck(action["player"])
                self.display.hide_confirm_cancel()
                pg.display.flip()
            elif action["type"] == "request_move":
                move = self.request_make_move(action["player"])
                self.response.add_move_made(action["player"], move)
            elif action["type"] == "game_finished":
                if action["body"] == 0:
                    self.display_message(
                        message_1="Wow! It's a draw! :O")
                else:
                    player_name = self.params[action["body"]]["name"]
                    self.display_message(
                        message_1="{} wins!".format(player_name))
                self.running = False
            else:
                raise ValueError('Unrecognised request item.')

    def render(self):
        pg.display.flip()

    def start_game_sequence(self):
        self.display.draw_start_game()
        self.display.display_messages(
            line1="Select 'Start Game' to get",
            line2="going"
            )
        pg.display.flip()
        while not self.event_handler.confirm() and self.running:
            pass
        self.display.draw_player_choices()
        pg.display.flip()
        if self.params["game_type"] == "real":
            self.display.display_messages(line1="Enter which player starts")
            player_selection = self.event_handler.confirm()
            player_to_start = 1 if player_selection else 2
            self.display.hide_confirm_cancel()
            pg.display.flip()
        else:
            player_to_start = None
        return player_to_start

    def run(self):
        self.render()
        player_to_start = self.start_game_sequence()
        self.gameplay.initialise_game(player_to_start)
        self.request = self.gameplay.get_initial_request()
        self.loop()
        while self.running:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    self.running = False
            self.request = self.gameplay.next(self.response)
            self.loop()
            self.render()
            self.clock.tick(10)

class Response:
    def __init__(self):
        self.actions = []

    def add_move_made(self, player, move):
        action = {"player": player,
                  "type": "move_made",
                  "body": move}
        self.actions.append(action)

    def add_tiles_picked_up(self, player, tiles):
        action = {"player": player,
                  "type": "tiles_picked_up",
                  "body": tiles}
        self.actions.append(action)

    def add_update_deck(self, player, tiles):
        action = {"player": player,
                  "type": "update_deck",
                  "body": deck}
        self.actions.append(action)

    def action_iterator(self):
        for action in self.actions:
            yield action
