from tetris.game import Tetris
import torch
import torch.nn as nn
from torch.cuda import amp
import numpy as np
from src.rl import ReplayMemory, Transition, DQN2D
from tetris.constants import play_width, play_height, rows, cols
from random import random, randrange
import matplotlib.pyplot as plt
import os

import datetime
from itertools import count
import math

plt.ion()


def main(opt):

    if torch.cuda.is_available():
        torch.cuda.manual_seed(123)
    else:
        torch.manual_seed(123)

    exp_folder = os.path.join(
        os.getcwd(), f"exp-{datetime.datetime.now()}"[:-7].replace(" ", "-")
    )
    os.mkdir(exp_folder)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    scaler = amp.GradScaler(enabled=(device.type != "cpu"))
    env = Tetris()
    env.reset()

    # states = torch.tensor(env.window_array, dtype=torch.float, device=device).transpose(0, 2)[None, :] / 255.0
    states = torch.tensor(env.binary, dtype=torch.float32, device=device)[None, :]
    policy_net = DQN2D(states.shape[1], rows, cols, env.n_actions).to(device)
    target_net = DQN2D(states.shape[1], rows, cols, env.n_actions).to(device)
    target_net.load_state_dict(policy_net.state_dict())
    target_net.eval()

    optimizer = torch.optim.Adam(policy_net.parameters())
    criterion = nn.MSELoss()

    memory = ReplayMemory(opt.replay_memory_size)

    count_actions = 0

    avg_epoch_tries = []
    avg_losses = []
    avg_rewards = []

    fig, (ax1, ax2, ax3) = plt.subplots(3, sharex=True)

    for epoch in range(opt.epochs):
        epoch_tries, losses, rewards = [], [], []
        for step in count():
            env.tile_fall()
            sample = random()
            eps_threshold = opt.epsilon_end + (
                opt.epsilon_start - opt.epsilon_end
            ) * math.exp(-1.0 * count_actions / opt.epsilon_decay)
            count_actions += 1
            if sample > eps_threshold:
                with torch.no_grad():
                    predictions = policy_net(states)
                    action = predictions.max(1)[1].view(1, 1)
            else:
                action = torch.tensor(
                    [[randrange(env.n_actions)]], device=device, dtype=torch.int64
                )

            reward, done = env.step(action.item())
            reward = torch.tensor([reward], device=device)

            env.display()

            if done:
                env.reset()
            # next_states = torch.tensor(env.window_array, dtype=torch.float, device=device).transpose(0, 2)[None, :] / 255.0
            next_states = torch.tensor(env.binary, dtype=torch.float32, device=device)[
                None, :
            ]

            # Store the transition in memory
            memory.push(states, action, next_states, reward)

            states = next_states

            if len(memory) >= opt.batch_size:
                transitions = memory.sample(opt.batch_size)
                # Transpose the batch (see https://stackoverflow.com/a/19343/3343043 for
                # detailed explanation). This converts batch-array of Transitions
                # to Transition of batch-arrays.
                batch = Transition(*zip(*transitions))

                states_batch = torch.cat(batch.states)
                action_batch = torch.cat(batch.action)
                reward_batch = torch.cat(batch.reward)

                with amp.autocast():
                    # Compute Q(s_t, a) - the model computes Q(s_t), then we select the
                    # columns of actions taken. These are the actions which would've been taken
                    # for each batch state according to policy_net
                    predictions = policy_net(states_batch)
                    action_batch.clamp_(0, 3)
                    state_action_values = predictions.gather(1, action_batch)

                    # Compute V(s_{t+1}) for all next states.
                    # Expected values of actions for non_final_next_states are computed based
                    # on the "older" target_net; selecting their best reward with max(1)[0].
                    # This is merged based on the mask, such that we'll have either the expected
                    # state value or 0 in case the state was final.
                    next_states_batch = torch.cat(batch.next_states)
                    next_state_values = target_net(next_states_batch).max(1)[0].detach()

                    # Compute the expected Q values
                    expected_state_action_values = (
                        next_state_values * opt.gamma
                    ) + reward_batch

                    # print('a', state_action_values)
                    # print('b', expected_state_action_values)
                    # Compute Huber loss
                    loss = criterion(
                        state_action_values.float(),
                        expected_state_action_values.unsqueeze(1).float(),
                    )

                # Optimize the model
                scaler.scale(loss).backward()
                for param in policy_net.parameters():
                    param.grad.data.clamp_(-1, 1)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad()

                epoch_tries.append(step + 1)
                losses.append(loss.item())
                rewards.append(reward.item())
                if done:
                    avg_du = sum(epoch_tries) / len(epoch_tries)
                    avg_loss = sum(losses) / len(losses)
                    avg_reward = sum(rewards) / len(rewards)

                    avg_epoch_tries.append(avg_du)
                    avg_losses.append(avg_loss)
                    avg_rewards.append(avg_reward)
                    ax1.clear()
                    ax2.clear()
                    ax3.clear()
                    ax1.set_ylabel("Avg Steps")
                    ax2.set_ylabel("Avg Loss")
                    ax3.set_ylabel("Avg Reward")
                    ax1.plot(np.array(avg_epoch_tries))
                    ax2.plot(np.array(avg_losses))
                    ax3.plot(np.array(avg_rewards))

                    plt.pause(0.001)  # pause a bit so that plots are updated
                    fig.savefig(os.path.join(exp_folder, "statistics.png"))
                    print(
                        f"Epoch: {epoch}, Avg Try: {avg_du:>6.2f}, Avg Loss: {avg_loss:>7.2f}, Avg Reward: {avg_reward:>6.2f}"
                    )
                    break
        # Update the target network, copying all weights and biases in DQN
        if epoch % opt.target_update == 0:
            target_net.load_state_dict(policy_net.state_dict())
            torch.save(
                policy_net.state_dict(),
                os.path.join(exp_folder, f"epoch-{epoch}-step-{step}.pt"),
            )

    print("Complete")
    env.quit()
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DeepQ with Tetris\n\n")

    parser.add_argument("--epochs", type=int, default=6000)
    parser.add_argument("--create_folder", action="store_true")
    parser.add_argument("--replay_memory_size", type=int, default=10000)
    parser.add_argument("--epsilon_start", type=float, default=1)
    parser.add_argument("--epsilon_decay", type=float, default=200)
    parser.add_argument("--epsilon_end", type=float, default=0.1)
    parser.add_argument("--target_update", type=float, default=10)
    parser.add_argument(
        "--batch_size", type=int, default=512, help="The number of images per batch"
    )
    parser.add_argument("--gamma", type=float, default=0.999)

    args = parser.parse_args()

    main(args)
