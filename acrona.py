import os, re
import numpy as np
import pandas as pd
from collections import defaultdict
from pandarallel import pandarallel
from pandas_profiling import ProfileReport
from sklearn import random_projection
from sklearn.preprocessing import StandardScaler
from sklearn.manifold import TSNE
from sklearn.model_selection import train_test_split
from sklearn.decomposition import KernelPCA
from tqdm import tqdm
from DLC_analysis_additional_functions import *


class get_coordinates:
    """ Class for loading and preprocessing DLC data of individual and social mice. """

    def __init__(
        self,
        video_format=".mp4",
        table_format=".h5",
        path=".",
        exp_conditions=False,
        arena="circular",
        arena_dims=[1],
        smooth_alpha=0.1,
        p=1,
        verbose=True,
        polar_coords=False,
        distances=False,
        ego=False,
    ):
        self.path = path
        self.video_path = self.path + "Videos/"
        self.table_path = self.path + "Tables/"
        self.videos = sorted(
            [vid for vid in os.listdir(self.video_path) if vid.endswith(video_format)]
        )
        self.tables = sorted(
            [tab for tab in os.listdir(self.table_path) if tab.endswith(table_format)]
        )
        self.exp_conditions = exp_conditions
        self.table_format = table_format
        self.video_format = video_format
        self.arena = arena
        self.arena_dims = arena_dims
        self.smooth_alpha = smooth_alpha
        self.p = p
        self.verbose = verbose
        self.polar_coords = polar_coords
        self.distances = distances
        self.ego = ego

        assert [re.findall("(.*)_", vid)[0] for vid in self.videos] == [
            re.findall("(.*)\.", tab)[0] for tab in self.tables
        ], "Video files should match table files"

    def __str__(self):
        if self.exp_conditions:
            return "DLC analysis of {} videos across {} conditions".format(
                len(self.videos), len(self.exp_conditions)
            )
        else:
            return "DLC analysis of {} videos".format(len(self.videos))

    def load_tables(self):
        """Loads videos and tables into dictionaries"""

        if self.verbose:
            print("Loading and smoothing trajectories...")

        if self.table_format == ".h5":
            table_dict = {
                re.findall("(.*?)_", tab)[0]: pd.read_hdf(
                    self.table_path + tab, dtype=float
                )
                for tab in self.tables
            }
        elif self.table_format == ".csv":
            table_dict = {
                re.findall("(.*?)_", tab)[0]: pd.read_csv(
                    self.table_path + tab, dtype=float
                )
                for tab in self.tables
            }

        lik_dict = defaultdict()

        for key, value in table_dict.items():
            x = value.xs("x", level="coords", axis=1, drop_level=False)
            y = value.xs("y", level="coords", axis=1, drop_level=False)
            l = value.xs("likelihood", level="coords", axis=1, drop_level=True)

            table_dict[key] = pd.concat([x, y], axis=1).sort_index(axis=1)
            lik_dict[key] = l

        if self.smooth_alpha:

            for dframe in tqdm(table_dict.keys()):
                table_dict[dframe] = table_dict[dframe].apply(
                    lambda x: smooth_mult_trajectory(x, alpha=self.smooth_alpha), axis=0
                )

        for key, tab in table_dict.items():
            table_dict[key] = tab[tab.columns.levels[0][0]]

        return table_dict, lik_dict

    def get_scale(self):
        """Returns the arena as recognised from the videos"""

        if self.arena in ["circular"]:

            scales = [
                (
                    recognize_arena(
                        self.tables,
                        self.videos,
                        vid_index,
                        path=self.video_path,
                        arena_type=self.arena,
                    )[2]
                    * 2,
                    self.arena_dims[0],
                )
                for vid_index, _ in enumerate(self.videos)
            ]

        else:
            raise NotImplementedError

        return scales

    def get_distances(self, verbose=1):
        """Computes the distances between all selected bodyparts over time.
           If ego is provided, it only returns distances to a specified bodypart"""

        table_dict, lik_dict = self.load_tables()

        if self.verbose:
            print("Computing distance based coordinates...")

        distance_dict = defaultdict()
        pandarallel.initialize(nb_workers=self.p, verbose=verbose)

        nodes = self.distances
        if nodes == "All":
            nodes = table_dict[list(table_dict.keys())[0]].columns.levels[0]

        assert [
            i in list(table_dict.values())[0].columns.levels[0] for i in nodes
        ], "Nodes should correspond to existent bodyparts"

        scales = self.get_scale()

        for ind, key in tqdm(
            enumerate(table_dict.keys()), total=len(table_dict.keys())
        ):

            distance_dict[key] = table_dict[key][nodes].parallel_apply(
                lambda x: bpart_distance(x, nodes, scales[ind][1], scales[ind][0]),
                axis=1,
            )

        if self.ego:
            for key, val in distance_dict.items():
                distance_dict[key] = val.loc[
                    :, [dist for dist in val.columns if self.ego in dist]
                ]

        return distance_dict, table_dict, lik_dict

    def get_angles(self, velocities=0):
        pass

    def run(self):
        """Generates a dataset using all the options specified during initialization"""

        if self.distances == False:
            tables, quality = self.load_tables()
            distances = None
        else:
            distances, tables, quality = self.get_distances()

        if self.polar_coords:
            for key,tab in tables.items():
                tables[key] = tab2polar(tab)

        if self.verbose == 1:
            print("Done!")

        return coordinates(
            tables,
            self.videos,
            self.arena,
            self.arena_dims,
            self.get_scale(),
            quality,
            self.exp_conditions,
            distances,
        )


