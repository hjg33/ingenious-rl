from numba import njit
import numpy as np
import torch

from game.board import combine_moves_and_deck
from game.utils import get_other_player, find_winner_fast
from learn.network import get_network

def get_strategy(strategy_type, params=None):
    if strategy_type == "random":
        return RandomStrategy()
    elif strategy_type == "max":
        return MaxStrategy()
    elif strategy_type == "increase_min":
        return IncreaseMinStrategy()
    elif strategy_type == "increase_other_min":
        return IncreaseOtherMinStrategy()
    elif strategy_type == "reduce_deficit":
        return ReduceDeficitStrategy()
    elif strategy_type == "mixed_1":
        return MixedStrategy1()
    elif strategy_type == "mixed_2":
        return MixedStrategy2()
    elif strategy_type == "mixed_3":
        return MixedStrategy3()
    elif strategy_type == "mixed_4":
        return MixedStrategy4()
    elif strategy_type == "rl":
        return RLVanilla(params=params)
    elif strategy_type == "rl_2ply":
        return RL2PlySearch(params=params)
    elif strategy_type == "rl_3ply":
        return RL3PlySearch(params=params)
    else:
        raise ValueError("Invalid strategy type chosen.")

@njit
def choose_random_move(board, deck):
    move_combinations = board.get_all_possible_moves()
    possible_moves = combine_moves_and_deck(move_combinations, deck.get_deck())
    random_idx = np.random.randint(0, high=possible_moves.shape[0] - 1)
    return possible_moves[random_idx]

@njit
def choose_max_scoring_move(board, deck, score):
    move_combinations = board.get_all_possible_moves()
    possible_moves = combine_moves_and_deck(move_combinations, deck.get_deck())
    original_score = np.expand_dims(score.get_score(), 0)
    move_scores = board.batch_calculate_move_scores(possible_moves)
    updated_scores, _, _ = score.batch_peek_next_scores(move_scores)
    scores_diff = updated_scores - original_score
    total_scores_diff = np.sum(scores_diff, axis=1)
    max_index = np.argmax(total_scores_diff)
    return possible_moves[max_index]

@njit
def choose_increase_min_move(board, deck, score):
    move_combinations = board.get_all_possible_moves()
    possible_moves = combine_moves_and_deck(move_combinations, deck.get_deck())

    original_score = score.get_score()
    min_score = np.min(original_score)
    min_idxs = np.where(original_score == min_score)[0]
    num_min_idxs = min_idxs.shape[0]

    moves_scores = board.batch_calculate_move_scores(possible_moves)
    updated_move_scores, _, _ = score.batch_peek_next_scores(moves_scores)
    min_scores = updated_move_scores[:, min_idxs].reshape(-1, num_min_idxs)
    total_min_scores = np.sum(min_scores, axis=1)
    max_total_min_score = np.max(total_min_scores)

    if max_total_min_score > 0:
        max_total_min_scores_idxs = np.where(total_min_scores == max_total_min_score)[0]

        if max_total_min_scores_idxs.shape[0] == 1:
            max_index = max_total_min_scores_idxs[0]
            return possible_moves[max_index]

        best_moves_so_far = possible_moves[max_total_min_scores_idxs, :]
        best_moves_updated_scores = updated_move_scores[max_total_min_scores_idxs, :]
        scores_diff = best_moves_updated_scores - np.expand_dims(original_score, 0)
        total_scores_diff = np.sum(scores_diff, axis=1)
        max_index = np.argmax(total_scores_diff)
        return best_moves_so_far[max_index]

    else:
        # Where you can't increase your lowest score just choose max
        return choose_max_scoring_move(board, deck, score)

@njit
def choose_increase_other_min_move(board, deck, score, other_score):
    move_combinations = board.get_all_possible_moves()
    possible_moves = combine_moves_and_deck(move_combinations, deck.get_deck())

    original_score = score.get_score()
    original_other_score = other_score.get_score()
    min_score = np.min(original_other_score)
    min_idxs = np.where(original_other_score == min_score)[0]
    num_min_idxs = min_idxs.shape[0]

    moves_scores = board.batch_calculate_move_scores(possible_moves)
    updated_move_scores, _, _ = score.batch_peek_next_scores(moves_scores)
    min_scores = updated_move_scores[:, min_idxs].reshape(-1, num_min_idxs)
    total_min_scores = np.sum(min_scores, axis=1)
    max_total_min_score = np.max(total_min_scores)

    if max_total_min_score > 0:
        max_total_min_scores_idxs = np.where(total_min_scores == max_total_min_score)[0]

        if max_total_min_scores_idxs.shape[0] == 1:
            max_index = max_total_min_scores_idxs[0]
            return possible_moves[max_index]

        best_moves_so_far = possible_moves[max_total_min_scores_idxs, :]
        best_moves_updated_scores = updated_move_scores[max_total_min_scores_idxs, :]
        scores_diff = best_moves_updated_scores - np.expand_dims(original_score, 0)
        total_scores_diff = np.sum(scores_diff, axis=1)
        max_index = np.argmax(total_scores_diff)
        return best_moves_so_far[max_index]

    else:
        # Where you can't increase your lowest score just choose max
        return choose_increase_min_move(board, deck, score)

