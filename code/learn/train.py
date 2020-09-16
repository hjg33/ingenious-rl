import os
import time
import argparse
from itertools import product
from shutil import copyfile

import numpy as np
from tqdm import tqdm

import torch
from torch import nn
from torch import optim
from torch.utils.tensorboard import SummaryWriter

from utils.io import load_json, write_json, make_dir_if_not_exists
from game.player import get_player
from game.gameplay import get_gameplay

from learn.network import get_network
from learn.replay_buffer import ReplayBuffer
from learn.representation import RepresentationGenerator
from learn.visualisation import generate_debug_visualisation
from learn.train_utils import set_model_to_half, set_optimizer_learning_rate

class Params: 
    def __init__(self, d):
        self.__dict__ = d

    def save(self, save_path):
        output_path = os.path.join(save_path, "config.json")
        write_json(output_path, self.__dict__)

class SelfPlayTrainingSession:
    def __init__(self, config):
        self.config = config
        self.p = Params(self.config)
        self.game = get_gameplay(self.config)
        self.repr = RepresentationGenerator()
        self.replay_buffer = ReplayBuffer(self.config)

        self.logs_dir = "logs/self_play_{}_{}".format(self.p.network_type, time.strftime("%Y-%m-%d_%H-%M"))
        self.logs_base_str = os.path.join(self.logs_dir, "ckpt-{}.pth")

        make_dir_if_not_exists(self.logs_dir)
        make_dir_if_not_exists(os.path.join(self.logs_dir, "tensorboard"))
        self.p.save(self.logs_dir)

        self.best_self_ckpt_path = os.path.join(self.logs_dir, "best_self.pth")
        self.best_rule_ckpt_path = os.path.join(self.logs_dir, "best_rule.pth")
        self.latest_ckpt_path = os.path.join(self.logs_dir, "latest.pth")

        if self.p.restore_ckpt_dir is not None:
            self.load_training_state(self.p.restore_ckpt_dir)
        else:
            self.steps_since_improvement = 0

        self.best_self_model_step = 0
        self.best_rule_model_step = 0

        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.net = self.get_new_network()
        print("Training using device:", self.device)

        self.optimizer = optim.SGD(
            self.net.parameters(),
            lr=self.p.initial_learning_rate,
            momentum=0.9,
            weight_decay=self.p.weight_decay,
            )
        self.lr_tracker = self.p.initial_learning_rate
        self.loss_criterion = nn.MSELoss(reduction='mean')

        if self.p.effective_batch_size % self.p.max_train_batch_size == 0:
            self.accumulate_loss_n_times = self.p.effective_batch_size // self.p.max_train_batch_size
        else:
            self.accumulate_loss_n_times = self.p.effective_batch_size // self.p.max_train_batch_size + 1

        self.strategy_types = ["random", "max", "increase_min", "reduce_deficit", "mixed"]
        self.writer = SummaryWriter(os.path.join(self.logs_dir, "tensorboard"))
        print(f"Writing logs to: {self.logs_dir}")

    def get_new_network(self):
        net = get_network(self.config).to(self.device)
        set_model_to_half(net)
        return net

    def save_model(self, filename):
        self.net.train()
        torch.save(self.net.state_dict(), filename)

    def load_model(self, filename):
        self.net.load_state_dict(torch.load(filename, map_location=self.device))
        set_model_to_half(self.net)

    def save_optimiser(self):
        torch.save(self.optimizer.state_dict(), os.path.join(self.logs_dir, "optimiser_state.pth"))

    def load_optimiser(self, load_dir):
        optimiser_path = os.path.join(load_dir, "optimiser_state.pth")
        self.optimizer.load_state_dict(torch.load(optimiser_path, map_location=self.device))

    def save_training_state(self):
        state = {
            "steps_since_improvement": self.steps_since_improvement,
            "best_self_model_step": self.best_self_model_step,
            "best_rule_model_step": self.best_rule_model_step,
            "learning_rate": self.optimizer.param_groups[0]['lr'],
        }
        write_json(os.path.join(self.logs_dir, "training_state.json"), state)

    def load_training_state(self, load_dir):
        state = load_json(os.path.join(load_dir, "training_state.json"))
        self.steps_since_improvement = state["steps_since_improvement"]
        self.best_self_model_step = state["best_self_model_step"]
        self.best_rule_model_step = state["best_rule_model_step"]
        self.lr_tracker = state["learning_rate"]

    def log_network_weights_hists(self, step):
        for name, params in self.net.named_parameters():
            self.writer.add_histogram(f"weights/{name}", params, global_step=step)

    def initialise_rule_based_players(self):
        self.players = {}
        for strat in self.strategy_types:
            self.players[strat] = get_player("computer", None, strat)

        self.players_rand = {1: [], 2: []}
        for p, strat in product([1, 2], self.strategy_types):
            self.players_rand[p].append(get_player("computer", None, strat))

    def fill_replay_buffer(self, p1, p2):
        print("Filling replay buffer")
        while self.replay_buffer.is_not_full():
            print(f"Filled {len(self.replay_buffer)} / {self.replay_buffer.buffer_size}")
            _, new_reprs = self.game.generate_episode(p1, p2)
            self.replay_buffer.add(new_reprs)

    def apply_learning_update(self):
        self.optimizer.zero_grad()
        loss_invalid = True

        while loss_invalid:
            for _ in range(self.accumulate_loss_n_times):
                loss_invalid = False
                inputs, labels = self.replay_buffer.sample_training_minibatch()

                grid_input_device = torch.tensor(inputs[0][0], dtype=torch.float16, device=self.device)
                grid_vector_device = torch.tensor(inputs[0][1], dtype=torch.float16, device=self.device)
                vector_input_device = torch.tensor(inputs[1], dtype=torch.float16, device=self.device)

                labels[labels == 0] = -1
                labels_device = torch.tensor(labels, dtype=torch.float16, device=self.device)

                predictions = self.net(grid_input_device, grid_vector_device, vector_input_device)

                loss = self.loss_criterion(torch.squeeze(predictions), torch.squeeze(labels_device))

                if not torch.isnan(loss) and not torch.isinf(loss):
                    normalised_loss = loss / float(self.accumulate_loss_n_times)
                    normalised_loss.backward()
                else:
                    print("\n\nInvalid loss encountered!\n\n")
                    loss_invalid = True

        self.optimizer.step()

        labels_np = np.squeeze(labels).astype(np.float32)
        loss_np = normalised_loss.detach().cpu().numpy().astype(np.float32)
        predictions_np = torch.squeeze(predictions).detach().cpu().numpy().astype(np.float32)

        diffs = np.abs(labels_np - predictions_np)
        mean_abs_error = np.mean(diffs) 
        return loss_np, mean_abs_error, (inputs, labels_np, predictions_np)

    def apply_n_learning_updates(self, n):
        self.net.train()

        loss_sum = 0.
        abs_error_sum = 0.
        for _ in range(int(n)):
            loss, abs_error, vis_inputs = self.apply_learning_update()
            loss_sum += loss
            abs_error_sum += abs_error

        avg_loss = loss_sum / float(n)
        mean_abs_error = abs_error_sum / float(n)
        return avg_loss, mean_abs_error, vis_inputs

    def add_n_games_to_replay_buffer(self, p1, p2, n):
        for i in range(int(n)):
            _, new_reprs = self.game.generate_episode(p1, p2)
            self.replay_buffer.add(new_reprs)

    def play_n_test_games(self, p1, p2, n, learn=True):
        num_wins = 0
        avg_loss_sum = 0
        abs_error_sum = 0.

        episode_fn = self.game.generate_episode if learn else self.game.play_test_game

        for i in tqdm(range(int(n))):
            winner, new_reprs = episode_fn(p1, p2)
            if learn:
                self.replay_buffer.add(new_reprs)
                avg_loss, abs_error, _ = self.apply_n_learning_updates(self.p.updates_per_step)
                avg_loss_sum += avg_loss
                abs_error_sum += abs_error
                self.steps_since_improvement += 1

            if winner == 1:
                num_wins += 1

        p1_win_rate = num_wins / float(n)
        avg_avg_loss = avg_loss_sum / float(n)
        mean_abs_error = abs_error_sum / float(n)
        return p1_win_rate, avg_avg_loss, mean_abs_error

    def step_learning_rate_scheduling(self, improved=True, elapsed_steps=0):
        self.steps_since_improvement += elapsed_steps

        if improved:
            self.steps_since_improvement = 0
            print("Model improved...")
            if self.lr_tracker < self.p.restart_learning_rate:
                self.lr_tracker = self.p.restart_learning_rate
                print(f"Increasing learning rate to {self.lr_tracker}")
                set_optimizer_learning_rate(self.optimizer, self.lr_tracker)
        else:
            if self.steps_since_improvement >= int(self.p.reduce_lr_step_threshold):
                print(f"No improvement in {self.p.reduce_lr_step_threshold} steps...")
                self.lr_tracker *= 0.1
                print(f"Reducing learning rate to {self.lr_tracker}")
                set_optimizer_learning_rate(self.optimizer, self.lr_tracker)

        finish_training = False
        if self.lr_tracker < self.p.lowest_learning_rate:
            finish_training = True

        return finish_training

    def add_graph_to_logs(self):
        inputs, _ = self.replay_buffer.sample_training_minibatch()
        grid_input_device = torch.tensor(inputs[0][0], dtype=torch.float16, device=self.device)
        grid_vector_device = torch.tensor(inputs[0][1], dtype=torch.float16, device=self.device)
        vector_input_device = torch.tensor(inputs[1], dtype=torch.float16, device=self.device)
        self.writer.add_graph(self.net, (grid_input_device, grid_vector_device, vector_input_device))

    def train(self):
        self.initialise_rule_based_players()

        self.training_p1 = get_player("computer", None, "rl", params={"max_eval_batch_size": self.p.max_eval_batch_size})
        self.training_p2 = get_player("computer", None, "rl", params={"max_eval_batch_size": self.p.max_eval_batch_size})
        self.training_p1.strategy.set_model(self.get_new_network())
        self.training_p2.strategy.set_model(self.get_new_network())

        self.test_player = get_player("computer", None, "rl", params={"max_eval_batch_size": self.p.max_eval_batch_size})
        self.test_player.strategy.set_model(self.get_new_network())

        if self.p.restore_ckpt_dir is not None:
            print("Loading checkpoint")
            load_ckpt_path = os.path.join(self.p.restore_ckpt_dir, "latest.pth")
            self.load_model(load_ckpt_path)
            self.load_optimiser(self.p.restore_ckpt_dir)
            set_optimizer_learning_rate(self.optimizer, self.lr_tracker)

        self.save_model(self.latest_ckpt_path)

        if self.p.restore_ckpt_dir is not None:
            load_ckpt_path = os.path.join(self.p.restore_ckpt_dir, "best_self.pth")
        else:
            load_ckpt_path = self.latest_ckpt_path

        self.training_p1.strategy.load_model(load_ckpt_path)
        self.training_p2.strategy.load_model(load_ckpt_path)

        if self.p.restore_ckpt_dir is not None:
            p1 = self.training_p1
            p2 = self.training_p2
        else:
            p1 = get_player("computer", None, "random")
            p2 = get_player("computer", None, "random")

        self.fill_replay_buffer(p1, p2)
        self.add_n_games_to_replay_buffer(self.training_p1, self.training_p2, 2)
        self.net = self.net.to(self.device)

        running_loss, running_error = 0.0, 0.0
        best_win_rate_rule = 0.0
        training_finished = False
        self.add_graph_to_logs()

        print("Start training")
        for i in range(int(self.p.total_training_steps + 1)):
            print("Step {} / {}".format(i, self.p.total_training_steps))

            self.add_n_games_to_replay_buffer(self.training_p1, self.training_p2, self.p.episodes_per_step)
            avg_loss, abs_error, vis_inputs = self.apply_n_learning_updates(self.p.updates_per_step)
            running_loss += avg_loss
            running_error += abs_error

            if i > 0 and i % int(self.p.log_every_n_steps) == 0:
                avg_running_loss = running_loss / float(self.p.log_every_n_steps)
                mean_abs_error = running_error / float(self.p.log_every_n_steps)
                running_loss, running_error = 0.0, 0.0

                self.writer.add_scalar('metrics/learning_rate', self.optimizer.param_groups[0]['lr'], i)
                self.writer.add_scalar('metrics/steps_since_improvement', self.steps_since_improvement, i)
                self.writer.add_scalar('metrics/train_loss', avg_running_loss, i)
                self.writer.add_scalar('metrics/train_error', mean_abs_error, i)
                self.log_network_weights_hists(i)

            if i % int(self.p.vis_every_n_steps) == 0:
                vis_figs = generate_debug_visualisation(vis_inputs)
                self.writer.add_figure('examples', vis_figs, global_step=i)

            if i % int(self.p.test_every_n_steps) == 0:
                self.save_model(self.latest_ckpt_path)
                copyfile(self.latest_ckpt_path, os.path.join(self.logs_dir, f"ckpt-{i}.pth"))
                self.test_player.strategy.load_model(self.latest_ckpt_path)
                self.save_optimiser()

                print(f"Playing {self.p.n_test_games} test games against self")
                self_win_rate, _, _ = self.play_n_test_games(self.test_player, self.training_p1, self.p.n_test_games, learn=False)
                self.writer.add_scalar('win_rates/rl', self_win_rate, i)
                print("Win rate: {:.2f}".format(self_win_rate))

                if self_win_rate > self.p.improvement_threshold:
                    print("Best self model improved!")
                    self.training_p1.strategy.load_model(self.latest_ckpt_path)
                    self.training_p2.strategy.load_model(self.latest_ckpt_path)
                    copyfile(self.latest_ckpt_path, self.best_self_ckpt_path)
                    self.best_self_model_step = i
                    model_improved = True
                else:
                    model_improved = False

                training_finished = self.step_learning_rate_scheduling(improved=model_improved, elapsed_steps=self.p.test_every_n_steps)

                win_rate_rule = 0.0
                for strat in self.strategy_types:
                    print(f"Playing {self.p.n_other_games} test games against {strat}")
                    win_rate, _, _ = self.play_n_test_games(self.test_player, self.players[strat], self.p.n_other_games, learn=False)
                    self.writer.add_scalar(f'win_rates/{strat}', win_rate, i)
                    print("Win rate: {:.2f}".format(win_rate))
                    win_rate_rule += win_rate

                if win_rate_rule >= best_win_rate_rule:
                    best_win_rate_rule = win_rate_rule
                    print("Best rule model improved!")
                    copyfile(self.latest_ckpt_path, self.best_rule_ckpt_path)
                    self.best_rule_model_step = i

                self.save_training_state()

            if training_finished:
                print(f"Training finished after {i} steps...")
                break

        print(f"Final best self model was at step {self.best_self_model_step}")
        print(f"Final best rule model was at step {self.best_rule_model_step}")
        self.writer.close()

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('config_name', default=None)
    args = parser.parse_args()
    return args

def main():
    args = parse_args()
    config = load_json(args.config_name)
    SelfPlayTrainingSession(config).train()

if __name__ == "__main__":
    main()