class coordinates:
    def __init__(
        self,
        tables,
        videos,
        arena,
        arena_dims,
        scales,
        quality,
        exp_conditions=None,
        distances=None,
    ):
        self._tables = tables
        self.distances = distances
        self._videos = videos
        self._exp_conditions = exp_conditions
        self._arena = arena
        self._arena_dims = arena_dims
        self._scales = scales
        self._quality = quality

    def __str__(self):
        if self._exp_conditions:
            return "Coordinates of {} videos across {} conditions".format(
                len(self._videos), len(self._exp_conditions)
            )
        else:
            return "DLC analysis of {} videos".format(len(self._videos))

    def get_coords(self):
        return table_dict(self._tables)

    def get_distances(self):
        if self.distances != None:
            return table_dict(self.distances)
        raise ValueError(
            "Distances not computed. Read the documentation for more details"
        )

    def get_videos(self, play=False):
        if play:
            raise NotImplementedError

        return self._videos

    def get_exp_conditions(self):
        return self._exp_conditions

    def get_quality(self, report=False):
        if report:
            profile = ProfileReport(
                self._quality[report],
                title="Quality Report, {}".format(report),
                html={"style": {"full_width": True}},
            )
            return profile
        return self._quality

    def get_arenas(self):
        return self._arena, self._arena_dims, self._scales

    def plot_heatmaps(self, bodyparts, save=False, i=0):
        plot_heatmap(
            self._tables[i],
            bodyparts,
            xlim=self._arena_dims[0],
            ylim=self._arena_dims[0],
            save=save,
        )


class table_dict(dict):
    def __init__(self, *args, **kwargs):
        self.update(*args, **kwargs)

    def get_training_set(self):
        rmax = max([i.shape[0] for i in self.values()])

        X_train = np.concatenate(
            [np.pad(v, ((0, rmax - v.shape[0]), (0, 0))) for v in self.values()]
        )

        return X_train

    def preprocess(
        self,
        window_size=1,
        window_step=1,
        scale=True,
        test_proportion=0,
        random_state=None,
        verbose=False,
    ):
        """Builds a sliding window. If desired, splits train and test and
           Z-scores the data using sklearn's standard scaler"""

        X_train = self.get_training_set()

        if test_proportion:
            if verbose:
                print("Splitting train and test...")
            X_train, X_test = train_test_split(
                X_train, test_size=test_proportion, random_state=random_state
            )

        if scale:
            if verbose:
                print("Scaling data...")

            scaler = StandardScaler()
            X_train = scaler.fit_transform(
                X_train.reshape(-1, X_train.shape[-1])
            ).reshape(X_train.shape)

            assert np.allclose(np.mean(X_train), 0)
            assert np.allclose(np.std(X_train), 1)

            if test_proportion:
                X_test = scaler.transform(X_test.reshape(-1, X_test.shape[-1])).reshape(
                    X_test.shape
                )

            if verbose:
                print("Done!")

        X_train = rolling_window(X_train, window_size, window_step)

        if test_proportion:
            X_test = rolling_window(X_test, window_size, window_step)
            return X_train, X_test

        return X_train

    def random_projection(self, n_components=None, sample=1000):

        X = self.get_training_set()
        X = X[np.random.choice(X.shape[0], sample, replace=False), :]

        rproj = random_projection.GaussianRandomProjection(n_components=n_components)
        X = rproj.fit_transform(X)

        return X, rproj

    def pca(self, n_components=None, sample=1000, kernel='linear'):

        X = self.get_training_set()
        X = X[np.random.choice(X.shape[0], sample, replace=False), :]

        pca = KernelPCA(n_components=n_components, kernel=kernel)
        X = pca.fit_transform(X)

        return X, pca

    def tSNE(self, n_components=None, sample=1000):

        X = self.get_training_set()
        X = X[np.random.choice(X.shape[0], sample, replace=False), :]

        tsne = TSNE(n_components=n_components)
        X = tsne.fit_transform(X)

        return X, tsne