@njit
def choose_reduce_deficit_move(board, deck, score, other_score, margin):
    move_combinations = board.get_all_possible_moves()
    possible_moves = combine_moves_and_deck(move_combinations, deck.get_deck())
    moves_scores = board.batch_calculate_move_scores(possible_moves)
    updated_move_scores, _, _ = score.batch_peek_next_scores(moves_scores)
    other_score = np.expand_dims(other_score.get_score(), 0)

    diffs = other_score - updated_move_scores + 36 + margin

    for i in range(diffs.shape[0]):
        for j in range(diffs.shape[1]):
            if diffs[i, j] < 36:
                diffs[i, j] = 36

    diffs -= 36
    total_diffs = np.sum(diffs, axis=1)
    min_diff = np.min(total_diffs)
    min_diff_idxs = np.where(total_diffs == min_diff)[0]
    num_min_diff_moves = min_diff_idxs.shape[0]

    if num_min_diff_moves == 1:
        return possible_moves[min_diff_idxs[0]]

    original_score = np.expand_dims(score.get_score(), 0)
    scores_diff = updated_move_scores - original_score
    total_scores_diff = np.sum(scores_diff, axis=1)

    min_diff_subset = total_scores_diff[min_diff_idxs]
    max_min_diff_idx = np.argmax(min_diff_subset)

    return possible_moves[min_diff_idxs][max_min_diff_idx]

@njit
def choose_mixed_strategy_1_move(board, deck, score, other_score):
    margin = np.random.randint(0, high=5)
    return choose_reduce_deficit_move(board, deck, score, other_score, margin)

@njit
def choose_mixed_strategy_2_move(board, deck, score, other_score):
    idx = np.random.randint(0, high=5)
    if idx == 0:
        return choose_max_scoring_move(board, deck, score)
    elif idx == 1:
        return choose_increase_min_move(board, deck, score)
    elif idx == 2:
        return choose_increase_other_min_move(board, deck, score, other_score)
    elif idx == 3:
        return choose_reduce_deficit_move(board, deck, score, other_score, 5)
    elif idx == 4:
        return choose_mixed_strategy_1_move(board, deck, score, other_score)
    elif idx == 5:
        return choose_random_move(board, deck)

@njit
def choose_should_exchange(inference):
    if inference:
        return True
    else:
        return np.random.randint(0, high=1) == 1

class RandomStrategy:
    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_random_move(board, deck)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class MaxStrategy:
    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_max_scoring_move(board, deck, score)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class IncreaseMinStrategy:
    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_increase_min_move(board, deck, score)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class IncreaseOtherMinStrategy:
    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_increase_other_min_move(board, deck, score, other_score)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class ReduceDeficitStrategy:
    def __init__(self):
        self.margin = 5

    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_reduce_deficit_move(board, deck, score, other_score, self.margin)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class MixedStrategy1:
    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_mixed_strategy_1_move(board, deck, score, other_score)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class MixedStrategy2:
    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_mixed_strategy_2_move(board, deck, score, other_score)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class MixedStrategy3:
    def __init__(self):
        self.margin = 15

    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_reduce_deficit_move(board, deck, score, other_score, self.margin)
        self.margin = np.max(self.margin - 1, 0)
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class MixedStrategy4:
    def __init__(self):
        self.margin = 5
        self.count = 4

    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        move = choose_reduce_deficit_move(board, deck, score, other_score, self.margin)
        if self.count == 0:
            self.margin = np.max(self.margin - 1, 0)
            self.count = 4
        else:
            self.count -= 1
        should_exchange = choose_should_exchange(inference)
        return (move, should_exchange), 0.5

