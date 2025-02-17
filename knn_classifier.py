#!/usr/bin/env python3

import os
import json
import itertools
import argparse
import numpy as np
import matplotlib.pyplot as plt

from sklearn.neighbors import KNeighborsClassifier
from sklearn.model_selection import cross_validate
from feature_selection import *  # noqa F403
from feature_selection import ALL_FEATURES
from prepare_training_data import CLIENTS

K = 5
WEIGHTS = "distance"

MIN_GUESS_THRESHOLD = 0.20
CONFIDENCE_THRESHOLD = 0.95

DEFAULT_FEATURES = ["percent_redundant", "percent_pairwise_ordered", "norm_reward"]

VIABLE_FEATURES = [
    "percent_pairwise_ordered",
    "percent_redundant",
    "norm_reward",
    "norm_reward_per_slot",
    "median_density",
]

ENABLED_CLIENTS = ["Lighthouse", "Nimbus", "Prysm", "Teku"]


def all_feature_vecs_with_dimension(dimension):
    return sorted(map(list, itertools.combinations(VIABLE_FEATURES, dimension)))


def all_client_groupings_with_dimension(dimension):
    return sorted(map(list, itertools.combinations(ENABLED_CLIENTS, dimension)))


def into_feature_row(block_reward, features):
    return [ALL_FEATURES[feature](block_reward) for feature in features]


class Classifier:
    def __init__(
        self, data_dir, grouped_clients=[], features=DEFAULT_FEATURES, enable_cv=False
    ):
        feature_matrix = []
        training_labels = []

        enabled_clients = []
        other_index = CLIENTS.index("Other")

        for i, client in enumerate(CLIENTS):
            client_dir = os.path.join(data_dir, client)

            if os.path.exists(client_dir):
                if client not in grouped_clients:
                    enabled_clients.append(client)
            else:
                if client == "Other" and len(grouped_clients) > 0:
                    enabled_clients.append(client)
                continue

            for reward_file in os.listdir(client_dir):
                with open(os.path.join(client_dir, reward_file), "r") as f:
                    block_reward = json.load(f)

                feature_matrix.append(into_feature_row(block_reward, features))

                if client in grouped_clients:
                    training_labels.append(other_index)
                else:
                    training_labels.append(i)

        feature_matrix = np.array(feature_matrix)

        knn = KNeighborsClassifier(n_neighbors=K, weights=WEIGHTS)

        if enable_cv:
            self.scores = cross_validate(
                knn, feature_matrix, training_labels, scoring="balanced_accuracy"
            )
        else:
            self.scores = None

        knn.fit(feature_matrix, training_labels)

        self.knn = knn
        self.enabled_clients = enabled_clients
        self.features = features

        self.feature_matrix = feature_matrix
        self.training_labels = training_labels

    def classify(self, block_reward):
        row = into_feature_row(block_reward, self.features)
        res = self.knn.predict_proba([row])

        prob_by_client = {
            client: res[0][i] for i, client in enumerate(self.enabled_clients)
        }

        multilabel = compute_multilabel(
            compute_guess_list(prob_by_client, self.enabled_clients)
        )

        label = compute_best_guess(prob_by_client)

        return (label, multilabel, prob_by_client)

    def plot_feature_matrix(self, output_path):
        fig = plt.figure()

        ax = fig.add_subplot(projection="3d")

        x = self.feature_matrix[:, 0]
        y = self.feature_matrix[:, 1]
        z = self.feature_matrix[:, 2]

        scatter = ax.scatter(
            x, y, z, c=self.training_labels, marker=".", alpha=0.25, cmap="Set1"
        )

        handles, _ = scatter.legend_elements()
        labels = self.enabled_clients

        legend1 = ax.legend(handles, labels, loc="best", title="Client")
        ax.add_artist(legend1)

        ax.set_xlabel("redundant %")
        ax.set_ylabel("ordered %")
        ax.set_zlabel("reward norm")

        fig.savefig(output_path)


def compute_guess_list(probability_map, enabled_clients) -> list:
    guesses = []
    for client in enabled_clients:
        if probability_map[client] > CONFIDENCE_THRESHOLD:
            return [client]
        elif probability_map[client] > MIN_GUESS_THRESHOLD:
            guesses.append(client)
    return guesses


def compute_multilabel(guess_list):
    if len(guess_list) == 1:
        return guess_list[0]
    elif len(guess_list) == 2:
        return f"{guess_list[0]} or {guess_list[1]}"
    else:
        return "Uncertain"


def compute_best_guess(probability_map) -> str:
    return max(
        probability_map.keys(),
        key=lambda client: probability_map[client],
        default="Uncertain",
    )


def parse_args():
    parser = argparse.ArgumentParser("KNN testing and cross validation")

    parser.add_argument("data_dir", help="training data directory")
    parser.add_argument("--classify", help="data to classify")
    parser.add_argument(
        "--cv", action="store_true", dest="enable_cv", help="enable cross validation"
    )
    parser.add_argument(
        "--cv-group", default=0, type=int, help="number of clients to group for CV"
    )
    parser.add_argument(
        "--cv-num-features", type=int, help="feature dimensionality for CV"
    )
    parser.add_argument(
        "--group", default=[], nargs="+", help="clients to group during classification"
    )

    return parser.parse_args()


def main():
    args = parse_args()
    data_dir = args.data_dir
    classify_dir = args.classify
    enable_cv = args.enable_cv
    num_grouped = args.cv_group
    num_features = args.cv_num_features
    grouped_clients = args.group

    if enable_cv:
        print("performing cross validation")
        if num_features is None:
            feature_vecs = [DEFAULT_FEATURES]
        else:
            feature_vecs = all_feature_vecs_with_dimension(num_features)

        for grouped_clients in all_client_groupings_with_dimension(num_grouped):
            for feature_vec in feature_vecs:
                print(f"features: {feature_vec}")
                classifier = Classifier(
                    data_dir,
                    grouped_clients=grouped_clients,
                    features=feature_vec,
                    enable_cv=True,
                )
                print(f"enabled clients: {classifier.enabled_clients}")
                print(f"classifier scores: {classifier.scores['test_score']}")
        return

    assert classify_dir is not None, "classify dir required"
    print(f"classifying all data in directory {classify_dir}")
    print(f"grouped clients: {grouped_clients}")
    classifier = Classifier(data_dir, grouped_clients=grouped_clients)

    frequency_map = {}
    total_blocks = 0

    for input_file in os.listdir(classify_dir):
        print(f"classifying rewards from file {input_file}")
        with open(os.path.join(classify_dir, input_file), "r") as f:
            block_rewards = json.load(f)

        for block_reward in block_rewards:
            _, multilabel, prob_by_client = classifier.classify(block_reward)

            if multilabel not in frequency_map:
                frequency_map[multilabel] = 0

            frequency_map[multilabel] += 1

        total_blocks += len(block_rewards)

    print(f"total blocks processed: {total_blocks}")

    for multilabel, num_blocks in sorted(frequency_map.items()):
        percentage = round(num_blocks / total_blocks, 4)
        print(f"{multilabel},{percentage}")


if __name__ == "__main__":
    main()