class RLVanilla:
    def __init__(self, params=None):
        self.model = None
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        if params is not None and "ckpt_path" in params:
            self.model = get_network(params).to(self.device)
            self.load_model(params["ckpt_path"])

        if params is not None and "max_eval_batch_size" in params:
            self.max_batch = params["max_eval_batch_size"]
        else:
            self.max_batch = 1024

    def set_model(self, model):
        self.model = model

    def load_model(self, filename):
        self.model.load_state_dict(torch.load(filename, map_location=self.device))

    def prepare_model_inputs(self, r):
        inputs = (r.board_repr, r.deck_repr, r.scores_repr, r.general_repr)
        inputs = r.augment(*inputs)
        inputs = r.normalise(*inputs)
        inputs = r.prepare(*inputs)
        return inputs

    def predict_values(self, model_inputs):
        """Split predictions over smaller sub-batches to avoid surpassing memory limits"""
        num_inputs = model_inputs[0].shape[0]
        num_full_minibatches = num_inputs // self.max_batch
        remainder = num_inputs % self.max_batch

        if num_full_minibatches == 0:
            return self.run_model(model_inputs)
        else:
            all_values = np.zeros((1,)).astype(np.float32)
            num_passes = num_full_minibatches + 1 if remainder > 0 else num_full_minibatches

            for i in range(num_passes):
                inputs_subset = (
                    model_inputs[0][i * self.max_batch : (i + 1) * self.max_batch, ...],
                    model_inputs[1][i * self.max_batch : (i + 1) * self.max_batch, ...]
                    )
                values_subset = self.run_model(inputs_subset)
                values_subset = values_subset if values_subset.shape else np.expand_dims(values_subset, 0)
                all_values = np.concatenate([all_values, values_subset])

            return all_values[1:]

    def run_model(self, inputs):
        self.model.eval()
        with torch.no_grad():
            grid_inputs = torch.tensor(inputs[0], dtype=torch.float32, device=self.device)
            vector_inputs = torch.tensor(inputs[1], dtype=torch.float32, device=self.device)
            move_values = self.model(grid_inputs, vector_inputs)
            move_values = torch.squeeze(move_values).detach().cpu().numpy()
            move_values = move_values.astype(np.float32)
        return move_values

    def get_representations(self, board, deck, score, other_score, turn_of, repr_fn):
        move_combinations = board.get_all_possible_moves()
        possible_moves = combine_moves_and_deck(move_combinations, deck.get_deck())
        representations, possible_moves_subset = repr_fn(board, deck, score, other_score, turn_of, possible_moves)
        return representations, possible_moves_subset

    def choose_move(self, board, deck, score, other_score, turn_of, repr_fn, inference=False):
        representations, possible_moves_subset = self.get_representations(board, deck, score, other_score, turn_of, repr_fn)
        model_inputs = self.prepare_model_inputs(representations)
        move_values = self.predict_values(model_inputs)

        move_idx = np.argmax(move_values)
        best_move = possible_moves_subset[move_idx]
        should_exchange = representations.general_repr[move_idx, 3]
        best_move_value = move_values[move_idx]

        return (best_move, should_exchange), best_move_value

class _RLNPlySearch(RLVanilla):
    def __init__(self, params=None):
        super().__init__(params=params)

    def mock_move(self, chosen_move, board, score, deck):
        move_score = board.calculate_move_score(chosen_move)
        ingenious, _ = score.update_score(move_score)
        board.update_board(chosen_move)
        tile_to_play = np.array([chosen_move[6], chosen_move[7]], dtype=np.uint8)
        deck.play_tile(tile_to_play)
        return board, score, deck, ingenious

    def mock_turn(self, state, move, move_value, turn_of, repr_fn):
        board, score, other_score, deck = state
        game_finished = False

        while True:
            board, score, deck, ingenious = self.mock_move(move, board, score, deck)

            if board.game_is_finished():
                p1_score = score.get_score_copy()
                p2_score = other_score.get_score_copy()
                winner = find_winner_fast(p1_score, p2_score)
                move_value = 1. if winner == turn_of else -1.
                return (board, score, other_score, deck), True, move_value

            if not ingenious:
                return (board, score, other_score, deck), False, move_value

            representations, possible_moves_subset = self.get_representations(board, deck, score, other_score, turn_of, repr_fn)
            model_inputs = self.prepare_model_inputs(representations)
            move_values = self.predict_values(model_inputs)

            move_idx = np.argmax(move_values)
            move = possible_moves_subset[move_idx]
            move_value = move_values[move_idx]

class RL2PlySearch(_RLNPlySearch):
    def __init__(self, params=None):
        super().__init__(params=params)
        self.search_n = 8

    def choose_move(self, board_original, deck_original, score_original, other_score_original, turn_of, repr_fn, inference=False):
        representations_original, possible_moves_subset_original = self.get_representations(board_original, deck_original, score_original, other_score_original, turn_of, repr_fn)
        model_inputs_original = self.prepare_model_inputs(representations_original)
        move_values_original = self.predict_values(model_inputs_original)

        num_to_search = np.minimum(self.search_n, possible_moves_subset_original.shape[0])
        top_k_indices = np.argsort(move_values_original)[-num_to_search:]
        top_k_values = np.zeros(num_to_search).astype(np.float32)

        for i in range(num_to_search):
            # print(f"searching {i}")
            board = board_original.get_copy()
            score = score_original.get_copy()
            other_score = other_score_original.get_copy()
            deck = deck_original.get_copy()

            move_idx = top_k_indices[i]
            move = possible_moves_subset_original[move_idx]
            move_value = move_values_original[move_idx]

            # Your move
            state = (board, score, other_score, deck)
            (board, score, _, deck), game_finished, move_value = self.mock_turn(state, move, move_value, turn_of, repr_fn)

            if game_finished:
                top_k_values[i] = move_value
                continue

            # Other's move
            representations, possible_moves_subset = self.get_representations(board, deck.create_dummy_deck(), other_score, score, get_other_player(turn_of), repr_fn)
            model_inputs = self.prepare_model_inputs(representations)
            move_values = self.predict_values(model_inputs)

            move_idx = np.argmax(move_values)
            move = possible_moves_subset[move_idx]
            move_value = move_values[move_idx]

            state = (board, other_score, score, deck.create_dummy_deck())
            _, _, move_value = self.mock_turn(state, move, move_value, get_other_player(turn_of), repr_fn)

            top_k_values[i] = -move_value

        best_move_idx = top_k_indices[np.argmax(top_k_values)]
        best_move_value = np.max(top_k_values)

        best_move = possible_moves_subset_original[best_move_idx]
        should_exchange = representations_original.general_repr[best_move_idx, 3]

        return (best_move, should_exchange), best_move_value

class RL3PlySearch(_RLNPlySearch):
    def __init__(self, params=None):
        super().__init__(params=params)
        self.search_n = 8

    def choose_move(self, board_original, deck_original, score_original, other_score_original, turn_of, repr_fn, inference=False):
        representations_original, possible_moves_subset_original = self.get_representations(board_original, deck_original, score_original, other_score_original, turn_of, repr_fn)
        model_inputs_original = self.prepare_model_inputs(representations_original)
        move_values_original = self.predict_values(model_inputs_original)

        num_to_search = np.minimum(self.search_n, possible_moves_subset_original.shape[0])
        top_k_indices = np.argsort(move_values_original)[-num_to_search:]
        top_k_values = np.zeros(num_to_search).astype(np.float32)

        for i in range(num_to_search):
            # print(f"searching {i}")
            board = board_original.get_copy()
            score = score_original.get_copy()
            other_score = other_score_original.get_copy()
            deck = deck_original.get_copy()

            move_idx = top_k_indices[i]
            move = possible_moves_subset_original[move_idx]
            move_value = move_values_original[move_idx]

            # Your first move
            state = (board, score, other_score, deck)
            (board, score, _, deck), game_finished, move_value = self.mock_turn(state, move, move_value, turn_of, repr_fn)

            if game_finished:
                top_k_values[i] = move_value
                continue

            # Other's first move
            representations, possible_moves_subset = self.get_representations(board, deck.create_dummy_deck(), other_score, score, get_other_player(turn_of), repr_fn)
            model_inputs = self.prepare_model_inputs(representations)
            move_values = self.predict_values(model_inputs)

            move_idx = np.argmax(move_values)
            move = possible_moves_subset[move_idx]
            move_value = move_values[move_idx]

            state = (board, other_score, score, deck.create_dummy_deck())
            (board, other_score, _, _), game_finished, move_value = self.mock_turn(state, move, move_value, get_other_player(turn_of), repr_fn)

            if game_finished:
                top_k_values[i] = -move_value
                continue

            # Your second move
            representations, possible_moves_subset = self.get_representations(board, deck, score, other_score, turn_of, repr_fn)
            model_inputs = self.prepare_model_inputs(representations)
            move_values = self.predict_values(model_inputs)

            move_idx = np.argmax(move_values)
            move = possible_moves_subset[move_idx]
            move_value = move_values[move_idx]

            state = (board, score, other_score, deck)
            _, _, move_value = self.mock_turn(state, move, move_value, get_other_player(turn_of), repr_fn)

            top_k_values[i] = move_value

        best_move_idx = top_k_indices[np.argmax(top_k_values)]
        best_move_value = np.max(top_k_values)

        best_move = possible_moves_subset_original[best_move_idx]
        should_exchange = representations_original.general_repr[best_move_idx, 3]

        return (best_move, should_exchange), best_move_value